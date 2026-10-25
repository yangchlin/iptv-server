"""认证模块 - 独立于主应用

安全设计说明：
- 密码：PBKDF2-HMAC-SHA256 加盐哈希（20 万次迭代）。兼容读取旧版无盐 SHA256，
  登录成功时自动升级为新格式。
- 比较：用户名/密码均使用 hmac.compare_digest 常量时间比较，防时序侧信道。
- Token：服务端记录 expires_at（7 天），每次访问校验；登录时顺带清理过期项。
- 限流：同一 IP 5 分钟内失败 5 次后拒绝登录，成功则清零。
- 会话：修改密码后踢掉除当前 Token 外的所有会话。
- 存储：auth.json 原子写入（tmp + os.replace），权限收紧为 0600。
- Cookie：secure 属性根据实际请求协议自动判断（HTTP 调试时也能正常登录）。
- 并发：_active_tokens / _fail_counts 由 threading.Lock 保护。
"""
import datetime
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import JSONResponse

from . import config

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── 默认凭据（生产环境建议通过环境变量覆盖，并在首次登录后立即修改）──
DEFAULT_USERNAME = os.environ.get("AUTH_USERNAME", "666")
DEFAULT_PASSWORD = os.environ.get("AUTH_PASSWORD", "666")

# ── Token 有效期（秒）──
TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 天

# ── 密码哈希参数 ──
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_PREFIX = "pbkdf2"

# ── 登录失败限流参数 ──
_MAX_FAILS = 5
_FAIL_WINDOW = 300  # 秒

# ── 进程级状态 ──
# token -> {"username": str, "created_at": ISO 字符串, "expires_at": unix 秒}
_active_tokens: dict = {}
# ip -> [失败次数, 窗口起始时间]
_fail_counts: dict = {}
# 简单互斥锁：保护上面两个 dict 的读写
_lock = threading.Lock()


# ════════════════════════════════════════════════════════════════
# 密码哈希
# ════════════════════════════════════════════════════════════════

