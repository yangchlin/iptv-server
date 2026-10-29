# -*- coding: utf-8 -*-
"""中兴平台 EPG 相关 API 路由

与华为平台（telecom_api.py）完全独立：
- 前缀 /api/zte/*，配置、日志、频道、EPG 都只操作中兴自己的数据
- 频道缓存复用「电信频道」页面的通用接口（/api/telecom/cached-channels，
  该接口按当前生效平台读写），因此这里不再重复实现

认证：由 main.py 的 auth_and_security_middleware 统一强制登录。
"""
import asyncio
import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from . import config
from . import zte_epg
from .logger import logger
from .scheduler import scheduler
from .progress import download_progress
from .rtsp_proxy import (
    rtsp_proxy,
    ensure_proxy_from_settings,
    proxy_config_from_settings,
)

router = APIRouter(prefix="/api/zte", tags=["中兴EPG"])

# ── 中兴配置白名单（与 config.DEFAULT_SETTINGS["telecom_zte"] 保持一致）──
_ALLOWED_ZTE_KEYS = {
    "enabled", "server", "UserID", "Authenticator", "StbIP", "STBID",
    "UserToken", "UserGroupNMB", "EPGGroupNMB", "LastTermno", "stbinfo",
    "UserAgent", "epg_server_url", "epgname", "logo_url", "live_proxy",
    "replay_proxy", "enable_group", "epg_days", "rtsp_proxy_enabled",
    "rtsp_proxy_port", "rtsp_upstream", "rtsp_upstream_port",
    "rtsp_proxy_bad_server",
}
_ZTE_BOOL_KEYS = {"enabled", "enable_group", "rtsp_proxy_enabled"}
_ZTE_INT_KEYS = {"epg_days", "rtsp_proxy_port", "rtsp_upstream_port"}

# ── 中兴任务的实时日志 ──
_zte_logs: list = []
_ZTE_LOG_MAX = 800
_zte_running = False


def zte_log(msg: str) -> None:
    """追加一条中兴任务日志（内存环形缓冲 + 落服务端日志）。"""
    try:
        line = str(msg)
        _zte_logs.append({"t": datetime.datetime.now().strftime("%H:%M:%S"), "msg": line})
        if len(_zte_logs) > _ZTE_LOG_MAX:
            del _zte_logs[:len(_zte_logs) - _ZTE_LOG_MAX]
        logger.log(f"[中兴] {line}", category="电信EPG")
    except Exception:
        pass


@router.get("/logs")
async def zte_logs_api(since: int = 0):
    """中兴任务实时日志：前端按 since 游标增量拉取。"""
    try:
        since = max(0, int(since or 0))
    except (TypeError, ValueError):
        since = 0
    return {"running": _zte_running, "total": len(_zte_logs), "logs": _zte_logs[since:]}


# ── 把 app 通用日志镜像到中兴面板（仅中兴操作进行中）──
_zte_active = 0
_sink_busy = False


def _zte_begin() -> None:
    global _zte_active
    _zte_active += 1


def _zte_end() -> None:
    global _zte_active
    _zte_active = max(0, _zte_active - 1)


def _zte_log_sink(message: str) -> None:
    global _sink_busy
    if _sink_busy or _zte_active <= 0:
        return
    msg = str(message)
    if msg.startswith("[中兴] "):
        return
    _sink_busy = True
    try:
        zte_log(msg)
    finally:
        _sink_busy = False


logger.add_extra_sink(_zte_log_sink)


# ════════════════════════════════════════════════════════════════
# 内部工具
# ════════════════════════════════════════════════════════════════

def _require_active(data: Any = None) -> str:
    """中兴操作必须在中兴平台生效时执行，防止切错平台把数据写乱。"""
    active = config.get_active_platform()
    if active != "zte":
        label = config.platform_label(active)
        raise HTTPException(
            409,
            f"当前生效平台是「{label}」，请先在「电信EPG」页切换到「中兴平台」再操作")
    return active


