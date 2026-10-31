"""iptv-server EPG 管理系统主应用"""
import asyncio
import datetime
import gzip
import io
import json
import re
import time
import uuid
import httpx
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, HTTPException, Query, Body
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    HTMLResponse, JSONResponse, FileResponse, Response
)
from fastapi.staticfiles import StaticFiles

from . import auth as auth_mod
from . import config
from . import downloader
from . import xml_builder
from . import epg_parser
from . import m3u_handler
from .telecom_epg import append_r2h_token
from . import zte_epg
from .logger import logger
from .scheduler import scheduler
from .auth import router as auth_router
from .telecom_api import router as telecom_router
from .telecom_platform import router as platform_router
from .zte_api import router as zte_router
from .rtsp_proxy import ensure_proxy_from_settings, rtsp_proxy
from .progress import download_progress   # 与 telecom_api.py 共享的进度对象


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    # 中兴回看代理：配置里启用了就随服务启动（失败只记日志，不影响主服务）
    try:
        r = ensure_proxy_from_settings()
        if not r.get("success"):
            logger.warning(f"[中兴代理] 启动失败: {r.get('message', '')}")
        elif rtsp_proxy.status().get("running"):
            logger.log(f"[中兴代理] {r.get('message', '')}", category="电信EPG")
    except Exception as e:
        logger.warning(f"[中兴代理] 启动失败: {e}")
    yield
    try:
        rtsp_proxy.stop()
    except Exception:
        pass
    scheduler.stop()
    await downloader.close_client()


app = FastAPI(title="iptv-server EPG 管理系统", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)

static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth_router, prefix="")
app.include_router(telecom_router)
app.include_router(platform_router)
app.include_router(zte_router)


# ════════════════════════════════════════════════════════════════
# 安全工具
# ════════════════════════════════════════════════════════════════

_INVALID_FS_CHARS = re.compile(r'[\\:*?"<>|\x00-\x1f]')
_HOST_RE = re.compile(r'^[A-Za-z0-9.\-]+(:\d{1,5})?$')


def _safe_basename(name: str, max_len: int = 200) -> str:
    """取纯文件名（去目录、去非法字符），用于 m3u/icon/share 上传场景。"""
    if not name:
        return ""
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = _INVALID_FS_CHARS.sub("_", name)
    name = name.lstrip(". ").strip()
    if len(name) > max_len:
        name = name[:max_len]
    return name


def _safe_path_under(base: Path, rel: str) -> Path:
    """把 rel 解析到 base 下，防 ../ 逃逸。越界抛 HTTPException(400)。"""
    base = base.resolve()
    rel = (rel or "").replace("\\", "/")
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(400, "非法路径")
    return target


# ── 下载用 MIME 映射（统一下载入口 / 共享目录共用）──
_MEDIA_TYPES = {
    ".apk": "application/vnd.android.package-archive",
    ".ipa": "application/octet-stream",
    ".exe": "application/octet-stream",
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".m3u": "audio/x-mpegurl",
    ".m3u8": "application/x-mpegURL",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".xml": "application/xml",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}


# ── 认证白名单 ──
_PUBLIC_PATHS = {
    "/",
    "/favicon.ico",                      # ★ 本次：浏览器兜底请求，避免 401
    "/robots.txt",                       # ★ 本次：爬虫探测，避免 401
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/check",
    "/api/auth/username",
    "/api/xml",
    "/api/xml/epg.xml",
    "/api/xml/epg.xml.gz",
    "/epg.xml",
    "/epg.xml.gz",
}


def _is_public_path(path: str, method: str = "GET") -> bool:
    """判断请求是否免认证。

    公开：首页 / 静态资源 / EPG 直链 / 统一下载入口（m3u、图标、xml）。
    需登录：其余业务接口，以及共享目录文件（`/api/download/share/...`
    由路由内部再判一次，因为同一入口还要放行 m3u/图标/xml）。
    """
    if path in _PUBLIC_PATHS:
        return True
    if path.startswith("/static/"):
        return True
    if path.startswith("/logo/"):
        # 台标短链：播放器 / 客户端匿名取图
        return True
    if path.startswith("/tools/"):
        # 外部工具下载（IPTV.exe 等），和原来的 /static/tools/... 一样免登录
        return True
    if method in ("GET", "HEAD"):
        # 统一下载入口：文件本身公开，但下载任务接口（start/history/progress）仍需登录
        if path.startswith("/api/download/"):
            seg = path[len("/api/download/"):].split("/", 1)[0]
            return seg not in ("start", "history", "progress")
    return False


def _is_request_authed(request: Request) -> bool:
    # ★ 修改：调用 auth_mod._extract_token 动态获取当前端口对应的 Token
    token = auth_mod._extract_token(request)
    return auth_mod._lookup_token(token) is not None


# ════════════════════════════════════════════════════════════════
# 认证 + 安全响应头中间件
# ════════════════════════════════════════════════════════════════

@app.middleware("http")
async def auth_and_security_middleware(request: Request, call_next):
    path = request.url.path
    if not _is_public_path(path, request.method):     # ★ 传 method
        if not _is_request_authed(request):
            return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)

    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: http: https:; "
        "connect-src 'self'  http: https:;"
    )
    return response


# ════════════════════════════════════════════════════════════════
# 输入清洗
# ════════════════════════════════════════════════════════════════

_CHANNEL_FIELDS = {"name", "url", "tvg_id", "logic_id", "enabled", "sort_order"}
_EPG_CHANNEL_FIELDS = {
    "name", "tvg_id", "tvg_name", "logic_id", "enabled", "sort_order",
    "source_type", "source_remark", "extract_name",
}
_EXTRACT_FIELDS = {"name", "source", "source_url", "tvg_id", "rule", "logic_id",
                   "target_logic_id"}


def _sanitize_record(data: dict, allowed: set, str_max: int = 2000) -> dict:
    """只保留 allowed 中的字段，字符串截断，int/bool 转换。"""
    if not isinstance(data, dict):
        return {}
    out = {}
    for k in allowed:
        if k not in data:
            continue
        v = data[k]
        if k == "enabled":
            out[k] = bool(v)
        elif k == "sort_order":
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                out[k] = 0
        else:
            s = str(v) if v is not None else ""
            out[k] = s[:str_max]
    return out


# ════════════════════════════════════════════════════════════════
# 页面
# ════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index():
    """登录页 / 单页应用入口（公开）。"""
    html_path = static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>EPG 管理界面</h1><p>请确保 index.html 存在</p>")


@app.get("/api/status")
async def api_status():
    """系统状态（需登录）。"""
    settings = config.get_settings()
    channels = config.get_channels()
    extracts = config.get_epg_extracts()
    active_platform = config.get_active_platform()
    platform_cfg = config.get_platform_config(active_platform)
    channel_total = len(channels) + len(extracts)
    json_count = len(list(config.JSON_DIR.glob("*.json")))
    icon_count = len([f for f in config.ICONS_DIR.iterdir() if f.is_file()]) if config.ICONS_DIR.exists() else 0
    xml_path = config.XML_DIR / "epg.xml"
    xml_size = "0 KB"
    if xml_path.exists():
        size_kb = xml_path.stat().st_size / 1024
        xml_size = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"

    return {
        "status": "running",
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cron_time": settings.get("cron_time", "11:00"),
        "cron_enabled": settings.get("cron_enabled", True),
        "downloading": download_progress._running,
        "stats": {
            "channel_total": channel_total,
            "json_count": json_count,
            "icon_count": icon_count,
            "xml_size": xml_size,
        },
        "telecom_platform": active_platform,
        "telecom_platform_label": config.platform_label(active_platform),
        "telecom_enabled": platform_cfg.get("enabled", False),
        "telecom_configured": bool(platform_cfg.get("UserID")
                                   and (platform_cfg.get("STBID") or platform_cfg.get("mac"))),
    }


# ════════════════════════════════════════════════════════════════
# 频道管理
# ════════════════════════════════════════════════════════════════

@app.get("/api/channels")
async def get_channels():
    return {"channels": config.get_channels()}


@app.post("/api/channels")
async def add_channel(data: dict = Body(...)):
    channels = config.get_channels()
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    tvg_id = data.get("tvg_id", "")

    if not name or not url:
        raise HTTPException(400, "频道名称和链接不能为空")
    if len(name) > 200 or len(url) > 2000:
        raise HTTPException(400, "名称或链接过长")

    # 自动检测日期并替换为 {date}
    url = re.sub(r'_\d{8}\.json', '_{date}.json', url)

    if not tvg_id:
        existing_ids = [ch.get("tvg_id", "0") for ch in channels]
        max_id = 0
        for eid in existing_ids:
            try:
                max_id = max(max_id, int(eid))
            except (ValueError, TypeError):
                pass
        tvg_id = str(max_id + 1)

    channel = {
        "name": name,
        "url": url,
        "tvg_id": str(tvg_id),
        "logic_id": str(uuid.uuid4()),
        "enabled": True,
        "sort_order": len(channels),
    }
    channels.append(channel)
    config.save_channels(channels)
    return {"success": True, "channel": channel}


@app.put("/api/channels/{index}")
async def update_channel(index: int, data: dict = Body(...)):
    channels = config.get_channels()
    if index < 0 or index >= len(channels):
        raise HTTPException(404, "频道不存在")

    ch = channels[index]
    if "name" in data:
        ch["name"] = str(data["name"])[:200]
    if "url" in data:
        url = str(data["url"])[:2000]
        url = re.sub(r'_\d{8}\.json', '_{date}.json', url)
        ch["url"] = url
    if "tvg_id" in data:
        ch["tvg_id"] = str(data["tvg_id"])[:50]
    if "enabled" in data:
        ch["enabled"] = bool(data["enabled"])
    if "sort_order" in data:
        try:
            ch["sort_order"] = int(data["sort_order"])
        except (TypeError, ValueError):
            pass

    channels[index] = ch
    config.save_channels(channels)
    return {"success": True}


@app.delete("/api/channels/{index}")
async def delete_channel(index: int):
    channels = config.get_channels()
    if index < 0 or index >= len(channels):
        raise HTTPException(404, "频道不存在")
    deleted = channels.pop(index)
    config.save_channels(channels)
    return {"success": True, "deleted": deleted}


@app.post("/api/channels/clear")
async def clear_channels():
    config.save_channels([])
    return {"success": True}


@app.post("/api/channels/reorder")
async def reorder_channels(data: dict = Body(...)):
    """拖拽排序。indices 必须是 0..n-1 的排列。"""
    indices = data.get("indices")
    channels = config.get_channels()
    n = len(channels)
    if not isinstance(indices, list) or sorted(indices) != list(range(n)):
        raise HTTPException(400, "indices 必须是 0..n-1 的排列")
    reordered = [channels[i] for i in indices]
    for idx, ch in enumerate(reordered):
        ch["sort_order"] = idx
    config.save_channels(reordered)
    return {"success": True}


@app.post("/api/channels/replace-url")
async def replace_url(data: dict = Body(...)):
    old_part = (data.get("old") or "").strip()
    new_part = (data.get("new") or "").strip()
    if not old_part:
        raise HTTPException(400, "请填写需要替换的内容")
    if len(old_part) > 500 or len(new_part) > 500:
        raise HTTPException(400, "替换内容过长")

    channels = config.get_channels()
    count = 0
    for ch in channels:
        if old_part in ch.get("url", ""):
            ch["url"] = ch["url"].replace(old_part, new_part)
            count += 1
    config.save_channels(channels)
    return {"success": True, "count": count}


@app.post("/api/channels/export", include_in_schema=False)
@app.get("/api/channels/export")
async def export_channels():
    channels = config.get_channels()
    content = json.dumps(channels, ensure_ascii=False, indent=2).replace("\r\n", "\n")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition":
                 f"attachment; filename=channels_{datetime.date.today().strftime('%Y%m%d')}.json"},
    )


@app.post("/api/channels/import")
async def import_channels(request: Request):
    body = await request.body()
    if len(body) > 10 * 1024 * 1024:
        raise HTTPException(400, "导入文件过大")
    try:
        data = json.loads(body)
        if isinstance(data, list):
            raw_list = data
        elif isinstance(data, dict) and "channels" in data:
            raw_list = data["channels"]
        else:
            raise HTTPException(400, "无效的导入格式")
    except json.JSONDecodeError:
        raise HTTPException(400, "无效的 JSON 格式")

    if not isinstance(raw_list, list) or not raw_list:
        raise HTTPException(400, "导入数据里没有频道列表")
    if len(raw_list) > 5000:
        raise HTTPException(400, "一次最多导入 5000 个频道")

    channels = []
    for ch in raw_list:
        clean = _sanitize_record(ch, _CHANNEL_FIELDS)
        if not clean.get("name") or not clean.get("url"):
            continue
        if not clean.get("logic_id"):
            clean["logic_id"] = str(uuid.uuid4())
        if "enabled" not in clean:
            clean["enabled"] = True
        channels.append(clean)

    if not channels:
        raise HTTPException(400, "没有有效的频道（name/url 不能为空）")

    config.save_channels(channels)
    return {"success": True, "count": len(channels)}


