# -*- coding: utf-8 -*-
"""中兴平台 EPG 抓取核心模块

与华为平台（telecom_epg.py）完全独立：
- 认证：/iptvepg/platform/auth.jsp → /iptvepg/function/index.jsp → /iptvepg/function/funcportalauth.jsp
- 频道：/iptvepg/function/frameset_builder.jsp（解析 jsSetChannelInfo / ChannelID 等属性）
- 节目：/iptvepg/frame299/action/tvod/getTvodlist.jsp（按 channelcode + 日期逐天查询）

数据落盘与华为分开：
- 频道缓存：data/config/zte_channels.json
- EPG JSON：data/json/<频道名>_zte_<日期>.json
- 专用 XML：data/xml/epg_zte.xml
- 输出：data/m3u/zte.m3u（同时刷新 sctv.txt / sctv.m3u 两个「当前平台」别名）
"""
import datetime
import json
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from . import config
from .channel_groups import apply_group_rules
from .logger import logger

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
HTTP_TIMEOUT = 20


# ════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════

def get_zte_config() -> Dict[str, Any]:
    """中兴平台配置（settings.telecom_zte）。"""
    return config.get_platform_config("zte")


def save_zte_config(config_data: Dict[str, Any]) -> bool:
    try:
        return config.save_platform_config("zte", config_data)
    except Exception as e:
        logger.error(f"保存中兴配置失败: {e}")
        return False


def _base(cfg: Dict[str, Any]) -> str:
    return str(cfg.get("server", "") or "").strip().rstrip("/")


def _mask(s: Any, keep: int = 3) -> str:
    s = str(s or "")
    if not s:
        return ""
    return s[:keep] + "*" * max(0, len(s) - keep)


def auth_config_error(cfg: Dict[str, Any]) -> str:
    """认证前置校验：返回错误文案，空串表示可以继续。"""
    missing = []
    if not _base(cfg):
        missing.append("IPTVServer 地址")
    for label, field in (("UserID", "UserID"), ("STBID", "STBID")):
        if not str(cfg.get(field, "") or "").strip():
            missing.append(label)
    if not str(cfg.get("Authenticator", "") or "").strip():
        missing.append("Authenticator")
    if missing:
        return "缺少必要配置项: " + ", ".join(missing)
    return ""


def zte_platform_status() -> Dict[str, Any]:
    """中兴平台状态摘要（供 /api/telecom/status 与前端使用）。"""
    cfg = get_zte_config()
    cached = config.get_zte_channels()
    return {
        "platform": "zte",
        "platform_label": config.platform_label("zte"),
        "enabled": bool(cfg.get("enabled", False)),
        "configured": not bool(auth_config_error(cfg)),
        "config_error": auth_config_error(cfg),
        "AuthenticationIP": _base(cfg),
        "server": _base(cfg),
        "channels": len(cached.get("channels", []) or []),
        "updated": cached.get("updated", ""),
        "rtsp_proxy_enabled": bool(cfg.get("rtsp_proxy_enabled", False)),
    }


# ════════════════════════════════════════════════════════════════
# 认证
# ════════════════════════════════════════════════════════════════

