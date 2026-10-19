"""iptv-server EPG 管理系统主应用"""
import asyncio
import datetime
import gzip
import io
import json
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Query, Body
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    HTMLResponse, JSONResponse, FileResponse, Response, StreamingResponse
)
from fastapi.staticfiles import StaticFiles

from . import config
from . import downloader
from . import xml_builder
from . import epg_parser
from . import m3u_handler
from . import proxy_handler
from . import proxy_handler as _proxy_handler
from .logger import logger
from .scheduler import scheduler
from .auth import router as auth_router
from .ku9_api import router as ku9_router

app = FastAPI(title="iptv-server EPG 管理系统")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 挂载静态文件
static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 注册独立模块路由
app.include_router(auth_router, prefix="")
app.include_router(ku9_router)

# ===================== 下载进度管理 =====================

class DownloadProgress:
    def __init__(self):
        self._messages = []
        self._running = False
        self._paused = False
        self._stopped = False
        self._current = {"current": 0, "total": 0, "message": ""}

    async def add_message(self, msg: str):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self._messages.append(f"[{timestamp}] {msg}")
        self._current["message"] = msg

    def get_messages(self):
        return list(self._messages)

    def clear(self):
        self._messages.clear()
        self._current = {"current": 0, "total": 0, "message": ""}

    def set_progress(self, current: int, total: int):
        self._current["current"] = current
        self._current["total"] = total

    def is_paused(self):
        return self._paused

    def is_stopped(self):
        return self._stopped


download_progress = DownloadProgress()

# ===================== 页面路由 =====================

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>EPG 管理界面</h1><p>请确保 index.html 存在</p>")


@app.get("/api/status")
async def api_status():
    """系统状态"""
    settings = config.get_settings()
    channels = config.get_channels()
    extracts = config.get_epg_extracts()
    channel_total = len(channels) + len(extracts)
    # JSON 文件数
    json_count = len(list(config.JSON_DIR.glob("*.json")))
    # 台标文件数
    icon_count = len([f for f in config.ICONS_DIR.iterdir() if f.is_file()]) if config.ICONS_DIR.exists() else 0
    # XML 文件大小
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
    }

# ===================== 频道管理 API =====================

@app.get("/api/channels")
async def get_channels():
    return {"channels": config.get_channels()}


@app.post("/api/channels")
async def add_channel(data: dict = Body(...)):
    channels = config.get_channels()
    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    tvg_id = data.get("tvg_id", "")

    if not name or not url:
        raise HTTPException(400, "频道名称和链接不能为空")

    # 自动检测日期并替换为 {date}
    url = re.sub(r'_\d{8}\.json', '_{date}.json', url)

    # 自动生成 TVG-ID
    if not tvg_id:
        existing_ids = [ch.get("tvg_id", "0") for ch in channels]
        max_id = 0
        for eid in existing_ids:
            try:
                max_id = max(max_id, int(eid))
            except ValueError:
                pass
        tvg_id = str(max_id + 1)

    channel = {
        "name": name,
        "url": url,
        "tvg_id": tvg_id,
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
        ch["name"] = data["name"]
    if "url" in data:
        url = data["url"]
        url = re.sub(r'_\d{8}\.json', '_{date}.json', url)
        ch["url"] = url
    if "tvg_id" in data:
        ch["tvg_id"] = data["tvg_id"]
    if "enabled" in data:
        ch["enabled"] = data["enabled"]
    if "sort_order" in data:
        ch["sort_order"] = data["sort_order"]

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
    """拖拽排序后更新顺序"""
    indices = data.get("indices", [])
    channels = config.get_channels()
    reordered = [channels[i] for i in indices if i < len(channels)]
    for idx, ch in enumerate(reordered):
        ch["sort_order"] = idx
    config.save_channels(reordered)
    return {"success": True}


@app.post("/api/channels/replace-url")
async def replace_url(data: dict = Body(...)):
    """一键替换链接中的域名"""
    old_part = data.get("old", "")
    new_part = data.get("new", "")
    if not old_part:
        raise HTTPException(400, "请填写需要替换的内容")

    channels = config.get_channels()
    count = 0
    for ch in channels:
        if old_part in ch.get("url", ""):
            ch["url"] = ch["url"].replace(old_part, new_part)
            count += 1

    config.save_channels(channels)
    return {"success": True, "count": count}


@app.get("/api/channels/export")
@app.post("/api/channels/export")
async def export_channels():
    channels = config.get_channels()
    content = json.dumps(channels, ensure_ascii=False, indent=2)
    content = content.replace("\r\n", "\n")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="channels_{datetime.date.today().strftime("%Y%m%d")}.json"'},
    )