# ════════════════════════════════════════════════════════════════
# EPG 频道管理
# ════════════════════════════════════════════════════════════════

@app.get("/api/epg-channels")
async def get_epg_channels_list():
    return {"channels": config.get_epg_channels()}


@app.post("/api/epg-channels")
async def add_epg_channel(data: dict = Body(...)):
    channels = config.get_epg_channels()
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "频道名称不能为空")
    if len(name) > 200:
        raise HTTPException(400, "频道名称过长")
    tvg_id = str(data.get("tvg_id", ""))[:50] or name
    tvg_name = str(data.get("tvg_name", name))[:200]
    channel = {
        "name": name,
        "tvg_id": tvg_id,
        "tvg_name": tvg_name,
        "logic_id": str(data.get("logic_id", ""))[:100] or str(uuid.uuid4()),
        "source_type": "manual",          # 手动添加：刷新同步时保留
        "source_remark": "手动添加",
        "enabled": True,
        "sort_order": len(channels),
    }
    channels.append(channel)
    config.save_epg_channels(channels)
    return {"success": True, "channel": channel}


@app.put("/api/epg-channels/{index}")
async def update_epg_channel(index: int, data: dict = Body(...)):
    channels = config.get_epg_channels()
    if index < 0 or index >= len(channels):
        raise HTTPException(404, "频道不存在")
    ch = channels[index]
    for key in ("name", "tvg_id", "tvg_name", "logic_id",
                "source_type", "source_remark", "extract_name"):
        if key in data:
            ch[key] = str(data[key])[:200]
    if "enabled" in data:
        ch["enabled"] = bool(data["enabled"])
    if "sort_order" in data:
        try:
            ch["sort_order"] = int(data["sort_order"])
        except (TypeError, ValueError):
            pass
    channels[index] = ch
    config.save_epg_channels(channels)
    return {"success": True}


@app.delete("/api/epg-channels/{index}")
async def delete_epg_channel(index: int):
    channels = config.get_epg_channels()
    if index < 0 or index >= len(channels):
        raise HTTPException(404, "频道不存在")
        
    deleted = channels.pop(index)
    config.save_epg_channels(channels)
    
    # ★ 新增：同步清理关联的提取记录，防止死灰复燃
    lid = deleted.get("logic_id")
    if lid:
        extracts = config.get_epg_extracts()
        # 过滤掉：目标是被删频道的记录，或者是提取记录本身生成的独立频道
        new_extracts = [
            e for e in extracts 
            if str(e.get("target_logic_id", "")) != str(lid) and str(e.get("logic_id", "")) != str(lid)
        ]
        if len(new_extracts) != len(extracts):
            config.save_epg_extracts(new_extracts)
            
    return {"success": True}


@app.post("/api/epg-channels/clear")
async def clear_epg_channels():
    config.save_epg_channels([])
    return {"success": True}


@app.post("/api/epg-channels/reorder")
async def reorder_epg_channels(data: dict = Body(...)):
    indices = data.get("indices")
    channels = config.get_epg_channels()
    n = len(channels)
    if not isinstance(indices, list) or sorted(indices) != list(range(n)):
        raise HTTPException(400, "indices 必须是 0..n-1 的排列")
    reordered = [channels[i] for i in indices]
    for idx, ch in enumerate(reordered):
        ch["sort_order"] = idx
    config.save_epg_channels(reordered)
    return {"success": True}


@app.post("/api/epg-channels/sync-all")
async def sync_all_epg_channels():
    """把「外部 EPG 提取记录」增量补进 EPG 管理。

    只增不删：
    - 提取记录里有、EPG 管理里没有的 → 补一条
    - 已有的不动（不会覆盖你改过的名字 / tvg-id）
    - 不会因为「频道管理」或来源变动删除任何频道（清理请用 /cleanup-orphans）
    """
    extracts = config.get_epg_extracts()
    channels = config.get_epg_channels()

    # 提取记录没有 logic_id 的（比如手工导入的旧数据）补一个，保证能稳定对应
    changed = False
    for ext in extracts:
        if not ext.get("logic_id"):
            ext["logic_id"] = str(uuid.uuid4())
            changed = True
    if changed:
        config.save_epg_extracts(extracts)

    by_lid = {str(c.get("logic_id", "")): c for c in channels if c.get("logic_id")}
    by_name = {str(c.get("name", "")): c for c in channels}
    taken_tvg = {str(c.get("tvg_id", "")) for c in channels}

    added = 0
    for ext in extracts:
        if ext.get("target_logic_id"):
            continue                       # 已并入别的频道，不单独建
        name = str(ext.get("name", "") or "").strip()
        lid = str(ext.get("logic_id", "") or "")
        if not name or not lid:
            continue

        existing = by_lid.get(lid) or by_name.get(name)
        if existing is not None:
            # 已有就只补元信息，绝不改名字
            if not existing.get("logic_id"):
                existing["logic_id"] = lid
            if not existing.get("source_type"):
                existing["source_type"] = "extract"
                existing["source_remark"] = str(ext.get("source", ""))[:200]
                existing["extract_name"] = name
            continue

        tvg_id = name
        suffix = 2
        while tvg_id in taken_tvg:
            tvg_id = f"{name}_{suffix}"
            suffix += 1
        taken_tvg.add(tvg_id)

        ch = {
            "name": name,
            "tvg_id": tvg_id,
            "tvg_name": name,
            "logic_id": lid,
            "source_type": "extract",
            "source_remark": str(ext.get("source", ""))[:200],
            "extract_name": name,
            "enabled": True,
            "sort_order": len(channels),
        }
        channels.append(ch)
        by_lid[lid] = ch
        by_name[name] = ch
        added += 1

    config.save_epg_channels(channels)
    return {"success": True, "added": added, "count": len(channels)}


@app.post("/api/epg-channels/cleanup-orphans")
async def cleanup_epg_orphans():
    """清理「来源已不存在」的频道（电信 / 手动 / 提取记录还在的都保留）。"""
    extracts = config.get_epg_extracts()
    extract_ids = {str(e.get("logic_id", "")) for e in extracts if e.get("logic_id")}

    keep, removed = [], []
    for ch in config.get_epg_channels():
        st = str(ch.get("source_type", ""))
        lid = str(ch.get("logic_id", ""))
        if st in ("telecom", "manual") or (lid and lid in extract_ids):
            keep.append(ch)
        else:
            removed.append(str(ch.get("name", "")))

    if removed:
        config.save_epg_channels(keep)
    return {"success": True, "removed": len(removed),
            "names": removed[:50], "count": len(keep)}


# ════════════════════════════════════════════════════════════════
# XML 管理
# ════════════════════════════════════════════════════════════════

@app.get("/api/xml")
async def get_xml():
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>',
                        media_type="application/xml")
    return FileResponse(str(xml_path), media_type="application/xml")


@app.get("/api/xml/url")
async def get_xml_url(request: Request):
    """返回可复制的 XML 直链（Host 头白名单校验，防 Host 头注入）。"""
    host = request.headers.get("host", "")
    if not _HOST_RE.match(host):
        host = "localhost:5673"
    scheme = request.headers.get("x-forwarded-proto", "http").split(",")[0].strip().lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    return {"url": f"{scheme}://{host}/api/xml/epg.xml"}


@app.head("/api/xml/epg.xml", include_in_schema=False)
@app.get("/api/xml/epg.xml")
async def get_xml_file():
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>',
                        media_type="application/xml")
    return FileResponse(str(xml_path), media_type="application/xml",
                        headers={"Cache-Control": "public, max-age=300"})


@app.head("/api/xml/epg.xml.gz", include_in_schema=False)
@app.get("/api/xml/epg.xml.gz")
async def get_xml_gz():
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>',
                        media_type="application/xml")
    xml_bytes = xml_path.read_bytes()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        gz.write(xml_bytes)
    return Response(content=buf.getvalue(), media_type="application/gzip")


@app.post("/api/xml/build")
async def build_xml():
    """手动生成 XML（同步重活放线程池）。"""
    if download_progress._running:
        return {"success": False, "error": "下载任务正在进行中，请等待完成"}
    active = config.get_active_platform()
    result = await asyncio.to_thread(
        xml_builder.build_xml,
        log_callback=lambda msg: logger.log(msg),
        platform=active,
    )
    # 平台专用 XML 一起刷新（没获取过该平台频道时内部会跳过，不影响主 XML）
    telecom_xml = await asyncio.to_thread(
        xml_builder.build_telecom_xml,
        log_callback=lambda msg: logger.log(msg, category="电信EPG"),
        platform=active,
    )
    result["telecom_xml"] = {
        k: telecom_xml.get(k)
        for k in ("success", "channel_count", "programme_count", "error")
    }
    return result


# ════════════════════════════════════════════════════════════════
# 下载管理
# ════════════════════════════════════════════════════════════════

@app.post("/api/download/start")
async def start_download():
    if download_progress._running:
        return {"success": False, "error": "已有下载任务正在运行"}

    download_progress.clear()
    download_progress._running = True
    download_progress._paused = False
    download_progress._stopped = False

    today = datetime.date.today()
    dates = [
        today.strftime("%Y%m%d"),
        (today + datetime.timedelta(days=1)).strftime("%Y%m%d"),
        (today + datetime.timedelta(days=2)).strftime("%Y%m%d"),
    ]
    asyncio.create_task(_run_download(dates))
    return {"success": True, "message": "下载任务已启动"}


