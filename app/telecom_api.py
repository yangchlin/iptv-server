# -*- coding: utf-8 -*-
"""
电信 EPG 相关 API 路由

认证：所有 /api/telecom/* 路径均通过 main.py 的 auth_and_security_middleware
强制登录，本文件无需再加 Depends(require_auth)。

进度对象：从 progress.py 导入共享的 download_progress，与 main.py 为同一实例，
避免原先引用未定义变量导致的 NameError。
"""
import asyncio
import datetime
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from . import config
from . import xml_builder
from .crypt_1 import find_key_parallel
from .logger import logger
from .scheduler import scheduler
from .progress import download_progress          # ★ 修复：从独立模块导入，避免 NameError
from .telecom_epg import (
    get_telecom_config,
    save_telecom_config,
    test_telecom_connection,
    telecom_auth,
    telecom_get_channels,
    telecom_download_epg,
    telecom_generate_m3u,
    telecom_sync_to_server,
    generate_sctv_files,
)

router = APIRouter(prefix="/api/telecom", tags=["电信EPG"])

# ── 电信配置白名单（与 config.DEFAULT_SETTINGS["telecom"] 保持一致）──
_ALLOWED_TELECOM_KEYS = {
    "enabled", "AuthenticationIP", "key", "UserID", "mac", "STBID", "ip",
    "STBType", "STBVersion", "UserAgent", "Authenticator", "RTP2HTTPD",
    "templateName", "areaId", "epg_server_url", "epgname", "logo_url",
    "merge_output_files", "auth_require_key", "enable_group", "r2h_token",
}
# 布尔类型字段（其余按字符串处理）
_TELECOM_BOOL_KEYS = {"enabled", "merge_output_files", "auth_require_key", "enable_group"}

# ── 电信任务的实时日志 ──
# 电信的频道抓取 / EPG 下载是长任务，日志单独存一份给「电信EPG」页轮询，
# 不再写 download_progress（否则会顶到页面顶部的全局进度条上）。
_telecom_logs: list = []
_TELECOM_LOG_MAX = 800
_telecom_running = False


def telecom_log(msg: str) -> None:
    """追加一条电信任务日志（内存环形缓冲 + 落服务端日志）。"""
    try:
        line = str(msg)
        _telecom_logs.append({
            "t": datetime.datetime.now().strftime("%H:%M:%S"),
            "msg": line,
        })
        if len(_telecom_logs) > _TELECOM_LOG_MAX:
            del _telecom_logs[:len(_telecom_logs) - _TELECOM_LOG_MAX]
        logger.log(f"[电信] {line}", category="电信EPG")
    except Exception:
        pass


@router.get("/logs")
async def telecom_logs_api(since: int = 0):
    """电信任务实时日志：前端按 since 游标增量拉取。"""
    try:
        since = max(0, int(since or 0))
    except (TypeError, ValueError):
        since = 0
    return {
        "running": _telecom_running,
        "total": len(_telecom_logs),
        "logs": _telecom_logs[since:],
    }


# ── 把 app 通用日志镜像到电信面板（仅电信操作进行中）──
_telecom_active = 0
_sink_busy = False


def _telecom_begin() -> None:
    global _telecom_active
    _telecom_active += 1


def _telecom_end() -> None:
    global _telecom_active
    _telecom_active = max(0, _telecom_active - 1)


def _telecom_log_sink(message: str) -> None:
    """logger 每条日志的实时回调：电信操作期间镜像到面板。"""
    global _sink_busy
    if _sink_busy or _telecom_active <= 0:
        return
    msg = str(message)
    if msg.startswith("[电信] "):        # telecom_log 自己写的，跳过以免重复
        return
    _sink_busy = True
    try:
        telecom_log(msg)
    finally:
        _sink_busy = False


logger.add_extra_sink(_telecom_log_sink)
# 频道对象允许的字段（telecom_epg / zte_epg 生成的字段集，两个平台共用）
_CHANNEL_KEEP_KEYS = {
    "channel_id", "name", "user_channel_id", "channel_url", "timeshift",
    "timeshift_length", "rtsp_url", "fcc_enable", "fcc_ip", "fcc_port", "fec_port",
    "_manual", "_disabled", "tvg_id", "channel_no",  # ★ 新增这两个标记 ★ 新增 "tvg_id"
}

# schedule 时间格式：HH:MM
_TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


