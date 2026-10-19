"""认证模块 - 独立于主应用"""
import datetime
import hashlib
import secrets
import json
import os
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import JSONResponse

from . import config

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 默认凭据
DEFAULT_USERNAME = os.environ.get("AUTH_USERNAME", "666")
DEFAULT_PASSWORD = os.environ.get("AUTH_PASSWORD", "666")

# 活跃 token（进程级）
_active_tokens: dict = {}


def _get_auth_file() -> Path:
    return config.CONFIG_DIR / "auth.json"


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _verify_password(password: str, password_hash: str) -> bool:
    return _hash_password(password) == password_hash


def _generate_token() -> str:
    return secrets.token_hex(32)


def _save_auth(data: dict) -> bool:
    """写入 auth.json，忽略权限错误"""
    path = _get_auth_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def _load_auth() -> dict:
    """加载认证配置：优先文件，文件不存在则用默认值"""
    path = _get_auth_file()
    try:
        if path.exists() and path.stat().st_size > 0:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("username") and data.get("password_hash"):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    # 文件不存在或无效 → 返回默认值
    return {"username": DEFAULT_USERNAME, "password_hash": _hash_password(DEFAULT_PASSWORD)}


def _ensure_auth_file():
    """确保 auth.json 存在，不存在则创建（启动时调用一次）"""
    path = _get_auth_file()
    if not path.exists() or path.stat().st_size == 0:
        creds = {"username": DEFAULT_USERNAME, "password_hash": _hash_password(DEFAULT_PASSWORD)}
        _save_auth(creds)


# 启动时确保 auth.json 存在
_ensure_auth_file()


# ─── 认证依赖 ──────────────────────────────────────────────────

async def require_auth(request: Request):
    """依赖注入：验证请求是否已登录"""
    token = request.cookies.get("auth_token") or request.headers.get("X-Auth-Token", "")
    if not token or token not in _active_tokens:
        raise HTTPException(401, "未登录")
    return token


# ─── 路由 ──────────────────────────────────────────────────────

@router.post("/login")
async def login(data: dict = Body(...)):
    """登录"""
    username = data.get("username", "").strip()
    password = data.get("password", "")

    auth = _load_auth()

    if username != auth.get("username", ""):
        raise HTTPException(401, "用户名或密码错误")

    if not _verify_password(password, auth.get("password_hash", "")):
        raise HTTPException(401, "用户名或密码错误")

    # 生成 Token
    token = _generate_token()
    _active_tokens[token] = {
        "username": username,
        "created_at": datetime.datetime.now().isoformat(),
    }

    resp = JSONResponse({"success": True, "token": token, "message": "登录成功"})
    resp.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,
        max_age=86400 * 7,  # 7 天
        path="/",
        samesite="lax",
    )
    return resp


@router.post("/logout")
async def logout(request: Request):
    """登出"""
    token = request.cookies.get("auth_token") or request.headers.get("X-Auth-Token", "")
    _active_tokens.pop(token, None)
    resp = JSONResponse({"success": True, "message": "已登出"})
    resp.delete_cookie("auth_token", path="/")
    return resp


@router.get("/check")
async def check_auth(request: Request):
    """检查登录状态"""
    token = request.cookies.get("auth_token") or request.headers.get("X-Auth-Token", "")
    if token and token in _active_tokens:
        return {"logged_in": True, "username": _active_tokens[token]["username"]}
    return {"logged_in": False}


@router.post("/change-username")
async def change_username(request: Request, data: dict = Body(...)):
    """修改用户名（仅需登录状态）"""
    await require_auth(request)

    new_username = data.get("new_username", "").strip()
    if not new_username:
        raise HTTPException(400, "用户名不能为空")
    if len(new_username) > 20:
        raise HTTPException(400, "用户名最多20个字符")

    auth = _load_auth()
    auth["username"] = new_username
    _save_auth(auth)
    return {"success": True, "message": "用户名已修改"}


@router.post("/change-password")
async def change_password(request: Request, data: dict = Body(...)):
    """修改密码"""
    await require_auth(request)

    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")
    if len(new_password) < 4:
        raise HTTPException(400, "新密码至少4位")

    auth = _load_auth()
    if not _verify_password(old_password, auth.get("password_hash", "")):
        raise HTTPException(403, "旧密码错误")

    auth["password_hash"] = _hash_password(new_password)
    _save_auth(auth)
    return {"success": True, "message": "密码已修改"}


@router.get("/username")
async def get_username(request: Request):
    """获取当前用户名"""
    token = request.cookies.get("auth_token") or request.headers.get("X-Auth-Token", "")
    if token and token in _active_tokens:
        return {"username": _active_tokens[token]["username"]}
    return {"username": None}