@app.post("/api/channels/import")
async def import_channels(request: Request):
    body = await request.body()
    try:
        data = json.loads(body)
        if isinstance(data, list):
            channels = data
        elif isinstance(data, dict) and "channels" in data:
            channels = data["channels"]
        else:
            raise HTTPException(400, "无效的导入格式")
        # 确保每条记录都有 logic_id
        for ch in channels:
            if not ch.get("logic_id"):
                ch["logic_id"] = str(uuid.uuid4())
        config.save_channels(channels)
        return {"success": True, "count": len(channels)}
    except json.JSONDecodeError:
        raise HTTPException(400, "无效的 JSON 格式")


# ===================== EPG 频道管理 API =====================

@app.get("/api/epg-channels")
async def get_epg_channels_list():
    return {"channels": config.get_epg_channels()}


@app.post("/api/epg-channels")
async def add_epg_channel(data: dict = Body(...)):
    channels = config.get_epg_channels()
    name = data.get("name", "").strip()
    tvg_id = data.get("tvg_id", "")
    if not name:
        raise HTTPException(400, "频道名称不能为空")
    if not tvg_id:
        # 不再自动递增，由前端从频道管理列表读取
        tvg_id = ''
    tvg_name = data.get("tvg_name", name)
    channel = {"name": name, "tvg_id": tvg_id, "tvg_name": tvg_name, "logic_id": data.get("logic_id", ""), "enabled": True, "sort_order": len(channels)}
    channels.append(channel)
    config.save_epg_channels(channels)
    return {"success": True, "channel": channel}


@app.put("/api/epg-channels/{index}")
async def update_epg_channel(index: int, data: dict = Body(...)):
    channels = config.get_epg_channels()
    if index < 0 or index >= len(channels):
        raise HTTPException(404, "频道不存在")
    ch = channels[index]
    if "name" in data:
        ch["name"] = data["name"]
    if "tvg_id" in data:
        ch["tvg_id"] = data["tvg_id"]
    if "tvg_name" in data:
        ch["tvg_name"] = data["tvg_name"]
    if "logic_id" in data:
        ch["logic_id"] = data["logic_id"]
    if "enabled" in data:
        ch["enabled"] = data["enabled"]
    if "sort_order" in data:
        ch["sort_order"] = data["sort_order"]
    channels[index] = ch
    config.save_epg_channels(channels)
    return {"success": True}


@app.delete("/api/epg-channels/{index}")
async def delete_epg_channel(index: int):
    channels = config.get_epg_channels()
    if index < 0 or index >= len(channels):
        raise HTTPException(404, "频道不存在")
    channels.pop(index)
    config.save_epg_channels(channels)
    return {"success": True}


@app.post("/api/epg-channels/clear")
async def clear_epg_channels():
    config.save_epg_channels([])
    return {"success": True}


@app.post("/api/epg-channels/reorder")
async def reorder_epg_channels(data: dict = Body(...)):
    indices = data.get("indices", [])
    channels = config.get_epg_channels()
    reordered = [channels[i] for i in indices if i < len(channels)]
    for idx, ch in enumerate(reordered):
        ch["sort_order"] = idx
    config.save_epg_channels(reordered)
    return {"success": True}


@app.post("/api/epg-channels/sync-all")
async def sync_all_epg_channels(data: dict = Body(...)):
    """按 logic_id 同步 EPG 管理列表（增删不动已有）"""
    channels = config.get_epg_channels()
    source_ids = set(data.get("logic_ids", []))
    source_ids.discard("")

    source_info = {}
    for src in data.get("sources", []):
        lid = src.get("logic_id", "")
        if lid:
            source_info[lid] = {
                "source_type": src.get("source_type", "channel"),
                "source_remark": src.get("source_remark", ""),
                "extract_name": src.get("extract_name", ""),
            }

    keep = []
    for ch in channels:
        lid = ch.get("logic_id", "")
        if lid in source_ids:
            info = source_info.get(lid, {})
            if info.get("source_type"):
                ch["source_type"] = info["source_type"]
            if info.get("source_remark"):
                ch["source_remark"] = info["source_remark"]
            ch["extract_name"] = info.get("extract_name", "")
            keep.append(ch)

    existing_ids = {ch.get("logic_id", "") for ch in keep if ch.get("logic_id")}
    for src in data.get("sources", []):
        lid = src.get("logic_id", "")
        if lid and lid not in existing_ids:
            existing_ids.add(lid)
            keep.append({
                "name": src.get("name", ""),
                "tvg_id": src.get("tvg_id", ""),
                "tvg_name": src.get("tvg_name", src.get("name", "")),
                "logic_id": lid,
                "source_type": src.get("source_type", "channel"),
                "source_remark": src.get("source_remark", ""),
                "extract_name": src.get("extract_name", ""),
                "enabled": True,
                "sort_order": len(keep),
            })

    config.save_epg_channels(keep)
    return {"success": True, "count": len(keep)}


# ===================== XML 管理 =====================

@app.get("/api/xml")
async def get_xml():
    """获取生成的 XML 文件"""
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>', media_type="application/xml")
    return FileResponse(str(xml_path), media_type="application/xml")


