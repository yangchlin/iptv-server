# -*- coding: utf-8 -*-
"""频道分组推断（华为 / 中兴两个平台共用）

原来这段逻辑写在 telecom_epg.py 里，中兴平台也要用同一套「分组规则」，
所以单独抽出来，避免中兴模块反向依赖华为模块。
"""
from typing import Any


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