def zte_test_connection(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """测试中兴服务器连通性（任意 HTTP 响应即算连通）。"""
    result = {"success": False, "message": "", "host": "", "details": ""}
    base = _base(cfg)
    if not base.startswith(("http://", "https://")):
        result["message"] = "IPTVServer 地址需要以 http:// 开头（抓包得到，例如 http://182.137.9.86:8080）"
        return result
    try:
        resp = requests.get(base + "/", timeout=10,
                            headers={"User-Agent": cfg.get("UserAgent") or DEFAULT_UA})
        result.update({
            "success": True,
            "host": urlparse(resp.url).netloc,
            "message": f"连接成功（HTTP {resp.status_code}），服务器: {urlparse(resp.url).netloc}",
            "details": (resp.text or "")[:300],
        })
    except requests.exceptions.Timeout:
        result["message"] = "连接超时，请检查服务器地址和网络（中兴平台一般只能在电信内网访问）"
    except requests.exceptions.ConnectionError:
        result["message"] = "连接失败，请检查服务器地址是否正确"
    except Exception as e:
        result["message"] = f"测试失败: {e}"
    return result


def zte_auth(cfg: Dict[str, Any], session: Optional[requests.Session] = None) -> Dict[str, Any]:
    """中兴三步认证，返回带已认证 Cookie 的 requests.Session。

    成功：{"success": True, "session": Session, "host": "...", "message": "认证成功"}
    """
    result: Dict[str, Any] = {"success": False, "message": "", "session": None,
                              "host": "", "cookies": {}}
    err = auth_config_error(cfg)
    if err:
        result["message"] = err
        return result

    base = _base(cfg)
    if not base.startswith(("http://", "https://")):
        result["message"] = "IPTVServer 地址需要以 http:// 开头"
        return result

    user_id = str(cfg.get("UserID", "") or "")
    authenticator = str(cfg.get("Authenticator", "") or "")
    stb_ip = str(cfg.get("StbIP", "") or "")
    stb_id = str(cfg.get("STBID", "") or "")
    last_termno = str(cfg.get("LastTermno", "0") or "0")
    user_token = str(cfg.get("UserToken", "") or "")
    user_group = str(cfg.get("UserGroupNMB", "") or "")
    epg_group = str(cfg.get("EPGGroupNMB", "") or "")
    stbinfo = str(cfg.get("stbinfo", "undefined") or "undefined")
    ua = str(cfg.get("UserAgent", "") or DEFAULT_UA)

    headers = {"User-Agent": ua, "X-Requested-With": "com.huawei.iptv"}
    logger.info(f"中兴认证: server={base} UserID={_mask(user_id)} STBID={_mask(stb_id)}")

    if session is None:
        session = requests.Session()

    last_err = ""
    for attempt in range(2):
        try:
            # 第一步：platform/auth.jsp
            step1 = session.post(
                f"{base}/iptvepg/platform/auth.jsp",
                data={
                    "UserID": user_id,
                    "Authenticator": authenticator,
                    "StbIP": stb_ip,
                    "LastTermno": last_termno,
                },
                headers=headers, timeout=HTTP_TIMEOUT)
            step1.raise_for_status()

            # 第二步：function/index.jsp
            step2 = session.get(
                f"{base}/iptvepg/function/index.jsp",
                params={
                    "UserGroupNMB": user_group,
                    "EPGGroupNMB": epg_group,
                    "UserToken": user_token,
                    "UserID": user_id,
                    "STBID": stb_id,
                    "LastTermno": last_termno,
                },
                headers=headers, timeout=HTTP_TIMEOUT)
            step2.raise_for_status()

            # 第三步：function/funcportalauth.jsp（成功后 Cookie 才真正生效）
            step3 = session.post(
                f"{base}/iptvepg/function/funcportalauth.jsp",
                data={
                    "UserToken": user_token,
                    "LastTermno": last_termno,
                    "UserID": user_id,
                    "stbinfo": stbinfo,
                },
                headers=headers, timeout=HTTP_TIMEOUT)
            step3.raise_for_status()

            # 部分省份会在页面里回写新的 UserToken，能抓到就顺手更新
            new_token = _extract_user_token(step3.text) or _extract_user_token(step2.text)
            if new_token and new_token != user_token:
                try:
                    latest = get_zte_config()
                    latest["UserToken"] = new_token
                    save_zte_config(latest)
                    logger.info("中兴认证返回了新的 UserToken，已写回配置")
                except Exception as e:
                    logger.warning(f"写回中兴 UserToken 失败: {e}")

            result.update({
                "success": True,
                "message": "认证成功",
                "session": session,
                "host": urlparse(base).netloc,
                "cookies": dict(session.cookies.get_dict()),
            })
            logger.info("中兴认证成功")
            return result
        except requests.exceptions.RequestException as e:
            last_err = str(e)[:200]
            logger.error(f"中兴认证请求失败: {last_err}")
            if attempt < 1:
                time.sleep(2)
                session = requests.Session()
        except Exception as e:
            last_err = str(e)[:200]
            logger.error(f"中兴认证异常: {last_err}")
            if attempt < 1:
                time.sleep(2)

    result["message"] = f"认证失败: {last_err}" if last_err else "认证失败，请检查配置和网络"
    return result


_TOKEN_PATTERNS = (
    r'name="UserToken"\s+value="([^"]+)"',
    r"UserToken\s*=\s*['\"]([^'\"]+)['\"]",
    r"userToken['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]",
)


def _extract_user_token(text: str) -> str:
    for pat in _TOKEN_PATTERNS:
        m = re.search(pat, text or "", re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


# ════════════════════════════════════════════════════════════════
# 频道列表
# ════════════════════════════════════════════════════════════════

_ATTR_RE_CACHE: Dict[str, re.Pattern] = {}


def _attr_list(text: str, attr: str) -> List[str]:
    pat = _ATTR_RE_CACHE.get(attr)
    if pat is None:
        pat = re.compile(attr + r'\s*=\s*"([^"]*)"')
        _ATTR_RE_CACHE[attr] = pat
    return [str(v).strip() for v in pat.findall(text or "")]


def _first_igmp(value: str) -> str:
    m = re.search(r'igmp://([^\s|"\'<>]+)', value or "")
    if m:
        return m.group(1).strip()
    m = re.search(r'(\d{1,3}(?:\.\d{1,3}){3}:\d+)', value or "")
    return m.group(1) if m else ""


def _first_rtsp(value: str) -> str:
    m = re.search(r'(rtsp://[^\s|"\'<>]+)', value or "")
    return m.group(1).strip() if m else ""


def clean_channel_name(name: str) -> str:
    """频道名只做空白归一，保留清晰度标记（中兴 EPG 用原始名做 key）。"""
    return re.sub(r'\s+', ' ', str(name or "")).strip()


def parse_zte_channels(text: str) -> List[Dict[str, Any]]:
    """解析 frameset_builder.jsp。

    兼容两种来源：
    - jsSetChannelInfo('a','b','c','d','<channelcode>') 提供 EPG 查询用的 channelcode
    - ChannelID / ChannelName / UserChannelID / ChannelURL / ChannelSDP 属性提供地址
    数量对不上时以 ChannelName 为准逐项兜底，避免 zip 截断丢频道。
    """
    if not text:
        return []

    codes: List[str] = []
    for m in re.finditer(r'jsSetChannelInfo\s*\(([^)]*)\)', text):
        args = re.findall(r"'([^']*)'", m.group(1))
        codes.append(args[4].strip() if len(args) >= 5 else "")

    names = _attr_list(text, "ChannelName")
    if not names:
        return []

    ids = _attr_list(text, "ChannelID")
    ucids = _attr_list(text, "UserChannelID")
    urls = _attr_list(text, "ChannelURL")
    sdps = _attr_list(text, "ChannelSDP")
    shifts = _attr_list(text, "TimeShift")
    shift_lens = _attr_list(text, "TimeShiftLength")

    def at(seq: List[str], i: int) -> str:
        return seq[i] if i < len(seq) else ""

    channels: List[Dict[str, Any]] = []
    seen = set()
    for i, raw_name in enumerate(names):
        name = clean_channel_name(raw_name)
        if not name:
            continue
        # 过滤掉无效频道（与华为平台一致）
        if "画中画" in name or "单音轨" in name:
            continue

        channel_id = at(ids, i)
        code = at(codes, i) or channel_id
        igmp = _first_igmp(at(urls, i))
        rtsp = _first_rtsp(at(sdps, i))
        if not igmp and not rtsp:
            continue

        key = (name, igmp, rtsp)
        if key in seen:
            continue
        seen.add(key)

        channels.append({
            "channel_id": code,
            "name": name,
            "user_channel_id": at(ucids, i),
            "channel_url": igmp,
            "rtsp_url": rtsp,
            "timeshift": at(shifts, i),
            "timeshift_length": at(shift_lens, i),
            "channel_no": str(len(channels) + 1),
        })
    return channels


def zte_get_channels(cfg: Dict[str, Any], session: requests.Session) -> Dict[str, Any]:
    """拉取中兴频道列表。"""
    result: Dict[str, Any] = {"success": False, "channels": [], "message": ""}
    base = _base(cfg)
    ua = str(cfg.get("UserAgent", "") or DEFAULT_UA)
    try:
        headers = {
            "User-Agent": ua,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{base}/iptvepg/function/index.jsp",
            "X-Requested-With": "com.huawei.iptv",
        }
        resp = session.post(
            f"{base}/iptvepg/function/frameset_builder.jsp",
            data={
                "MAIN_WIN_SRC": "/iptvepg/frame78/portal.jsp",
                "NEED_UPDATE_STB": "1",
                "BUILD_ACTION": "FRAMESET_BUILDER",
            },
            headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()

        if os.environ.get("ZTE_DEBUG_DUMP") == "1":
            try:
                dump = config.LOG_DIR / "zte_frameset_builder.jsp"
                dump.write_text(resp.text, encoding="utf-8")
                logger.info(f"中兴频道列表源文件已保存至: {dump}")
            except Exception as e:
                logger.error(f"保存中兴频道列表源文件失败: {e}")

        channels = parse_zte_channels(resp.text)
        if not channels:
            result["message"] = ("频道列表为空：认证可能已失效，或页面结构不是预期的 frameset_builder.jsp"
                                 "（可设置环境变量 ZTE_DEBUG_DUMP=1 保存原始页面排查）")
            return result

        result.update({
            "success": True,
            "channels": channels,
            "message": f"成功获取 {len(channels)} 个频道",
        })
        logger.info(f"中兴频道获取成功: {len(channels)} 个")
    except Exception as e:
        result["message"] = f"获取频道列表失败: {e}"
        logger.error(result["message"])
    return result


# ════════════════════════════════════════════════════════════════
# 节目单
# ════════════════════════════════════════════════════════════════

def _fmt_time(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y%m%d%H%M%S") + " +0800"
        except ValueError:
            continue
    return ""


def parse_zte_day(text: str) -> List[Dict[str, str]]:
    """解析 getTvodlist.jsp 返回的一天节目。

    返回 [{"begin_time", "end_time", "program_name"}]
    """
    out: List[Dict[str, str]] = []
    if not text:
        return out

    # 以 prevuename 为记录起点切块：每块只包含本记录到下一记录之前的字段
    for chunk in re.split(r'(?=prevuename"\s*:)', text)[1:]:
        name_m = re.search(r'prevuename"\s*:\s*"(.*?)"', chunk)
        begin_m = re.search(r'begintime"\s*:\s*"(.*?)"', chunk)
        end_m = re.search(r'endtime"\s*:\s*"(.*?)"', chunk)
        if not (name_m and begin_m and end_m):
            continue
        begin = _fmt_time(begin_m.group(1))
        end = _fmt_time(end_m.group(1))
        if not begin or not end:
            continue
        name = str(name_m.group(1)).replace("<", "《").replace(">", "》").strip()
        if not name:
            continue
        out.append({"begin_time": begin, "end_time": end, "program_name": name})
    return out


def zte_download_epg(cfg: Dict[str, Any], session: requests.Session,
                     channel_id: str, channel_name: str,
                     dates: List[str]) -> Dict[str, Any]:
    """下载一个频道若干天的节目单。

    dates 为 YYYYMMDD 列表；内部转换成中兴要求的 YYYY.MM.DD。
    """
    result: Dict[str, Any] = {"success": False, "programs": [], "message": ""}
    base = _base(cfg)
    code = str(channel_id or "").strip()
    if not code:
        result["message"] = "频道没有 channelcode，无法查询节目单"
        return result

    ua = str(cfg.get("UserAgent", "") or DEFAULT_UA)
    headers = {
        "User-Agent": ua,
        "Referer": f"{base}/iptvepg/function/index.jsp",
        "X-Requested-With": "com.huawei.iptv",
    }
    url = f"{base}/iptvepg/frame299/action/tvod/getTvodlist.jsp"

    programs: List[Dict[str, str]] = []
    failed_days = []
    for date_str in dates:
        try:
            query_date = datetime.datetime.strptime(date_str, "%Y%m%d").strftime("%Y.%m.%d")
        except ValueError:
            continue
        try:
            resp = session.get(url, params={"channelcode": code, "timedata": query_date},
                               headers=headers, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            programs.extend(parse_zte_day(resp.text))
        except Exception as e:
            failed_days.append(f"{query_date}({str(e)[:60]})")
            continue

    if programs:
        result.update({
            "success": True,
            "programs": programs,
            "message": f"成功下载 {len(programs)} 条节目",
        })
    else:
        result["message"] = "未获取到节目单" + (f"（失败日期: {', '.join(failed_days)}）" if failed_days else "")
    return result


def zte_sync_to_server(epg_data: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """把中兴 EPG 写入 data/json/<频道名>_zte_<日期>.json（原子写）。"""
    result = {"success": False, "message": "", "saved_files": 0}
    try:
        json_dir = config.JSON_DIR
        json_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for ch in epg_data.get("channels", []):
            name = str(ch.get("name", "") or "").strip()
            programs = ch.get("programs", []) or []
            if not name or not programs:
                continue

            date_progs: Dict[str, List[Dict[str, str]]] = {}
            for p in programs:
                begin = str(p.get("begin_time", "") or "")
                if len(begin) < 8:
                    continue
                date_progs.setdefault(begin[:8], []).append({
                    "name": name,
                    "start": begin,
                    "stop": str(p.get("end_time", "") or ""),
                    "title": str(p.get("program_name", "") or ""),
                    "desc": "",
                })

            safe = config.safe_filename(name)
            for date_str, progs in date_progs.items():
                fname = f"{safe}{config.ZTE_EPG_SUFFIX}_{date_str}.json"
                path = json_dir / fname
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(
                    json.dumps({"programmes": progs, "source": "zte"},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8")
                tmp.replace(path)
                saved += 1

        result.update({
            "success": True,
            "message": f"成功同步 {saved} 个中兴EPG文件",
            "saved_files": saved,
        })
    except Exception as e:
        result["message"] = f"同步失败: {e}"
    return result


# ════════════════════════════════════════════════════════════════
# 播放地址（直播 / 回看）
# ════════════════════════════════════════════════════════════════

def _mcast(channel_url: str) -> str:
    s = str(channel_url or "").strip()
    s = re.sub(r'^igmp://', '', s, flags=re.IGNORECASE)
    s = s.split("|")[0].strip()
    return s


def zte_live_url(ch: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """直播地址：live_proxy（udpxy 等）/udp/<组播>；没配则退回 rtp://。"""
    mcast = _mcast(ch.get("channel_url", ""))
    if not mcast:
        return ""
    live_proxy = str(cfg.get("live_proxy", "") or "").strip().rstrip("/")
    if live_proxy:
        return f"{live_proxy}/udp/{mcast}"
    return f"rtp://{mcast}"


def _replay_suffix(rtsp_url: str) -> str:
    """rtsp://ip:port/live/xxx.sdp → /PLTV/xxx.smil（中兴回看地址格式）。"""
    s = str(rtsp_url or "").strip().split("?")[0]
    m = re.search(r'/(?:live|pltv)/([^/]+?)\.(?:sdp|smil)$', s, re.IGNORECASE)
    if m:
        return f"/PLTV/{m.group(1)}.smil"
    m = re.search(r'/([^/]+?)\.(?:sdp|smil)$', s, re.IGNORECASE)
    if m:
        return f"/PLTV/{m.group(1)}.smil"
    return ""


def zte_catchup_url(ch: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """回看地址：replay_proxy + /PLTV/xxx.smil + playseek。

    replay_proxy 一般填本程序内置 RTSP 代理的地址（rtsp://内网IP:554），
    或自建代理地址；留空时退回原始 rtsp 主机 + 转换后的 PLTV 路径。
    """
    rtsp = str(ch.get("rtsp_url", "") or "").strip()
    if not rtsp.lower().startswith("rtsp://"):
        return ""
    suffix = _replay_suffix(rtsp)
    replay_proxy = str(cfg.get("replay_proxy", "") or "").strip().rstrip("/")

    if not suffix:
        # 路径不像 /live/xxx.sdp 或 /PLTV/xxx.smil：不动路径，直接原地址加 playseek
        return rtsp.split("?")[0] + "?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"

    if replay_proxy:
        base = replay_proxy + suffix
    else:
        m = re.match(r'(rtsp://[^/]+)', rtsp)
        host = m.group(1) if m else ""
        base = host + suffix if host else rtsp.split("?")[0]

    if not base.lower().startswith("rtsp://"):
        return ""
    return base + "?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"


# ════════════════════════════════════════════════════════════════
# 输出文件
# ════════════════════════════════════════════════════════════════

def _output_bases(cfg: Dict[str, Any]):
    epg_base = str(cfg.get("epg_server_url", "") or "").strip().rstrip("/")
    epgname = str(cfg.get("epgname", "") or "epg.xml").lstrip("/")
    logo_base = str(cfg.get("logo_url", "") or "").strip().rstrip("/")
    epg_url = f"{epg_base}/{epgname}" if epg_base else ""
    logo_base = logo_base or epg_base
    return epg_base, epgname, epg_url, logo_base


def zte_generate_m3u(channels: List[Dict], cfg: Dict[str, Any]) -> str:
    """生成中兴平台 m3u 内容。"""
    epg_base, _epgname, epg_url, logo_base = _output_bases(cfg)
    enable_group = bool(cfg.get("enable_group", False))
    group_rules = config.get_group_rules() if enable_group else None
    platform_tag = "中兴"

    lines = [f'#EXTM3U name="四川电信（{platform_tag}）" x-tvg-url="{epg_url}"']
    for ch in channels:
        if ch.get("_disabled"):
            continue
        name = str(ch.get("name", "") or "").strip()
        if not name:
            continue
        live = zte_live_url(ch, cfg)
        if not live:
            continue
        catchup = zte_catchup_url(ch, cfg)

        tvg_logo = config.logo_link(logo_base, epg_base, name)
        tvg_id = str(ch.get("tvg_id", "") or name).strip() or name
        attrs = f'tvg-logo="{tvg_logo}" tvg-id="{tvg_id}" tvg-name="{name}"'
        if enable_group:
            g = apply_group_rules(name, group_rules)
            if g:
                attrs += f' group-title="{g}"'
        if catchup:
            attrs += f' catchup="default" catchup-source="{catchup}"'
        lines.append(f'#EXTINF:-1 {attrs}, {name}')
        lines.append(live)
        lines.append("")
    return "\n".join(lines)


def zte_generate_sctv_files(channels: List[Dict], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """生成中兴输出：sctv.txt / sctv.m3u（当前平台别名）+ zte.m3u（平台专用）。"""
    result = {"success": False, "txt_path": "", "m3u_path": "", "m3u_platform_path": "",
              "count": 0, "message": ""}
    try:
        m3u_dir = config.M3U_DIR
        m3u_dir.mkdir(parents=True, exist_ok=True)
        epg_base, _epgname, epg_url, logo_base = _output_bases(cfg)
        enable_group = bool(cfg.get("enable_group", False))
        group_rules = config.get_group_rules() if enable_group else None

        txt_path = m3u_dir / "sctv.txt"
        m3u_path = m3u_dir / "sctv.m3u"
        platform_path = m3u_dir / "zte.m3u"

        count = 0
        with open(txt_path, "w", encoding="utf-8", newline="\n") as ftxt, \
             open(m3u_path, "w", encoding="utf-8", newline="\n") as fm3u:
            fm3u.write(f'#EXTM3U name="四川电信（中兴）" x-tvg-url="{epg_url}"\n')
            for ch in channels:
                if ch.get("_disabled"):
                    continue
                name = str(ch.get("name", "") or "").strip()
                if not name:
                    continue
                live = zte_live_url(ch, cfg)
                if not live:
                    continue
                catchup = zte_catchup_url(ch, cfg)

                group_str = ""
                if enable_group:
                    g = apply_group_rules(name, group_rules)
                    group_str = f",{g}" if g else ""

                if catchup:
                    ftxt.write(f"{name},{catchup}#{live}{group_str}\n")
                else:
                    ftxt.write(f"{name},{live}{group_str}\n")

                tvg_logo = config.logo_link(logo_base, epg_base, name)
                tvg_id = str(ch.get("tvg_id", "") or name).strip() or name
                attrs = f'tvg-logo="{tvg_logo}" tvg-id="{tvg_id}" tvg-name="{name}"'
                if enable_group:
                    g = apply_group_rules(name, group_rules)
                    if g:
                        attrs += f' group-title="{g}"'
                if catchup:
                    attrs += f' catchup="default" catchup-source="{catchup}"'
                fm3u.write(f"#EXTINF:-1 {attrs}, {name}\n{live}\n")
                count += 1

        # 平台专用 m3u（与 sctv.m3u 内容一致，便于区分两个平台）
        try:
            content = zte_generate_m3u(channels, cfg)
            tmp = platform_path.with_suffix(".m3u.tmp")
            tmp.write_text(content, encoding="utf-8", newline="\n")
            tmp.replace(platform_path)
        except Exception as e:
            logger.warning(f"生成 zte.m3u 失败: {e}")

        result.update({
            "success": True,
            "txt_path": str(txt_path),
            "m3u_path": str(m3u_path),
            "m3u_platform_path": str(platform_path),
            "count": count,
            "message": f"已生成 {count} 个频道",
        })
    except Exception as e:
        result["message"] = f"生成失败: {e}"
    return result


def zte_epg_dates(days: Any, today: Optional[datetime.date] = None) -> List[str]:
    """中兴回看日期：默认 8 天 = 前 6 天 + 今天 + 明天（与原始脚本一致）。"""
    try:
        n = int(days)
    except (TypeError, ValueError):
        n = 8
    n = max(1, min(n, 10))
    today = today or datetime.date.today()
    future = max(0, n - 7)
    start = today - datetime.timedelta(days=n - 1 - future)
    return [(start + datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


# ════════════════════════════════════════════════════════════════
# 频道缓存合并（保留手工修改）
# ════════════════════════════════════════════════════════════════

def merge_manual_channels(old: List[Dict[str, Any]],
                          fresh: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """用新拉到的频道列表刷新缓存，但保留手工改过的名字/地址/禁用状态。

    规则与华为平台一致：
    - 以 (channel_id, name) 为 key
    - 手工频道（_manual）的 name / channel_url / rtsp_url / _disabled 覆盖回来
    - 手工加的、这次没拉到的频道追加到末尾
    """
    def key(c: Dict[str, Any]):
        cid = str(c.get("channel_id", "") or "").strip()
        if cid:
            return ("cid", cid)
        return ("name", str(c.get("name", "") or "").strip())

    manual = {key(c): c for c in (old or []) if c.get("_manual")}
    merged: List[Dict[str, Any]] = []
    for ch in fresh or []:
        k = key(ch)
        if k in manual:
            m = dict(ch)
            for f in ("name", "channel_url", "rtsp_url", "_disabled"):
                if f in manual[k]:
                    m[f] = manual[k][f]
            m["_manual"] = True
            merged.append(m)
            manual.pop(k, None)
        else:
            merged.append(ch)
    merged.extend(manual.values())
    return merged


# ════════════════════════════════════════════════════════════════
# 完整任务（供 API 后台任务与定时任务共用）
# ════════════════════════════════════════════════════════════════

def run_zte_job(cfg: Dict[str, Any], days: Any = 8, force_fetch: bool = False,
                auto_fetch: bool = True, log=None) -> Dict[str, Any]:
    """同步执行：认证 → 频道（按需）→ EPG → JSON → sctv/m3u → XML。

    调用方负责 async：`await asyncio.to_thread(run_zte_job, ...)`。
    返回统计信息；异常不抛出，写进 message。
    """
    def emit(msg: str) -> None:
        try:
            if log:
                log(msg)
            else:
                logger.log(f"[中兴] {msg}", category="电信EPG")
        except Exception:
            pass

    stats = {"success": False, "channels": 0, "ok": 0, "fail": 0,
             "programmes": 0, "message": ""}

    try:
        emit("===== 开始中兴频道/EPG 获取 =====")

        cached = config.get_zte_channels().get("channels", [])
        channels = cached
        need_fetch = bool(force_fetch) or not channels
        auth = None

        if need_fetch and not (force_fetch or auto_fetch):
            emit("❌ 频道缓存为空且未开启自动抓取")
            stats["message"] = "频道缓存为空且未开启自动抓取"
            return stats

        if need_fetch:
            emit("1. 正在认证并获取频道列表...")
            auth = zte_auth(cfg)
            if not auth.get("success"):
                emit(f"❌ 认证失败: {auth.get('message', '')}")
                stats["message"] = auth.get("message", "认证失败")
                return stats
            r = zte_get_channels(cfg, auth["session"])
            if not r.get("success"):
                emit(f"❌ 获取频道失败: {r.get('message', '')}")
                stats["message"] = r.get("message", "")
                return stats
            channels = merge_manual_channels(cached, r["channels"])
            config.save_zte_channels(channels)
            emit(f"✅ 成功获取 {len(channels)} 个频道（已写入 zte_channels.json）")
            _auto_merge_into_epg(channels, emit)

        if not channels:
            emit("❌ 无可用频道")
            stats["message"] = "无可用频道"
            return stats

        if auth is None:
            emit("1. 正在重新认证...")
            auth = zte_auth(cfg)
            if not auth.get("success"):
                emit(f"❌ 认证失败: {auth.get('message', '')}")
                stats["message"] = auth.get("message", "认证失败")
                return stats

        # ── EPG ──
        date_list = zte_epg_dates(days)
        query_dates = date_list
        total = len(channels)
        all_epg: Dict[str, Any] = {"channels": [], "total": total}
        ok = fail = 0
        emit(f"2. 开始下载 {total} 个频道的 EPG（{query_dates[0]} ~ {query_dates[-1]}）...")

        for i, ch in enumerate(channels, 1):
            name = str(ch.get("name", "") or ch.get("channel_id", ""))
            # 中兴一次鉴权可查的频道数有限，每 50 个重新认证
            if i > 1 and (i - 1) % 50 == 0:
                emit("   正在重新认证（避免会话过期）...")
                new_auth = zte_auth(cfg)
                if new_auth.get("success"):
                    auth = new_auth

            r = zte_download_epg(cfg, auth["session"], ch.get("channel_id", ""),
                                 name, query_dates)
            if r.get("success") and r.get("programs"):
                ok += 1
                all_epg["channels"].append({
                    "name": name,
                    "channel_id": ch.get("channel_id", ""),
                    "programs": r["programs"],
                })
                stats["programmes"] += len(r["programs"])
                emit(f"  [{i}/{total}] {name}: {len(r['programs'])} 条")
            else:
                fail += 1
                emit(f"  [{i}/{total}] ⚠️ {name}: {r.get('message', '失败')}")
            time.sleep(0.3)

        # ── JSON + 输出 ──
        emit(f"3. 同步 JSON + 生成 sctv/zte.m3u（成功 {ok} / 失败 {fail}）...")
        sync = zte_sync_to_server(all_epg, cfg)
        emit("✅ " + sync.get("message", "JSON 已写入"))
        if config.get_active_platform() == "zte":
            out = zte_generate_sctv_files(channels, cfg)
            emit("✅ " + (out.get("message") or "输出文件已生成"))
        else:
            emit("⚠️ 平台已切换，跳过 sctv / zte.m3u 生成")

        # ── XML ──
        if config.get_active_platform() != "zte":
            emit("⚠️ 平台已切换到其它平台，跳过 XML 重建")
            stats.update({"success": True, "channels": total, "ok": ok, "fail": fail,
                          "message": f"成功 {ok} / 失败 {fail}（平台已切换，未重建 XML）"})
            return stats

        emit("4. 正在重建 epg.xml / 平台 XML ...")
        try:
            from . import xml_builder
            main_xml = xml_builder.build_xml(log_callback=emit, platform="zte")
            emit(f"✅ epg.xml: {main_xml.get('channel_count', 0)} 频道 / "
                 f"{main_xml.get('programme_count', 0)} 节目")
            platform_xml = xml_builder.build_telecom_xml(log_callback=emit, platform="zte")
            if platform_xml.get("success"):
                emit(f"✅ {config.platform_xml_name('zte')}: "
                     f"{platform_xml.get('channel_count', 0)} 频道 / "
                     f"{platform_xml.get('programme_count', 0)} 节目")
            else:
                emit(f"⚠️ 平台 XML 跳过: {platform_xml.get('error', '')}")
        except Exception as e:
            emit(f"⚠️ XML 生成失败: {e}")

        stats.update({"success": True, "channels": total, "ok": ok, "fail": fail,
                      "message": f"成功 {ok} / 失败 {fail}"})
        emit(f"===== 全部完成: 成功 {ok} / 失败 {fail} =====")
        return stats
    except Exception as e:
        emit(f"❌ 执行异常: {e}")
        stats["message"] = f"执行异常: {e}"
        return stats


def _auto_merge_into_epg(channels: List[Dict[str, Any]], emit) -> None:
    try:
        if not config.get_settings().get("telecom_auto_add_to_epg", True):
            return
        merged = config.merge_telecom_channels_into_epg(channels, "zte")
        if merged.get("added"):
            emit(f"已自动加入 {merged['added']} 个频道到「EPG 管理」（共 {merged['total']} 个）")
    except Exception as e:
        logger.warning(f"[中兴] 自动并入 EPG 管理失败: {e}")