@app.get("/api/xml/url")
async def get_xml_url(request: Request):
    """获取可复制的 XML 直链"""
    host = request.headers.get("host", "localhost:5673")
    scheme = request.headers.get("x-forwarded-proto", "http")
    return {"url": f"{scheme}://{host}/api/xml/epg.xml"}


@app.api_route("/api/xml/epg.xml", methods=["GET", "HEAD"])
async def get_xml_file():
    """返回原始 XML 文件（非压缩）"""
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>', media_type="application/xml")
    return FileResponse(str(xml_path), media_type="application/xml", headers={
        "Cache-Control": "public, max-age=300",
    })


@app.api_route("/api/xml/epg.xml.gz", methods=["GET", "HEAD"])
async def get_xml_gz():
    """返回 gzip 压缩的 XML（与 nginx 静态 .gz 文件返回一致）"""
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>', media_type="application/xml")
    xml_bytes = xml_path.read_bytes()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        gz.write(xml_bytes)
    return Response(content=buf.getvalue(), media_type="application/gzip")


@app.post("/api/xml/build")
async def build_xml():
    """手动生成 XML"""
    if download_progress._running:
        return {"success": False, "error": "下载任务正在进行中，请等待完成"}

    result = xml_builder.build_xml(
        log_callback=lambda msg: logger.log(msg),
    )
    return result

# ===================== 下载管理 API =====================

@app.post("/api/download/start")
async def start_download():
    """开始下载（今天+明天+后天）"""
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
    """下载过去6天的数据"""
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
    """执行下载任务（支持暂停/继续/停止）"""
    try:
        channels = config.get_channels()
        epg_channels = config.get_epg_channels()
        # 按 logic_id 建立 EPG 名称映射（logic_id → name），无 logic_id 的按原名称匹配
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

            # 检查停止
            if download_progress._stopped:
                await download_progress.add_message("下载已停止")
                break

            # 检查暂停
            while download_progress._paused and not download_progress._stopped:
                await asyncio.sleep(0.5)

            if download_progress._stopped:
                break

            download_progress.set_progress(current, total)

            # 使用 EPG 管理列表的频道名作为保存文件名
            save_name = epg_name_map.get(ch.get("logic_id", ""), ch.get("name", ""))
            ch_copy = dict(ch)
            if save_name:
                ch_copy["name"] = save_name

            results = await downloader.download_channel_json(
                ch_copy, dates,
                progress_callback=download_progress.add_message,
                log_callback=lambda msg: logger.log(msg),
            )

        # 清理旧文件
        downloader.cleanup_old_files()

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
        "messages": download_progress.get_messages()[-200:],  # 最近200条
        "current": download_progress._current,
    }

# ===================== 外部 EPG 管理 API =====================

@app.get("/api/epg/sources")
async def get_epg_sources():
    return {"sources": config.get_epg_sources()}


