# -*- coding: utf-8 -*-
"""电信平台选择（华为 / 中兴）

四川各地市的电信平台并不相同，同一个账号只会在其中一家的平台上。
本模块负责：
- 读取当前生效平台与两个平台各自的配置 / 频道缓存状态
- 切换平台（切换前必须确认，避免沿用错误城市的配置）
- 切换后按新平台重建 sctv.txt / sctv.m3u / epg.xml / 平台专用 XML
"""
import asyncio
import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException

from . import config
from . import telecom_epg
from . import xml_builder
from . import zte_epg
from .logger import logger
from .rtsp_proxy import ensure_proxy_from_settings

router = APIRouter(prefix="/api/telecom", tags=["电信平台"])

_PLATFORM_ORDER = ("huawei", "zte")


def _platform_summary(platform: str) -> Dict[str, Any]:
    if platform == "huawei":
        st = telecom_epg.telecom_platform_status()
    else:
        st = zte_epg.zte_platform_status()
    st["id"] = platform
    st["label"] = config.platform_label(platform)
    st["xml"] = config.platform_xml_name(platform)
    st["m3u"] = "telecom.m3u" if platform == "huawei" else "zte.m3u"
    return st


def _all_platforms() -> List[Dict[str, Any]]:
    return [_platform_summary(p) for p in _PLATFORM_ORDER]


def _switch_warning(active: Dict[str, Any], target: Dict[str, Any]) -> str:
    """切换平台前展示的警告文案（前端弹窗直接显示）。"""
    lines = [
        f"⚠️ 当前生效平台是「{active['label']}」"
        + (f"，已有 {active['channels']} 个频道缓存" if active.get("channels") else "")
        + (f"，认证配置{'已填写' if active.get('configured') else '不完整'}")
        + "。",
        "四川各地市的电信平台不一样：如果这里选错，频道地址和节目单都会取不到或对不上。",
        "",
        f"切换到「{target['label']}」后：",
        f"① 频道列表、EPG 下载、sctv.txt / sctv.m3u 都改为使用「{target['label']}」的数据；",
        f"② 「{active['label']}」的配置和缓存会原样保留在各自的文件里，切回去还能用，不会被删除；",
        "③ 已并入「EPG 管理」的频道不会自动移除（主 epg.xml 只读取新平台的节目数据）；",
        f"④ 切换后会按「{target['label']}」的频道缓存重建 sctv / XML；若新平台还没配置或没有频道，需要先认证并获取频道。",
    ]
    return "\n".join(lines)


def _regenerate_outputs(platform: str) -> Dict[str, Any]:
    """按指定平台重建输出文件（本地操作，不访问电信服务器）。"""
    out = {"platform": platform, "channels": 0, "sctv": False, "xml": False}
    try:
        channels = config.get_platform_channels(platform).get("channels", [])
        out["channels"] = len(channels)

        if not channels:
            # 没有频道时清掉「当前平台」别名，避免继续对外提供上一平台的频道
            for name in ("sctv.txt", "sctv.m3u"):
                path = config.M3U_DIR / name
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
            logger.log(f"[平台] {config.platform_label(platform)} 没有频道缓存，已清除 sctv 别名文件",
                       category="电信EPG")
            return out

        cfg = config.get_platform_config(platform)
        try:
            if platform == "huawei":
                telecom_epg.generate_sctv_files(channels, cfg)
            else:
                zte_epg.zte_generate_sctv_files(channels, cfg)
            out["sctv"] = True
        except Exception as e:
            logger.warning(f"[平台] 重建 {platform} sctv 失败: {e}")

        try:
            r = xml_builder.build_xml(platform=platform,
                                      log_callback=lambda m: logger.log(m, category="EPG"))
            out["xml"] = bool(r.get("success"))
            r2 = xml_builder.build_telecom_xml(
                platform=platform,
                log_callback=lambda m: logger.log(m, category="电信EPG"))
            out["platform_xml"] = bool(r2.get("success"))
        except Exception as e:
            logger.warning(f"[平台] 重建 {platform} XML 失败: {e}")
    except Exception as e:
        logger.warning(f"[平台] 重建 {platform} 输出失败: {e}")
    return out


# ════════════════════════════════════════════════════════════════
# 平台状态 / 切换
# ════════════════════════════════════════════════════════════════

@router.get("/platform")
async def get_platform_api():
    """当前平台 + 两个平台各自的状态（供前端平台选择器显示）。"""
    active = config.get_active_platform()
    return {
        "active": active,
        "active_label": config.platform_label(active),
        "platforms": _all_platforms(),
        "changed_at": config.get_settings().get("telecom_platform_changed_at", ""),
    }


@router.post("/platform")
async def switch_platform_api(data: dict = Body(...)):
    """切换平台。

    data = {"platform": "zte", "confirm": false}
    - 当前平台已经配置过 / 有频道缓存时，必须 confirm=true 才真正切换，
      否则返回 need_confirm=true + 警告文案，由前端弹窗让用户确认。
    """
    if not isinstance(data, dict):
        raise HTTPException(400, "无效数据")
    target = str(data.get("platform", "") or "").strip().lower()
    if target not in config.PLATFORM_LABEL:
        raise HTTPException(400, "platform 只能是 huawei 或 zte")

    active = config.get_active_platform()
    if target == active:
        return {
            "success": True,
            "changed": False,
            "active": active,
            "active_label": config.platform_label(active),
            "message": f"当前已经是「{config.platform_label(active)}」，无需切换",
        }

    current = _platform_summary(active)
    nxt = _platform_summary(target)
    need_confirm = bool(current.get("configured") or current.get("channels"))

    if need_confirm and not bool(data.get("confirm")):
        return {
            "success": False,
            "need_confirm": True,
            "active": active,
            "target": target,
            "message": _switch_warning(current, nxt),
            "platforms": _all_platforms(),
        }

    if not config.set_active_platform(target):
        return {"success": False, "message": "写入平台配置失败"}

    # 记录切换时间，便于排查「某次数据是哪个平台抓的」
    try:
        settings = config.get_settings()
        settings["telecom_platform_changed_at"] = datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S")
        config.save_settings(settings)
    except Exception as e:
        logger.warning(f"[平台] 记录切换时间失败: {e}")

    logger.log(f"[平台] 已切换: {config.platform_label(active)} → {config.platform_label(target)}",
               category="电信EPG")

    # 启用中兴且配置里打开了代理时，切过去顺手把代理拉起来
    proxy_msg = ""
    if target == "zte":
        try:
            proxy_msg = ensure_proxy_from_settings().get("message", "")
        except Exception as e:
            proxy_msg = f"代理启动失败: {e}"

    # 本地重建输出（频道多时 XML 较慢，放到后台）
    asyncio.create_task(asyncio.to_thread(_regenerate_outputs, target))

    return {
        "success": True,
        "changed": True,
        "active": target,
        "active_label": config.platform_label(target),
        "message": f"已切换到「{config.platform_label(target)}」，正在后台重建 sctv / XML",
        "proxy_message": proxy_msg,
    }


@router.get("/status")
async def telecom_status_api():
    """当前电信平台状态摘要（仪表盘 / 状态条使用）。"""
    active = config.get_active_platform()
    st = _platform_summary(active)
    st["active"] = active
    st["active_label"] = config.platform_label(active)
    st["platforms"] = _all_platforms()
    return st