@app.post("/api/download/history")
async def download_history():
    if download_progress._running:
        return {"success": False, "error": "已有下载任务正在运行"}

    download_progress.clear()
    download_progress._running = True
    download_progress._paused = False
    download_progress._stopped = False

    today = datetime.date.today()
    dates = [(today - datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(6, 0, -1)]
    asyncio.create_task(_run_download(dates))
    return {"success": True, "message": "历史下载任务已启动"}


async def _run_download(dates):
    """执行下载任务（支持暂停/继续/停止）。"""
    try:
        channels = config.get_channels()
        epg_channels = config.get_epg_channels()
        epg_name_map = {}
        for ech in epg_channels:
            lid = ech.get("logic_id", "")
            if lid:
                epg_name_map[lid] = ech.get("name", "")

        total = sum(1 for ch in channels if ch.get("enabled", True))
        current = 0
        await download_progress.add_message(f"开始下载，共 {len(dates)} 天，{total} 个频道")

        for ch in channels:
            if not ch.get("enabled", True):
                continue
            current += 1

            if download_progress._stopped:
                await download_progress.add_message("下载已停止")
                break

            while download_progress._paused and not download_progress._stopped:
                await asyncio.sleep(0.5)
            if download_progress._stopped:
                break

            download_progress.set_progress(current, total)

            save_name = epg_name_map.get(ch.get("logic_id", ""), ch.get("name", ""))
            ch_copy = dict(ch)
            if save_name:
                ch_copy["name"] = save_name

            await downloader.download_channel_json(
                ch_copy, dates,
                progress_callback=download_progress.add_message,
                log_callback=lambda msg: logger.log(msg),
            )

        await asyncio.to_thread(downloader.cleanup_old_files)
        await download_progress.add_message("下载任务完成")
    except Exception as e:
        await download_progress.add_message(f"下载出错: {str(e)}")
    finally:
        download_progress._running = False
        download_progress._paused = False
        download_progress._stopped = False


@app.get("/api/download/progress")
async def get_download_progress():
    return {
        "running": download_progress._running,
        "paused": download_progress._paused,
        "messages": download_progress.get_messages()[-200:],
        "current": download_progress._current,
    }


# ════════════════════════════════════════════════════════════════
# 外部 EPG 源管理
# ════════════════════════════════════════════════════════════════

@app.get("/api/epg/sources")
async def get_epg_sources():
    """EPG 源列表（附带缓存文件名 / 是否已下载，方便看出“为什么搜不到”）。"""
    sources = config.get_epg_sources()
    cache_dir = config.CONFIG_DIR / "epg_cache"
    files = [f.name for f in cache_dir.glob("*.xml")] if cache_dir.exists() else []
    out = []
    for s in sources:
        item = dict(s)                      # cache_name 保留用户填的值（可能为空＝自动）
        effective = downloader.epg_cache_name(s)
        item["cache_target"] = effective    # 实际会写成的文件名（自动 or 自定义）
        hit = effective if effective in files else ""
        if not hit:
            # 兼容老版本留下的缓存（txml.xml 这种），只用于显示“已下载”
            # ★ 修复：旧命名的兜底也改成"精确文件名"，不用子串
            #   （否则两个同名 txml.gz 的源会互相"认领"对方）
            stem = str(s.get("url", "")).split("/")[-1].replace(".gz", "").replace(".xml", "")
            stem = re.sub(r'[^\w\-_.]', '_', stem).strip(' .')
            legacy = f"{stem}.xml" if stem else ""
            hit = legacy if legacy in files else ""
        parked = (cache_dir / (effective + ".disabled")).is_file()
        item["parked"] = parked             # 源被禁用 → 缓存改名挂起，不参与合并
        item["downloaded"] = bool(hit or parked)
        item["cache_file"] = hit
        out.append(item)
    return {"sources": out}


@app.post("/api/epg/sources")
async def add_epg_source(data: dict = Body(...)):
    sources = config.get_epg_sources()
    url = (data.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "URL 不能为空")
    if len(url) > 2000:
        raise HTTPException(400, "URL 过长")
    if not downloader._is_safe_http_url(url):
        raise HTTPException(400, "URL 必须是 http:// 或 https://")

    source = {
        "url": url,
        "rule": str(data.get("rule", "auto"))[:20],
        "enabled": True,
        "remark": str(data.get("remark", ""))[:200],
        "cache_name": downloader.clean_cache_name(data.get("cache_name", "")),
    }
    sources.append(source)
    config.save_epg_sources(sources)
    return {"success": True, "source": source}


@app.put("/api/epg/sources/{index}")
async def update_epg_source(index: int, data: dict = Body(...)):
    sources = config.get_epg_sources()
    if index < 0 or index >= len(sources):
        raise HTTPException(404, "源不存在")
    src = sources[index]
    if "url" in data:
        new_url = str(data["url"]).strip()[:2000]
        if not downloader._is_safe_http_url(new_url):
            raise HTTPException(400, "URL 必须是 http:// 或 https://")
        src["url"] = new_url
    if "rule" in data:
        src["rule"] = str(data["rule"])[:20]
    if "enabled" in data:
        src["enabled"] = bool(data["enabled"])
    if "remark" in data:
        src["remark"] = str(data["remark"])[:200]
    if "cache_name" in data:
        src["cache_name"] = downloader.clean_cache_name(data["cache_name"])
    sources[index] = src
    config.save_epg_sources(sources)
    if "enabled" in data:
        # 禁用时把缓存挂起（改名 .disabled），搜索/提取/合并就不会再用它的旧数据
        try:
            downloader.set_source_cache_enabled(src, bool(src.get("enabled", True)))
        except Exception as e:
            logger.warning(f"切换 EPG 源缓存状态失败: {e}")
    return {"success": True}


@app.delete("/api/epg/sources/{index}")
async def delete_epg_source(index: int):
    sources = config.get_epg_sources()
    if index < 0 or index >= len(sources):
        raise HTTPException(404, "源不存在")
    removed = sources.pop(index)
    downloader.clear_epg_failure(str(removed.get("url", "") or ""))
    config.save_epg_sources(sources)
    return {"success": True}


@app.post("/api/epg/download")
async def download_epg():
    if download_progress._running:
        return {"success": False, "error": "已有任务运行中"}
    download_progress.clear()
    download_progress._running = True
    download_progress._paused = False
    download_progress._stopped = False
    asyncio.create_task(_run_epg_download())
    return {"success": True, "message": "EPG 下载已启动"}


async def _run_epg_download():
    try:
        results = await downloader.download_all_epg_sources(
            progress_callback=download_progress.add_message,
            log_callback=lambda msg: logger.log(msg),
        )
        if not results:
            await download_progress.add_message(
                "⚠️ 没有启用的外部 EPG 源（请先在「外部EPG」里添加并启用源）"
            )
            return
        ok = sum(1 for r in results if r and r.get("success"))
        fail = sum(1 for r in results if r and not r.get("success"))
        if ok == 0:
            await download_progress.add_message(
                f"❌ 全部 {fail} 个源都下载失败，已跳过合并（请检查源 URL / 网络）"
            )
            return
        await download_progress.add_message(
            f"EPG 下载完成：成功 {ok} / 失败 {fail}。请前往「频道提取」或手动点击「生成XML」。"
        )
    except Exception as e:
        await download_progress.add_message(f"❌ EPG 下载出错: {e}")
    finally:
        download_progress._running = False
        download_progress._paused = False
        download_progress._stopped = False


@app.post("/api/epg/sources/{index}/download")
async def download_single_epg_source(index: int):
    """单独下载某一个 EPG 源；合并/生成 XML 仍由「生成XML」按钮执行。"""
    if download_progress._running:
        return {"success": False, "error": "已有任务运行中，请稍候"}

    sources = config.get_epg_sources()
    if index < 0 or index >= len(sources):
        raise HTTPException(404, "源不存在")

    src = sources[index]
    if not src.get("enabled", True):
        # 禁用源的缓存被改名成 .xml.disabled，直接下载会生成 .xml，
        # 出现两个文件并存，导致搜索/合并行为混乱 —— 直接拒绝
        return {"success": False, "error": "该源已禁用，请先启用再下载"}
    if not str(src.get("url") or "").strip():
        return {"success": False, "error": "该源没有配置 URL"}

    download_progress.clear()
    download_progress._running = True
    download_progress._paused = False
    download_progress._stopped = False
    asyncio.create_task(_run_single_epg_download(src))
    return {"success": True, "message": "该源下载已启动"}


async def _run_single_epg_download(src: dict):
    """单个 EPG 源下载任务：下载完成后提示手动合并/生成 XML。"""
    try:
        await download_progress.add_message(f"开始下载 EPG 源: {src.get('url', '')}")
        result = await downloader.download_epg_source(
            src,
            progress_callback=download_progress.add_message,
            log_callback=lambda msg: logger.log(msg),
            index=1, total=1,
        )
        if not result or not result.get("success"):
            err = (result or {}).get("error") or "未知错误"
            await download_progress.add_message(f"EPG 源下载失败: {err}")
            return
        await download_progress.add_message("EPG 源下载完成。请前往「频道提取」或手动点击「生成XML」。")
    except Exception as e:
        await download_progress.add_message(f"下载异常: {e}")
    finally:
        download_progress._running = False
        download_progress._paused = False
        download_progress._stopped = False

@app.post("/api/epg/clear-cache")
async def clear_epg_cache():
    """清空所有外部 EPG 缓存文件（彻底解决删除源后幽灵频道还在的问题）"""
    cache_dir = config.CONFIG_DIR / "epg_cache"
    deleted_count = 0
    if cache_dir.exists():
        for f in cache_dir.glob("*.xml*"):
            try:
                f.unlink()
                deleted_count += 1
            except OSError as e:
                logger.warning(f"删除缓存文件 {f.name} 失败: {e}")
    
    logger.log(f"[EPG] 已手动清空 {deleted_count} 个外部 EPG 缓存文件", category="清理")
    return {"success": True, "message": f"已清空 {deleted_count} 个缓存文件"}


@app.get("/api/epg/channels")
async def search_epg_channels(keyword: str = ""):
    epg_cache = config.CONFIG_DIR / "epg_cache"
    sources = config.get_epg_sources()
    all_channels = []
    seen = set()

    cache_files = sorted(epg_cache.glob("*.xml")) if epg_cache.exists() else []
    per_file = {}

    # ★ 修复：用 epg_cache_name() 精确算出每个源的缓存文件名，
    #   不再用 "stem in f.name" 子串匹配 —— 多个源的文件名部分同名时
    #   （比如都是 txml.gz），子串匹配会让它们全部归到第一个源。
    cache_to_source = {}
    for s in sources:
        try:
            cname = downloader.epg_cache_name(s)
        except Exception:
            cname = ""
        if cname:
            cache_to_source[cname] = s

    for f in cache_files:
        try:
            text = f.read_text(encoding="utf-8")
            channels = epg_parser.get_channel_list(text)
        except Exception:
            continue

        per_file[f.name] = len(channels)

        src = cache_to_source.get(f.name)
        if src:
            source_url = str(src.get("url", "") or "")
            source_name = (str(src.get("remark") or "").strip()
                           or source_url.split("/")[-1] or f.name)
        else:
            # 缓存对不上任何配置源（旧命名残留 / 源已删）→ 用文件名兜底，别冒充成别的源
            source_url = ""
            source_name = f.name

        for ch in channels:
            key = ch.get("id", "") + ch.get("name", "") + source_url
            if key not in seen:
                seen.add(key)
                ch["source_url"] = source_url
                ch["source_name"] = source_name
                all_channels.append(ch)

    # 每个配置源的状态：也用精确名字查
    source_status = []
    for s in sources:
        try:
            cname = downloader.epg_cache_name(s) or ""
        except Exception:
            cname = ""
        count = per_file.get(cname, 0)
        source_status.append({
            "name": s.get("remark") or cname or s.get("url", ""),
            "url": s.get("url", ""),
            "downloaded": cname in per_file,
            "file": cname if cname in per_file else "",
            "channels": count,
        })

    # 标记「是否已添加」，前端频道表里直接显示状态
    extracts = config.get_epg_extracts()
    epg_channels = config.get_epg_channels()
    name_by_lid = {str(c.get("logic_id", "")): str(c.get("name", "")) for c in epg_channels}
    added_map = {}
    for e in extracts:
        key = (str(e.get("name", "")), str(e.get("source_url", "")))
        tid = str(e.get("target_logic_id", "") or "")
        added_map[key] = name_by_lid.get(tid, "") if tid else ""

    for ch in all_channels:
        key = (str(ch.get("name", "")), str(ch.get("source_url", "")))
        if key in added_map:
            ch["added"] = True
            ch["target_name"] = added_map[key]
            ch["merged"] = bool(added_map[key])
        else:
            ch["added"] = False
            ch["target_name"] = ""
            ch["merged"] = False

    if keyword:
        kw = keyword.lower()[:100]
        all_channels = [c for c in all_channels
                        if kw in c["name"].lower() or kw in c["id"].lower()]

    return {
        "channels": all_channels,
        "sources": source_status,
        "cache_count": len(cache_files),
        "total_channels": len(seen),
    }

# ════════════════════════════════════════════════════════════════
# EPG 数据透视 / 批量操作 / 失败告警 / 纯净 M3U
# ════════════════════════════════════════════════════════════════

@app.get("/api/epg/pivot")
async def epg_pivot(date: str = ""):
    """EPG 数据透视：每个 EPG 频道今天是否有数据、数据来源、节目数。

    候选文件名（依次尝试，命中即止）：
      - <频道名><当前平台后缀>_<日期>.json （电信数据：华为 _telecom / 中兴 _zte）
      - <频道名>_<日期>.json               （频道 URL 直下 / 外部提取）
      - <safe_filename(频道名)>_<日期>.json （外部提取走的是 safe_filename）
    """
    if not date:
        date = datetime.date.today().strftime("%Y%m%d")
    elif not re.fullmatch(r"\d{8}", date):
        raise HTTPException(400, "日期格式错误")

    active = config.get_active_platform()
    epg_suffix = config.platform_epg_suffix(active)
    platform_short = config.platform_label(active).replace("平台", "")
    channels = config.get_epg_channels()
    extracts = config.get_epg_extracts()

    # {epg logic_id: [提取记录名...]}，用于显示"并入"
    merged_map = {}
    for e in extracts:
        tid = str(e.get("target_logic_id", "") or "")
        if tid:
            merged_map.setdefault(tid, []).append(str(e.get("name", "") or ""))

    rows = []
    for ch in channels:
        name = ch.get("name", "") or ""
        if not name:
            continue
        safe = config.safe_filename(name)
        bases = [name]
        if safe and safe != name:
            bases.append(safe)

        # 候选路径（当前平台优先，其次普通，去重）
        candidates = []
        seen = set()
        for base in bases:
            for fn in (f"{base}{epg_suffix}_{date}.json", f"{base}_{date}.json"):
                if fn not in seen:
                    seen.add(fn)
                    candidates.append(fn)

        found_path = None
        for fn in candidates:
            p = config.JSON_DIR / fn
            if p.is_file():
                found_path = p
                break

        prog_count = 0
        data_source = "无"
        file_name = ""
        if found_path:
            file_name = found_path.name
            try:
                d = json.loads(found_path.read_text(encoding="utf-8"))
                prog_count = len(d.get("programmes") or [])
            except Exception:
                prog_count = 0
            if f"{epg_suffix}_" in found_path.name:
                data_source = f"电信（{platform_short}）"
            elif str(ch.get("source_type", "")) == "extract":
                data_source = "外部EPG提取"
            elif str(ch.get("source_type", "")) == "telecom":
                data_source = "电信（自建JSON）"
            else:
                data_source = "自建JSON"

        rows.append({
            "name": name,
            "tvg_id": ch.get("tvg_id", ""),
            "source_type": ch.get("source_type", ""),
            "enabled": ch.get("enabled", True),
            "has_data": prog_count > 0,
            "programme_count": prog_count,
            "data_source": data_source,
            "file": file_name,
            "merged_from": merged_map.get(str(ch.get("logic_id", "")), []),
        })

    return {
        "date": date,
        "total": len(rows),
        "with_data": sum(1 for r in rows if r["has_data"]),
        "without_data": sum(1 for r in rows if not r["has_data"]),
        "rows": rows,
    }


@app.post("/api/epg-channels/batch-delete")
async def batch_delete_epg_channels(data: dict = Body(...)):
    """批量删除 EPG 频道（按 logic_id；同步清理关联提取记录）。"""
    lids = data.get("logic_ids") or []
    if not isinstance(lids, list) or not lids:
        raise HTTPException(400, "logic_ids 不能为空")
    lidset = {str(x) for x in lids}

    channels = config.get_epg_channels()
    kept = [c for c in channels if str(c.get("logic_id", "")) not in lidset]
    removed = len(channels) - len(kept)
    if not removed:
        return {"success": True, "deleted": 0}

    config.save_epg_channels(kept)

    extracts = config.get_epg_extracts()
    new_extracts = [
        e for e in extracts
        if str(e.get("target_logic_id", "")) not in lidset
        and str(e.get("logic_id", "")) not in lidset
    ]
    if len(new_extracts) != len(extracts):
        config.save_epg_extracts(new_extracts)

    return {"success": True, "deleted": removed}


@app.post("/api/epg-channels/batch-toggle")
async def batch_toggle_epg_channels(data: dict = Body(...)):
    """批量启用/禁用 EPG 频道（按 logic_id）。"""
    lids = data.get("logic_ids") or []
    enabled = bool(data.get("enabled", True))
    if not isinstance(lids, list) or not lids:
        raise HTTPException(400, "logic_ids 不能为空")
    lidset = {str(x) for x in lids}

    channels = config.get_epg_channels()
    n = 0
    for c in channels:
        if str(c.get("logic_id", "")) in lidset:
            c["enabled"] = enabled
            n += 1
    if n:
        config.save_epg_channels(channels)
    return {"success": True, "affected": n, "enabled": enabled}


@app.get("/api/epg/failures")
async def get_epg_failures_api():
    """外部 EPG 源最近下载失败的告警列表。"""
    return {"failures": downloader.get_epg_failures()}


@app.post("/api/epg/failures/clear")
async def clear_epg_failures_api(data: dict = Body(default={})):
    url = str((data or {}).get("url", "") or "")
    downloader.clear_epg_failure(url)
    return {"success": True}


@app.get("/api/m3u/{filename}/export-clean")
async def export_clean_m3u(filename: str):
    """导出"仅保留匹配到节目单的频道"的 M3U（附件形式下载）。

    过滤规则：tvg-name 为空 / tvg-id 为 9999 / 频道名为"暂无节目单" → 丢弃。
    """
    safe = _safe_basename(filename)
    if not safe:
        raise HTTPException(400, "非法文件名")
    path = config.M3U_DIR / safe
    if not path.is_file():
        raise HTTPException(404, "文件不存在")

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(500, f"读取失败: {e}")

    if safe.lower().endswith(".txt"):
        parsed = m3u_handler.parse_txt(content)
    else:
        parsed = m3u_handler.parse_m3u(content, merge_duplicates=False)

    clean = []
    for e in parsed.get("entries", []):
        tvg_name = (e.get("tvg_name") or "").strip()
        tvg_id = (e.get("tvg_id") or "").strip()
        if not tvg_name or tvg_id == "9999" or tvg_name == "暂无节目单":
            continue
        clean.append(e)

    header = parsed.get("header") or "#EXTM3U"
    new_content = m3u_handler.rebuild_m3u(clean, header)

    base = safe.rsplit(".", 1)[0] if "." in safe else safe
    clean_name = f"{base}_clean.m3u"

    from urllib.parse import quote as _q
    return Response(
        content=new_content,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition":
                f'attachment; filename="{clean_name}"; '
                f"filename*=UTF-8''{_q(clean_name)}",
        },
    )

