"""配置管理模块

设计要点：
- 所有 JSON 读写均使用 UTF-8 无 BOM + LF 换行。
- 写入采用"临时文件 + os.replace"原子替换，避免进程崩溃留下半成品。
- 读取失败（文件损坏/权限异常）时不覆盖原文件，仅本次返回默认值。
- 默认值统一用 copy.deepcopy 拷贝，避免调用方修改污染全局常量。
- 提供全项目统一的 safe_filename()，保证 JSON 文件名读写一致。
"""
import copy
import datetime as _dt
import json
import os
import re as _re
import uuid
from pathlib import Path
from typing import Any, Optional

# ── 数据目录（容器内 /app/data；本地开发用 IPTV_DATA_DIR=/tmp/iptv-data）──
DATA_ROOT = Path(os.environ.get("IPTV_DATA_DIR", "/app/data"))

JSON_DIR = DATA_ROOT / "json"
XML_DIR = DATA_ROOT / "xml"
M3U_DIR = DATA_ROOT / "m3u"
ICONS_DIR = DATA_ROOT / "icons"
LOG_DIR = DATA_ROOT / "logs"
CONFIG_DIR = DATA_ROOT / "config"
# 外部工具（Windows 播放器 IPTV.exe 等）：挂在宿主机 ./data/tools，
# 换版本直接丢文件，不用重建镜像；镜像里 static/tools 只作为兜底。
TOOLS_DIR = DATA_ROOT / "tools"

# 说明：SHARE_DIR 是早期硬编码路径，仅作为 XML 生成时的备份共享位置；
# 前端"共享目录"功能实际使用 settings.share_xml_path，两者互不影响。
SHARE_DIR = Path("/app/data/share")