@app.post("/api/epg/sources")
async def add_epg_source(data: dict = Body(...)):
    sources = config.get_epg_sources()
    url = data.get("url", "").strip()
    rule = data.get("rule", "auto")

    if not url:
        raise HTTPException(400, "URL 不能为空")

    source = {
        "url": url,
        "rule": rule,
        "enabled": True,
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
        src["url"] = data["url"]
    if "rule" in data:
        src["rule"] = data["rule"]
    if "enabled" in data:
        src["enabled"] = data["enabled"]
    sources[index] = src
    config.save_epg_sources(sources)
    return {"success": True}


@app.delete("/api/epg/sources/{index}")
async def delete_epg_source(index: int):
    sources = config.get_epg_sources()
    if index < 0 or index >= len(sources):
        raise HTTPException(404, "源不存在")
    sources.pop(index)
    config.save_epg_sources(sources)
    return {"success": True}


@app.post("/api/epg/download")
async def download_epg():
    """下载所有外部 EPG 源"""
    if download_progress._running:
        return {"success": False, "error": "已有任务运行中"}

    asyncio.create_task(_run_epg_download())
    return {"success": True, "message": "EPG 下载已启动"}


async def _run_epg_download():
    await downloader.download_all_epg_sources(
        progress_callback=download_progress.add_message,
        log_callback=lambda msg: logger.log(msg),
    )
    # 下载完成后自动合并
    await download_progress.add_message("EPG 下载完成，正在合并...")
    scheduler.run_merge()
    await download_progress.add_message("EPG 合并完成")


@app.get("/api/epg/channels")
async def search_epg_channels(keyword: str = ""):
    """搜索外部 EPG 中的频道（包含来源URL）"""
    epg_cache = config.CONFIG_DIR / "epg_cache"
    all_channels = []
    seen = set()

    if epg_cache.exists():
        sources = config.get_epg_sources()
        for f in sorted(epg_cache.glob("*.xml")):
            try:
                text = f.read_text(encoding="utf-8")
                # 找到该源文件的原始 URL
                source_url = ""
                for s in sources:
                    sname = s.get("url", "").split("/")[-1].replace(".gz", "").replace(".xml", "")
                    if sname and sname in f.name:
                        source_url = s.get("url", "")
                        break

                channels = epg_parser.get_channel_list(text)
                for ch in channels:
                    key = ch.get("id", "") + ch.get("name", "") + source_url
                    if key not in seen:
                        seen.add(key)
                        ch["source_url"] = source_url
                        all_channels.append(ch)
            except Exception:
                pass

    if keyword:
        all_channels = [c for c in all_channels if keyword.lower() in c["name"].lower() or keyword.lower() in c["id"].lower()]

    return {"channels": all_channels}


# ===================== EPG 提取记录 API =====================

@app.get("/api/epg/extracts")
async def get_epg_extracts():
    return {"extracts": config.get_epg_extracts()}


@app.post("/api/epg/extracts")
async def add_epg_extract(data: dict = Body(...)):
    extracts = config.get_epg_extracts()
    name = data.get("name", "").strip()
    source = data.get("source", "").strip()
    source_url = data.get("source_url", "").strip()
    tvg_id = data.get("tvg_id", "")
    rule = data.get("rule", "auto")

    if not name or not source:
        raise HTTPException(400, "名称和来源不能为空")

    if not tvg_id:
        existing_ids = [e.get("tvg_id", "9999") for e in extracts]
        max_id = 9999
        for eid in existing_ids:
            try:
                max_id = max(max_id, int(eid))
            except ValueError:
                pass
        tvg_id = str(max_id + 1)

    extract = {
        "name": name,
        "source": source,
        "source_url": source_url,
        "tvg_id": tvg_id,
        "rule": rule,
        "logic_id": str(uuid.uuid4()),
    }
    extracts.append(extract)
    config.save_epg_extracts(extracts)

    # 同步到 EPG 管理列表（使用 EPG 管理列表自身的 TVG-ID 递增）
    epg_channels = config.get_epg_channels()
    exists = any(ch.get("name") == name for ch in epg_channels)
    if not exists:
        epg_tvg_id = str(len(epg_channels) + 1)
        epg_channels.append({
            "name": name,
            "tvg_id": epg_tvg_id,
            "enabled": True,
            "sort_order": len(epg_channels),
        })
        config.save_epg_channels(epg_channels)

    return {"success": True, "extract": extract}


@app.put("/api/epg/extracts/{index}")
async def update_epg_extract(index: int, data: dict = Body(...)):
    extracts = config.get_epg_extracts()
    if index < 0 or index >= len(extracts):
        raise HTTPException(404, "记录不存在")
    ext = extracts[index]
    for key in ["name", "source", "tvg_id", "rule"]:
        if key in data:
            ext[key] = data[key]
    extracts[index] = ext
    config.save_epg_extracts(extracts)
    return {"success": True}


@app.delete("/api/epg/extracts/{index}")
async def delete_epg_extract(index: int):
    extracts = config.get_epg_extracts()
    if index < 0 or index >= len(extracts):
        raise HTTPException(404, "记录不存在")
    extracts.pop(index)
    config.save_epg_extracts(extracts)
    return {"success": True}


@app.post("/api/epg/extracts/import")
async def import_epg_extracts(data: list = Body(...)):
    """批量导入提取记录"""
    extracts = config.get_epg_extracts()
    added = 0
    for item in data:
        name = item.get("name", "").strip()
        source = item.get("source", "").strip()
        if not name or not source:
            continue
        exists = any(e.get("name") == name for e in extracts)
        if exists:
            continue
        source_url = item.get("source_url", "")
        tvg_id = item.get("tvg_id", "")
        rule = item.get("rule", "auto")
        if not tvg_id:
            tvg_id = str(len(extracts) + 1000)
        logic_id = item.get("logic_id", "") or str(uuid.uuid4())
        extract = {
            "name": name,
            "source": source,
            "source_url": source_url,
            "tvg_id": tvg_id,
            "rule": rule,
            "logic_id": logic_id,
        }
        extracts.append(extract)
        added += 1
    if added > 0:
        config.save_epg_extracts(extracts)
    return {"success": True, "added": added}


@app.post("/api/epg/extract-to-json")
async def extract_epg_to_json():
    """下载外部EPG并提取到JSON文件"""
    if download_progress._running:
        return {"success": False, "error": "有任务正在运行"}
    asyncio.create_task(_run_extract_to_json())
    return {"success": True, "message": "外部EPG提取已启动"}


async def _run_extract_to_json():
    import time as _time
    _start = _time.time()
    try:
        await download_progress.add_message("===== 外部EPG提取到JSON =====")
        logger.log("[任务] 外部EPG提取到JSON: 开始", category="EPG")

        # 1. 下载外部 EPG 源
        await download_progress.add_message("[第1步] 下载外部 EPG 源...")
        epg_results = await downloader.download_all_epg_sources(
            progress_callback=download_progress.add_message,
            log_callback=lambda msg: logger.log(msg),
        )
        epg_ok = sum(1 for r in epg_results if r and r.get("success"))
        epg_fail = sum(1 for r in epg_results if r and not r.get("success"))
        logger.log(f"[任务] EPG 源下载完成: 成功{epg_ok}个, 失败{epg_fail}个", category="下载")
        await download_progress.add_message(f"[第1步] EPG 源下载完成 (成功{epg_ok}/失败{epg_fail})")

        # 2. 按提取记录提取节目
        extracts = config.get_epg_extracts()
        epg_cache = config.CONFIG_DIR / "epg_cache"
        today = datetime.date.today()
        extract_dates = [
            today.strftime("%Y%m%d"),
            (today + datetime.timedelta(days=1)).strftime("%Y%m%d"),
            (today + datetime.timedelta(days=2)).strftime("%Y%m%d"),
        ]
        json_dir = config.JSON_DIR

        logger.log(f"[任务] [第2步] 开始提取，共 {len(extracts)} 条记录", category="EPG")
        await download_progress.add_message(f"[第2步] 提取节目，共 {len(extracts)} 条记录...")

        # 按 logic_id 建立 EPG 名称映射
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
            # 用原始提取名称搜索 EPG 缓存
            search_name = raw_name
            # 用 EPG 管理列表的频道名作为 JSON 保存文件名
            save_name = epg_name_map.get(ext.get("logic_id", ""), raw_name)

            # 在所有已下载的 EPG 缓存中搜索该频道名
            programmes = []
            # 严格按提取记录指定的源(source_url)提取, 不兜底遍历
            for cf in epg_parser.resolve_cache_files(epg_cache, ext.get("source_url", "")):
                try:
                    cache_text = cf.read_text(encoding="utf-8")
                except Exception:
                    continue
                progs = epg_parser.extract_programmes(cache_text, rule, search_name)
                if progs:
                    programmes.extend(progs)
                    break

            date_progs = defaultdict(list)

            if programmes:
                for prog in programmes:
                    start = prog.get("start", "")
                    d = start[:8] if len(start) >= 8 else ""
                    if d in extract_dates:
                        date_progs[d].append(prog)

            # 只保留实际提取到的日期（在 extract_dates 范围内），不再生成"暂无节目单"占位
            got_dates = sorted(set(d for d in date_progs if date_progs[d]))
            if not programmes:
                fail_count += 1
                logger.log(f"[任务]   [{idx}/{len(extracts)}] {raw_name}: ❌ 未提取到节目（源内无该频道或源文件缺失）", category="EPG")
            else:
                logger.log(f"[任务]   [{idx}/{len(extracts)}] {raw_name}: 已提取到（{', '.join(got_dates)}）", category="EPG")

            for d, progs in date_progs.items():
                save_filename = f"{save_name}_{d}.json"
                save_path = json_dir / save_filename
                prog_list = [{"name": raw_name, "start": p.get("start", ""), "stop": p.get("stop", ""), "title": p.get("title", "")} for p in progs]
                try:
                    json_dir.mkdir(parents=True, exist_ok=True)
                    with open(save_path, "w", encoding="utf-8") as f:
                        json.dump({"programmes": prog_list}, f, ensure_ascii=False)
                    extract_count += 1
                except Exception as e:
                    logger.log(f"[任务] 保存 {save_name} 失败: {e}", category="失败")

            if idx % 10 == 0 or idx == len(extracts):
                await download_progress.add_message(f"  [{idx}/{len(extracts)}] 成功{extract_count}个JSON, 失败{fail_count}条")

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
    """提取所有外部 EPG 记录并合并到 XML"""
    if download_progress._running:
        return {"success": False, "error": "有任务正在运行"}

    asyncio.create_task(_run_extract_and_merge())
    return {"success": True, "message": "提取合并任务已启动"}


async def _run_extract_and_merge():
    try:
        import time
        start_time = time.time()
        logger.log("[EPG] ===== 开始外部EPG提取合并流程 =====", category="EPG")
        await download_progress.add_message("===== 开始外部EPG提取合并流程 =====")

        # 1. 下载外部 EPG 文件
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

        # 2. 遍历提取记录，逐条从原始 EPG 文件提取节目单
        extracts = config.get_epg_extracts()
        epg_cache = config.CONFIG_DIR / "epg_cache"
        logger.log(f"[EPG] [第2步] 开始逐条提取节目单（共 {len(extracts)} 条记录）...", category="EPG")
        await download_progress.add_message(f"[第2步] 开始逐条提取节目单（共 {len(extracts)} 条记录）...")

        total_programmes = 0
        extract_success = 0
        extract_fail = 0
        for idx, ext in enumerate(extracts, 1):
            name = ext.get("name", "未知")
            source = ext.get("source", "")
            rule = ext.get("rule", "auto")
            source_url = ext.get("source_url", "")

            # 找到对应的 EPG 缓存文件
            cache_text = ""
            if source_url and epg_cache.exists():
                cache_name = source_url.split("/")[-1].replace(".gz", "")
                for cf in epg_cache.glob("*.xml"):
                    if cache_name in cf.name or cf.name.startswith(cache_name):
                        try:
                            cache_text = cf.read_text(encoding="utf-8")
                        except Exception:
                            pass
                        break

            if not cache_text:
                logger.log(f"[EPG]   [{idx}/{len(extracts)}] {name}: ⚠️ 未找到EPG缓存文件，跳过", category="EPG")
                await download_progress.add_message(f"  [{idx}/{len(extracts)}] {name}: 未找到对应的 EPG 缓存文件")
                extract_fail += 1
                continue

            programmes = epg_parser.extract_programmes(cache_text, rule, name)

            count = len(programmes)
            total_programmes += count
            remaining = len(extracts) - idx
            if count > 0:
                extract_success += 1
                logger.log(f"[EPG]   [{idx}/{len(extracts)}] ✓ {name}: 提取到 {count} 条节目 (剩余 {remaining} 条)", category="EPG")
            else:
                extract_fail += 1
                logger.log(f"[EPG]   [{idx}/{len(extracts)}] ⚠️ {name}: 未提取到节目 (剩余 {remaining} 条)", category="EPG")
            await download_progress.add_message(f"  [{idx}/{len(extracts)}] {name}: 从 {source_url.split('/')[-1][:30]} 提取到 {count} 条节目")

        elapsed = int(time.time() - start_time)
        logger.log(f"[EPG] [第2步] 提取完成: 成功 {extract_success} 条, 失败 {extract_fail} 条, 共 {total_programmes} 条节目, 耗时 {elapsed}秒", category="EPG")
        await download_progress.add_message(f"[第2步] 提取完成，共 {total_programmes} 条节目")

        # 3. 生成 XML（整合所有数据）
        logger.log("[EPG] [第3步] 生成自建 XML 文件...", category="EPG")
        await download_progress.add_message("[第3步] 生成自建 XML 文件...")
        result = xml_builder.build_xml(
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


# ===================== 设置 API =====================

@app.get("/api/settings")
async def get_settings():
    return config.get_settings()


@app.post("/api/settings")
async def update_settings(data: dict = Body(...)):
    settings = config.get_settings()
    for key in ["cron_time", "cron_enabled", "proxy_port", "share_xml_path", "xml_gz_enabled"]:
        if key in data:
            settings[key] = data[key]
    config.save_settings(settings)

    # 重设定时任务
    if "cron_time" in data or "cron_enabled" in data:
        scheduler.reschedule()

    return {"success": True}

# ===================== 端口映射 API（独立文件） =====================
from . import port_map_api
app.include_router(port_map_api.router)

@app.get("/api/m3u")
async def list_m3u_files():
    """列出 M3U 文件"""
    files = []
    for f in sorted(config.M3U_DIR.glob("*.m3u")):
        try:
            content = f.read_text(encoding="utf-8")
            parsed = m3u_handler.parse_m3u(content)
            files.append({
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "mtime": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "channel_count": len(parsed["entries"]),
            })
        except Exception:
            pass
    # 同时也支持 m3u8 后缀
    for f in sorted(config.M3U_DIR.glob("*.m3u8")):
        try:
            content = f.read_text(encoding="utf-8")
            parsed = m3u_handler.parse_m3u(content)
            files.append({
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "mtime": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "channel_count": len(parsed["entries"]),
            })
        except Exception:
            pass
    return {"files": files}


@app.post("/api/m3u/upload")
async def upload_m3u(request: Request):
    """上传 M3U 文件"""
    body = await request.body()
    content = body.decode("utf-8", errors="replace")
    content = content.replace("\r\n", "\n")

    # 从 Content-Disposition 获取文件名
    filename = f"m3u_{int(time.time())}.m3u"
    content_type = request.headers.get("content-type", "")
    if "filename=" in request.headers.get("content-disposition", ""):
        cd = request.headers.get("content-disposition", "")
        m = re.search(r'filename="?([^"]*)"?', cd)
        if m:
            filename = m.group(1)
            # 解码 URL 编码的文件名（中文等）
            try:
                from urllib.parse import unquote
                filename = unquote(filename)
            except Exception:
                pass

    save_path = config.M3U_DIR / filename
    with open(save_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    return {"success": True, "filename": filename, "path": str(save_path)}


@app.get("/api/m3u/channel-options")
async def get_m3u_channel_options():
    """获取用于匹配的频道选项"""
    options = m3u_handler.get_channel_options()
    # 包含调试信息
    debug = {
        "xml_exists": config.XML_DIR.joinpath("epg.xml").exists(),
        "base_xml_exists": config.BASE_EPG_XML.exists(),
        "channel_count": len(config.get_channels()),
        "option_count": len(options),
    }
    return {"options": options, "debug": debug}

# ===================== 日志 API =====================


@app.get("/api/m3u/{filename}")
async def get_m3u_content(filename: str, merge: bool = Query(True)):
    """获取 M3U 文件内容"""
    path = config.M3U_DIR / filename
    if not path.exists():
        raise HTTPException(404, "文件不存在")

    content = path.read_text(encoding="utf-8")
    parsed = m3u_handler.parse_m3u(content, merge_duplicates=merge)
    return {"content": content, "parsed": parsed}


@app.post("/api/m3u/{filename}/save")
async def save_m3u_content(filename: str, data: dict = Body(...)):
    """保存 M3U 文件内容"""
    path = config.M3U_DIR / filename
    entries = data.get("entries", [])
    header = data.get("header", "")

    content = m3u_handler.rebuild_m3u(entries, header)
    path.write_text(content, encoding="utf-8")

    return {"success": True}


@app.delete("/api/m3u/{filename}")
async def delete_m3u(filename: str):
    path = config.M3U_DIR / filename
    if path.exists():
        path.unlink()
    return {"success": True}


@app.get("/api/m3u/{filename}/download")
async def download_m3u(filename: str):
    path = config.M3U_DIR / filename
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(path), filename=filename, media_type="audio/x-mpegurl")

@app.get("/api/logs")
async def get_log_files():
    return {"logs": logger.get_log_files()}


@app.get("/api/logs/global")
async def get_global_logs(category: str = "", limit: int = 200):
    """获取全局日志，可按分类筛选"""
    entries = logger.get_global(category=category, limit=limit)
    categories = logger.get_categories()
    return {"entries": entries, "categories": categories}


@app.get("/api/logs/{date}")
async def get_log_content(date: str):
    entries = logger.get_logs(date)
    return {"date": date, "entries": entries}


@app.get("/api/logs/{date}/download")
async def download_log(date: str):
    entries = logger.get_logs(date)
    content = "\n".join(entries)
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="download_{date}.log"'},
    )

# ===================== 列宽配置 API =====================

@app.get("/api/column-widths")
async def get_column_widths():
    """获取保存的列宽配置"""
    return config.get_column_widths()


@app.post("/api/column-widths")
async def save_column_widths(data: dict = Body(...)):
    """保存列宽配置"""
    config.save_column_widths(data)
    return {"success": True}


# ===================== 清理 API =====================

@app.post("/api/cleanup")
async def manual_cleanup():
    logs = []
    def log_msg(msg):
        logger.log(msg)
        logs.append(msg)
    downloader.cleanup_old_files(log_callback=log_msg)
    return {"success": True, "logs": logs}

# ===================== 共享目录 API =====================

@app.get("/api/share/files")
async def list_share_files(path: str = ""):
    """列出共享目录中的文件（支持子目录）"""
    settings = config.get_settings()
    share_path_str = settings.get("share_xml_path", "")
    if not share_path_str:
        return {"files": [], "error": "未设置共享目录路径"}
    
    share_dir = Path(share_path_str)
    if not share_dir.exists():
        return {"files": [], "error": f"目录不存在: {share_path_str}"}
    
    target_dir = share_dir
    if path:
        # 防止路径穿越
        target_dir = (share_dir / path).resolve()
        if not str(target_dir).startswith(str(share_dir.resolve())):
            return {"files": [], "error": "非法路径"}
    
    if not target_dir.exists():
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
    """上传文件到共享目录"""
    settings = config.get_settings()
    share_path_str = settings.get("share_xml_path", "")
    if not share_path_str:
        return {"success": False, "error": "未设置共享目录路径"}
    
    share_dir = Path(share_path_str)
    try:
        share_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"success": False, "error": f"无法创建目录: {share_path_str}"}
    
    filename = request.headers.get("X-Filename", "")
    if filename:
        import urllib.parse
        filename = urllib.parse.unquote(filename)
    else:
        filename = f"file_{int(time.time())}"
    
    body = await request.body()
    import base64
    try:
        body = base64.b64decode(body)
    except Exception:
        pass
    save_path = share_dir / filename
    
    # 如果已存在，添加时间戳
    if save_path.exists():
        name_stem = save_path.stem
        suffix = save_path.suffix
        save_path = share_dir / f"{name_stem}_{int(time.time())}{suffix}"
    
    try:
        with open(save_path, "wb") as f:
            f.write(body)
        return {"success": True, "filename": save_path.name, "path": str(save_path)}
    except OSError as e:
        return {"success": False, "error": str(e)}