# ════════════════════════════════════════════════════════════════
# EPG 提取记录
# ════════════════════════════════════════════════════════════════

@app.get("/api/epg/extracts")
async def get_epg_extracts():
    return {"extracts": config.get_epg_extracts()}


def _find_epg_target(epg_channels, name, tvg_id="", exclude_logic_id=""):
    """为外部 EPG 频道找一个已有 EPG 管理频道（用于自动并入）。

    匹配优先级：频道名 → 源频道 ID（对 TVG-ID）→ TVG-Name。
    同名有多个时优先电信频道，避免把外部节目并到另一个外部源的独立条目上。
    """
    name = str(name or "").strip()
    tvg_id = str(tvg_id or "").strip()
    exclude = str(exclude_logic_id or "")
    candidates = [
        ch for ch in epg_channels
        if str(ch.get("logic_id", "") or "")
        and str(ch.get("logic_id", "") or "") != exclude
    ]
    if not candidates:
        return None

    def pick(matches):
        if not matches:
            return None
        def rank(ch):
            st = str(ch.get("source_type", "") or "")
            return {"telecom": 0, "manual": 1, "extract": 2}.get(st, 3)
        return sorted(matches, key=rank)[0]

    # 1) 频道名精确匹配：最符合「并入 EPG 管理已有频道」
    if name:
        hit = [ch for ch in candidates if str(ch.get("name", "")).strip() == name]
        if hit:
            return pick(hit)

    # 2) 源频道 ID 对 EPG 管理的 TVG-ID
    if tvg_id:
        hit = [ch for ch in candidates if str(ch.get("tvg_id", "")).strip() == tvg_id]
        if hit:
            return pick(hit)

    # 3) 深度清洗兜底匹配：解决 CCTV-1综合 / CCTV1 高清 / CCTV1 匹配不上的问题
    def _norm(s):
        s = str(s or "").strip()
        # 去掉括号及括号内的内容
        s = re.sub(r'[（(].*?[)）]', '', s)
        # 去掉常见的后缀词
        s = re.sub(r'(高清|超清|标清|蓝光|HD|FHD|UHD|4K|频道|综合|剧场|剧场版|专区)$', '', s, flags=re.IGNORECASE)
        # 只保留中英文和数字，去掉所有空格、横杠、下划线、斜杠等符号
        s = re.sub(r'[^\w\u4e00-\u9fa5]', '', s)
        return s.lower()

    # 把查询的条件和目标的属性全部拆解、清洗为集合
    query_keys = set()
    for p in re.split(r'[,，;；/]', name + "/" + tvg_id):
        if p.strip():
            query_keys.add(_norm(p))

    if not query_keys:
        return None

    for ch in candidates:
        ch_keys = set()
        ch_fields = [
            str(ch.get("name", "")),
            str(ch.get("tvg_id", "")),
            str(ch.get("tvg_name", ""))
        ]
        for f in ch_fields:
            for p in re.split(r'[,，;；/]', f):
                if p.strip():
                    ch_keys.add(_norm(p))
        
        # 如果双方有任何一个清洗后的名字重合，就视为匹配
        if query_keys & ch_keys:
            return ch

    return None


# 替换为以下代码：
def _sync_epg_target_meta(target, name, source):
    """把外部源频道名写入目标 TVG-Name，并更新来源名称（强力清洗与去重）。"""
    changed = False
    name = str(name or "").strip()
    
    # 1. 剥离括号干扰：如 "zsdc1 (少儿动画)" -> "zsdc1"
    clean_source = str(source or "").strip()
    clean_source = re.sub(r'[（(].*?[)）]', '', clean_source).strip()

    # 2. 处理 TVG-Name
    if name:
        current_tvg = str(target.get("tvg_name", "") or "").strip()
        all_names = []
        for n in re.split(r'[,，;；/]', current_tvg + "/" + name):
            n = n.strip()
            if n and n not in all_names:
                all_names.append(n)
        
        new_tvg = "/".join(all_names)
        if new_tvg != current_tvg:
            target["tvg_name"] = new_tvg
            changed = True

    # 3. 处理来源
    if clean_source:
        current_src = str(target.get("source_remark", "") or "").strip()
        all_srcs = []
        for s in re.split(r'[,，;；/]', current_src + "/" + clean_source):
            s = s.strip()
            if s and s not in all_srcs:
                all_srcs.append(s)
        
        new_src = "/".join(all_srcs)
        if new_src != current_src:
            target["source_remark"] = new_src
            changed = True

    if not str(target.get("source_type", "") or ""):
        target["source_type"] = "extract"
        changed = True

    if str(target.get("source_type", "") or "") == "extract" and name:
        if str(target.get("extract_name", "") or "") != name:
            target["extract_name"] = name
            changed = True
    return changed

    # 老数据没有 source_type 时标记为 extract，避免被「清理孤儿」误删
    if not str(target.get("source_type", "") or ""):
        target["source_type"] = "extract"
        changed = True

    # 目标本身是提取生成的独立频道时，同步 extract_name 便于来源列显示
    if str(target.get("source_type", "") or "") == "extract" and name:
        if str(target.get("extract_name", "") or "") != name:
            target["extract_name"] = name
            changed = True
    return changed


def _new_extract_epg_channel(epg_channels, name, source, logic_id, tvg_id=""):
    """为没有可并入目标的提取记录新建一个独立 EPG 频道。"""
    new_tvg = str(name or "").strip()
    suffix = 2
    taken = {str(c.get("tvg_id", "")) for c in epg_channels}
    while new_tvg in taken:
        new_tvg = f"{name}_{suffix}"
        suffix += 1
    channel = {
        "name": name,
        "tvg_id": new_tvg,
        "tvg_name": name,
        "logic_id": logic_id,
        "source_type": "extract",
        "source_remark": source,
        "extract_name": name,
        "enabled": True,
        "sort_order": len(epg_channels),
    }
    epg_channels.append(channel)
    return channel


def _create_extract(extracts, epg_channels, name, source, source_url,
                    tvg_id, rule, target_logic_id):
    """新建一条提取记录；有匹配的 EPG 频道就并入，否则建独立频道。

    返回 (extract, epg_created, merged, updated)。
    - merged：找到了已有 EPG 频道并写入 target_logic_id
    - updated：目标频道的 TVG-Name / 来源确实发生变化
    """
    target = None
    if target_logic_id:
        for ch in epg_channels:
            if str(ch.get("logic_id", "")) == target_logic_id:
                target = ch
                break
    if target is None:
        target = _find_epg_target(epg_channels, name, tvg_id)

    if not tvg_id:
        max_id = 9999
        for e in extracts:
            try:
                max_id = max(max_id, int(e.get("tvg_id", "9999")))
            except (ValueError, TypeError):
                pass
        tvg_id = str(max_id + 1)

    extract = {
        "name": name,
        "source": source,
        "source_url": source_url,
        "tvg_id": str(tvg_id)[:50],
        "rule": rule,
        "logic_id": str(uuid.uuid4()),
    }

    created = merged = updated = False
    if target is not None:
        extract["target_logic_id"] = str(target.get("logic_id", ""))
        merged = True
        updated = _sync_epg_target_meta(target, name, source)
    else:
        _new_extract_epg_channel(
            epg_channels, name, source, extract["logic_id"], tvg_id)
        created = True

    extracts.append(extract)
    return extract, created, merged, updated


def _reapply_extract(ext, epg_channels, name, source, tvg_id=""):
    """重复添加时，把已有提取记录重新指向匹配频道并刷新 TVG-Name / 来源。"""
    own_lid = str(ext.get("logic_id", "") or "")
    target = None
    old_target = str(ext.get("target_logic_id", "") or "")
    if old_target:
        target = next((ch for ch in epg_channels
                       if str(ch.get("logic_id", "")) == old_target), None)
    if target is None:
        target = _find_epg_target(epg_channels, name, tvg_id, exclude_logic_id=own_lid)
    if target is not None and str(target.get("logic_id", "")) == own_lid:
        target = None

    changed = False
    if target is not None:
        if old_target != str(target.get("logic_id", "")):
            ext["target_logic_id"] = str(target.get("logic_id", ""))
            changed = True
        if source and str(ext.get("source", "") or "") != source:
            ext["source"] = source
            changed = True
        if _sync_epg_target_meta(target, name, source):
            changed = True

        # 原来为这条提取记录生成过独立频道时，并入后删掉多余独立频道
        if own_lid:
            before = len(epg_channels)
            epg_channels[:] = [
                ch for ch in epg_channels
                if str(ch.get("logic_id", "")) != own_lid
            ]
            if len(epg_channels) != before:
                changed = True
        return True, changed

    # 没有别的目标：更新/补回自己的独立频道
    if source and str(ext.get("source", "") or "") != source:
        ext["source"] = source
        changed = True
    own = next((ch for ch in epg_channels
                if str(ch.get("logic_id", "")) == own_lid), None)
    if own is None:
        _new_extract_epg_channel(epg_channels, name, source, own_lid)
        changed = True
    elif _sync_epg_target_meta(own, name, source):
        changed = True
    return False, changed