# 确保所有工作目录存在
for _d in (JSON_DIR, XML_DIR, M3U_DIR, ICONS_DIR, LOG_DIR, CONFIG_DIR, TOOLS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── 配置文件路径 ──
# ★ 所有"配置"合并到一个文件：电信配置 + 定时任务 + 全局设置 + 外部 EPG 源
APP_CONFIG_FILE = CONFIG_DIR / "app_config.json"
# 旧路径仅用于一次性迁移（迁移后改名为 .json.bak）
LEGACY_SETTINGS_FILE = CONFIG_DIR / "settings.json"
LEGACY_EPG_SOURCES_FILE = CONFIG_DIR / "epg_sources.json"
CHANNELS_FILE = CONFIG_DIR / "channels.json"
EPG_EXTRACT_FILE = CONFIG_DIR / "epg_extract.json"
EPG_CHANNELS_FILE = CONFIG_DIR / "epg_channels.json"
COLUMN_WIDTHS_FILE = CONFIG_DIR / "column_widths.json"
COLUMN_ORDER_FILE = CONFIG_DIR / "column_order.json"
AUTH_FILE = CONFIG_DIR / "auth.json"
BASE_EPG_XML = CONFIG_DIR / "base_epg.xml"        # 状态接口里展示是否存在
TELECOM_CHANNELS_FILE = CONFIG_DIR / "telecom_channels.json"
FCC_PRESETS_FILE = CONFIG_DIR / "fcc_presets.json"
TELECOM_EPG_SUFFIX = "_telecom"
GROUP_RULES_FILE = CONFIG_DIR / "group_rules.json"

# ── 保留策略 ──
LOG_RETENTION_DAYS = 7
JSON_RETENTION_DAYS = 7
DOWNLOAD_DELAY = 0.5

# ── 外部 EPG 源 ──
# ★ 脚本里不再内置任何默认源：源列表只存在于配置文件 app_config.json 的 epg_sources 字段
#   （首次启动为空，请在页面「➕ 添加源」或直接编辑配置文件填写）

# ── 默认设置 ──
DEFAULT_SETTINGS = {
    "cron_time": "11:00",
    "cron_enabled": True,
    "proxy_port": 1234,          # 预留：当前版本不监听该端口
    "share_xml_path": "/app/data/share",
    "xml_gz_enabled": True,
    "telecom": {
        "enabled": False,
        "AuthenticationIP": "http://182.138.3.142:8082",
        "key": "",
        "UserID": "",
        "mac": "",
        "STBID": "",
        "ip": "",
        "STBType": "",
        "STBVersion": "",
        "UserAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Authenticator": "",
        "auth_require_key": False,     # True = 必须用 key 现场加密出 Authenticator，没有 key 就拒绝认证
        "RTP2HTTPD": "",
        "templateName": "",
        "areaId": "",
        "epg_server_url": "",
        "epgname": "epg.xml",
        "logo_url": "",
        "merge_output_files": True,
    },
    "telecom_channels_cron_enabled": False,
    "telecom_channels_cron_mode": "daily",
    "telecom_channels_cron_time": "02:00",
    "telecom_channels_cron_interval": 3,
    "telecom_channels_last_run": "",
    "telecom_epg_cron_enabled": False,
    "telecom_epg_cron_mode": "daily",
    "telecom_epg_cron_time": "03:00",
    "telecom_epg_cron_interval": 3,
    "telecom_epg_last_run": "",
    "telecom_epg_auto_fetch_channels": True,
    # 拉完电信频道后，自动把频道并入「EPG 管理」，让电信节目进入主 epg.xml
    "telecom_auto_add_to_epg": True,
}

# ── 默认 FCC 预设 ──
_DEFAULT_FCC_PRESETS = {
    "enabled": False,
    "active": "中国电信",
    "presets": [
        {"name": "中国电信", "server": ""},
        {"name": "中国联通", "server": ""},
        {"name": "中国移动", "server": ""},
    ],
    "rtp_proxy_enabled": False,
    "rtp_proxy": "",
}

# ── 默认分组规则 ──
_DEFAULT_GROUP_RULES = {
    "version": 1,
    "rules": [],
    "default_group": "",
    "auto_apply": False,
}


# ════════════════════════════════════════════════════════════════
# 文件名安全化（全项目统一入口）
# ════════════════════════════════════════════════════════════════

_INVALID_FS = _re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, max_len: int = 80) -> str:
    """统一的文件名安全化。

    规则：
      1. 非法字符（\\ / : * ? " < > | 及控制字符）→ 下划线
      2. 去首尾点与空格（避免 Windows 保留名与尾部点）
      3. 长度上限（默认 80）
      4. 空则返回 "channel"

    所有 JSON 文件名的写入与读取必须走这里，保证一致。
    """
    s = _INVALID_FS.sub("_", str(name or ""))
    s = s.strip(". ").strip()
    return s[:max_len] or "channel"


# ════════════════════════════════════════════════════════════════
# JSON 读写工具
# ════════════════════════════════════════════════════════════════

def _load_json(path: Path, default: Any = None) -> Any:
    """读取 JSON。

    文件不存在 → 返回 default。
    文件损坏或权限错误 → 打印警告，仍返回 default；不覆盖原文件。
    """
    if default is None:
        default = {}
    try:
        if path.exists() and path.stat().st_size > 0:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[config] 加载 {path.name} 失败: {e}（保留原文件，使用默认值）")
    return copy.deepcopy(default)


def _load_app() -> dict:
    """读统一配置；文件不存在时先跑一次迁移（兜底，防止迁移失败导致配置丢失）。"""
    data = _load_json(APP_CONFIG_FILE, None)
    if not isinstance(data, dict):
        try:
            migrate_to_app_config()
        except Exception as e:
            print(f"[config] 迁移配置失败: {e}")
        data = _load_json(APP_CONFIG_FILE, {})
    return data if isinstance(data, dict) else {}