def _hash_password(password: str, salt: Optional[str] = None) -> str:
    """生成密码哈希。

    参数 salt：
      - None  → 生成随机盐，返回 "pbkdf2$<salt>$<hash>"（推荐，新写入一律走这里）
      - ""    → 返回旧版无盐 SHA256 hex（仅用于兼容读取历史 auth.json）
      - 字符串 → 用该盐计算 PBKDF2，返回 "pbkdf2$<salt>$<hash>"
    """
    if salt == "":
        # 旧格式：无盐 SHA256。只用于跟历史数据比对，不再用于新写入。
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    if salt is None:
        salt = secrets.token_hex(16)  # 128 bit 随机盐

    # PBKDF2-HMAC-SHA256，20 万次迭代，抗彩虹表与暴力破解
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    )
    return f"{_PBKDF2_PREFIX}${salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """校验密码，兼容新旧两种存储格式。返回 True/False，不抛异常。"""
    if not stored:
        return False

    if stored.startswith(_PBKDF2_PREFIX + "$"):
        parts = stored.split("$")
        if len(parts) != 3:
            return False
        _, salt, expected = parts
        # 用存储的盐重新计算，然后常量时间比较
        actual = _hash_password(password, salt).split("$")[2]
        return hmac.compare_digest(actual, expected)

    # 旧格式：无盐 SHA256
    return hmac.compare_digest(_hash_password(password, salt=""), stored)


def _is_legacy_hash(stored: str) -> bool:
    """判断是否为旧版无盐哈希（登录成功后需要自动升级）。"""
    return bool(stored) and not stored.startswith(_PBKDF2_PREFIX + "$")


def _generate_token() -> str:
    """生成 256 bit 随机 Token（hex 字符串）。"""
    return secrets.token_hex(32)


# ════════════════════════════════════════════════════════════════
# auth.json 读写
# ════════════════════════════════════════════════════════════════

def _get_auth_file() -> Path:
    """返回 auth.json 的路径。"""
    return config.CONFIG_DIR / "auth.json"


def _save_auth(data: dict) -> bool:
    """原子写入 auth.json。

    先写同目录下的 .tmp 文件，再用 os.replace 原子替换，
    避免进程崩溃/并发写入导致文件损坏。最后尝试收紧权限为 0600。
    """
    path = _get_auth_file()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # 原子替换
        try:
            os.chmod(path, 0o600)  # 仅属主可读写
        except OSError:
            pass  # Windows 等不支持时忽略
        return True
    except OSError:
        # 失败时清理临时文件
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def _load_auth() -> dict:
    """加载认证配置。

    优先读取文件；文件不存在/为空/损坏时回退到默认凭据（加盐格式，不落盘）。
    """
    path = _get_auth_file()
    try:
        if path.exists() and path.stat().st_size > 0:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("username") and data.get("password_hash"):
                return data
    except (json.JSONDecodeError, OSError):
        # 文件损坏：不覆盖原文件，仅本次使用默认值
        pass

    return {
        "username": DEFAULT_USERNAME,
        "password_hash": _hash_password(DEFAULT_PASSWORD),  # 随机盐
    }


def _ensure_auth_file():
    """启动时确保 auth.json 存在（加盐格式）。已存在则不覆盖。"""
    path = _get_auth_file()
    if not path.exists() or path.stat().st_size == 0:
        _save_auth({
            "username": DEFAULT_USERNAME,
            "password_hash": _hash_password(DEFAULT_PASSWORD),
        })
        # 提示风险，但不打印密码本身
        if DEFAULT_USERNAME == "666" and DEFAULT_PASSWORD == "666":
            print("[认证] 警告：正在使用默认凭据 666/666，请尽快在「账户密码」中修改！")


# 模块导入时执行一次
_ensure_auth_file()


# ════════════════════════════════════════════════════════════════
# Token 管理
# ════════════════════════════════════════════════════════════════

def _purge_expired_tokens() -> None:
    """清理所有已过期的 Token（登录时顺带调用，避免字典无限增长）。"""
    now = time.time()
    with _lock:
        expired = [t for t, info in _active_tokens.items()
                   if info.get("expires_at", 0) <= now]
        for t in expired:
            _active_tokens.pop(t, None)


def _lookup_token(token: str) -> Optional[dict]:
    """查找并校验 Token；过期则顺手移除。返回 info 的副本或 None。"""
    if not token:
        return None
    with _lock:
        info = _active_tokens.get(token)
        if not info:
            return None
        if info.get("expires_at", 0) <= time.time():
            _active_tokens.pop(token, None)
            return None
        return dict(info)


def _extract_token(request: Request) -> str:
    """从 Cookie 或 X-Auth-Token 头中提取 Token。"""
    return request.cookies.get("auth_token") or request.headers.get("X-Auth-Token", "")


# ════════════════════════════════════════════════════════════════
# 请求辅助
# ════════════════════════════════════════════════════════════════

def _client_ip(request: Request) -> str:
    """获取真实客户端 IP（支持反向代理）。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_https(request: Request) -> bool:
    """判断当前请求是否为 HTTPS（含反向代理场景）。"""
    if request.url.scheme == "https":
        return True
    xfp = request.headers.get("x-forwarded-proto", "")
    return xfp.split(",")[0].strip().lower() == "https"


def _is_rate_limited(ip: str) -> bool:
    """该 IP 是否已触发登录失败限流。"""
    now = time.time()
    with _lock:
        entry = _fail_counts.get(ip)
        if not entry:
            return False
        count, first_ts = entry
        if now - first_ts > _FAIL_WINDOW:
            # 窗口已过期，重置
            _fail_counts.pop(ip, None)
            return False
        return count >= _MAX_FAILS


def _record_failure(ip: str) -> None:
    """记录一次登录失败。"""
    now = time.time()
    with _lock:
        entry = _fail_counts.get(ip)
        if not entry or now - entry[1] > _FAIL_WINDOW:
            _fail_counts[ip] = [1, now]
        else:
            entry[0] += 1


def _clear_failures(ip: str) -> None:
    """登录成功后清零失败计数。"""
    with _lock:
        _fail_counts.pop(ip, None)


# ════════════════════════════════════════════════════════════════
# 认证依赖
# ════════════════════════════════════════════════════════════════

async def require_auth(request: Request) -> str:
    """FastAPI 依赖：要求已登录，返回 Token。

    用法：
        @app.get("/api/xxx")
        async def xxx(_=Depends(require_auth)):
            ...
    """
    token = _extract_token(request)
    info = _lookup_token(token)
    if not info:
        raise HTTPException(401, "未登录或登录已过期")
    return token


# ════════════════════════════════════════════════════════════════
# 路由
# ════════════════════════════════════════════════════════════════