@app.post("/api/epg/extracts")
async def add_epg_extract(data: dict = Body(...)):
    extracts = config.get_epg_extracts()
    name = (data.get("name") or "").strip()
    source = (data.get("source") or "").strip() or "外部EPG"
    source_url = (data.get("source_url") or "").strip()
    tvg_id = data.get("tvg_id", "")
    rule = str(data.get("rule", "auto"))[:20]
    target_logic_id = str(data.get("target_logic_id", "") or "").strip()[:64]

    if not name or not source:
        raise HTTPException(400, "名称和来源不能为空")
    if len(name) > 200 or len(source) > 500 or len(source_url) > 2000:
        raise HTTPException(400, "字段过长")

    epg_channels = config.get_epg_channels()
    extract, created, merged, updated = _create_extract(
        extracts, epg_channels, name, source, source_url, tvg_id, rule, target_logic_id)

    config.save_epg_extracts(extracts)
    if created or updated:
        config.save_epg_channels(epg_channels)

    return {"success": True, "extract": extract,
            "merged": merged, "updated": updated}


@app.post("/api/epg/extracts/batch")
async def add_epg_extracts_batch(data: dict = Body(...)):
    """批量添加提取记录（从频道表勾选后一次提交）。"""
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items 不能为空")
    if len(items) > 300:
        raise HTTPException(400, "一次最多添加 300 个频道")

    extracts = config.get_epg_extracts()
    epg_channels = config.get_epg_channels()
    existing_map = {}
    for e in extracts:
        key = (str(e.get("name", "")), str(e.get("source_url", "")))
        existing_map.setdefault(key, e)

    added = skipped = epg_created = merged_count = updated_count = 0
    extracts_changed = epg_changed = False
    for it in items:
        if not isinstance(it, dict):
            skipped += 1
            continue
        name = str(it.get("name", "") or "").strip()[:200]
        source = str(it.get("source", "") or "").strip()[:500] or "外部EPG"
        source_url = str(it.get("source_url", "") or "").strip()[:2000]
        rule = str(it.get("rule", "auto") or "auto")[:20]
        target = str(it.get("target_logic_id", "") or "").strip()[:64]
        source_tvg = str(it.get("tvg_id", "") or "").strip()
        if not name:
            skipped += 1
            continue

        key = (name, source_url)
        if key in existing_map:
            # 已存在也重新匹配一次：旧版本加过的记录可能没有 target_logic_id，
            # 或目标频道的 TVG-Name / 来源还没写入，这里顺手补齐。
            ext = existing_map[key]
            merged, changed = _reapply_extract(
                ext, epg_channels, name, source, source_tvg)
            skipped += 1
            if merged:
                merged_count += 1
            if changed:
                extracts_changed = epg_changed = True
                updated_count += 1
            continue

        _, created, merged, updated = _create_extract(
            extracts, epg_channels, name, source, source_url,
            source_tvg, rule, target)
        existing_map[key] = extracts[-1]
        added += 1
        if created:
            epg_created += 1
            epg_changed = True
        if merged:
            merged_count += 1
        if updated:
            updated_count += 1
            epg_changed = True
        extracts_changed = True

    if extracts_changed:
        config.save_epg_extracts(extracts)
    if epg_changed:
        config.save_epg_channels(epg_channels)

    return {"success": True, "added": added, "skipped": skipped,
            "epg_created": epg_created, "merged": merged_count,
            "updated": updated_count}


@app.put("/api/epg/extracts/{index}")
async def update_epg_extract(index: int, data: dict = Body(...)):
    extracts = config.get_epg_extracts()
    if index < 0 or index >= len(extracts):
        raise HTTPException(404, "记录不存在")
    ext = extracts[index]
    for key in ("name", "source", "source_url", "tvg_id", "rule"):
        if key in data:
            ext[key] = str(data[key])[:2000]

    # 「并入目标」：只接受指向已存在 EPG 频道的 logic_id，空值表示恢复为独立频道
    if "target_logic_id" in data:
        tid = str(data.get("target_logic_id", "") or "").strip()[:64]
        epg_channels = config.get_epg_channels()
        target_ch = next((ch for ch in epg_channels
                          if str(ch.get("logic_id", "")) == tid), None)
        valid = target_ch is not None

        if valid:
            ext["target_logic_id"] = tid

            # 手动并入时同样写入 TVG-Name（不同才写）和来源名称
            meta_changed = _sync_epg_target_meta(
                target_ch, ext.get("name", ""), ext.get("source", ""))

            # ★ 新增：如果并入了别的频道，且原本这个提取记录生成过一个独立的频道，自动把它删掉
            own_lid = str(ext.get("logic_id", "") or "")
            new_channels = epg_channels
            if own_lid:
                new_channels = [ch for ch in epg_channels
                                if str(ch.get("logic_id", "")) != own_lid]
                if len(new_channels) < len(epg_channels):
                    logger.log(f"[EPG] 自动清理了因并入而多余的独立频道（Logic ID: {own_lid}）", category="EPG")
            if meta_changed or len(new_channels) < len(epg_channels):
                config.save_epg_channels(new_channels)
        else:
            ext.pop("target_logic_id", None)
    extracts[index] = ext
    config.save_epg_extracts(extracts)
    return {"success": True}


@app.delete("/api/epg/extracts/{index}")
async def delete_epg_extract(index: int):
    extracts = config.get_epg_extracts()
    if index < 0 or index >= len(extracts):
        raise HTTPException(404, "记录不存在")
    deleted = extracts.pop(index)
    config.save_epg_extracts(extracts)
    
    # 1. 清理由此记录生成的独立频道
    lid = deleted.get("logic_id")
    channels = config.get_epg_channels()
    new_channels = [ch for ch in channels if str(ch.get("logic_id", "")) != str(lid)]
    if len(new_channels) != len(channels):
        channels = new_channels
        logger.log(f"[EPG] 删除提取记录时，同步清理了对应的独立频道（Logic ID: {lid}）", category="EPG")

    # 2. 从目标频道的来源中移除该记录的 source
    target_lid = deleted.get("target_logic_id")
    if target_lid:
        del_src = str(deleted.get("source", "") or "").strip()
        del_src = re.sub(r'[（(].*?[)）]', '', del_src).strip() # 剥离括号
        if del_src:
            for ch in channels:
                if str(ch.get("logic_id")) == str(target_lid):
                    current_src = str(ch.get("source_remark", "") or "")
                    # 拆分 -> 剔除 -> 重组
                    parts = [s.strip() for s in re.split(r'[,，;；/]', current_src) if s.strip()]
                    if del_src in parts:
                        parts.remove(del_src)
                        ch["source_remark"] = "/".join(parts)
                    break
        config.save_epg_channels(channels)
    elif len(new_channels) != len(channels) or target_lid:
        config.save_epg_channels(channels)
            
    return {"success": True}

@app.post("/api/epg/extracts/clear")
async def clear_epg_extracts():
    """一键清空所有提取记录，并重置 EPG 频道的来源"""
    config.save_epg_extracts([])
    
    # 动态获取当前生效的平台名称
    active_platform = config.get_active_platform()
    default_telecom_label = "电信源" if active_platform == "huawei" else "中兴源"

    channels = config.get_epg_channels()
    new_channels = []
    for ch in channels:
        st = str(ch.get("source_type", ""))
        if st == "extract":
            continue  # 纯提取生成的独立频道直接删掉
        elif st == "telecom":
            # 恢复为当前平台专属的干净标记
            ch["source_remark"] = default_telecom_label 
        elif st == "manual":
            ch["source_remark"] = "手动添加"
        else:
            ch["source_remark"] = "自建json"
        new_channels.append(ch)

    config.save_epg_channels(new_channels)
    logger.log(f"[EPG] 清空提取记录，同时重置了所有频道的来源", category="EPG")
    return {"success": True}

@app.post("/api/epg/extracts/import")
async def import_epg_extracts(data: list = Body(...)):
    if not isinstance(data, list) or len(data) > 5000:
        raise HTTPException(400, "无效或过大的导入数据")

    extracts = config.get_epg_extracts()
    added = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        clean = _sanitize_record(item, _EXTRACT_FIELDS)
        name = (clean.get("name") or "").strip()
        source = (clean.get("source") or "").strip()
        if not name or not source:
            continue
        if any(e.get("name") == name for e in extracts):
            continue
        if not clean.get("tvg_id"):
            clean["tvg_id"] = str(len(extracts) + 1000)
        if not clean.get("logic_id"):
            clean["logic_id"] = str(uuid.uuid4())
        if not clean.get("rule"):
            clean["rule"] = "auto"
        extracts.append(clean)
        added += 1
    if added > 0:
        config.save_epg_extracts(extracts)
    return {"success": True, "added": added}


@app.post("/api/epg/extract-to-json")
async def extract_epg_to_json():
    if download_progress._running:
        return {"success": False, "error": "有任务正在运行"}
    download_progress.clear()
    download_progress._running = True
    download_progress._paused = False
    download_progress._stopped = False
    asyncio.create_task(_run_extract_to_json())
    return {"success": True, "message": "外部EPG提取已启动"}


async def _run_extract_to_json():
    """下载外部 EPG 并提取到 JSON。"""
    import time as _time
    _start = _time.time()
    try:
        await download_progress.add_message("===== 外部EPG提取到JSON =====")
        logger.log("[任务] 外部EPG提取到JSON: 开始", category="EPG")

        await download_progress.add_message("[第1步] 下载外部 EPG 源...")
        epg_results = await downloader.download_all_epg_sources(
            progress_callback=download_progress.add_message,
            log_callback=lambda msg: logger.log(msg),
        )
        epg_ok = sum(1 for r in epg_results if r and r.get("success"))
        epg_fail = sum(1 for r in epg_results if r and not r.get("success"))
        logger.log(f"[任务] EPG 源下载完成: 成功{epg_ok}个, 失败{epg_fail}个", category="下载")
        await download_progress.add_message(f"[第1步] EPG 源下载完成 (成功{epg_ok}/失败{epg_fail})")

        extracts = config.get_epg_extracts()
        epg_cache = config.CONFIG_DIR / "epg_cache"
        today = datetime.date.today()
        # ★ 与 xml_builder 保持一致：前 6 天 + 今天 + 后 2 天（共 9 天）
        extract_dates = [
            (today + datetime.timedelta(days=i)).strftime("%Y%m%d")
            for i in range(-6, 3)
        ]
        json_dir = config.JSON_DIR

        logger.log(f"[任务] [第2步] 开始提取，共 {len(extracts)} 条记录", category="EPG")
        await download_progress.add_message(f"[第2步] 提取节目，共 {len(extracts)} 条记录...")

        epg_name_map = {}
        for ech in config.get_epg_channels():
            lid = ech.get("logic_id", "")
            if lid:
                epg_name_map[lid] = ech.get("name", "")

        extract_count = 0
        fail_count = 0
        for idx, ext in enumerate(extracts, 1):
            raw_name = ext.get("name", "")
            rule = ext.get("rule", "auto")
            if not raw_name:
                continue
            _tgt = str(ext.get("target_logic_id", "") or "")
            _own = str(ext.get("logic_id", "") or "")
            save_name = (epg_name_map.get(_tgt) or epg_name_map.get(_own)
                         or raw_name)

            programmes = []
            for cf in epg_parser.resolve_cache_files(epg_cache, ext.get("source_url", "")):
                try:
                    cache_text = cf.read_text(encoding="utf-8")
                except Exception:
                    continue
                progs = epg_parser.extract_programmes(cache_text, rule, raw_name)
                if progs:
                    programmes.extend(progs)
                    break

            date_progs = defaultdict(list)
            for prog in programmes:
                start = prog.get("start", "")
                # ★ 统一成 YYYYMMDDHHMMSS 再取日期，兼容 2026-10-30 19:00:00 这种 ISO 时间
                d = epg_parser.format_xmltv_time(start)[:8]
                if d in extract_dates:
                    date_progs[d].append(prog)

            got_dates = sorted(set(d for d in date_progs if date_progs[d]))
            if not programmes:
                fail_count += 1
                logger.log(f"[任务]   [{idx}/{len(extracts)}] {raw_name}: ❌ 未提取到节目", category="EPG")
            else:
                logger.log(f"[任务]   [{idx}/{len(extracts)}] {raw_name}: 已提取到（{', '.join(got_dates)}）", category="EPG")

            for d, progs in date_progs.items():
                # ★ 本次：改用 config.safe_filename（与 xml_builder 读取路径一致）
                safe = config.safe_filename(save_name)
                save_path = json_dir / f"{safe}_{d}.json"
                prog_list = [
                    {"name": raw_name, "start": p.get("start", ""),
                     "stop": p.get("stop", ""), "title": p.get("title", ""),
                     "desc": p.get("desc", "")}
                    for p in progs
                ]
                try:
                    json_dir.mkdir(parents=True, exist_ok=True)
                    # ★ 本次：tmp 命名统一为 name + ".tmp"
                    tmp = save_path.with_name(save_path.name + ".tmp")
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump({"programmes": prog_list}, f, ensure_ascii=False)
                    tmp.replace(save_path)
                    extract_count += 1
                except Exception as e:
                    logger.log(f"[任务] 保存 {save_name} 失败: {e}", category="失败")

            if idx % 10 == 0 or idx == len(extracts):
                await download_progress.add_message(
                    f"  [{idx}/{len(extracts)}] 成功{extract_count}个JSON, 失败{fail_count}条")

        total_elapsed = int(_time.time() - _start)
        logger.log(f"[任务] 外部EPG提取到JSON完成: 新增{extract_count}个JSON, 失败{fail_count}条, 耗时{total_elapsed}秒", category="EPG")
        await download_progress.add_message(f"[第2步] 提取完成: 成功{extract_count}个JSON, 失败{fail_count}条")
        await download_progress.add_message(f"===== 外部EPG提取到JSON完成，耗时 {total_elapsed}秒 =====")
    except Exception as e:
        logger.log(f"[任务] 提取到JSON失败: {str(e)}", category="失败")
        await download_progress.add_message(f"❌ 错误: {str(e)}")
        import traceback
        await download_progress.add_message(traceback.format_exc())
    finally:
        download_progress._running = False
        download_progress._paused = False
        download_progress._stopped = False


