"""配置管理模块"""
import json
import os
import shutil
from pathlib import Path
from typing import Optional

# 数据目录（容器内用 /app/data，宿主机开发用 /tmp/iptv-data）
DATA_ROOT = Path(os.environ.get("IPTV_DATA_DIR", "/app/data"))
JSON_DIR = DATA_ROOT / "json"
XML_DIR = DATA_ROOT / "xml"
M3U_DIR = DATA_ROOT / "m3u"
ICONS_DIR = DATA_ROOT / "icons"
LOG_DIR = DATA_ROOT / "logs"
CONFIG_DIR = DATA_ROOT / "config"
SHARE_DIR = Path("/app/data/share")  # 可选共享目录

# 确保目录存在
for d in [JSON_DIR, XML_DIR, M3U_DIR, ICONS_DIR, LOG_DIR, CONFIG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 配置文件路径
CHANNELS_FILE = CONFIG_DIR / "channels.json"
EPG_SOURCES_FILE = CONFIG_DIR / "epg_sources.json"
EPG_EXTRACT_FILE = CONFIG_DIR / "epg_extract.json"
EPG_CHANNELS_FILE = CONFIG_DIR / "epg_channels.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
PORT_MAP_FILE = CONFIG_DIR / "port_map.json"
M3U_CHANNELS_FILE = CONFIG_DIR / "m3u_channels.json"
COLUMN_WIDTHS_FILE = CONFIG_DIR / "column_widths.json"
AUTH_FILE = CONFIG_DIR / "auth.json"
BASE_EPG_XML = CONFIG_DIR / "base_epg.xml"
MERGE_EPG_XML = CONFIG_DIR / "merge_epg.xml"

# 日志保留天数
LOG_RETENTION_DAYS = 7
# JSON 保留天数（按文件名日期判断，保留今天+过去6天=7天）
JSON_RETENTION_DAYS = 7
# 下载延时（秒）
DOWNLOAD_DELAY = 0.5
# 默认外部 EPG 源
DEFAULT_EPG_SOURCES = [
    {"url": "http://epg.51zmt.top:8000/e1.xml.gz", "rule": "auto", "enabled": True, "remark": "51zmt EPG"},
    {"url": "https://m3u.ibert.me/epg/112114_xyz.xml", "rule": "auto", "enabled": True, "remark": "112114 EPG"},
]

DEFAULT_SETTINGS = {
    "cron_time": "11:00",
    "cron_enabled": True,
    "proxy_port": 1234,
    "share_xml_path": "/app/data/share",
    "xml_gz_enabled": True,
}


def _load_json(path, default=None):
    if default is None:
        default = {}
    try:
        if path.exists() and path.stat().st_size > 0:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"加载 {path} 失败: {e}")
    return default


def _save_json(path, data):
    """保存 JSON，确保使用 LF 换行"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        content = content.replace("\r\n", "\n")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return True
    except OSError as e:
        print(f"保存 {path} 失败: {e}")
        return False


# -------- 频道管理 --------
def get_channels():
    data = _load_json(CHANNELS_FILE, {"channels": []})
    return data.get("channels", [])


def save_channels(channels):
    return _save_json(CHANNELS_FILE, {"channels": channels})


# -------- 外部 EPG 源 --------
def get_epg_sources():
    data = _load_json(EPG_SOURCES_FILE, {"sources": DEFAULT_EPG_SOURCES})
    sources = data.get("sources", [])
    if not sources:
        sources = DEFAULT_EPG_SOURCES
        save_epg_sources(sources)
    return sources


def save_epg_sources(sources):
    return _save_json(EPG_SOURCES_FILE, {"sources": sources})


# -------- EPG 提取记录 --------
def get_epg_extracts():
    data = _load_json(EPG_EXTRACT_FILE, {"extracts": []})
    return data.get("extracts", [])


def save_epg_extracts(extracts):
    return _save_json(EPG_EXTRACT_FILE, {"extracts": extracts})


# -------- 设置 --------
def get_settings():
    data = _load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    return merged


def save_settings(settings):
    merged = dict(DEFAULT_SETTINGS)
    merged.update(settings)
    return _save_json(SETTINGS_FILE, merged)


# -------- 端口映射 --------
def get_port_maps():
    data = _load_json(PORT_MAP_FILE, {"maps": []})
    return data.get("maps", [])


def save_port_maps(maps):
    return _save_json(PORT_MAP_FILE, {"maps": maps})


# -------- M3U 频道匹配 --------
def get_m3u_channels():
    data = _load_json(M3U_CHANNELS_FILE, {"channels": []})
    return data.get("channels", [])


def save_m3u_channels(channels):
    return _save_json(M3U_CHANNELS_FILE, {"channels": channels})


# -------- EPG 频道列表（用于生成 XML 和 M3U 匹配）--------
def get_epg_channels():
    data = _load_json(EPG_CHANNELS_FILE, {"channels": []})
    return data.get("channels", [])


def save_epg_channels(channels):
    return _save_json(EPG_CHANNELS_FILE, {"channels": channels})


# -------- 列宽配置 --------
def get_column_widths():
    return _load_json(COLUMN_WIDTHS_FILE, {})


def save_column_widths(widths: dict):
    return _save_json(COLUMN_WIDTHS_FILE, widths)