@app.get("/api/share/file/{filename:path}")
async def get_share_file(filename: str):
    """获取共享目录中的文件（外网可访问）"""
    settings = config.get_settings()
    share_path_str = settings.get("share_xml_path", "")
    if share_path_str:
        file_path = Path(share_path_str) / filename
        if file_path.exists() and file_path.is_file():
            # 根据扩展名设置 Content-Type
            ext = Path(filename).suffix.lower()
            media_type_map = {
                ".apk": "application/vnd.android.package-archive",
                ".ipa": "application/octet-stream",
                ".m3u": "audio/x-mpegurl",
                ".m3u8": "application/x-mpegURL",
                ".xml": "application/xml",
                ".json": "application/json",
                ".txt": "text/plain",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".svg": "image/svg+xml",
                ".webp": "image/webp",
                ".ico": "image/x-icon",
            }
            media_type = media_type_map.get(ext, "application/octet-stream")
            return FileResponse(str(file_path), media_type=media_type, filename=Path(filename).name)
    raise HTTPException(404, "文件不存在")


@app.delete("/api/share/{filename:path}")
async def delete_share_file(filename: str):
    """删除共享目录中的文件"""
    settings = config.get_settings()
    share_path_str = settings.get("share_xml_path", "")
    if not share_path_str:
        return {"success": False, "error": "未设置共享目录路径"}
    
    file_path = Path(share_path_str) / filename
    if not file_path.exists():
        return {"success": False, "error": "文件不存在"}
    
    try:
        file_path.unlink()
        return {"success": True}
    except OSError as e:
        return {"success": False, "error": str(e)}