@app.post("/api/epg/extract-and-merge")
async def extract_and_merge_all():
    if download_progress._running:
        return {"success": False, "error": "有任务正在运行"}
    download_progress.clear()
    download_progress._running = True
    download_progress._paused = False
    download_progress._stopped = False
    asyncio.create_task(_run_extract_and_merge())
    return {"success": True, "message": "提取合并任务已启动"}


async def _run_extract_and_merge():
    """下载外部 EPG → 提取到 JSON → 重建 epg.xml（完整流程）。"""
    try:
        import time
        start_time = time.time()
        logger.log("[EPG] ===== 开始外部EPG提取合并流程 =====", category="EPG")
        await download_progress.add_message("===== 开始外部EPG提取合并流程 =====")

        logger.log("[EPG] [第1步] 下载外部 EPG 源文件...", category="下载")
        await download_progress.add_message("[第1步] 下载外部 EPG 源文件...")
        epg_results = await downloader.download_all_epg_sources(
            progress_callback=download_progress.add_message,
            log_callback=lambda msg: logger.log(msg),
        )
        epg_success = sum(1 for r in epg_results if r and r.get("success"))
        epg_fail = sum(1 for r in epg_results if r and not r.get("success"))
        logger.log(f"[EPG] [第1步] EPG 源下载完成: 成功 {epg_success} 个, 失败 {epg_fail} 个", category="下载")
        await download_progress.add_message(f"[第1步] EPG 源文件下载完成 (成功{epg_success}/失败{epg_fail})")

        extracts = config.get_epg_extracts()
        epg_cache = config.CONFIG_DIR / "epg_cache"
        json_dir = config.JSON_DIR
        today = datetime.date.today()
        # 与 xml_builder 一致：前 6 天 + 今天 + 后 2 天
        extract_dates = [
            (today + datetime.timedelta(days=i)).strftime("%Y%m%d")
            for i in range(-6, 3)
        ]
        epg_name_map = {}
        for ech in config.get_epg_channels():
            lid = ech.get("logic_id", "")
            if lid:
                epg_name_map[lid] = ech.get("name", "")

        logger.log(f"[EPG] [第2步] 开始逐条提取节目单（共 {len(extracts)} 条记录）...", category="EPG")
        await download_progress.add_message(f"[第2步] 开始逐条提取节目单（共 {len(extracts)} 条记录）...")

        total_programmes = 0
        extract_success = 0
        extract_fail = 0
        saved_files = 0
        for idx, ext in enumerate(extracts, 1):
            raw_name = ext.get("name", "未知")
            rule = ext.get("rule", "auto")
            source_url = ext.get("source_url", "")
            _tgt = str(ext.get("target_logic_id", "") or "")
            _own = str(ext.get("logic_id", "") or "")
            save_name = (epg_name_map.get(_tgt) or epg_name_map.get(_own)
                         or raw_name)

            # ★ 修复：用 resolve_cache_files 精确匹配，不再用 "cache_name in cf.name" 子串
            programmes = []
            for cf in epg_parser.resolve_cache_files(epg_cache, source_url):
                try:
                    cache_text = cf.read_text(encoding="utf-8")
                except Exception:
                    continue
                progs = epg_parser.extract_programmes(cache_text, rule, raw_name)
                if progs:
                    programmes.extend(progs)
                    break

            if not programmes:
                logger.log(f"[EPG]   [{idx}/{len(extracts)}] {raw_name}: ⚠️ 未找到对应缓存或节目为空，跳过", category="EPG")
                await download_progress.add_message(f"  [{idx}/{len(extracts)}] {raw_name}: 未找到对应的 EPG 缓存文件或节目为空")
                extract_fail += 1
                continue

            # ★ 修复：这里原来只统计、不写 JSON，导致「提取合并」后 XML 还是旧数据
            date_progs = defaultdict(list)
            for prog in programmes:
                start = prog.get("start", "")
                # ★ 统一成 YYYYMMDDHHMMSS 再取日期，兼容 2026-10-30 19:00:00 这种 ISO 时间
                d = epg_parser.format_xmltv_time(start)[:8]
                if d in extract_dates:
                    date_progs[d].append(prog)

            safe = config.safe_filename(save_name)
            json_dir.mkdir(parents=True, exist_ok=True)
            for d, progs in date_progs.items():
                save_path = json_dir / f"{safe}_{d}.json"
                prog_list = [
                    {"name": raw_name, "start": p.get("start", ""),
                     "stop": p.get("stop", ""), "title": p.get("title", ""),
                     "desc": p.get("desc", "")}
                    for p in progs
                ]
                try:
                    tmp = save_path.with_name(save_path.name + ".tmp")
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump({"programmes": prog_list}, f, ensure_ascii=False)
                    tmp.replace(save_path)
                    saved_files += 1
                except Exception as e:
                    logger.log(f"[EPG] 保存 {save_name} {d} 失败: {e}", category="失败")

            count = len(programmes)
            total_programmes += count
            extract_success += 1
            await download_progress.add_message(
                f"  [{idx}/{len(extracts)}] {raw_name}: 从 {source_url.split('/')[-1][:30]} 提取到 {count} 条节目")

        elapsed = int(time.time() - start_time)
        logger.log(
            f"[EPG] [第2步] 提取完成: 成功 {extract_success} 条, 失败 {extract_fail} 条, "
            f"共 {total_programmes} 条节目, 写入 {saved_files} 个JSON, 耗时 {elapsed}秒",
            category="EPG")
        await download_progress.add_message(
            f"[第2步] 提取完成，共 {total_programmes} 条节目（写入 {saved_files} 个 JSON）")

        logger.log("[EPG] [第3步] 生成自建 XML 文件...", category="EPG")
        await download_progress.add_message("[第3步] 生成自建 XML 文件...")
        result = await asyncio.to_thread(
            xml_builder.build_xml,
            log_callback=lambda msg: logger.log(msg),
        )
        if result["success"]:
            msg = f"[EPG] ✅ XML 生成完成: {result['channel_count']} 频道, {result['programme_count']} 节目"
            logger.log(msg)
            await download_progress.add_message(f"✅ XML 生成完成: {result['channel_count']} 频道, {result['programme_count']} 节目")
        else:
            msg = f"[EPG] ❌ XML 生成失败: {result.get('error', '')}"
            logger.log(msg)
            await download_progress.add_message(f"❌ XML 生成失败: {result.get('error', '')}")

        total_elapsed = int(time.time() - start_time)
        logger.log(f"[EPG] ===== 外部EPG提取合并流程完成，共耗时 {total_elapsed}秒 =====", category="EPG")
        await download_progress.add_message("===== 外部EPG提取合并流程完成 =====")
    except Exception as e:
        logger.log(f"[EPG] ❌ 提取流程异常: {str(e)}", category="失败")
        await download_progress.add_message(f"❌ 错误: {str(e)}")
        import traceback
        await download_progress.add_message(traceback.format_exc())
    finally:
        # ★ 修复：原来没有 finally，任务结束后 _running 永远为 True，后续任务全被挡住
        download_progress._running = False
        download_progress._paused = False
        download_progress._stopped = False


# ════════════════════════════════════════════════════════════════
# 设置
# ════════════════════════════════════════════════════════════════

@app.get("/api/settings")
async def get_settings():
    return config.get_settings()


@app.post("/api/settings")
async def update_settings(data: dict = Body(...)):
    settings = config.get_settings()
    for key in ("cron_time", "cron_enabled", "proxy_port", "share_xml_path",
                "xml_gz_enabled", "telecom_auto_add_to_epg"):
        if key in data:
            settings[key] = data[key]
    config.save_settings(settings)
    if "cron_time" in data or "cron_enabled" in data:
        scheduler.reschedule()
    return {"success": True}


# ════════════════════════════════════════════════════════════════
# M3U 合并
# ════════════════════════════════════════════════════════════════

@app.post("/api/m3u/merge")
async def merge_m3u_files(data: dict = Body(...)):
    files = data.get("files", [])
    merge_mode = data.get("merge_mode", "url")
    groups = data.get("groups", {}) or {}
    output_name = (data.get("output_name") or "").strip()

    if not isinstance(files, list) or len(files) < 2:
        raise HTTPException(400, "至少选择 2 个文件")
    if len(files) > 100:
        raise HTTPException(400, "文件数量过多")

    safe_output = _safe_basename(output_name)
    if not safe_output:
        raise HTTPException(400, "请指定输出文件名")
    if not safe_output.endswith((".m3u", ".m3u8")):
        safe_output += ".m3u"

    saved = config.get_fcc_presets()

    rtp_proxy = data.get("rtp_proxy")
    if rtp_proxy is None:
        rtp_proxy = saved.get("rtp_proxy", "") if saved.get("rtp_proxy_enabled") else ""

    fcc_config = data.get("fcc_config")
    if fcc_config is None:
        if saved.get("enabled"):
            active = saved.get("active", "")
            server = next((p["server"] for p in saved.get("presets", [])
                           if p.get("name") == active and p.get("server")), "")
            fcc_config = {"enabled": bool(server), "server": server}
        else:
            fcc_config = {"enabled": False, "server": ""}

    group_rules = data.get("group_rules")
    if group_rules is None:
        saved_rules = config.get_group_rules()
        group_rules = saved_rules if saved_rules.get("auto_apply") else None

    contents = []
    for fn in files:
        safe_fn = _safe_basename(str(fn))
        if not safe_fn:
            continue
        fp = config.M3U_DIR / safe_fn
        if not fp.exists() or not fp.is_file():
            continue
        try:
            content = fp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = fp.read_text(encoding="utf-8-sig")
            except Exception:
                continue
        contents.append({"name": safe_fn, "content": content})

    if not contents:
        raise HTTPException(400, "没有可读取的文件")

    result = m3u_handler.merge_files(
        contents, merge_mode, groups,
        fcc_config=fcc_config, rtp_proxy=rtp_proxy, group_rules=group_rules,
        keep_order=bool(data.get("keep_order", False)),
    )

    output_path = config.M3U_DIR / safe_output
    tmp = output_path.with_name(output_path.name + ".tmp")   # ★ 本次：统一命名
    tmp.write_text(result, encoding="utf-8", newline="\n")
    tmp.replace(output_path)

    parsed = m3u_handler.parse_m3u(result, merge_duplicates=False)
    mcast_merged = sum(1 for e in parsed["entries"] if e.get("_mcast_merged"))

    rtp_converted = 0
    if rtp_proxy:
        for e in parsed["entries"]:
            urls = e.get("_urls") or [e.get("url", "")]
            if any("/rtp/" in u and u.startswith("http") for u in urls):
                rtp_converted += 1

    fcc_applied = 0
    if fcc_config and fcc_config.get("enabled"):
        server = fcc_config.get("server", "")
        if server:
            for e in parsed["entries"]:
                urls = e.get("_urls") or [e.get("url", "")]
                if any(f"fcc={server}" in u for u in urls):
                    fcc_applied += 1

    return {
        "success": True,
        "filename": safe_output,
        "merged_from": len(contents),
        "entry_count": len(parsed["entries"]),
        "mcast_merged_groups": mcast_merged,
        "rtp_converted": rtp_converted,
        "fcc_applied": fcc_applied,
        "path": str(output_path),
    }


@app.get("/api/fcc/presets")
async def get_fcc_presets_api():
    return config.get_fcc_presets()


@app.post("/api/fcc/presets")
async def save_fcc_presets_api(data: dict = Body(...)):
    if not isinstance(data, dict):
        raise HTTPException(400, "无效数据")
    return {"success": config.save_fcc_presets(data)}


@app.get("/api/group-rules")
async def get_group_rules_api():
    return config.get_group_rules()


@app.post("/api/group-rules")
async def save_group_rules_api(data: dict = Body(...)):
    if not isinstance(data, dict):
        raise HTTPException(400, "无效数据")
    return {"success": config.save_group_rules(data)}


# ════════════════════════════════════════════════════════════════
# M3U 文件列表 / 上传 / 编辑 / 直链下载
# ════════════════════════════════════════════════════════════════