def _request_base_url(request: Any) -> str:
    if request is None:
        return ""
    try:
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http")
        proto = str(proto).split(",")[0].strip() or "http"
        host = (request.headers.get("x-forwarded-host")
                or request.headers.get("host")
                or request.url.netloc or "")
        host = str(host).split(",")[0].strip()
        return f"{proto}://{host}".rstrip("/") if host else ""
    except Exception:
        return ""


def _is_loopback_base(base: str) -> bool:
    try:
        host = base.split("://", 1)[-1].split("/")[0].split(":")[0].strip().lower()
        return host in ("127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0")
    except Exception:
        return False


def _fill_output_urls(cfg: dict, request: Any = None, persist: bool = False) -> dict:
    """epg_server_url / logo_url 留空时用当前访问地址补齐。"""
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
            zte_epg.save_zte_config(out)
        except Exception as e:
            logger.warning(f"自动补齐中兴 EPG/台标地址后写回配置失败: {e}")
    return out


def _sanitize_config(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if k not in _ALLOWED_ZTE_KEYS:
            continue
        if k in _ZTE_BOOL_KEYS:
            out[k] = bool(v)
        elif k in _ZTE_INT_KEYS:
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                out[k] = 0
        else:
            out[k] = str(v)[:3000] if v is not None else ""
    return out


def _parse_days(data: Any, default: int = 8) -> int:
    try:
        n = int((data or {}).get("days", default))
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, 10))


# ════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════

@router.get("/config")
async def get_zte_config_api(request: Request):
    """读取中兴配置 + 代理状态。"""
    cfg = _fill_output_urls(zte_epg.get_zte_config(), request)
    return {
        "success": True,
        "platform": "zte",
        "platform_label": config.platform_label("zte"),
        "active": config.get_active_platform(),
        "config": cfg,
        "proxy": rtsp_proxy.status(),
        "proxy_config": proxy_config_from_settings(),
        "auto_fetch_channels": config.get_settings().get(
            "telecom_epg_auto_fetch_channels", True),
    }


@router.post("/config")
async def save_zte_config_api(data: dict = Body(...)):
    """保存中兴配置。只写 settings.telecom_zte，不动华为配置。"""
    if not isinstance(data, dict):
        raise HTTPException(400, "无效数据")

    raw_cfg = data.get("config", data)
    clean = _sanitize_config(raw_cfg)
    merged = zte_epg.get_zte_config()
    merged.update(clean)

    # 数值字段范围保护
    try:
        merged["epg_days"] = max(1, min(int(merged.get("epg_days") or 8), 10))
    except (TypeError, ValueError):
        merged["epg_days"] = 8
    for key, default in (("rtsp_proxy_port", 554), ("rtsp_upstream_port", 554)):
        try:
            port = int(merged.get(key) or default)
        except (TypeError, ValueError):
            port = default
        merged[key] = max(1, min(port, 65535))

    if not zte_epg.save_zte_config(merged):
        return {"success": False, "message": "保存失败"}

    # 代理：配置里启用就自动拉起，停用就关掉
    proxy_result = ensure_proxy_from_settings()

    scheduler.reschedule()
    return {
        "success": True,
        "message": "中兴配置已保存",
        "proxy": rtsp_proxy.status(),
        "proxy_message": proxy_result.get("message", ""),
    }


# ════════════════════════════════════════════════════════════════
# 连接 / 认证
# ════════════════════════════════════════════════════════════════

@router.post("/test-connection")
async def test_connection_api(data: dict = Body(default={})):
    _require_active(data)
    _zte_begin()
    try:
        return await asyncio.to_thread(zte_epg.zte_test_connection, zte_epg.get_zte_config())
    finally:
        _zte_end()


@router.post("/auth")
async def auth_api(data: dict = Body(default={})):
    _require_active(data)
    _zte_begin()
    try:
        r = await asyncio.to_thread(zte_epg.zte_auth, zte_epg.get_zte_config())
        return {
            "success": bool(r.get("success")),
            "message": r.get("message", ""),
            "host": r.get("host", ""),
        }
    finally:
        _zte_end()


