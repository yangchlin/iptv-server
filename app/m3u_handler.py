"""M3U 文件处理模块

要点：
- _build_extinf 对所有属性值转义，防止频道名中的引号/换行破坏 M3U 结构
- 保留 merge_files 的全部去重 / RTP / FCC / 分组逻辑
"""
import html
import re

from . import config


# ════════════════════════════════════════════════════════════════
# 字段转义
# ════════════════════════════════════════════════════════════════

def _sanitize_field(s) -> str:
    """M3U 属性值转义：
    - 双引号 → 单引号（属性用双引号包围，内部不能出现双引号）
    - 换行 / 回车 → 空格
    - 首尾去空白
    """
    return (str(s or "")
            .replace('"', "'")
            .replace("\r", " ")
            .replace("\n", " ")
            .strip())


# ════════════════════════════════════════════════════════════════
# M3U 解析
# ════════════════════════════════════════════════════════════════

def parse_m3u(content: str, merge_duplicates: bool = True) -> dict:
    """解析 M3U 内容为结构化数据，支持合并重复条目。"""
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
            if raw_entries and not raw_entries[-1].get("url"):
                raw_entries[-1]["url"] = line

    if merge_duplicates:
        merged = {}
        for entry in raw_entries:
            key = (entry.get("tvg_id", ""), entry.get("tvg_name", ""), entry.get("display_name", ""))
            if key in merged:
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
    """解析 #EXTINF 行。"""
    entry = {
        "raw": line,
        "tvg_id": "",
        "tvg_name": "",
        "tvg_logo": "",
        "group_title": "",
        "display_name": "",
        "catchup": "",
        "catchup_source": "",
        "url": "",
        "_urls": [],
        "_dup_count": 1,
    }

    for attr, key in (("tvg-id", "tvg_id"),
                      ("tvg-name", "tvg_name"),
                      ("tvg-logo", "tvg_logo"),
                      ("group-title", "group_title"),
                      ("catchup-source", "catchup_source"),
                      ("catchup", "catchup")):
        m = re.search(rf'{attr}="([^"]*)"', line)
        if m:
            entry[key] = m.group(1)

    # 显示名称：最后一个逗号后
    m = re.search(r',([^,]+)$', line)
    if m:
        entry["display_name"] = m.group(1).strip()

    return entry


# ════════════════════════════════════════════════════════════════
# 重建 M3U
# ════════════════════════════════════════════════════════════════

def rebuild_m3u(entries: list, header: str = "") -> str:
    """根据条目重建 M3U 内容（自动展开合并的 URL）。"""
    if not header:
        header = "#EXTM3U"

    lines = [header]
    for entry in entries:
        extinf = _build_extinf(entry)
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
    """构建 EXTINF 行（所有字段转义，避免引号/换行注入）。"""
    tvg_id = _sanitize_field(entry.get("tvg_id", ""))
    tvg_name = _sanitize_field(entry.get("tvg_name", ""))
    tvg_logo = _sanitize_field(entry.get("tvg_logo", ""))
    group_title = _sanitize_field(entry.get("group_title", ""))
    display_name = _sanitize_field(entry.get("display_name", entry.get("tvg_name", "")))
    catchup = _sanitize_field(entry.get("catchup_source", ""))

    parts = ["#EXTINF:-1"]
    if tvg_id:
        parts.append(f'tvg-id="{tvg_id}"')
    if tvg_name:
        parts.append(f'tvg-name="{tvg_name}"')
    if tvg_logo:
        parts.append(f'tvg-logo="{tvg_logo}"')
    if group_title:
        parts.append(f'group-title="{group_title}"')
    if catchup:
        parts.append(f'catchup="default" catchup-source="{catchup}"')
    parts.append(f",{display_name}")

    return " ".join(parts)


# ════════════════════════════════════════════════════════════════
# 频道选项
# ════════════════════════════════════════════════════════════════