@app.post("/api/m3u/download-url")
async def download_m3u_url_api(data: dict = Body(...)):
    """后端代下载 M3U 链接（解决浏览器跨域限制）"""
    url = (data.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "URL 不能为空")
    if not downloader._is_safe_http_url(url):
        raise HTTPException(400, "URL 不允许（协议非法）")

    try:
        client = downloader.get_client()
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content = await downloader._read_limited(resp, 50 * 1024 * 1024) # 限制 50MB
            if content is None:
                raise HTTPException(400, "文件超过 50MB 上限")
            
        text = content.decode("utf-8", errors="replace").replace("\r\n", "\n")
        
        filename = url.split("/")[-1] or f"m3u_{int(time.time())}.m3u"
        filename = _safe_basename(filename)
        if not filename.endswith((".m3u", ".m3u8", ".txt")):
            filename += ".m3u"
            
        save_path = config.M3U_DIR / filename
        tmp = save_path.with_name(save_path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        tmp.replace(save_path)
        
        return {"success": True, "filename": filename, "path": str(save_path)}
    except httpx.HTTPStatusError as e:
        raise HTTPException(400, f"对方服务器返回错误: HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(400, f"网络请求失败: {e}")
    except Exception as e:
        raise HTTPException(500, f"下载失败: {e}")

@app.get("/api/m3u")
async def list_m3u_files():
    """列出 m3u / m3u8 / txt 文件（TXT 是电信 sctv.txt 等，必须能在这里看到）。"""
    files = []
    allowed = (".m3u", ".m3u8", ".txt")
    for f in sorted(config.M3U_DIR.iterdir()):
        if not f.is_file() or f.suffix.lower() not in allowed:
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            if f.suffix.lower() == ".txt":
                parsed = m3u_handler.parse_txt(content)
            else:
                parsed = m3u_handler.parse_m3u(content)
            files.append({
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "mtime": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "channel_count": len(parsed["entries"]),
                "is_txt": f.suffix.lower() == ".txt",
            })
        except Exception:
            pass
    return {"files": files}


@app.post("/api/m3u/upload")
async def upload_m3u(request: Request):
    body = await request.body()
    if len(body) > 50 * 1024 * 1024:
        raise HTTPException(400, "文件过大")
    content = body.decode("utf-8", errors="replace").replace("\r\n", "\n")

    filename = ""
    cd = request.headers.get("content-disposition", "")
    if "filename=" in cd:
        m = re.search(r'filename="?([^";]+)"?', cd)
        if m:
            from urllib.parse import unquote
            filename = unquote(m.group(1))
    filename = _safe_basename(filename) or f"m3u_{int(time.time())}.m3u"
    if not filename.endswith((".m3u", ".m3u8", ".txt")):
        filename += ".m3u"

    save_path = config.M3U_DIR / filename
    tmp = save_path.with_name(save_path.name + ".tmp")   # ★ 本次：统一命名
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    tmp.replace(save_path)
    return {"success": True, "filename": filename, "path": str(save_path)}


@app.get("/api/m3u/channel-options")
async def get_m3u_channel_options():
    options = m3u_handler.get_channel_options()
    debug = {
        "xml_exists": (config.XML_DIR / "epg.xml").exists(),
        "base_xml_exists": config.BASE_EPG_XML.exists(),
        "channel_count": len(config.get_channels()),
        "option_count": len(options),
    }
    return {"options": options, "debug": debug}


@app.get("/api/m3u/{filename}")
async def get_m3u_content(filename: str, merge: bool = Query(True)):
    safe = _safe_basename(filename)
    if not safe:
        raise HTTPException(400, "非法文件名")
    path = config.M3U_DIR / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    content = path.read_text(encoding="utf-8")
    if safe.lower().endswith(".txt"):
        parsed = m3u_handler.parse_txt(content)
    else:
        parsed = m3u_handler.parse_m3u(content, merge_duplicates=merge)
    return {"content": content, "parsed": parsed}


@app.post("/api/m3u/{filename}/save")
async def save_m3u_content(filename: str, data: dict = Body(...)):
    safe = _safe_basename(filename)
    if not safe:
        raise HTTPException(400, "非法文件名")
    path = config.M3U_DIR / safe
    entries = data.get("entries", [])
    header = data.get("header", "")
    if not isinstance(entries, list):
        raise HTTPException(400, "无效的 entries")
    # TXT 有独立格式，不能写成 M3U（否则编辑 sctv.txt 会把它变成 M3U）
    if safe.lower().endswith(".txt"):
        content = m3u_handler.rebuild_txt(entries)
    else:
        content = m3u_handler.rebuild_m3u(entries, header)
    tmp = path.with_name(path.name + ".tmp")   # ★ 本次：统一命名
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return {"success": True}


@app.delete("/api/m3u/{filename}")
async def delete_m3u(filename: str):
    safe = _safe_basename(filename)
    if not safe:
        raise HTTPException(400, "非法文件名")
    path = config.M3U_DIR / safe
    if path.exists() and path.is_file():
        path.unlink()
    return {"success": True}


# ════════════════════════════════════════════════════════════════
# 日志
# ════════════════════════════════════════════════════════════════

@app.get("/api/logs")
async def get_log_files():
    return {"logs": logger.get_log_files()}


@app.get("/api/logs/global")
async def get_global_logs(category: str = "", limit: int = 200):
    try:
        limit = max(1, min(int(limit), 2000))
    except (TypeError, ValueError):
        limit = 200
    entries = logger.get_global(category=category, limit=limit)
    categories = logger.get_categories()
    return {"entries": entries, "categories": categories}


@app.get("/api/logs/{date}")
async def get_log_content(date: str):
    if not re.fullmatch(r"\d{8}", date):
        raise HTTPException(400, "日期格式错误")
    entries = logger.get_logs(date)
    return {"date": date, "entries": entries}


@app.get("/api/logs/{date}/download")
async def download_log(date: str):
    if not re.fullmatch(r"\d{8}", date):
        raise HTTPException(400, "日期格式错误")
    entries = logger.get_logs(date)
    content = "\n".join(entries)
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=download_{date}.log"},
    )


# ════════════════════════════════════════════════════════════════
# 列宽
# ════════════════════════════════════════════════════════════════

@app.get("/api/column-widths")
async def get_column_widths():
    return config.get_column_widths()


@app.post("/api/column-widths")
async def save_column_widths(data: dict = Body(...)):
    if not isinstance(data, dict):
        raise HTTPException(400, "无效数据")
    config.save_column_widths(data)
    return {"success": True}


# ── 列顺序（拖动表头调整前后位置，刷新后保持） ──

@app.get("/api/column-order")
async def get_column_order():
    return config.get_column_order()


@app.post("/api/column-order")
async def save_column_order(data: dict = Body(...)):
    if not isinstance(data, dict):
        raise HTTPException(400, "无效数据")
    config.save_column_order(data)
    return {"success": True}


# ════════════════════════════════════════════════════════════════
# 频道总览（电信频道地址 / M3U 文件里的频道）
# ════════════════════════════════════════════════════════════════

@app.get("/api/overview")
async def channels_overview(source: str = "telecom"):
    """频道总览。

    source=telecom           → 电信频道：直播地址 + 回看地址 + 时移/FCC
    source=m3u:<文件名>       → 某个 M3U/TXT 文件（含合并结果）里的频道
    """
    source = (source or "telecom").strip()

    if source == "telecom":
        # ★ 按当前生效平台取数：华为 / 中兴各自一套地址规则
        platform = config.get_active_platform()
        platform_cfg = config.get_platform_config(platform)
        logo_url = str(platform_cfg.get("logo_url", "") or "").strip()
        epg_base = str(platform_cfg.get("epg_server_url", "") or "").strip()
        logo_base = (logo_url or epg_base).rstrip("/")
        channels = config.get_platform_channels(platform).get("channels", [])

        rtp = str(platform_cfg.get("RTP2HTTPD", "") or "").rstrip("/")
        r2h_token = str(platform_cfg.get("r2h_token", "") or "").strip()

        rows = []
        for ch in channels:
            name = str(ch.get("name", "") or "")
            mcast = str(ch.get("channel_url", "") or "")
            rtsp = str(ch.get("rtsp_url", "") or "")
            catchup_source = rtsp

            if platform == "zte":
                live = zte_epg.zte_live_url(ch, platform_cfg)
                catchup = zte_epg.zte_catchup_url(ch, platform_cfg)
                # 中兴：优先展示完整签名回看地址（TimeShiftURL）
                catchup_source = str(ch.get("timeshift_url") or rtsp)
            else:
                live = ""
                if mcast:
                    live = f"{rtp}/rtp/{mcast}" if rtp else f"rtp://{mcast}"

                catchup = ""
                if rtsp:
                    base = rtsp
                    if ".smil?" in base:
                        base = base.split(".smil?")[0] + ".smil"
                    if rtp:
                        base = base.replace("rtsp://", f"{rtp}/rtsp/")
                    catchup = base + "?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"

                # ★ 与生成的 m3u/txt 保持一致：配了 r2h-token 就加到直播/回看地址上
                live = append_r2h_token(live, r2h_token)
                catchup = append_r2h_token(catchup, r2h_token)

            fcc = ""
            if str(ch.get("fcc_enable", "")) == "2" and ch.get("fcc_ip"):
                fcc = f"{ch.get('fcc_ip')}:{ch.get('fcc_port', '')}"

            logo_href = config.logo_link(logo_url or logo_base, epg_base, name)
            # 只有「台标也在本站」时才检查文件是否存在；外部台标服务器不去探测
            same_site_logo = (not logo_url) or (logo_url.rstrip("/") == epg_base.rstrip("/"))
            logo_missing = bool(logo_href) and same_site_logo and find_icon_file(name + ".png") is None

            rows.append({
                "name": name,
                "live": live,
                "catchup": catchup,
                "catchup_source": catchup_source,
                "tvg_id": str(ch.get("tvg_id", "") or name),
                "tvg_name": name,
                # ★ 与「频道管理」同一套规则：本站 /api/download/，外部台标服务器 /logo/
                "logo": logo_href,
                "logo_missing": logo_missing,
                "group": "",
                "timeshift": str(ch.get("timeshift", "") or ""),
                "timeshift_length": str(ch.get("timeshift_length", "") or ""),
                "fcc": fcc,
                "fec": str(ch.get("fec_port", "") or ""),
                "extra_urls": 0,
            })
        return {
            "source": "telecom",
            "platform": platform,
            "label": f"{config.platform_label(platform).replace('平台', '')}频道",
            "count": len(rows),
            "rows": rows,
        }

    if not source.startswith("m3u:"):
        raise HTTPException(400, "source 参数非法（telecom 或 m3u:文件名）")

    fname = _safe_basename(source[4:])
    if not fname:
        raise HTTPException(400, "文件名为空")
    path = config.M3U_DIR / fname
    if not path.is_file():
        raise HTTPException(404, "文件不存在")

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(500, f"读取失败: {e}")

    if path.suffix.lower() == ".txt":
        parsed = m3u_handler.parse_txt(text)
    else:
        parsed = m3u_handler.parse_m3u(text, merge_duplicates=False)

    rows = []
    for e in parsed.get("entries", []):
        urls = e.get("_urls") or ([e.get("url")] if e.get("url") else [])
        urls = [u for u in urls if u]
        rows.append({
            "name": e.get("display_name") or e.get("tvg_name") or "",
            "live": urls[0] if urls else "",
            "catchup": e.get("catchup_source", "") or "",
            "catchup_source": e.get("catchup_source", "") or "",
            "tvg_id": e.get("tvg_id", "") or "",
            "tvg_name": e.get("tvg_name", "") or "",
            "logo": e.get("tvg_logo", "") or "",
            "group": e.get("group_title", "") or "",
            "timeshift": "",
            "timeshift_length": "",
            "fcc": "",
            "fec": "",
            "extra_urls": max(0, len(urls) - 1),
        })
    return {"source": source, "label": fname, "count": len(rows), "rows": rows}


# ════════════════════════════════════════════════════════════════
# 清理
# ════════════════════════════════════════════════════════════════

@app.post("/api/cleanup")
async def manual_cleanup():
    logs = []
    def log_msg(msg):
        logger.log(msg)
        logs.append(msg)
    await asyncio.to_thread(downloader.cleanup_old_files, log_callback=log_msg)
    return {"success": True, "logs": logs}


# ════════════════════════════════════════════════════════════════
# 共享目录
# ════════════════════════════════════════════════════════════════

@app.get("/api/share/files")
async def list_share_files(path: str = ""):
    settings = config.get_settings()
    share_path_str = settings.get("share_xml_path", "")
    if not share_path_str:
        return {"files": [], "error": "未设置共享目录路径"}

    share_dir = Path(share_path_str)
    if not share_dir.exists():
        return {"files": [], "error": f"目录不存在: {share_path_str}"}

    target_dir = _safe_path_under(share_dir, path) if path else share_dir.resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        return {"files": [], "error": f"目录不存在: {path}"}

    files = []
    folders = []
    for f in sorted(target_dir.iterdir()):
        rel_path = str(Path(path) / f.name) if path else f.name
        if f.is_dir():
            folders.append({
                "name": f.name,
                "path": rel_path,
                "is_dir": True,
                "mtime": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        elif f.is_file():
            try:
                files.append({
                    "name": f.name,
                    "path": rel_path,
                    "is_dir": False,
                    "size": f.stat().st_size,
                    "mtime": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
            except OSError:
                pass
    return {"files": folders + files, "current_path": path}


@app.post("/api/share/upload")
async def upload_share_file(request: Request):
    settings = config.get_settings()
    share_path_str = settings.get("share_xml_path", "")
    if not share_path_str:
        return {"success": False, "error": "未设置共享目录路径"}

    share_dir = Path(share_path_str)
    try:
        share_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"success": False, "error": f"无法创建目录: {share_path_str}"}

    from urllib.parse import unquote
    raw = request.headers.get("X-Filename", "")
    filename = _safe_basename(unquote(raw)) if raw else f"file_{int(time.time())}"
    if not filename:
        return {"success": False, "error": "非法文件名"}

    body = await request.body()
    if len(body) > 200 * 1024 * 1024:
        return {"success": False, "error": "文件过大（>200MB）"}

    # ★ 只有前端带 X-Base64: 1 时才解码：避免 curl 直接发二进制、恰好是合法
    #   base64 字符集时被误解码成乱码写盘。
    import base64
    if request.headers.get("x-base64") == "1":
        try:
            body = base64.b64decode(body)
        except Exception:
            return {"success": False, "error": "base64 解码失败"}

    save_path = share_dir / filename
    if save_path.exists():
        save_path = share_dir / f"{save_path.stem}_{int(time.time())}{save_path.suffix}"

    try:
        tmp = save_path.with_name(save_path.name + ".tmp")   # ★ 本次：统一命名
        tmp.write_bytes(body)
        tmp.replace(save_path)
        return {"success": True, "filename": save_path.name, "path": str(save_path)}
    except OSError as e:
        return {"success": False, "error": str(e)}


@app.delete("/api/share/{filename:path}")
async def delete_share_file(filename: str):
    settings = config.get_settings()
    share_path_str = settings.get("share_xml_path", "")
    if not share_path_str:
        return {"success": False, "error": "未设置共享目录路径"}

    share_dir = Path(share_path_str)
    file_path = _safe_path_under(share_dir, filename)
    if not file_path.is_file():
        return {"success": False, "error": "文件不存在"}

    try:
        file_path.unlink()
        return {"success": True}
    except OSError as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════════
# ICON 管理
# ════════════════════════════════════════════════════════════════

@app.get("/api/icons")
async def list_icons():
    files = []
    for f in sorted(config.ICONS_DIR.glob("*")):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"):
            try:
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "mtime": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "url": f"/api/download/{quote(f.name, safe='')}",
                })
            except OSError:
                pass
    return {"files": files}


@app.post("/api/icons/upload")
async def upload_icon(request: Request):
    from urllib.parse import unquote
    raw = request.headers.get("X-Filename", "")
    filename = _safe_basename(unquote(raw)) if raw else f"icon_{int(time.time())}"
    if not filename:
        return {"success": False, "error": "非法文件名"}

    body = await request.body()
    if len(body) > 20 * 1024 * 1024:
        return {"success": False, "error": "文件过大（>20MB）"}

    # ★ 同 upload_share_file：仅 X-Base64: 1 时解码
    import base64
    if request.headers.get("x-base64") == "1":
        try:
            body = base64.b64decode(body)
        except Exception:
            return {"success": False, "error": "base64 解码失败"}

    save_path = config.ICONS_DIR / filename
    if save_path.exists():
        save_path = config.ICONS_DIR / f"{save_path.stem}_{int(time.time())}{save_path.suffix}"

    try:
        tmp = save_path.with_name(save_path.name + ".tmp")   # ★ 本次：统一命名
        tmp.write_bytes(body)
        tmp.replace(save_path)
        return {"success": True, "filename": save_path.name}
    except OSError as e:
        return {"success": False, "error": str(e)}


@app.delete("/api/icons/{filename}")
async def delete_icon(filename: str):
    safe = _safe_basename(filename)
    if not safe:
        return {"success": False, "error": "非法文件名"}
    file_path = config.ICONS_DIR / safe
    if not file_path.is_file():
        return {"success": False, "error": "文件不存在"}
    try:
        file_path.unlink()
        return {"success": True}
    except OSError as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════════
# 短路径 EPG URL
# ════════════════════════════════════════════════════════════════

@app.head("/epg.xml", include_in_schema=False)
@app.get("/epg.xml")
async def short_epg_xml():
    """短路径：返回原始 XML（公开，供播放器直接引用）。"""
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>',
                        media_type="application/xml")
    return FileResponse(str(xml_path), media_type="application/xml", headers={
        "Cache-Control": "no-store",
    })


@app.head("/epg.xml.gz", include_in_schema=False)
@app.get("/epg.xml.gz")
async def short_epg_gz():
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>',
                        media_type="text/xml")
    xml_bytes = xml_path.read_bytes()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        gz.write(xml_bytes)
    return Response(content=buf.getvalue(), media_type="application/gzip", headers={
        "Cache-Control": "no-store",
    })