# ===================== ICON 管理 API =====================

@app.get("/api/icons")
async def list_icons():
    """列出所有图标文件"""
    files = []
    for f in sorted(config.ICONS_DIR.glob("*")):
        if f.is_file():
            ext = f.suffix.lower()
            if ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"]:
                try:
                    files.append({
                        "name": f.name,
                        "size": f.stat().st_size,
                        "mtime": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "url": f"/api/icons/file/{f.name}",
                    })
                except OSError:
                    pass
    return {"files": files}


@app.get("/api/icons/file/{filename}")
async def get_icon_file(filename: str):
    """获取图标文件（外网可访问）"""
    file_path = config.ICONS_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(file_path))


@app.post("/api/icons/upload")
async def upload_icon(request: Request):
    """上传图标文件"""
    filename = request.headers.get("X-Filename", "")
    import urllib.parse
    if filename:
        filename = urllib.parse.unquote(filename)
    else:
        filename = f"icon_{int(time.time())}"
    
    body = await request.body()
    import base64
    try:
        body = base64.b64decode(body)
    except Exception:
        pass
    save_path = config.ICONS_DIR / filename
    
    if save_path.exists():
        name_stem = save_path.stem
        suffix = save_path.suffix
        save_path = config.ICONS_DIR / f"{name_stem}_{int(time.time())}{suffix}"
    
    try:
        with open(save_path, "wb") as f:
            f.write(body)
        return {"success": True, "filename": save_path.name}
    except OSError as e:
        return {"success": False, "error": str(e)}