# ════════════════════════════════════════════════════════════════
# 内部辅助
# ════════════════════════════════════════════════════════════════

def _get_effective_config(data: Any) -> dict:
    """★ 一律使用已保存的配置（app_config.json 的 telecom 段）。

    前端只负责"编辑并保存"，所有认证/取频道/下载 EPG 都从配置文件读取，
    避免请求体里的临时参数和落盘配置不一致。
    """
    return get_telecom_config()


def _require_active_platform() -> str:
    """华为的写操作必须在「华为平台」生效时执行，防止切错平台写乱数据。"""
    platform = config.get_active_platform()
    if platform != "huawei":
        raise HTTPException(
            409,
            f"当前生效平台是「{config.platform_label(platform)}」，"
            f"请先在「电信EPG」页切换到「华为平台」再操作")
    return platform


def _request_base_url(request: Any) -> str:
    """当前访问地址（如 http://iptv.example.com），供 EPG / 台标直链使用。

    优先 X-Forwarded-*，这样放在 nginx / 反代 / 内网穿透后面时拿到的是
    用户浏览器实际用的协议和域名，而不是容器里的 127.0.0.1:8001。
    """
    if request is None:
        return ""
    try:
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http")
        proto = str(proto).split(",")[0].strip() or "http"
        host = (request.headers.get("x-forwarded-host")
                or request.headers.get("host")
                or request.url.netloc or "")
        host = str(host).split(",")[0].strip()
        if not host:
            return ""
        return f"{proto}://{host}".rstrip("/")
    except Exception:
        return ""


def _is_loopback_base(base: str) -> bool:
    """127.0.0.1 / localhost 只对本机有意义，别把它写进配置。"""
    try:
        host = base.split("://", 1)[-1].split("/")[0].split(":")[0].strip().lower()
        return host in ("127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0")
    except Exception:
        return False


def _fill_output_urls(cfg: dict, request: Any = None, persist: bool = False) -> dict:
    """epg_server_url / logo_url 留空时用当前访问地址自动补齐。

    - epg_server_url 为空 → 用当前访问地址（配合 epgname 得到 <地址>/epg.xml，
      也就是「生成XML」合并后输出的那份 EPG）
    - logo_url 为空       → 用当前访问地址（台标走 /logo/<频道名>.png）
    - RTP2HTTPD 是组播转单播服务地址，必须手工填，这里绝不自动改。

    persist=True 时把补齐后的值写回 app_config.json，这样定时任务（没有请求
    上下文）生成 sctv.txt / sctv.m3u 时也能用上同一套地址。
    """
    out = dict(cfg or {})
    changed = False
    base = _request_base_url(request)
    if base:
        if not str(out.get("epg_server_url") or "").strip():
            out["epg_server_url"] = base
            changed = True
        if not str(out.get("logo_url") or "").strip():
            out["logo_url"] = base
            changed = True
    if not str(out.get("epgname") or "").strip():
        out["epgname"] = "epg.xml"
        changed = True

    if persist and changed and base and not _is_loopback_base(base):
        try:
            save_telecom_config(out)
        except Exception as e:
            logger.warning(f"自动补齐 EPG/台标地址后写回配置失败: {e}")
    return out


