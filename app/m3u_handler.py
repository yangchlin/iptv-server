"""M3U 文件处理模块"""
import re
from pathlib import Path
from typing import Optional

from . import config


def parse_m3u(content: str, merge_duplicates: bool = True) -> dict:
    """解析 M3U 内容为结构化数据，支持合并重复条目"""
    result = {
        "header": "",
        "entries": [],
        "raw_lines": [],
    }

    lines = content.split("\n")
    raw_entries = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            result["header"] = line
            continue
        if line.startswith("#EXTINF:"):
            entry = _parse_extinf(line)
            entry["_line_no"] = i
            raw_entries.append(entry)
        elif not line.startswith("#"):
            # 任何不以 # 开头的行都视为 URL（不限于标准协议，兼容测试数据）
            if raw_entries and not raw_entries[-1].get("url"):
                raw_entries[-1]["url"] = line

    # 合并重复条目（相同 tvg-id + tvg-name + display_name）
    if merge_duplicates:
        merged = {}
        for entry in raw_entries:
            key = (entry.get("tvg_id", ""), entry.get("tvg_name", ""), entry.get("display_name", ""))
            if key in merged:
                # 追加 URL
                existing = merged[key]
                urls = existing.get("_urls", [existing.get("url", "")])
                if entry.get("url") and entry["url"] not in urls:
                    urls.append(entry["url"])
                existing["_urls"] = urls
                existing["_dup_count"] = existing.get("_dup_count", 1) + 1
            else:
                entry["_urls"] = [entry.get("url", "")] if entry.get("url") else []
                entry["_dup_count"] = 1
                merged[key] = entry
        result["entries"] = list(merged.values())
    else:
        result["entries"] = raw_entries

    return result


def _parse_extinf(line: str) -> dict:
    """解析 #EXTINF 行"""
    entry = {
        "raw": line,
        "tvg_id": "",
        "tvg_name": "",
        "tvg_logo": "",
        "group_title": "",
        "display_name": "",
        "url": "",
        "_urls": [],
        "_dup_count": 1,
    }

    # 提取 tvg-id
    m = re.search(r'tvg-id="([^"]*)"', line)
    if m:
        entry["tvg_id"] = m.group(1)

    # 提取 tvg-name
    m = re.search(r'tvg-name="([^"]*)"', line)
    if m:
        entry["tvg_name"] = m.group(1)

    # 提取 tvg-logo
    m = re.search(r'tvg-logo="([^"]*)"', line)
    if m:
        entry["tvg_logo"] = m.group(1)

    # 提取 group-title
    m = re.search(r'group-title="([^"]*)"', line)
    if m:
        entry["group_title"] = m.group(1)

    # 提取显示名称（最后一个逗号后面）
    m = re.search(r',([^,]+)$', line)
    if m:
        entry["display_name"] = m.group(1).strip()

    return entry


def rebuild_m3u(entries: list, header: str = "") -> str:
    """根据条目重建 M3U 内容（自动展开合并的 URL）"""
    if not header:
        header = '#EXTM3U'

    lines = [header]
    for entry in entries:
        extinf = _build_extinf(entry)
        # 获取所有 URL
        _urls = entry.get("_urls")
        urls = _urls if _urls and len(_urls) > 0 else ([entry.get("url", "")] if entry.get("url") else [])
        if not urls:
            continue
        for url in urls:
            if url:
                lines.append(extinf)
                lines.append(url)

    return "\n".join(lines) + "\n"


def _build_extinf(entry: dict) -> str:
    """构建 EXTINF 行"""
    tvg_id = entry.get("tvg_id", "")
    tvg_name = entry.get("tvg_name", "")
    tvg_logo = entry.get("tvg_logo", "")
    group_title = entry.get("group_title", "")
    display_name = entry.get("display_name", entry.get("tvg_name", ""))

    parts = [f'#EXTINF:-1']
    if tvg_id:
        parts.append(f'tvg-id="{tvg_id}"')
    if tvg_name:
        parts.append(f'tvg-name="{tvg_name}"')
    if tvg_logo:
        parts.append(f'tvg-logo="{tvg_logo}"')
    if group_title:
        parts.append(f'group-title="{group_title}"')
    parts.append(f',{display_name}')

    return " ".join(parts)


def get_channel_options() -> list:
    """仅从 EPG 管理列表获取频道选项"""
    options = []
    seen = set()

    # 1. 占位频道 9999
    options.append({"tvg_id": "9999", "tvg_name": "暂无节目单"})
    seen.add("暂无节目单")

    # 2. 仅从 EPG 管理列表读取
    try:
        epg_channels = config.get_epg_channels()
        print(f"[频道选项] EPG管理列表: {len(epg_channels)} 条")
        for ch in epg_channels:
            name = ch.get("name", "")
            tvg_id = ch.get("tvg_id", "")
            epg_tvg_name = ch.get("tvg_name", "")
            enabled = ch.get("enabled", True)
            if name and name not in seen:
                options.append({"tvg_id": tvg_id, "tvg_name": name, "epg_tvg_name": epg_tvg_name or name})
                seen.add(name)
    except Exception as e:
        print(f"[频道选项] 读取EPG管理列表失败: {e}")

    print(f"[频道选项] 共返回 {len(options)} 个频道选项")
    return options
