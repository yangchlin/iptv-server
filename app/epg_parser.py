"""EPG 解析模块 - 解析外部 EPG XML 源

支持两种规则：
1. 112114 规则: <channel id="CCTV1"><display-name>CCTV1</display-name></channel>
   <programme channel="CCTV1" start="..." stop="..."><title>节目名</title></programme>
2. 51zmt 规则: <channel id="1"><display-name lang="zh">CCTV1</display-name></channel>
   <programme start="..." stop="..." channel="1"><title>节目名</title><desc>描述</desc></programme>
"""
import html
import re


def _clean_text(s: str) -> str:
    """清理节目文本：去 CDATA 包裹 + 反转义 XML 实体 + 去首尾空白。

    源站标题常见 `新闻&amp;天气` 或 `<![CDATA[xxx]]>`，若不处理，
    xml_builder 再转义一次就会变成 `&amp;amp;` / `&lt;![CDATA[...]]&gt;`。
    """
    if not s:
        return ""
    s = s.strip()
    if s.startswith("<![CDATA[") and s.endswith("]]>"):
        s = s[9:-3]
    return html.unescape(s).strip()


def resolve_cache_files(cache_dir, source_url: str):
    """根据 source_url 解析 EPG 缓存中对应的源文件(严格匹配)。

    只有提取记录明确指定了 source_url 且能在缓存目录中找到对应文件时,
    才返回该文件路径列表; 否则返回空列表(表示提取不到该频道的节目)。
    不做过多的兜底遍历, 保证"严格按指定源提取"。

    Args:
        cache_dir: EPG 缓存目录(Path 或 str)
        source_url: 提取记录指定的源 URL(为空则直接返回空)

    Returns:
        list[Path]: 匹配到的缓存文件路径列表(0 或 1 个)
    """
    from pathlib import Path
    cache_dir = Path(cache_dir)
    if not source_url:
        return []
    if not cache_dir.exists():
        return []

    # ① 新命名（域名_文件名_短哈希.xml / 自定义 cache_name）：按源配置精确算出来
    try:
        from . import config as _config
        from .downloader import epg_cache_name
        url_key = str(source_url).strip()
        for s in _config.get_epg_sources():
            if str(s.get("url", "")).strip() == url_key:
                target = cache_dir / epg_cache_name(s)
                if target.is_file():
                    return [target]
                break
    except Exception:
        pass

    # ② 旧命名兼容：xxx.xml / xxx.gz → xxx
    base = source_url.rstrip("/").split("/")[-1]
    base = base.replace(".gz", "").replace(".xml", "").strip()
    if not base:
        return []
    # 严格匹配：文件名主干等于 base，或以 base + 分隔符 开头
    # （不能用 base in p.name，否则 e1 会命中 e11.xml，抽到别的源的节目）
    exact, loose = [], []
    for p in cache_dir.glob("*.xml"):
        stem = p.stem
        if stem == base:
            exact.append(p)
        elif stem.startswith((base + ".", base + "_", base + "-")):
            loose.append(p)
    return sorted(exact) + sorted(loose)


def detect_rule(xml_text: str) -> str:
    """自动检测 XML 规则类型"""
    if 'epg.51zmt.top' in xml_text or '老张的EPG' in xml_text:
        return "51zmt"
    if '112114' in xml_text or 'epg.112114' in xml_text:
        return "112114"
    # 根据 channel id 格式检测
    channel_ids = re.findall(r'<channel\s+id="([^"]+)"', xml_text)
    if channel_ids:
        # 如果 id 是纯数字，可能是 51zmt
        if all(cid.isdigit() for cid in channel_ids[:5]):
            return "51zmt"
        else:
            return "112114"
    return "112114"


def extract_programmes_112114(xml_text: str, keyword: str = "") -> list:
    """112114 规则提取节目"""
    programmes = []
    try:
        # 使用正则提取以绕过命名空间问题
        # 获取所有 programme 块
        prog_pattern = r'<programme\s+([^>]+)>(.*?)</programme>'
        for match in re.finditer(prog_pattern, xml_text, re.DOTALL):
            attrs_str = match.group(1)
            inner = match.group(2)

            channel = re.search(r'channel="([^"]+)"', attrs_str)
            start = re.search(r'start="([^"]+)"', attrs_str)
            stop = re.search(r'stop="([^"]+)"', attrs_str)
            title_match = re.search(r'<title[^>]*>(.*?)</title>', inner, re.DOTALL)
            desc_match = re.search(r'<desc[^>]*>(.*?)</desc>', inner, re.DOTALL)

            ch = channel.group(1) if channel else ""
            # 只按频道名(display-name/channel属性)精确匹配，避免子串误匹配
            if keyword and ch.strip() != keyword.strip():
                continue

            programmes.append({
                "channel": ch,
                "start": start.group(1) if start else "",
                "stop": stop.group(1) if stop else "",
                "title": _clean_text(title_match.group(1)) if title_match else "",
                "desc": _clean_text(desc_match.group(1)) if desc_match else "",
            })
    except Exception as e:
        print(f"112114 解析错误: {e}")
    return programmes