# ════════════════════════════════════════════════════════════════
# 短路径台标 URL（公开）
#
# 生成 m3u 时 tvg-logo 写的是 <logo_url>/api/download/<频道名>.png，
# 这里把它映射到 data/icons 里的同名台标文件。
# ════════════════════════════════════════════════════════════════

_LOGO_MEDIA = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".ico": "image/x-icon",
}

# 台标索引缓存：{小写文件名: 真实文件名}，按目录 mtime 失效
_icon_index_cache = {"mtime": None, "names": {}}


def _icon_index() -> dict:
    """data/icons 里所有图片文件的小写名 → 真实名（用于忽略大小写/后缀查找）。"""
    try:
        st = config.ICONS_DIR.stat()
    except OSError:
        return {}
    if _icon_index_cache["mtime"] == st.st_mtime:
        return _icon_index_cache["names"]
    names = {}
    try:
        for f in config.ICONS_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in _LOGO_MEDIA:
                names[f.name.lower()] = f.name
    except OSError:
        names = {}
    _icon_index_cache["mtime"] = st.st_mtime
    _icon_index_cache["names"] = names
    return names


def find_icon_file(filename: str):
    """按名字找台标文件，找不到返回 None。

    查找顺序：完全同名 → 忽略大小写同名 → 同主名换其它图片后缀。
    这样「江苏卫视4K.png」也能命中磁盘上的 `江苏卫视4k.PNG` / `江苏卫视4K.jpg`，
    不会因为大小写或后缀不一致就 404。
    """
    name = _safe_basename(filename)
    if not name:
        return None
    exact = config.ICONS_DIR / name
    if exact.is_file():
        return exact

    idx = _icon_index()
    hit = idx.get(name.lower())
    if hit:
        return config.ICONS_DIR / hit

    stem = Path(name).stem.lower()
    for ext in _LOGO_MEDIA:
        hit = idx.get(stem + ext)
        if hit:
            return config.ICONS_DIR / hit
    return None


@app.head("/logo/{filename:path}", include_in_schema=False)
@app.get("/logo/{filename:path}")
async def short_logo(filename: str):
    """台标短链：/logo/CCTV1.png → data/icons 下的台标文件。"""
    target = find_icon_file(filename)
    if target is None:
        raise HTTPException(404, "台标不存在")

    return FileResponse(
        str(target),
        media_type=_LOGO_MEDIA.get(target.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ════════════════════════════════════════════════════════════════
# 外部工具下载（公开）
#
# 以前 IPTV.exe 是 COPY 进镜像的 static/tools/IPTV.exe：换个播放器版本就得
# 重建镜像，还让镜像白白大 100MB。现在优先读挂载的 data/tools（宿主 ./data/tools），
# 换文件即时生效；镜像里 static/tools 只作为老用户的兜底。
# ════════════════════════════════════════════════════════════════

@app.head("/tools/{filename:path}", include_in_schema=False)
@app.get("/tools/{filename:path}")
async def tools_download(filename: str):
    """下载 data/tools（或镜像内 static/tools）里的文件，例如 IPTV.exe。"""
    name = _safe_basename(filename)
    if not name:
        raise HTTPException(404, "文件不存在")

    for base in (config.TOOLS_DIR, static_dir / "tools"):
        target = Path(base) / name
        if target.is_file():
            return FileResponse(
                str(target),
                media_type="application/octet-stream",
                filename=name,          # 触发 Content-Disposition: attachment
                headers={"Cache-Control": "no-cache"},
            )
    raise HTTPException(404, "文件不存在")


# ════════════════════════════════════════════════════════════════
# 统一下载入口 /api/download/...
#
#   /api/download/telecom.m3u        → data/m3u   （m3u / m3u8 / txt）
#   /api/download/CCTV1.png          → data/icons （png/jpg/jpeg/gif/svg/webp/ico）
#   /api/download/epg.xml(.gz)       → data/xml   （xml / gz）
#   /api/download/share/子目录/文件   → 共享目录   （任意后缀，**需登录**）
#
# 也支持显式类型前缀：/api/download/{m3u|icon|xml|share}/...
# 可选参数：?type=m3u|icon|xml|share  ?download=1（强制作为附件下载）
# ════════════════════════════════════════════════════════════════

_M3U_EXTS = {".m3u", ".m3u8", ".txt"}
_ICON_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}
_XML_EXTS = {".xml", ".gz"}
_DOWNLOAD_KINDS = ("m3u", "icon", "xml", "share")


def _download_sources() -> list:
    """(类型, 目录, 允许的后缀) —— 按查找优先级排列。"""
    share_dir = config.get_settings().get("share_xml_path") or str(config.SHARE_DIR)
    return [
        ("m3u", config.M3U_DIR, _M3U_EXTS),
        ("icon", config.ICONS_DIR, _ICON_EXTS),
        ("xml", config.XML_DIR, _XML_EXTS),
        ("share", Path(share_dir), None),      # 共享目录：不限制后缀，但需登录
    ]


def _resolve_download(filepath: str, kind: str = ""):
    """把 /api/download/ 后面的路径解析成真实文件，返回 (类型, Path) 或 (None, None)。"""
    filepath = (filepath or "").replace("\\", "/").strip("/")
    if not filepath or ".." in filepath.split("/"):
        return None, None

    sources = _download_sources()

    # 显式类型前缀：/api/download/share/a/b.txt
    first, sep, rest = filepath.partition("/")
    if rest and first in _DOWNLOAD_KINDS:
        if not kind:
            kind, filepath = first, rest
            if not filepath:
                return None, None
        elif first == kind:
            # 已用 ?type=share 指定类型时，路径里再带 share/ 前缀也能正确解析
            filepath = rest
            if not filepath:
                return None, None

    for name, base, exts in sources:
        if kind and name != kind:
            continue

        if name == "share":
            try:
                target = _safe_path_under(base, filepath)
            except HTTPException:
                continue
        elif name == "icon":
            # 台标：精确名找不到时忽略大小写/后缀再找（中文名、大写 .PNG 都能命中）
            target = find_icon_file(filepath)
            if target is None:
                continue
        else:
            # m3u / xml 目录都是平铺的，只取文件名（顺带挡掉路径穿越）
            fname = _safe_basename(filepath)
            if not fname:
                continue
            target = base / fname

        if not target.is_file():
            continue
        if target.name.lower().endswith(".tmp"):
            continue
        if exts is not None and target.suffix.lower() not in exts:
            continue
        return name, target

    return None, None


@app.head("/api/download/{filepath:path}", include_in_schema=False)
@app.get("/api/download/{filepath:path}")
async def download_unified(request: Request, filepath: str,
                           type_: str = Query("", alias="type"),
                           download: int = Query(0)):
    """统一下载入口：m3u / txt / 台标图片 / EPG XML / 共享文件。

    - m3u、icon、xml 三类公开，播放器可直接引用；
    - 共享目录（share）需要登录。
    """
    kind_req = (type_ or "").strip().lower()
    if kind_req and kind_req not in _DOWNLOAD_KINDS:
        raise HTTPException(400, "type 只能是 m3u / icon / xml / share")

    kind, target = _resolve_download(filepath, kind_req)
    if target is None:
        raise HTTPException(404, "文件不存在")

    if kind == "share" and not _is_request_authed(request):
        raise HTTPException(401, "共享目录文件需要登录后才能下载")

    media_type = _MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
    quoted = quote(target.name, safe="")
    ascii_name = target.name.encode("ascii", "ignore").decode() or "download"
    disposition = "attachment" if download else "inline"
    disposition_header = (
        f'{disposition}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quoted}"
    )
    return FileResponse(
        str(target),
        media_type=media_type,
        headers={
            "Content-Disposition": disposition_header,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Access-Control-Allow-Origin": "*",
        },
    )