@router.post("/login")
async def login(request: Request, data: dict = Body(...)):
    """登录。

    请求：{"username": "...", "password": "..."}
    成功：{"success": true, "token": "...", "message": "登录成功"} + Set-Cookie
    失败：401 {"detail": "用户名或密码错误"}
    限流：429 {"detail": "登录失败次数过多，请稍后再试"}
    """
    ip = _client_ip(request)

    # 1. 限流检查
    if _is_rate_limited(ip):
        raise HTTPException(429, "登录失败次数过多，请稍后再试")

    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        raise HTTPException(400, "用户名和密码不能为空")

    auth = _load_auth()
    expected_user = str(auth.get("username", ""))
    stored_hash = str(auth.get("password_hash", ""))

    # 2. 常量时间比较：即使用户名错误也执行密码校验，避免通过响应时间区分
    user_ok = hmac.compare_digest(username, expected_user)
    pass_ok = _verify_password(password, stored_hash)

    if not (user_ok and pass_ok):
        _record_failure(ip)
        raise HTTPException(401, "用户名或密码错误")

    # 3. 登录成功：清空失败计数
    _clear_failures(ip)

    # 4. 旧格式哈希 → 自动升级为加盐格式
    if _is_legacy_hash(stored_hash):
        auth["password_hash"] = _hash_password(password)
        _save_auth(auth)

    # 5. 清理过期 Token（避免字典膨胀）
    _purge_expired_tokens()

    # 6. 生成新 Token 并登记
    token = _generate_token()
    now = time.time()
    with _lock:
        _active_tokens[token] = {
            "username": expected_user,
            "created_at": datetime.datetime.now().isoformat(),
            "expires_at": now + TOKEN_TTL_SECONDS,
        }

    # 7. 返回响应 + Cookie
    resp = JSONResponse({"success": True, "token": token, "message": "登录成功"})
    resp.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,            # 禁止 JS 读取，降低 XSS 盗取风险
        max_age=TOKEN_TTL_SECONDS,
        path="/",
        samesite="lax",           # 缓解 CSRF
        secure=_is_https(request),  # HTTPS 下强制 Secure；HTTP 调试时不设，避免 Cookie 被浏览器丢弃
    )
    return resp


@router.post("/logout")
async def logout(request: Request):
    """登出：服务端立即失效该 Token，并删除 Cookie。"""
    token = _extract_token(request)
    if token:
        with _lock:
            _active_tokens.pop(token, None)

    resp = JSONResponse({"success": True, "message": "已登出"})
    resp.delete_cookie("auth_token", path="/")
    return resp


@router.get("/check")
async def check_auth(request: Request):
    """检查登录状态。"""
    token = _extract_token(request)
    info = _lookup_token(token)
    if info:
        return {"logged_in": True, "username": info["username"]}
    return {"logged_in": False}


@router.post("/change-username")
async def change_username(request: Request, data: dict = Body(...)):
    """修改用户名（需已登录）。"""
    await require_auth(request)

    new_username = (data.get("new_username") or "").strip()
    if not new_username:
        raise HTTPException(400, "用户名不能为空")
    if len(new_username) > 20:
        raise HTTPException(400, "用户名最多20个字符")

    auth = _load_auth()
    auth["username"] = new_username
    if not _save_auth(auth):
        raise HTTPException(500, "保存失败")

    # 同步更新所有活跃 Token 中的用户名（/check 与 /username 会用到）
    with _lock:
        for info in _active_tokens.values():
            info["username"] = new_username

    return {"success": True, "message": "用户名已修改"}


@router.post("/change-password")
async def change_password(request: Request, data: dict = Body(...)):
    """修改密码（需已登录）。修改成功后踢掉其他所有会话。"""
    current_token = await require_auth(request)

    old_password = data.get("old_password") or ""
    new_password = data.get("new_password") or ""

    # 保持与前端一致的 4 位下限，避免前端校验冲突
    if len(new_password) < 4:
        raise HTTPException(400, "新密码至少4位")

    auth = _load_auth()
    if not _verify_password(old_password, auth.get("password_hash", "")):
        raise HTTPException(403, "旧密码错误")

    # 用新盐重新哈希
    auth["password_hash"] = _hash_password(new_password)
    if not _save_auth(auth):
        raise HTTPException(500, "保存失败")

    # 踢掉除当前会话外的所有 Token（防会话劫持后继续使用）
    with _lock:
        for tok in list(_active_tokens.keys()):
            if tok != current_token:
                _active_tokens.pop(tok, None)

    return {"success": True, "message": "密码已修改"}


@router.get("/username")
async def get_username(request: Request):
    """获取当前登录用户名。"""
    token = _extract_token(request)
    info = _lookup_token(token)
    if info:
        return {"username": info["username"]}
    return {"username": None}