def _save_json(path: Path, data: Any) -> bool:
    """原子写入 JSON：先写同目录 .tmp 文件，再 os.replace 原子替换。"""
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2).replace("\r\n", "\n")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError) as e:
        print(f"[config] 保存 {path.name} 失败: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def get_channels() -> list:
    data = _load_json(CHANNELS_FILE, {"channels": []})
    channels = data.get("channels", []) if isinstance(data, dict) else []
    return channels if isinstance(channels, list) else []


def save_channels(channels: Any) -> bool:
    if not isinstance(channels, list):
        return False
    return _save_json(CHANNELS_FILE, {"channels": channels})


# ════════════════════════════════════════════════════════════════
# 外部 EPG 源
# ════════════════════════════════════════════════════════════════

def get_epg_sources() -> list:
    """外部 EPG 源：完全来自配置文件 app_config.json 的 epg_sources 字段。

    ★ 脚本里没有任何默认源；文件里没写就是空列表。
    """
    data = _load_app()
    sources = data.get("epg_sources") if isinstance(data, dict) else None
    if not isinstance(sources, list):
        return []
    return sources


def save_epg_sources(sources: Any) -> bool:
    if not isinstance(sources, list):
        return False
    data = _load_app()
    if not isinstance(data, dict):
        data = copy.deepcopy(DEFAULT_SETTINGS)
    data["epg_sources"] = sources
    data.setdefault("config_version", 2)
    return _save_json(APP_CONFIG_FILE, data)


# ════════════════════════════════════════════════════════════════
# EPG 提取记录
# ════════════════════════════════════════════════════════════════

def get_epg_extracts() -> list:
    data = _load_json(EPG_EXTRACT_FILE, {"extracts": []})
    extracts = data.get("extracts", []) if isinstance(data, dict) else []
    return extracts if isinstance(extracts, list) else []


def save_epg_extracts(extracts: Any) -> bool:
    if not isinstance(extracts, list):
        return False
    return _save_json(EPG_EXTRACT_FILE, {"extracts": extracts})


# ════════════════════════════════════════════════════════════════
# 设置（telecom 子字典深合并）
# ════════════════════════════════════════════════════════════════

def _deep_merge_telecom(base: dict, override: Any) -> dict:
    if not isinstance(override, dict):
        return copy.deepcopy(base)
    merged = copy.deepcopy(base)
    for k, v in override.items():
        merged[k] = v
    return merged


def get_settings() -> dict:
    """读取配置（app_config.json）。深拷贝默认值，避免污染 DEFAULT_SETTINGS。"""
    data = _load_app()
    if not isinstance(data, dict):
        data = {}

    merged = copy.deepcopy(DEFAULT_SETTINGS)
    for k, v in data.items():
        if k in ("epg_sources", "config_version"):
            continue
        if k == "telecom":
            merged["telecom"] = _deep_merge_telecom(merged["telecom"], v)
        else:
            merged[k] = v
    return merged


def save_settings(settings: Any) -> bool:
    if not isinstance(settings, dict):
        return False

    # ★ 以「当前配置」为底做合并，避免只保存一个开关就把其它配置（电信账号等）冲回默认值
    current = _load_app()
    if not isinstance(current, dict):
        current = {}

    merged = copy.deepcopy(DEFAULT_SETTINGS)
    for k, v in current.items():
        if k in ("epg_sources", "config_version"):
            continue
        if k == "telecom":
            merged["telecom"] = _deep_merge_telecom(merged["telecom"], v)
        else:
            merged[k] = v

    for k, v in settings.items():
        if k in ("epg_sources", "config_version"):
            continue
        if k == "telecom":
            merged["telecom"] = _deep_merge_telecom(merged["telecom"], v)
        else:
            merged[k] = v

    # 保留同一文件里的外部 EPG 源（脚本不提供默认值，没有就是空列表）
    if isinstance(current.get("epg_sources"), list):
        merged["epg_sources"] = current["epg_sources"]
    else:
        merged["epg_sources"] = []
    merged["config_version"] = 2
    return _save_json(APP_CONFIG_FILE, merged)


# ════════════════════════════════════════════════════════════════
# EPG 频道管理
# ════════════════════════════════════════════════════════════════

def get_epg_channels() -> list:
    data = _load_json(EPG_CHANNELS_FILE, {"channels": []})
    channels = data.get("channels", []) if isinstance(data, dict) else []
    return channels if isinstance(channels, list) else []


def save_epg_channels(channels: Any) -> bool:
    if not isinstance(channels, list):
        return False
    return _save_json(EPG_CHANNELS_FILE, {"channels": channels})


# ════════════════════════════════════════════════════════════════
# 列宽配置
# ════════════════════════════════════════════════════════════════

def get_column_widths() -> dict:
    data = _load_json(COLUMN_WIDTHS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_column_widths(widths: Any) -> bool:
    if not isinstance(widths, dict):
        return False
    return _save_json(COLUMN_WIDTHS_FILE, widths)


# ════════════════════════════════════════════════════════════════
# 列顺序（拖动表头调整列的前后位置）
#   结构：{ "表名": ["频道", "TVG-NAME", ...] }
# ════════════════════════════════════════════════════════════════

def get_column_order() -> dict:
    data = _load_json(COLUMN_ORDER_FILE, {})
    return data if isinstance(data, dict) else {}


def save_column_order(order: Any) -> bool:
    if not isinstance(order, dict):
        return False
    clean = {}
    for k, v in order.items():
        if not isinstance(v, list) or not v or len(v) > 60:
            continue
        clean[str(k)[:120]] = [str(x)[:60] for x in v][:60]
    return _save_json(COLUMN_ORDER_FILE, clean)


# ════════════════════════════════════════════════════════════════
# 电信频道缓存
# ════════════════════════════════════════════════════════════════

def get_telecom_channels() -> dict:
    data = _load_json(TELECOM_CHANNELS_FILE, {"channels": [], "updated": ""})
    if not isinstance(data, dict):
        return {"channels": [], "updated": ""}
    channels = data.get("channels", [])
    if not isinstance(channels, list):
        channels = []
    return {"channels": channels, "updated": data.get("updated", "") or ""}


def save_telecom_channels(channels: list, updated: Optional[str] = None) -> bool:
    if not isinstance(channels, list):
        return False
    return _save_json(TELECOM_CHANNELS_FILE, {
        "channels": channels,
        "updated": updated or _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


def merge_telecom_channels_into_epg(telecom_channels: list) -> dict:
    """把电信频道批量并入「EPG 管理」列表（按频道名去重，已存在则跳过）。

    这样电信节目才能进入主 epg.xml —— xml_builder 只输出 epg_channels 里的频道，
    而电信数据以 `<频道名>_telecom_<日期>.json` 存放，频道名对上就能被优先取用。

    规则：
    - 只新增，不删除、不改动已有频道的排序和配置（幂等，重复执行不会重复添加）
    - tvg_id 用频道名（与 telecom.m3u 的 tvg-id 一致），重名冲突时自动加 `_2` 后缀
    - 标记 source_type=telecom，便于在「EPG 管理」里辨认

    返回 {"added", "skipped", "total"}（失败时多一个 "error"）
    """
    result = {"added": 0, "skipped": 0, "total": 0}
    if not isinstance(telecom_channels, list):
        return result

    epg_channels = get_epg_channels()
    names = {str(c.get("name", "")).strip() for c in epg_channels}
    tvg_ids = {str(c.get("tvg_id", "")).strip() for c in epg_channels}
    changed = False

    for ch in telecom_channels:
        name = str(ch.get("name", "") or "").strip()
        if not name:
            continue
        if name in names:
            result["skipped"] += 1
            continue

        tvg_id = str(ch.get("tvg_id", "") or name).strip()
        suffix = 2
        # 如果 tvg_id 冲突，就在英文后面加后缀
        suffix = 2
        base_tvg_id = tvg_id
        while tvg_id in tvg_ids:
            tvg_id = f"{base_tvg_id}_{suffix}"
            suffix += 1

        epg_channels.append({
            "name": name,
            "tvg_id": tvg_id,
            "tvg_name": name,
            "logic_id": str(uuid.uuid4()),
            "enabled": True,
            "sort_order": len(epg_channels),
            "source_type": "telecom",
            "source_remark": "自动来自电信频道列表",
        })
        names.add(name)
        tvg_ids.add(tvg_id)
        result["added"] += 1
        changed = True

    if changed and not save_epg_channels(epg_channels):
        result["error"] = "保存 epg_channels.json 失败"
    result["total"] = len(get_epg_channels())
    return result


# ════════════════════════════════════════════════════════════════
# FCC + RTP 预设
# ════════════════════════════════════════════════════════════════

def get_fcc_presets() -> dict:
    defaults = copy.deepcopy(_DEFAULT_FCC_PRESETS)
    data = _load_json(FCC_PRESETS_FILE, defaults)
    if not isinstance(data, dict):
        return defaults
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
    return data


def save_fcc_presets(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    return _save_json(FCC_PRESETS_FILE, data)


# ════════════════════════════════════════════════════════════════
# 频道分组规则
# ════════════════════════════════════════════════════════════════

def get_group_rules() -> dict:
    defaults = copy.deepcopy(_DEFAULT_GROUP_RULES)
    data = _load_json(GROUP_RULES_FILE, defaults)
    if not isinstance(data, dict):
        return defaults
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
    if not isinstance(data.get("rules"), list):
        data["rules"] = []
    return data


def save_group_rules(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    return _save_json(GROUP_RULES_FILE, data)


# ════════════════════════════════════════════════════════════════
# 一次性迁移：settings.json + epg_sources.json → app_config.json
# （幂等；旧文件改名 .json.bak 保留，出问题可手工找回）
# ════════════════════════════════════════════════════════════════

def migrate_to_app_config() -> dict:
    """把旧配置合并进 app_config.json。返回 {"migrated": bool, "path": str}。"""
    try:
        if APP_CONFIG_FILE.exists() and APP_CONFIG_FILE.stat().st_size > 0:
            return {"migrated": False, "path": str(APP_CONFIG_FILE)}
    except OSError:
        pass

    old_settings = _load_json(LEGACY_SETTINGS_FILE, {})
    if not isinstance(old_settings, dict):
        old_settings = {}

    old_src = _load_json(LEGACY_EPG_SOURCES_FILE, {})
    sources = old_src.get("sources") if isinstance(old_src, dict) else None
    if not isinstance(sources, list):
        sources = []

    merged = copy.deepcopy(DEFAULT_SETTINGS)
    for k, v in old_settings.items():
        if k in ("epg_sources", "config_version"):
            continue
        if k == "telecom":
            merged["telecom"] = _deep_merge_telecom(merged["telecom"], v)
        else:
            merged[k] = v
    merged["config_version"] = 2
    merged["epg_sources"] = sources

    ok = _save_json(APP_CONFIG_FILE, merged)
    if ok:
        for p in (LEGACY_SETTINGS_FILE, LEGACY_EPG_SOURCES_FILE):
            try:
                if p.exists():
                    p.rename(p.with_name(p.name + ".bak"))
            except OSError:
                pass
    return {"migrated": ok, "path": str(APP_CONFIG_FILE)}


# ════════════════════════════════════════════════════════════════
# 台标直链
# ════════════════════════════════════════════════════════════════

def logo_link(logo_base: str, epg_base: str, channel_name: str) -> str:
    """生成某个频道的台标直链。

    - logo_url 和本站 EPG 地址同源（或 logo_url 留空）→ 走 `/api/download/<文件名>`，
      与「频道管理」里选台标得到的链接完全一致；
    - logo_url 指向**外部台标服务器**（例如 http://192.168.6.1:3000）→ 沿用
      `/logo/<频道名>.png`，那类服务器只认这个路径。
    """
    from urllib.parse import quote
    base = (logo_base or epg_base or "").rstrip("/")
    if not base:
        return ""
    fname = quote(str(channel_name or "") + ".png", safe="")
    same_site = (not logo_base) or (logo_base.rstrip("/") == (epg_base or "").rstrip("/"))
    return f"{base}/api/download/{fname}" if same_site else f"{base}/logo/{fname}"


# 模块加载时执行一次（首次启动/升级后自动生成）
migrate_to_app_config()