@app.delete("/api/icons/{filename}")
async def delete_icon(filename: str):
    """删除图标文件"""
    file_path = config.ICONS_DIR / filename
    if not file_path.exists():
        return {"success": False, "error": "文件不存在"}
    try:
        file_path.unlink()
        return {"success": True}
    except OSError as e:
        return {"success": False, "error": str(e)}


# ===================== 文件查看 API =====================

# ===================== 短路径 EPG URL 别名（兼容 IPTV 播放器） =====================

@app.api_route("/epg.xml", methods=["GET", "HEAD"])
async def short_epg_xml():
    """返回 gzip 压缩的 XML（兼容 IPTV 播放器）"""
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>', media_type="text/xml")
    xml_bytes = xml_path.read_bytes()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        gz.write(xml_bytes)
    gz_data = buf.getvalue()
    async def gz_iter():
        yield gz_data
    return StreamingResponse(gz_iter(), media_type="application/gzip", headers={
        "Content-Disposition": "attachment; filename='epg.xml.gz'",
        "Cache-Control": "public, max-age=300",
        "Connection": "keep-alive",
    })

@app.api_route("/epg.xml.gz", methods=["GET", "HEAD"])
async def short_epg_gz():
    """简洁路径的 gzip XML"""
    xml_path = config.XML_DIR / "epg.xml"
    if not xml_path.exists():
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><tv></tv>', media_type="text/xml")
    xml_bytes = xml_path.read_bytes()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        gz.write(xml_bytes)
    return Response(content=buf.getvalue(), media_type="application/gzip", headers={
        "Cache-Control": "public, max-age=300",
        "Access-Control-Allow-Origin": "*",
    })




# ===================== 代理服务（自动追重定向） =====================

@app.api_route("/proxy/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE"])
async def proxy_handler_route(path: str, request: Request):
    """代理服务：工作台自动追重定向，不返回 302 给客户端"""
    status, headers, body, media_type = await _proxy_handler.proxy_forward(request, path)

    if body is None:
        # 3xx（超出最大重定向次数时返回给客户端，极少出现）
        return Response(status_code=status, headers=headers)
    elif isinstance(body, str):
        # 错误信息
        return Response(status_code=status, content=body, media_type=media_type)
    else:
        # 流式返回
        return StreamingResponse(content=body, status_code=status, headers=headers, media_type=media_type)


@app.on_event("startup")
async def startup():
    scheduler.start()


@app.on_event("shutdown")
async def shutdown():
    scheduler.stop()
    await downloader.close_client()