_CHANNEL_RE = re.compile(
    r'<channel\s+id="([^"]*)"\s*>\s*<display-name[^>]*>(.*?)</display-name>', re.S
)


def get_channel_options() -> list:
    """「匹配节目单」下拉选项 = 合并后真正输出到 epg.xml 的频道。

    优先读 epg.xml（电信 EPG + 外部 EPG 合并后的实际结果），
    再补上 EPG 管理里还没生成进 XML 的频道（刚添加、还没重建 XML 的情况）。
    """
    options = []
    seen = set()

    def add(tvg_id, tvg_name, epg_tvg_name=None):
        name = str(tvg_name or "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        options.append({
            "tvg_id": str(tvg_id or ""),
            "tvg_name": name,
            "epg_tvg_name": str(epg_tvg_name or name),
        })

    # 占位频道 9999
    options.append({"tvg_id": "9999", "tvg_name": "暂无节目单"})
    seen.add("暂无节目单")

    # 1) 先读真正生成出来的 epg.xml（电信 + 外部合并后的结果）
    xml_path = config.XML_DIR / "epg.xml"
    if xml_path.exists():
        try:
            text = xml_path.read_text(encoding="utf-8", errors="replace")
            for m in _CHANNEL_RE.finditer(text):
                add(html.unescape(m.group(1)), html.unescape(m.group(2)))
        except (OSError, ValueError) as e:
            print(f"[频道选项] 读取 epg.xml 失败: {e}")

    # 2) 再补 EPG 管理里还没进 XML 的频道
    try:
        for ch in config.get_epg_channels():
            if ch.get("enabled", True) is False:
                continue
            name = ch.get("name", "")
            add(ch.get("tvg_id", "") or name, name, ch.get("tvg_name", "") or name)
    except Exception as e:
        print(f"[频道选项] 读取EPG管理列表失败: {e}")

    return options


# ════════════════════════════════════════════════════════════════
# TXT 解析（每行：频道名,URL[,分组]）
# ════════════════════════════════════════════════════════════════

def parse_txt(content: str) -> dict:
    """解析 TXT 格式（每行：频道名,URL[,分组]），兼容 sctv.txt 的时移#直播。"""
    result = {"header": "", "entries": [], "raw_lines": []}
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",", 2)]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        name, url_part = parts[0], parts[1]
        group = parts[2] if len(parts) >= 3 else ""

        catchup = ""
        if "#" in url_part:
            catchup, url_part = url_part.split("#", 1)

        entry = {
            "display_name": name,
            "tvg_name": name,
            "tvg_id": "",
            "tvg_logo": "",
            "group_title": group,
            "url": url_part,
            "_urls": [url_part],
            "_dup_count": 1,
        }
        if catchup:
            entry["catchup_source"] = catchup
        result["entries"].append(entry)
    return result


# ════════════════════════════════════════════════════════════════
# 合并（完整保留原逻辑）
# ════════════════════════════════════════════════════════════════

_IP_PORT_RE = re.compile(r'(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)(?![\d])')


def _extract_mcast(url: str) -> str:
    """从 URL 中提取组播地址（仅 224-239 段）。"""
    if not url:
        return ""
    for m in _IP_PORT_RE.finditer(url):
        ip_port = m.group(1)
        try:
            if 224 <= int(ip_port.split(".")[0]) <= 239:
                return ip_port
        except ValueError:
            continue
    return ""


def _url_richness(url: str) -> int:
    """URL 信息量评分，分数越高越优先保留。"""
    score = 0
    if url.startswith(("http://", "https://")):
        score += 5
    if "?" in url:
        for p in url.split("?", 1)[1].split("&"):
            k = p.split("=", 1)[0].strip().lower()
            if k in ("fcc", "fec"):
                score += 10
            elif k == "playseek":
                score += 5
            elif k:
                score += 1
    return score


def _apply_rtp_proxy(url: str, proxy: str) -> str:
    """rtp://组播 → http://代理/rtp/组播。"""
    if not url or not proxy or not url.startswith("rtp://"):
        return url
    p = proxy.strip()
    for prefix in ("http://", "https://"):
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    p = p.rstrip("/")
    if not p:
        return url
    return f"http://{p}/rtp/{url[6:]}"