def _sanitize_telecom_config(raw: Any) -> dict:
    """★ 电信配置白名单 + 类型清洗，防塞入任意键污染 settings。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if k not in _ALLOWED_TELECOM_KEYS:
            continue
        if k in _TELECOM_BOOL_KEYS:
            out[k] = bool(v)
        else:
            out[k] = str(v)[:2000] if v is not None else ""
    return out


def _parse_days(data: Any, default: int = 3) -> int:
    """★ 修复：days 限制在 1..30，防生成超长日期列表。"""
    try:
        n = int((data or {}).get("days", default))
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, 30))


def _sanitize_channels(raw: Any) -> list:
    """★ 频道列表白名单：只保留 telecom_epg 生成的字段，且限制数量/长度。"""
    if not isinstance(raw, list):
        return []
    out = []
    for ch in raw[:5000]:                         # 上限 5000 个频道
        if not isinstance(ch, dict):
            continue
        item = {}
        for k in _CHANNEL_KEEP_KEYS:
            if k in ch:
                item[k] = str(ch[k])[:500]
        out.append(item)
    return out


def _apply_schedule(data: dict, settings: dict) -> bool:
    """★ 校验 schedule.time / interval_days 后写入 settings。返回是否发生改动。"""
    changed = False
    for key, prefix in (("channels", "telecom_channels"), ("epg", "telecom_epg")):
        item = (data.get("schedule") or {}).get(key) or {}
        if not isinstance(item, dict):
            continue

        if "enabled" in item:
            settings[f"{prefix}_cron_enabled"] = bool(item["enabled"])
            changed = True

        if "mode" in item:
            settings[f"{prefix}_cron_mode"] = "interval" if item["mode"] == "interval" else "daily"
            changed = True

        if "time" in item:
            t = str(item["time"]).strip()
            if _TIME_RE.match(t):                  # ★ 只接受 HH:MM
                settings[f"{prefix}_cron_time"] = t
                changed = True

        if "interval_days" in item:
            try:
                settings[f"{prefix}_cron_interval"] = max(1, min(int(item["interval_days"]), 365))
                changed = True
            except (TypeError, ValueError):
                pass
    return changed


def _sanitize_channel_arg(v: Any, max_len: int = 200) -> str:
    """频道参数长度限制。"""
    return str(v or "")[:max_len]


# ════════════════════════════════════════════════════════════════
# 配置读写
# ════════════════════════════════════════════════════════════════

@router.get("/config")
async def get_telecom_config_api(request: Request):
    """读取电信配置 + 定时任务 + 空缓存自动获取开关。

    epg_server_url / logo_url 为空时，返回「当前访问地址」，前端弹窗直接显示，
    用户点保存即可落盘（不保存也照样能用：生成 m3u/txt 时会自动补）。
    """
    s = config.get_settings()
    return {
        "config": _fill_output_urls(get_telecom_config(), request),
        "auto_fetch_channels": s.get("telecom_epg_auto_fetch_channels", True),
        "schedule": {
            "channels": {
                "enabled":       s.get("telecom_channels_cron_enabled", False),
                "mode":          s.get("telecom_channels_cron_mode", "daily"),
                "time":          s.get("telecom_channels_cron_time", "02:00"),
                "interval_days": s.get("telecom_channels_cron_interval", 3),
                "last_run":      s.get("telecom_channels_last_run", ""),
            },
            "epg": {
                "enabled":       s.get("telecom_epg_cron_enabled", False),
                "mode":          s.get("telecom_epg_cron_mode", "daily"),
                "time":          s.get("telecom_epg_cron_time", "03:00"),
                "interval_days": s.get("telecom_epg_cron_interval", 3),
                "last_run":      s.get("telecom_epg_last_run", ""),
            },
        },
    }


@router.post("/config")
async def save_telecom_config_api(data: dict = Body(...)):
    """保存电信配置 + 定时任务。★ 输入全部走白名单/格式校验。"""
    if not isinstance(data, dict):
        raise HTTPException(400, "无效数据")

    # 1. 电信配置白名单化
    raw_cfg = data.get("config", data)
    clean_cfg = _sanitize_telecom_config(raw_cfg)
    # 合并已有配置，保留未传字段（避免前端只传部分字段导致丢失）
    merged_cfg = get_telecom_config()
    merged_cfg.update(clean_cfg)

    if not save_telecom_config(merged_cfg):
        return {"success": False, "message": "保存失败"}

    # 2. 定时任务 & 其它开关
    s = config.get_settings()
    if "auto_fetch_channels" in data:
        s["telecom_epg_auto_fetch_channels"] = bool(data["auto_fetch_channels"])

    _apply_schedule(data, s)
    config.save_settings(s)
    scheduler.reschedule()
    return {"success": True, "message": "配置已保存"}


# ════════════════════════════════════════════════════════════════
# 反推 key
#
# 原理：Authenticator 的明文是 {key}${EncryptToken}${UserID}${STBID}${ip}${mac}$$CTC，
# 前 8 字节就是 key 本身。所以拿配置里现有的 Authenticator，穷举 00000000-99999999
# 逐个解密并比对前缀，命中即为 key。
# 找到后会写回 key，并用它认证一次、把现场生成的 Authenticator 一并写回配置。
# ════════════════════════════════════════════════════════════════

_key_search = {
    "running": False,
    "tested": 0,
    "total": 0,
    "elapsed": 0.0,
    "found": "",
    "message": "",
    "started_at": "",
}
_key_stop = threading.Event()


def _run_key_search(auth_hex: str, max_iters: int, workers: int) -> None:
    started = time.time()
    _key_search.update({
        "running": True, "tested": 0, "total": max_iters, "elapsed": 0.0,
        "found": "", "message": "正在反推 key…",
        "started_at": datetime.datetime.now().strftime("%H:%M:%S"),
    })
    _key_stop.clear()
    last = {"t": 0.0, "pct": -1}

    def progress(tested, total, elapsed):
        _key_search["tested"] = tested
        _key_search["elapsed"] = round(elapsed, 1)
        pct = int(tested * 100 / total) if total else 0
        now = time.time()
        if now - last["t"] >= 15 or pct // 10 != last["pct"] // 10:
            last["t"] = now
            last["pct"] = pct
            speed = tested / elapsed if elapsed else 0
            eta = (total - tested) / speed if speed else 0
            telecom_log(
                f"反推 key：{tested:,}/{total:,}（{pct}%），{speed:,.0f} 次/秒，"
                f"预计还需 {eta / 60:.1f} 分钟")

    telecom_log(f"开始反推 key：范围 00000000-{max_iters - 1:08d}，Authenticator {len(auth_hex)} 字符")
    try:
        keys = find_key_parallel(
            auth_hex, max_iters=max_iters, workers=workers,
            progress=progress, should_stop=_key_stop.is_set,
        )
    except Exception as e:
        _key_search.update({"running": False, "message": f"反推失败: {e}"})
        telecom_log(f"❌ 反推 key 失败: {e}")
        return

    if not keys:
        stopped = _key_stop.is_set()
        msg = ("已手动停止反推" if stopped
               else "没找到 key（它可能不是 8 位数字，或 Authenticator 不是这种格式生成的）")
        _key_search.update({"running": False, "message": msg})
        telecom_log(("⏹ " if stopped else "❌ ") + msg)
        return

    found = keys[0]
    cfg = get_telecom_config()
    cfg["key"] = found
    save_telecom_config(cfg)
    _key_search.update({
        "running": False, "found": found,
        "message": f"已找到 key={found}，已写入配置",
    })
    telecom_log(
        f"✅ 找到 key: {found}（试了 {_key_search['tested']:,} 个，用时 "
        f"{time.time() - started:.0f} 秒），已写入配置")

    # ★ 新增：写入 key.txt 备份
    try:
        path = config.LOG_DIR / "key.txt"
        path.write_text(
            f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"找到的加密 KEY：{found}\n"
            f"该 KEY 已自动写入配置文件 app_config.json。\n",
            encoding="utf-8",
        )
        telecom_log(f"📄 已将结果保存至日志目录的 key.txt")
    except Exception as e:
        telecom_log(f"⚠️ 保存 key.txt 失败: {e}")

    # 用新 key 认证一次：现场生成新的 Authenticator，并由 telecom_auth 写回配置
    try:
        telecom_log("正在用新 key 认证，并更新 Authenticator ...")
        r = telecom_auth(get_telecom_config())
        if r.get("success"):
            telecom_log("✅ 新 key 认证成功，Authenticator 已更新")
        else:
            telecom_log(f"⚠️ 新 key 认证未成功: {r.get('message', '')}")
    except Exception as e:
        telecom_log(f"⚠️ 用新 key 认证失败: {e}")


@router.post("/find-key")
async def find_key_api(data: dict = Body(default={})):
    """用配置里的 Authenticator 反推 8 位数字 key（后台执行，进度看执行日志）。"""
    cfg = get_telecom_config()
    auth_hex = str(cfg.get("Authenticator") or "").strip()
    if not auth_hex:
        return {"success": False,
                "message": "配置里没有 Authenticator，无法反推 key（反推需要一段用它加密过的密文）"}
    if _key_search.get("running"):
        return {"success": False, "message": "已经在反推中，进度看下方执行日志"}

    payload = data if isinstance(data, dict) else {}
    try:
        max_iters = int(payload.get("max_iters") or 100_000_000)
    except (TypeError, ValueError):
        max_iters = 100_000_000
    max_iters = max(10_000, min(max_iters, 100_000_000))
    try:
        workers = int(payload.get("workers") or 0)
    except (TypeError, ValueError):
        workers = 0
    workers = max(0, min(workers, 8))

    threading.Thread(target=_run_key_search,
                     args=(auth_hex, max_iters, workers), daemon=True).start()
    return {"success": True,
            "message": f"已开始反推 key（最多试 {max_iters:,} 个），进度看下方执行日志"}


@router.get("/find-key/status")
async def find_key_status():
    return dict(_key_search)


@router.post("/find-key/stop")
async def find_key_stop():
    _key_stop.set()
    return {"success": True, "message": "已请求停止，当前批次跑完后结束"}


# ════════════════════════════════════════════════════════════════
# 连接测试 / 认证
# ════════════════════════════════════════════════════════════════

@router.post("/test-connection")
async def test_connection_api(data: dict = Body(...)):
    """测试电信服务器连接。"""
    config_data = _get_effective_config(data)
    _telecom_begin()
    try:
        return await asyncio.to_thread(test_telecom_connection, config_data)
    finally:
        _telecom_end()


@router.post("/auth")
async def auth_api(data: dict = Body(...)):
    """执行电信认证。"""
    config_data = _get_effective_config(data)
    _telecom_begin()
    try:
        result = await asyncio.to_thread(telecom_auth, config_data)
        if result["success"]:
            logger.info(f"电信认证成功: host={result['host']}")
        else:
            logger.warning(f"电信认证失败: {result.get('message', '')}")
        return result
    finally:
        _telecom_end()


# ════════════════════════════════════════════════════════════════
# 频道列表
# ════════════════════════════════════════════════════════════════

@router.post("/get-channels")
async def get_channels_api(request: Request, data: dict = Body(...)):
    """获取频道列表并缓存。

    ★ 修复：原代码把未定义的 cfg 传给 _fetch_and_cache_channels 与
    generate_sctv_files，必 NameError。现统一用 config_data。
    ★ EPG / 台标地址留空时用当前访问地址补齐（并写回配置，供定时任务复用）。
    """
    _require_active_platform()
    config_data = _fill_output_urls(_get_effective_config(data), request, persist=True)

    _telecom_begin()
    try:
        # ① 认证 + 拉取频道 + 写缓存
        telecom_log("开始认证并获取频道列表 ...")
        r = await _fetch_and_cache_channels(config_data)
        if r["success"]:
            telecom_log(f"✅ 获取到 {len(r['channels'])} 个频道，已写入缓存")
        else:
            telecom_log(f"❌ 获取频道失败: {r.get('message', '')}")

        # ② 成功则生成 sctv.txt / sctv.m3u
        if r["success"]:
            try:
                r["sctv"] = await asyncio.to_thread(generate_sctv_files, r["channels"], config_data)
                telecom_log(f"✅ sctv.txt / sctv.m3u 已生成（{len(r['channels'])} 个频道）")
            except Exception as e:
                logger.warning(f"生成 sctv 文件失败: {e}")
                r["sctv"] = {"success": False, "message": str(e)}

        return {
            "success": r["success"],
            "message": r["message"],
            "channels": r["channels"],
            "sctv": r.get("sctv"),
        }
    finally:
        _telecom_end()


@router.get("/cached-channels")
async def get_cached_channels():
    """返回当前生效平台的频道缓存（华为 telecom_channels.json / 中兴 zte_channels.json）。"""
    platform = config.get_active_platform()
    c = config.get_platform_channels(platform)
    channels = c.get("channels", [])
    cfg = config.get_platform_config(platform)
    return {
        "platform": platform,
        "platform_label": config.platform_label(platform),
        "channels": channels,
        "updated": c.get("updated", ""),
        "count": len(channels),
        "enable_group": cfg.get("enable_group", False), # ★ 下发开关
    }

@router.put("/cached-channels")
async def save_cached_channels_api(data: dict = Body(...)):
    """手工编辑频道缓存（写回当前生效平台），并重建该平台的 sctv / XML。"""
    platform = config.get_active_platform()
    raw = data.get("channels")
    if not isinstance(raw, list):
        raise HTTPException(400, "channels 必须是数组")
    if len(raw) > 5000:
        raise HTTPException(400, "频道数量过多")

    cleaned = []
    seen_ids = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        ch = {}
        for k in _CHANNEL_KEEP_KEYS:
            if k not in item:
                continue
            if k in ("_manual", "_disabled"):
                ch[k] = bool(item[k])
            else:
                ch[k] = str(item[k] or "")[:500]

        name = ch.get("name", "").strip()
        cid = ch.get("channel_id", "").strip()
        url = ch.get("channel_url", "").strip()

        if not name:
            return {"success": False, "message": "频道名称不能为空"}
        if not cid and not url:
            return {"success": False,
                    "message": f"频道「{name}」的 channel_id 和 channel_url 至少填一个"}

        # channel_id 非空时才查重
        if cid:
            if cid in seen_ids:
                return {"success": False, "message": f"channel_id 重复: {cid}"}
            seen_ids.add(cid)

        if item.get("_manual"):
            ch["_manual"] = True
        else:
            ch.pop("_manual", None)
        cleaned.append(ch)

    if not cleaned:
        return {"success": False, "message": "至少保留一个频道"}

    if not config.save_platform_channels(platform, cleaned):
        return {"success": False, "message": "写入缓存失败"}

    # ★ 新增：保存开关（写到当前平台的配置段）
    if "enable_group" in data:
        cfg = config.get_platform_config(platform)
        cfg["enable_group"] = bool(data["enable_group"])
        if platform == "huawei":
            save_telecom_config(cfg)
        else:
            from . import zte_epg
            zte_epg.save_zte_config(cfg)

    # 自动重生成 sctv 和 XML（按当前平台）
    try:
        if platform == "huawei":
            cfg = _fill_output_urls(config.get_platform_config("huawei"), None, persist=False)
            generate_sctv_files(cleaned, cfg)
        else:
            from . import zte_epg
            cfg = config.get_platform_config("zte")
            zte_epg.zte_generate_sctv_files(cleaned, cfg)
        xml_builder.build_telecom_xml(platform=platform)
    except Exception as e:
        logger.warning(f"[电信] 保存频道后重新生成文件失败: {e}")

    return {"success": True, "message": f"已保存 {len(cleaned)} 个频道",
            "count": len(cleaned), "platform": platform}

# ════════════════════════════════════════════════════════════════
# EPG 下载
# ════════════════════════════════════════════════════════════════

@router.post("/download-epg")
async def download_epg_api(data: dict = Body(...)):
    """下载单个频道的 EPG。"""
    _require_active_platform()
    config_data = _get_effective_config(data)
    channel_id = _sanitize_channel_arg(data.get("channel_id", ""))
    channel_name = _sanitize_channel_arg(data.get("channel_name", ""))
    days = _parse_days(data, 3)

    if not channel_id or not channel_name:
        raise HTTPException(400, "需要提供 channel_id 和 channel_name")

    auth_result = await asyncio.to_thread(telecom_auth, config_data)
    if not auth_result["success"]:
        return {"success": False, "message": f"认证失败: {auth_result['message']}"}

    today = datetime.date.today()
    dates = [(today + datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]

    # ★ requests 是阻塞的，放线程池，别卡住事件循环
    return await asyncio.to_thread(
        telecom_download_epg,
        host=auth_result["host"],
        cookies=auth_result["cookies"],
        user_token=auth_result["user_token"],
        stbid=auth_result["stbid"],
        channel_id=channel_id,
        channel_name=channel_name,
        dates=dates,
        config_data=config_data,
    )


@router.post("/download-all-epg")
async def download_all_epg_api(request: Request, data: dict = Body(...)):
    """启动后台任务：拉取所有频道 EPG → 同步 JSON → 重建 XML。"""
    global _telecom_running
    _require_active_platform()
    if _telecom_running:
        return {"success": False, "message": "已有电信任务在运行，请稍候"}
    if download_progress._running:
        return {"success": False, "message": "有其他下载/提取任务在运行，请稍候"}

    config_data = _fill_output_urls(_get_effective_config(data), request, persist=True)
    days = _parse_days(data, 3)
    force = bool(data.get("force_refresh_channels", False))
    auto_fetch = bool(
        data.get("auto_fetch_channels",
                 config.get_settings().get("telecom_epg_auto_fetch_channels", True))
    )

    # 标记 + 互斥锁都在这里同步设置，前端轮询第一次就能看到 running=true
    _telecom_running = True
    _telecom_begin()                      # 期间把 app 日志镜像到面板
    download_progress._running = True     # 仅作互斥锁，不写消息 → 顶部进度条不会出现
    download_progress.clear()
    telecom_log("任务已接收，准备开始...")

    asyncio.create_task(_run_telecom_epg_task(config_data, days, force, auto_fetch))
    return {"success": True, "message": "电信EPG任务已启动，进度看下方执行日志"}


async def _run_telecom_epg_task(cfg: dict, days: int, force: bool, auto_fetch: bool):
    """后台执行：频道获取 + EPG 下载 + JSON 同步 + XML 重建。

    日志统一走 telecom_log()，实时显示在「电信EPG」页的执行日志里。
    """
    global _telecom_running
    try:
        telecom_log("===== 开始电信频道/EPG 获取 =====")

        # ── 1. 确定频道来源 ──
        cached = config.get_telecom_channels()
        channels = cached.get("channels", [])
        need_fetch = force or not channels

        if need_fetch and not (force or auto_fetch):
            telecom_log("❌ 频道缓存为空且未开启自动抓取")
            return

        auth = None
        if need_fetch:
            telecom_log("1. 正在认证并获取频道列表...")
            fetch = await _fetch_and_cache_channels(cfg)
            if not fetch["success"]:
                telecom_log(f"❌ 获取频道失败: {fetch['message']}")
                return
            channels = fetch["channels"]
            auth = fetch["auth"]
            telecom_log(f"✅ 成功获取 {len(channels)} 个频道")

        if not channels:
            telecom_log("❌ 无可用频道")
            return

        # 缓存命中时也要重新认证
        if auth is None:
            telecom_log("1. 正在重新认证...")
            auth = await asyncio.to_thread(telecom_auth, cfg)
            if not auth["success"]:
                telecom_log(f"❌ 认证失败: {auth['message']}")
                return

        # ── 2. 逐个频道下载 EPG ──
        today = datetime.date.today()
        dates = [(today + datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]
        total = len(channels)
        all_epg = {"channels": [], "total": total}
        ok = fail = 0
        telecom_log(f"2. 开始下载 {total} 个频道的 EPG（{dates[0]} ~ {dates[-1]}）...")

        for i, ch in enumerate(channels, 1):
            name = ch.get("name", "") or ch.get("channel_id", "")
            r = await asyncio.to_thread(
                telecom_download_epg,
                host=auth["host"], cookies=auth["cookies"],
                user_token=auth["user_token"], stbid=auth["stbid"],
                channel_id=ch.get("channel_id", ""), channel_name=name,
                dates=dates, config_data=cfg,
            )
            if r["success"]:
                ok += 1
                all_epg["channels"].append({
                    "name": name,
                    "channel_id": ch.get("channel_id", ""),
                    "programs": r["programs"],
                })
                telecom_log(f"  [{i}/{total}] {name}: {len(r['programs'])} 条")
            else:
                fail += 1
                telecom_log(f"  [{i}/{total}] ⚠️ {name} 失败: {r.get('message', '')}")

            await asyncio.sleep(0.5)             # 避免并发过高被服务器封禁

        # ── 3. 同步 JSON + 生成 sctv ──
        telecom_log(f"3. 同步 JSON + 生成 sctv（成功 {ok} / 失败 {fail}）...")
        try:
            await asyncio.to_thread(telecom_sync_to_server, all_epg, cfg)
            if config.get_active_platform() == "huawei":
                await asyncio.to_thread(generate_sctv_files, channels, cfg)
                telecom_log("✅ _telecom JSON / sctv 已写入")
            else:
                telecom_log("⚠️ 平台已切换到其它平台，跳过 sctv 生成（JSON 仍按华为后缀保存）")
        except Exception as e:
            telecom_log(f"⚠️ 同步/生成失败: {e}")

        # 平台被切走时不要再重建 XML，避免把华为数据写进当前（中兴）的输出
        if config.get_active_platform() != "huawei":
            telecom_log("⚠️ 平台已切换到其它平台，跳过 XML 重建")
            telecom_log(f"===== 任务结束: 成功 {ok} / 失败 {fail} =====")
            return

        # ── 4. 重建主 XML ──
        telecom_log("4. 正在重建 epg.xml ...")
        try:
            await asyncio.to_thread(xml_builder.build_xml,
                                    log_callback=lambda m: telecom_log(m),
                                    platform="huawei")
            telecom_log("✅ epg.xml 重建完成")
        except Exception as e:
            telecom_log(f"❌ epg.xml 生成失败: {e}")

        # ── 5. 华为专用 XML ──
        telecom_log("5. 正在生成华为专用 XML (epg_telecom.xml) ...")
        try:
            tr = await asyncio.to_thread(xml_builder.build_telecom_xml,
                                         log_callback=lambda m: telecom_log(m),
                                         platform="huawei")
            if tr.get("success"):
                telecom_log(
                    f"✅ epg_telecom.xml: {tr['channel_count']} 频道 / {tr['programme_count']} 节目"
                )
            else:
                telecom_log(f"⚠️ epg_telecom.xml 跳过: {tr.get('error', '')}")
        except Exception as e:
            telecom_log(f"⚠️ 华为 XML 生成失败: {e}")

        telecom_log(f"===== 全部完成: 成功 {ok} / 失败 {fail} =====")

    except Exception as e:
        telecom_log(f"❌ 执行异常: {e}")
        try:
            import traceback
            telecom_log(traceback.format_exc()[-600:])
        except Exception:
            pass
    finally:
        _telecom_running = False
        download_progress._running = False
        download_progress._paused = False
        download_progress._stopped = False
        _telecom_end()


# ════════════════════════════════════════════════════════════════
# 生成 M3U
# ════════════════════════════════════════════════════════════════

@router.post("/generate-m3u")
async def generate_m3u_api(request: Request, data: dict = Body(...)):
    """根据 channels + 输出配置生成 telecom.m3u。"""
    _require_active_platform()
    # 与 /config 一致：缺失 config 时读取已保存配置
    config_data = _fill_output_urls(_get_effective_config(data), request, persist=True)
    channels = _sanitize_channels(data.get("channels", []))

    m3u_content = telecom_generate_m3u(channels, config_data)

    m3u_dir = config.M3U_DIR
    m3u_dir.mkdir(parents=True, exist_ok=True)
    m3u_path = m3u_dir / "telecom.m3u"

    # ★ 原子写，避免客户端读取到写一半的 M3U
    tmp = m3u_path.with_suffix(".m3u.tmp")
    tmp.write_text(m3u_content, encoding="utf-8", newline="\n")
    tmp.replace(m3u_path)

    return {
        "success": True,
        "content": m3u_content,
        "saved_to": str(m3u_path),
    }


# ════════════════════════════════════════════════════════════════
# 内部函数
# ════════════════════════════════════════════════════════════════

async def _fetch_and_cache_channels(cfg: dict) -> dict:
    """认证 + 获取频道列表 + 写入缓存。

    返回 {"success", "channels", "message", "auth"}
    """
    auth = await asyncio.to_thread(telecom_auth, cfg)
    if not auth["success"]:
        return {
            "success": False,
            "channels": [],
            "message": f"认证失败: {auth['message']}",
            "auth": auth,
        }

    r = await asyncio.to_thread(
        telecom_get_channels,
        host=auth["host"],
        cookies=auth["cookies"],
        user_token=auth["user_token"],
        stbid=auth["stbid"],
        config_data=cfg,
    )
    if r["success"]:
        fresh = r["channels"]
        old = config.get_telecom_channels().get("channels", [])
        
        # ★ 核心逻辑：用 (channel_id, name) 做 key，保留带有 _manual 的频道
        def ch_key(c):
            cid = str(c.get("channel_id", "") or "").strip()
            if cid:
                return ("cid", cid)
            return ("name", str(c.get("name", "") or "").strip())

        manual = {ch_key(c): c for c in old if c.get("_manual")}
        merged = []
        
        for ch in fresh:
            k = ch_key(ch)
            if k in manual:
                m = dict(ch)
                # 把你改过的名字、地址、禁用状态覆盖回来
                for f in ("name", "channel_url", "rtsp_url", "_disabled"):
                    if f in manual[k]:
                        m[f] = manual[k][f]
                m["_manual"] = True
                merged.append(m)
                manual.pop(k, None)
            else:
                merged.append(ch)
                
        # 把你手工加的、但这次没拉到的频道（比如广元）追加到末尾
        merged.extend(manual.values())

        config.save_telecom_channels(merged)
        _auto_merge_into_epg(merged)
        r["channels"] = merged

    return {
        "success": r["success"],
        "channels": r.get("channels", []),
        "message": r.get("message", ""),
        "auth": auth,
    }


def _auto_merge_into_epg(channels: list) -> None:
    """把电信频道自动并入「EPG 管理」，让电信节目能进入主 epg.xml。"""
    try:
        if not config.get_settings().get("telecom_auto_add_to_epg", True):
            return
        merged = config.merge_telecom_channels_into_epg(channels, "huawei")
        if merged.get("added"):
            telecom_log(
                f"已自动加入 {merged['added']} 个频道到「EPG 管理」（共 {merged['total']} 个）"
            )
    except Exception as e:
        logger.warning(f"[电信] 自动并入 EPG 管理失败: {e}")