# ════════════════════════════════════════════════════════════════
# 频道
# ════════════════════════════════════════════════════════════════

async def _fetch_and_cache_channels(cfg: dict) -> dict:
    """认证 + 拉频道 + 写 zte_channels.json + 自动并入 EPG 管理。"""
    auth = await asyncio.to_thread(zte_epg.zte_auth, cfg)
    if not auth.get("success"):
        return {"success": False, "channels": [],
                "message": f"认证失败: {auth.get('message', '')}", "auth": auth}

    r = await asyncio.to_thread(zte_epg.zte_get_channels, cfg, auth["session"])
    if r.get("success"):
        old = config.get_zte_channels().get("channels", [])
        merged = zte_epg.merge_manual_channels(old, r["channels"])
        config.save_zte_channels(merged)
        if config.get_settings().get("telecom_auto_add_to_epg", True):
            try:
                m = config.merge_telecom_channels_into_epg(merged, "zte")
                if m.get("added"):
                    zte_log(f"已自动加入 {m['added']} 个频道到「EPG 管理」（共 {m['total']} 个）")
            except Exception as e:
                logger.warning(f"[中兴] 自动并入 EPG 管理失败: {e}")
        r["channels"] = merged
    return {"success": r.get("success", False), "channels": r.get("channels", []),
            "message": r.get("message", ""), "auth": auth}


@router.post("/get-channels")
async def get_channels_api(request: Request, data: dict = Body(default={})):
    _require_active(data)
    cfg = _fill_output_urls(zte_epg.get_zte_config(), request, persist=True)

    _zte_begin()
    try:
        zte_log("开始认证并获取中兴频道列表 ...")
        r = await _fetch_and_cache_channels(cfg)
        if r["success"]:
            zte_log(f"✅ 获取到 {len(r['channels'])} 个频道，已写入 zte_channels.json")
        else:
            zte_log(f"❌ 获取频道失败: {r.get('message', '')}")

        if r["success"]:
            try:
                r["sctv"] = await asyncio.to_thread(
                    zte_epg.zte_generate_sctv_files, r["channels"], cfg)
                zte_log("✅ sctv.txt / sctv.m3u / zte.m3u 已生成")
            except Exception as e:
                r["sctv"] = {"success": False, "message": str(e)}
                zte_log(f"⚠️ 生成输出文件失败: {e}")

        return {
            "success": r["success"],
            "message": r["message"],
            "channels": r["channels"],
            "sctv": r.get("sctv"),
        }
    finally:
        _zte_end()


# ════════════════════════════════════════════════════════════════
# EPG
# ════════════════════════════════════════════════════════════════

@router.post("/download-epg")
async def download_epg_api(data: dict = Body(default={})):
    """下载单个中兴频道的 EPG。"""
    _require_active(data)
    cfg = zte_epg.get_zte_config()
    channel_id = str(data.get("channel_id", "") or "")[:200]
    channel_name = str(data.get("channel_name", "") or "")[:200]
    default_days = _parse_days({"days": cfg.get("epg_days", 8)}, 8)
    days = _parse_days(data, default_days)
    if not channel_id:
        raise HTTPException(400, "需要提供 channel_id（中兴的 channelcode）")

    auth = await asyncio.to_thread(zte_epg.zte_auth, cfg)
    if not auth.get("success"):
        return {"success": False, "message": f"认证失败: {auth.get('message', '')}",
                "programs": []}

    return await asyncio.to_thread(
        zte_epg.zte_download_epg, cfg, auth["session"], channel_id,
        channel_name, zte_epg.zte_epg_dates(days))


