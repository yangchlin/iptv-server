# -*- coding: utf-8 -*-
"""频道名归一化 + 分组推断（华为 / 中兴两个平台共用）

原来这段逻辑写在 telecom_epg.py 里，中兴平台也要用同一套规则，
所以单独抽出来，避免中兴模块反向依赖华为模块。
"""
from typing import Any

# 频道代号 → 显示名 / EPG tvg-id（华为、中兴共用同一张表）
# 说明：清洗时会先去掉末尾“高清/HD/FHD/UHD”和所有“-”，
#       所以 “SCTV-2高清”“CCTV-少儿高清” 都会命中这里。
NAME_MAP = {
    "SCTV2": {"name": "四川经济", "tvg_id": "SCTV2"},
    "SCTV3": {"name": "四川文化旅游", "tvg_id": "SCTV3"},
    "SCTV4": {"name": "四川新闻", "tvg_id": "SCTV4"},
    "SCTV5": {"name": "四川影视文艺", "tvg_id": "SCTV5"},
    "SCTV6": {"name": "四川星空购物", "tvg_id": "SCTV6"},
    "SCTV7": {"name": "四川妇女儿童", "tvg_id": "SCTV7"},
    "SCTV科教": {"name": "四川科教", "tvg_id": "SCTV科教"},
    "CCTV少儿": {"name": "CCTV14", "tvg_id": "CCTV14"},
    "CETV1": {"name": "中国教育1台", "tvg_id": "CETV1"},
    "CETV2": {"name": "中国教育2台", "tvg_id": "CETV2"},
    "CETV4": {"name": "中国教育4台", "tvg_id": "CETV4"},
}

# 无效频道关键词（大小写不敏感）
_INVALID_KEYWORDS = ("画中画", "单音轨", "pip")


def clean_channel_name(name: str) -> str:
    """去掉清晰度后缀和中划线（与华为平台完全一致）。

    例：CCTV-1高清 → CCTV1；SCTV-2高清 → SCTV2；CCTV-少儿高清 → CCTV少儿
    """
    s = str(name or "").strip()
    for suffix in ("高清", "HD", "FHD", "UHD"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].rstrip()
    return s.replace("-", "").strip()


def normalize_channel_name(name: str):
    """返回 (显示名, tvg-id)，命中 NAME_MAP 时使用固定代号。"""
    cleaned = clean_channel_name(name)
    info = NAME_MAP.get(cleaned)
    if info:
        return info["name"], info["tvg_id"]
    return cleaned, cleaned


def is_invalid_channel_name(name: str) -> bool:
    """画中画 / 单音轨 / 带 pip 的频道直接跳过。"""
    s = str(name or "")
    low = s.lower()
    return any(kw in s for kw in _INVALID_KEYWORDS[:2]) or "pip" in low


def guess_group(name: str) -> str:
    """默认的智能猜测兜底。"""
    if "CCTV" in name or "CGTN" in name:
        return "央视频道"
    if any(x in name for x in ("卫视", "凤凰", "教育", "CETV", "卡通", "少儿")):
        return "卫视频道"
    if "SCTV" in name or "四川" in name:
        return "省内频道"
    if any(x in name for x in ("泸州", "宜宾", "内江", "雅安", "攀枝花", "自贡", "眉山",
                               "凉山", "彝语", "德阳", "CDTV", "广元", "乐山", "成都", "绵阳")):
        return "地市频道"
    return "其他频道"


def apply_group_rules(name: str, group_rules: Any) -> str:
    """根据『合并文件-分组规则』计算最终的分组。"""
    if not isinstance(group_rules, dict) or not group_rules.get("rules"):
        return guess_group(name)

    rules = sorted(
        [r for r in group_rules["rules"] if r.get("enabled", True)],
        key=lambda r: r.get("priority", 999),
    )
    default_g = (group_rules.get("default_group") or "").strip()

    for rule in rules:
        gname = (rule.get("name") or "").strip()
        if not gname:
            continue
        mt = rule.get("match_type", "keyword")
        if mt == "keyword":
            kws = [k.strip() for k in (rule.get("keywords") or []) if k and k.strip()]
            if any(kw in name for kw in kws):
                return gname
        elif mt == "channels":
            chs = [c.strip() for c in (rule.get("channels") or []) if c and c.strip()]
            if name in chs:
                return gname
    return default_g or guess_group(name)