def _apply_fcc_to_url(url: str, fcc_server: str) -> str:
    """给含组播的 http 地址补 fcc 参数（已有 fcc 不覆盖）。"""
    if not url or not fcc_server:
        return url
    if not url.startswith(("http://", "https://")):
        return url
    if not _extract_mcast(url):
        return url
    if re.search(r'[?&]fcc=', url, re.IGNORECASE):
        return url
    return f"{url}{'&' if '?' in url else '?'}fcc={fcc_server}"


def merge_files(file_contents: list, merge_mode: str = "url",
                groups: dict = None, header: str = "",
                fcc_config: dict = None, rtp_proxy: str = "",
                group_rules: dict = None, keep_order: bool = False) -> str:
    """合并多个 M3U/TXT 文件。

    keep_order=True 时：保持文件顺序（第一个文件为主，其它文件的新频道追加到末尾），
    不做按分组/名称重排。适合「以电信为主 + 其它文件往下加」。

    处理顺序：
      ① 组播地址聚合（同名同组播 → 留参数最全的）
      ② 名字去重（URL 相同但名字不同 → 留先出现的）
      ③ 模式去重（url / channel）
      ④ RTP 代理转换（rtp:// → http://代理/rtp/）
      ⑤ FCC 参数补全（给含组播的 http 加 fcc）
      ⑥ 分组映射 + 分组规则
    """
    # ── 步骤 0：解析所有文件 ──
    all_entries = []
    for f in file_contents:
        name = (f.get("name") or "").lower()
        content = f.get("content", "")
        if name.endswith((".m3u", ".m3u8")):
            parsed = parse_m3u(content, merge_duplicates=False)
        elif name.endswith(".txt"):
            parsed = parse_txt(content)
        else:
            if "#EXTINF" in content or content.strip().startswith("#EXTM3U"):
                parsed = parse_m3u(content, merge_duplicates=False)
            else:
                parsed = parse_txt(content)
        all_entries.extend(parsed["entries"])

    # 记录原始顺序（keep_order 用）
    for _i, _e in enumerate(all_entries):
        _e["_order"] = _i

    # ── 步骤 ①：组播地址聚合 ──
    mcast_groups = {}
    passthrough = []
    for entry in all_entries:
        name = (entry.get("display_name") or entry.get("tvg_name") or "").strip()
        url = (entry.get("url") or "").strip()
        mcast = _extract_mcast(url)
        if name and mcast:
            mcast_groups.setdefault((name, mcast), []).append(entry)
        else:
            passthrough.append(entry)

    aggregated = []
    for (name, mcast), entries in mcast_groups.items():
        if len(entries) == 1:
            aggregated.append(entries[0])
            continue
        best = max(entries, key=lambda e: (_url_richness(e.get("url", "")),
                                            len(e.get("url", ""))))
        me = dict(best)
        me["_urls"] = [best.get("url", "")]
        me["_dup_count"] = len(entries)
        me["_mcast_merged"] = len(entries)
        for other in entries:
            if other is best:
                continue
            for k in ("tvg_logo", "tvg_id", "group_title", "catchup_source"):
                if not me.get(k) and other.get(k):
                    me[k] = other[k]
        aggregated.append(me)
    all_entries = aggregated + passthrough
    if keep_order:
        # 恢复「文件顺序」，让去重与输出都按原顺序来
        all_entries.sort(key=lambda e: e.get("_order", 0))

    # ── 步骤 ②+③：名字去重 + 模式去重 ──
    merged = {}
    url_seen = {}
    for entry in all_entries:
        name = (entry.get("display_name") or entry.get("tvg_name") or "").strip()
        url = (entry.get("url") or "").strip()
        if not name or not url:
            continue

        key = (name, url) if merge_mode == "url" else name

        if key not in merged:
            # URL 已被别的名字占用 → 跳过并补字段
            if url in url_seen and url_seen[url] != name:
                owner_name = url_seen[url]
                owner_key = (owner_name, url) if merge_mode == "url" else owner_name
                if owner_key in merged:
                    owner = merged[owner_key]
                    for k in ("tvg_logo", "tvg_id", "group_title", "catchup_source"):
                        if not owner.get(k) and entry.get(k):
                            owner[k] = entry[k]
                continue

            e = dict(entry)
            e["_urls"] = [url]
            e["_dup_count"] = 1
            merged[key] = e
            url_seen[url] = name
        else:
            existing = merged[key]
            if url not in existing["_urls"]:
                existing["_urls"].append(url)
                url_seen[url] = name
            existing["_dup_count"] += 1
            for k in ("tvg_logo", "tvg_id", "group_title", "catchup_source"):
                if not existing.get(k) and entry.get(k):
                    existing[k] = entry[k]
    entries = list(merged.values())

    # ── 分组映射 ──
    if groups:
        ch_to_group = {}
        for gname, chs in groups.items():
            gname = (gname or "").strip()
            if not gname:
                continue
            for ch in (chs or []):
                ch = str(ch).strip()
                if ch and ch not in ch_to_group:
                    ch_to_group[ch] = gname
        for e in entries:
            name = (e.get("display_name") or e.get("tvg_name") or "").strip()
            if name in ch_to_group:
                e["group_title"] = ch_to_group[name]

    # ── 分组规则 ──
    if group_rules and group_rules.get("rules"):
        rules = sorted(
            [r for r in group_rules["rules"] if r.get("enabled", True)],
            key=lambda r: r.get("priority", 999),
        )
        default_g = (group_rules.get("default_group") or "").strip()

        for e in entries:
            name = (e.get("display_name") or e.get("tvg_name") or "").strip()
            if not name:
                continue
            if groups and e.get("group_title"):
                continue

            matched = False
            for rule in rules:
                gname = (rule.get("name") or "").strip()
                if not gname:
                    continue
                mt = rule.get("match_type", "keyword")
                if mt == "keyword":
                    kws = [k.strip() for k in (rule.get("keywords") or []) if k and k.strip()]
                    if any(kw in name for kw in kws):
                        e["group_title"] = gname
                        matched = True
                        break
                elif mt == "channels":
                    chs = [c.strip() for c in (rule.get("channels") or []) if c and c.strip()]
                    if name in chs:
                        e["group_title"] = gname
                        matched = True
                        break

            if not matched and default_g and not e.get("group_title"):
                e["group_title"] = default_g

    # 排序：keep_order 保持文件顺序；否则有分组在前
    if keep_order:
        entries.sort(key=lambda e: e.get("_order", 0))
    else:
        def sort_key(e):
            g = (e.get("group_title") or "").strip()
            return (0 if g else 1, g, e.get("display_name") or "")
        entries.sort(key=sort_key)

    # ── 步骤 ④：RTP 代理转换 ──
    if rtp_proxy:
        for e in entries:
            urls = e.get("_urls") or ([e.get("url")] if e.get("url") else [])
            new_urls = [_apply_rtp_proxy(u, rtp_proxy) for u in urls]
            if new_urls and new_urls != urls:
                e["_urls"] = new_urls
                if new_urls[0]:
                    e["url"] = new_urls[0]

    # ── 步骤 ⑤：FCC 参数补全 ──
    if fcc_config and fcc_config.get("enabled"):
        server = (fcc_config.get("server") or "").strip()
        if server:
            for e in entries:
                urls = e.get("_urls") or ([e.get("url")] if e.get("url") else [])
                new_urls = [_apply_fcc_to_url(u, server) for u in urls]
                if new_urls and new_urls != urls:
                    e["_urls"] = new_urls
                    if new_urls[0]:
                        e["url"] = new_urls[0]

    return rebuild_m3u(entries, header or "#EXTM3U")