@router.post("/download-all-epg")
async def download_all_epg_api(request: Request, data: dict = Body(default={})):
    """启动后台任务：拉取所有中兴频道 EPG → JSON → sctv → XML。"""
    global _zte_running
    _require_active(data)
    if _zte_running:
        return {"success": False, "message": "已有中兴任务在运行，请稍候"}
    if download_progress._running:
        return {"success": False, "message": "有其他下载/提取任务在运行，请稍候"}

    cfg = _fill_output_urls(zte_epg.get_zte_config(), request, persist=True)
    days = _parse_days(data, _parse_days({"days": cfg.get("epg_days", 8)}, 8))
    force = bool(data.get("force_refresh_channels", False))
    auto_fetch = bool(data.get("auto_fetch_channels",
                               config.get_settings().get("telecom_epg_auto_fetch_channels", True)))

    _zte_running = True
    _zte_begin()
    download_progress._running = True
    download_progress.clear()
    zte_log("任务已接收，准备开始...")

    asyncio.create_task(_run_zte_epg_task(cfg, days, force, auto_fetch))
    return {"success": True, "message": "中兴EPG任务已启动，进度看下方执行日志"}


async def _run_zte_epg_task(cfg: dict, days: int, force: bool, auto_fetch: bool):
    global _zte_running
    try:
        result = await asyncio.to_thread(
            zte_epg.run_zte_job, cfg, days, force, auto_fetch, zte_log)
        if not result.get("success"):
            zte_log(f"❌ 任务结束但未成功: {result.get('message', '')}")
    except Exception as e:
        zte_log(f"❌ 执行异常: {e}")
    finally:
        _zte_running = False
        download_progress._running = False
        download_progress._paused = False
        download_progress._stopped = False
        _zte_end()


# ════════════════════════════════════════════════════════════════
# 输出 / 状态
# ════════════════════════════════════════════════════════════════

@router.post("/generate-m3u")
async def generate_m3u_api(request: Request, data: dict = Body(default={})):
    """用当前中兴频道缓存重新生成 zte.m3u（sctv.txt / sctv.m3u 也一并刷新）。"""
    _require_active(data)
    cfg = _fill_output_urls(zte_epg.get_zte_config(), request, persist=True)
    channels = config.get_zte_channels().get("channels", [])
    if not channels:
        return {"success": False, "message": "中兴频道缓存为空，请先「更新频道列表」"}

    content = await asyncio.to_thread(zte_epg.zte_generate_m3u, channels, cfg)
    m3u_path = config.M3U_DIR / "zte.m3u"
    m3u_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = m3u_path.with_suffix(".m3u.tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    tmp.replace(m3u_path)

    # 顺便刷新当前平台别名文件
    try:
        await asyncio.to_thread(zte_epg.zte_generate_sctv_files, channels, cfg)
    except Exception as e:
        logger.warning(f"[中兴] 刷新 sctv 失败: {e}")

    return {"success": True, "content": content, "saved_to": str(m3u_path)}


@router.get("/status")
async def zte_status_api():
    status = zte_epg.zte_platform_status()
    status["proxy"] = rtsp_proxy.status()
    status["active"] = config.get_active_platform()
    return status


# ════════════════════════════════════════════════════════════════
# RTSP 代理
# ════════════════════════════════════════════════════════════════

@router.get("/proxy/status")
async def proxy_status_api():
    st = rtsp_proxy.status()
    st["configured"] = proxy_config_from_settings()
    return st


@router.post("/proxy/start")
async def proxy_start_api(data: dict = Body(default={})):
    _require_active(data)
    cfg = proxy_config_from_settings()
    # 允许前端用弹窗里的临时参数启动（不落盘）
    for key in ("port", "upstream", "upstream_port", "bad_server"):
        if isinstance(data, dict) and key in data:
            cfg[key] = data[key]
    result = rtsp_proxy.start(cfg)
    result["status"] = rtsp_proxy.status()
    return result


@router.post("/proxy/stop")
async def proxy_stop_api(data: dict = Body(default={})):
    result = rtsp_proxy.stop()
    result["status"] = rtsp_proxy.status()
    return result


@router.post("/proxy/restart")
async def proxy_restart_api(data: dict = Body(default={})):
    rtsp_proxy.stop()
    result = rtsp_proxy.start(proxy_config_from_settings())
    result["status"] = rtsp_proxy.status()
    return result