def extract_programmes_51zmt(xml_text: str, keyword: str = "") -> list:
    """51zmt 规则提取节目"""
    programmes = []
    try:
        # 构建 channel id 到 display-name 的映射
        ch_map = {}
        channel_pattern = r'<channel\s+id="(\d+)">\s*<display-name[^>]*>(.*?)</display-name>'
        for match in re.finditer(channel_pattern, xml_text, re.DOTALL):
            ch_map[match.group(1)] = match.group(2).strip()

        prog_pattern = r'<programme\s+([^>]+)>(.*?)</programme>'
        for match in re.finditer(prog_pattern, xml_text, re.DOTALL):
            attrs_str = match.group(1)
            inner = match.group(2)

            channel = re.search(r'channel="(\d+)"', attrs_str)
            start = re.search(r'start="([^"]+)"', attrs_str)
            stop = re.search(r'stop="([^"]+)"', attrs_str)
            title_match = re.search(r'<title[^>]*>(.*?)</title>', inner, re.DOTALL)
            desc_match = re.search(r'<desc[^>]*>(.*?)</desc>', inner, re.DOTALL)

            ch_id = channel.group(1) if channel else ""
            display_name = ch_map.get(ch_id, "")

            # 只按频道名(display-name)精确匹配，避免数字id子串误匹配
            # (如 id=6 的 CCTV5+ 不能误匹配含"6"的所有频道)
            if keyword and display_name.strip() != keyword.strip():
                continue

            programmes.append({
                "channel": display_name or ch_id,
                "channel_id": ch_id,
                "start": start.group(1) if start else "",
                "stop": stop.group(1) if stop else "",
                "title": _clean_text(title_match.group(1)) if title_match else "",
                "desc": _clean_text(desc_match.group(1)) if desc_match else "",
            })
    except Exception as e:
        print(f"51zmt 解析错误: {e}")
    return programmes


def extract_programmes(xml_text: str, rule: str = "auto", keyword: str = "") -> list:
    """根据规则提取节目"""
    if rule == "auto":
        rule = detect_rule(xml_text)

    if rule == "112114":
        return extract_programmes_112114(xml_text, keyword)
    elif rule == "51zmt":
        return extract_programmes_51zmt(xml_text, keyword)
    else:
        # 默认尝试 112114
        result = extract_programmes_112114(xml_text, keyword)
        if result:
            return result
        return extract_programmes_51zmt(xml_text, keyword)


def get_channel_list(xml_text: str, rule: str = "auto") -> list:
    """获取 EPG 源中的频道列表"""
    if rule == "auto":
        rule = detect_rule(xml_text)

    channels = []
    pattern = r'<channel\s+id="([^"]+)">\s*<display-name[^>]*>(.*?)</display-name>'
    for match in re.finditer(pattern, xml_text, re.DOTALL):
        ch_id = match.group(1)
        name = match.group(2).strip()
        channels.append({"id": ch_id, "name": name, "rule": rule})

    if not channels:
        # 简单模式
        pattern2 = r'<channel\s+id="([^"]+)"'
        for match in re.finditer(pattern2, xml_text):
            ch_id = match.group(1)
            channels.append({"id": ch_id, "name": ch_id, "rule": rule})

    return channels


def format_xmltv_time(dt_str: str) -> str:
    """将时间格式化为 XMLTV 格式: YYYYMMDDHHMMSS +0800"""
    if not dt_str:
        return ""
    dt_str = str(dt_str).strip()

    # 已经是标准格式：14 位数字 + 时区（如 20260724000400 +0800）
    if re.match(r'^\d{14}\s+[+-]\d{4}$', dt_str):
        return dt_str

    # 纯 14 位数字（无时区，部分 EPG 源就是这样）→ 补 +0800
    if re.match(r'^\d{14}$', dt_str):
        return f"{dt_str} +0800"

    # 纯 12 位数字（无秒）→ 补秒和时区
    if re.match(r'^\d{12}$', dt_str):
        return f"{dt_str}00 +0800"

    # JSON / 日志常见格式：2026-07-24 00:04:00 或 2026-07-24T00:04:00
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2})(?::(\d{2}))?', dt_str)
    if m:
        sec = m.group(6) or "00"
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}{sec} +0800"

    return dt_str
