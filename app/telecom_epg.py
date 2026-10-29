# -*- coding: utf-8 -*-
"""电信 EPG 抓取核心模块（华为平台）

要点：
- 频道名统一用 config.safe_filename()
- telecom_sync_to_server 写 JSON 用原子写（tmp + replace）
- 敏感字段脱敏日志；HTTP 明文警告；指数退避；调试落盘开关
- 平台相关：本文件只实现华为平台；中兴平台在 zte_epg.py
"""
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List
from urllib.parse import urlparse, quote

import requests

from . import config
from .channel_groups import apply_group_rules as _apply_group_rules
from .logger import logger
from .crypt_1 import PRPCrypt

# 电信专用 logger
telecom_logger = logging.getLogger(__name__)
telecom_logger.setLevel(logging.INFO)

TELECOM_CONFIG_KEY = "telecom"


def _mask(s: Any, keep: int = 3) -> str:
    """敏感字符串脱敏：保留前 keep 位。"""
    s = str(s or "")
    if not s:
        return ""
    return s[:keep] + "*" * max(0, len(s) - keep)


# ════════════════════════════════════════════════════════════════
# 配置读写
# ════════════════════════════════════════════════════════════════

def get_telecom_config() -> Dict[str, Any]:
    """华为平台配置（settings.telecom）。"""
    return config.get_platform_config("huawei")


def save_telecom_config(config_data: Dict[str, Any]) -> bool:
    try:
        return config.save_platform_config("huawei", config_data)
    except Exception as e:
        logger.error(f"保存电信配置失败: {e}")
        return False


def telecom_platform_status() -> Dict[str, Any]:
    """华为平台状态摘要（供 /api/telecom/status 与前端使用）。"""
    cfg = get_telecom_config()
    cached = config.get_telecom_channels()
    return {
        "platform": "huawei",
        "platform_label": config.platform_label("huawei"),
        "enabled": bool(cfg.get("enabled", False)),
        "configured": bool(cfg.get("UserID") and cfg.get("mac") and cfg.get("STBID")),
        "config_error": _auth_config_error(cfg),
        "AuthenticationIP": cfg.get("AuthenticationIP", ""),
        "channels": len(cached.get("channels", []) or []),
        "updated": cached.get("updated", ""),
    }


# ════════════════════════════════════════════════════════════════
# 连接测试
# ════════════════════════════════════════════════════════════════

def test_telecom_connection(config_data: Dict[str, Any]) -> Dict[str, Any]:
    result = {"success": False, "message": "", "details": ""}
    try:
        auth_ip = config_data.get("AuthenticationIP", "http://182.138.3.142:8082")
        user_id = config_data.get("UserID", "")
        if not user_id:
            result["message"] = "请先配置 UserID"
            return result

        if auth_ip.startswith("http://"):
            logger.warning("[安全] 电信认证使用明文 HTTP，凭据可能被窃听")

        url = f"{auth_ip}/EDS/jsp/AuthenticationURL?UserID={user_id}&Action=Login"
        headers = {
            "User-Agent": config_data.get("UserAgent", "Mozilla/5.0"),
            "X-Requested-With": "com.huawei.iptv",
        }
        resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        host = urlparse(resp.url).netloc
        result["success"] = True
        result["message"] = f"连接成功，解析到服务器地址: {host}"
        result["host"] = host
        result["details"] = resp.text[:500]
    except requests.exceptions.Timeout:
        result["message"] = "连接超时，请检查服务器地址和网络"
    except requests.exceptions.ConnectionError:
        result["message"] = "连接失败，请检查服务器地址是否正确"
    except Exception as e:
        result["message"] = f"测试失败: {e}"
        logger.error(f"电信连接测试失败: {e}")
    return result


# ════════════════════════════════════════════════════════════════
# 认证
# ════════════════════════════════════════════════════════════════

def _auth_config_error(config_data: Dict[str, Any]) -> str:
    """认证前置校验：返回错误文案，空串表示可以继续。

    规则：
    - 配了 key：现场用 3DES 加密生成 Authenticator（推荐，每次会话都重新算）
    - 没配 key：退化成使用配置里那串静态 Authenticator（抓包得到的，可能过期）
    - auth_require_key=True 时不允许退化，没 key 直接拒绝
    """
    key = str(config_data.get("key", "") or "").strip()
    if bool(config_data.get("auth_require_key", False)) and not key:
        return ("未配置加密 key（已勾选「必须用 key 生成 Authenticator」）；"
                "可在配置弹窗点「🔍 反推 key」，或取消该勾选")
    missing = []
    for label, field in (("UserID", "UserID"), ("mac", "mac"), ("STBID", "STBID")):
        if not config_data.get(field, ""):
            missing.append(label)
    if not key and not config_data.get("Authenticator", ""):
        missing.append("key 或 Authenticator")
    if missing:
        return "缺少必要配置项: " + ", ".join(missing)
    return ""


def telecom_auth(config_data: Dict[str, Any]) -> Dict[str, Any]:
    result = {"success": False, "message": "", "host": "",
              "cookies": {}, "user_token": "", "stbid": ""}

    key = str(config_data.get("key", "") or "").strip()
    user_id = config_data.get("UserID", "")
    mac = config_data.get("mac", "")
    stbid_config = config_data.get("STBID", "")
    ip = config_data.get("ip", "")
    auth_string = config_data.get("Authenticator", "")

    err = _auth_config_error(config_data)
    if err:
        result["message"] = err
        logger.warning(f"电信认证未执行: {err}")
        return result

    AuthenticationIP = config_data.get("AuthenticationIP", "http://182.138.3.142:8082")
    UserAgent = config_data.get("UserAgent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    STBType = config_data.get("STBType", "")
    STBVersion = config_data.get("STBVersion", "")
    templateName = config_data.get("templateName", "")
    areaId = config_data.get("areaId", "")
    X_Requested_With = "com.huawei.iptv"

    if AuthenticationIP.startswith("http://"):
        logger.warning(
            f"[安全] 电信认证使用明文 HTTP（{AuthenticationIP}），"
            f"UserID/mac/Authenticator 可能被中间人窃取；建议走内网/VPN"
        )

    logger.info(f"电信认证: UserID={_mask(user_id)} stbid={_mask(stbid_config)} mac={_mask(mac)}")

    for attempt in range(3):
        try:
            url = f"{AuthenticationIP}/EDS/jsp/AuthenticationURL?UserID={user_id}&Action=Login"
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "User-Agent": UserAgent,
                "X-Requested-With": X_Requested_With,
            }
            logger.info(f"电信认证尝试 {attempt + 1}/3: 获取认证URL")
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            host = urlparse(response.url).netloc
            logger.info(f"解析到服务器地址: {host}")

            auth_url = f"http://{host}/EPG/jsp/authLoginHWCTC.jsp"
            auth_headers = {
                "User-Agent": UserAgent,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"http://{host}/EPG/jsp/AuthenticationURL?UserID={user_id}&Action=Login",
                "X-Requested-With": X_Requested_With,
            }
            auth_response = requests.post(
                auth_url, headers=auth_headers,
                data={"UserID": user_id, "VIP": ""}, timeout=30)
            auth_response.raise_for_status()

            encrypt_match = re.search(r'var EncryptToken = "([^"]+)"', auth_response.text)
            if not encrypt_match:
                raise ValueError("无法找到EncryptToken")
            EncryptToken = encrypt_match.group(1)

            user_token_match = re.search(r'document\.authform\.userToken\.value = "([^"]+)"', auth_response.text)
            if not user_token_match:
                raise ValueError("无法找到userToken")
            userToken = user_token_match.group(1)

            if key:
                pc = PRPCrypt(key)
                auth_str = f"{key}${EncryptToken}${user_id}${stbid_config}${ip}${mac}$$CTC"
                Authenticator = pc.encrypt(auth_str)
                logger.info("使用 key 加密生成 Authenticator")
            else:
                Authenticator = auth_string
                logger.warning(
                    "未配置加密 key：本次直接用配置里那串静态 Authenticator（抓包值，可能过期/绑设备）；"
                    "要强制每次现场加密，请在配置里勾选「必须用 key 生成 Authenticator」")

            valid_url = f"http://{host}/EPG/jsp/ValidAuthenticationHWCTC.jsp"
            valid_headers = {
                "User-Agent": UserAgent,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"http://{host}/EPG/jsp/authLoginHWCTC.jsp",
                "X-Requested-With": X_Requested_With,
            }
            valid_data = {
                "UserID": user_id, "Lang": "0", "SupportHD": "1", "NetUserID": "",
                "Authenticator": Authenticator, "STBType": STBType, "STBVersion": STBVersion,
                "conntype": "dhcp", "STBID": stbid_config, "templateName": templateName,
                "areaId": areaId, "userToken": userToken, "userGroupId": "1",
                "productPackageId": "", "mac": mac, "UserField": "", "SoftwareVersion": "",
                "IsSmartStb": "", "desktopId": "", "stbmaker": "", "XMPPCapability": "",
                "ChipID": "", "VIP": "",
            }
            valid_response = requests.post(
                valid_url, headers=valid_headers, data=valid_data, timeout=30)
            valid_response.raise_for_status()

            cookies = valid_response.cookies
            logger.info(f"认证成功, JSESSIONID: {_mask(cookies.get('JSESSIONID'), 6)}")

            user_token = stbid = None
            if valid_response.headers.get("Content-Type", "").startswith("text/html"):
                html_content = valid_response.text
                ut_match = re.search(r'name="UserToken"\s*value="([^"]+)"', html_content)
                user_token = ut_match.group(1) if ut_match else None
                sb_match = re.search(r'name="stbid"\s*value="([^"]+)"', html_content)
                stbid = sb_match.group(1) if sb_match else None
                if user_token and stbid:
                    logger.info("提取到 UserToken 和 stbid")

            result.update({
                "success": True,
                "message": "认证成功",
                "host": host,
                "cookies": {k: v for k, v in cookies.items()},
                "user_token": user_token or "",
                "stbid": stbid or "",
            })

            # 用 key 现场算出来的 Authenticator 写回配置：
            # 这样「key」和「Authenticator」两个字段都保持最新，不用再手工粘贴。
            if key:
                try:
                    latest = get_telecom_config()
                    if (latest.get("Authenticator") != Authenticator
                            or latest.get("key") != key):
                        latest["Authenticator"] = Authenticator
                        latest["key"] = key
                        save_telecom_config(latest)
                        logger.info("已把本次生成的 Authenticator 写回配置")
                except Exception as e:
                    logger.warning(f"写回 Authenticator 失败: {e}")

            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"电信认证请求失败: {_mask(e, 200)}")
            if attempt < 2:
                delay = 2 ** attempt
                logger.info(f"{delay} 秒后重试...")
                time.sleep(delay)
            continue
        except Exception as e:
            logger.error(f"电信认证异常: {_mask(e, 200)}")
            if attempt < 2:
                time.sleep(2 ** attempt)
            continue

    result["message"] = "认证失败，请检查配置和网络"
    return result


# ════════════════════════════════════════════════════════════════
# 获取频道列表
# ════════════════════════════════════════════════════════════════

def telecom_get_channels(host: str, cookies: dict, user_token: str, stbid: str,
                         config_data: Dict[str, Any]) -> Dict[str, Any]:
    result = {"success": False, "channels": [], "message": ""}
    UserAgent = config_data.get("UserAgent", "Mozilla/5.0")
    X_Requested_With = "com.huawei.iptv"
    user_id = config_data.get("UserID", "")

    try:
        channel_url = f"http://{host}/EPG/jsp/getchannellistHWCTC.jsp"
        channel_data = {
            "conntype": "dhcp", "UserToken": user_token, "tempKey": "",
            "stbid": stbid, "SupportHD": "1", "UserID": user_id, "Lang": "1",
        }
        headers = {
            "User-Agent": UserAgent,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"http://{host}/EPG/jsp/ValidAuthenticationHWCTC.jsp",
            "X-Requested-With": X_Requested_With,
        }
        logger.info("正在获取电信频道列表...")
        response = requests.post(channel_url, headers=headers, cookies=cookies,
                                 data=channel_data, timeout=30)
        response.raise_for_status()

        # 仅调试时落盘
        if os.environ.get("TELECOM_DEBUG_DUMP") == "1":
            try:
                log_dir = config.LOG_DIR
                log_dir.mkdir(parents=True, exist_ok=True)
                dump_path = log_dir / "getchannellistHWCTC.jsp"
                dump_path.write_text(response.text, encoding="utf-8")
                logger.info(f"频道列表源文件已保存至: {dump_path}")
            except Exception as e:
                logger.error(f"保存频道列表源文件失败: {e}")

        pattern = re.compile(
            r'ChannelID\=\"(\d+)\",'
            r'ChannelName\=\"(.+?)\",'
            r'UserChannelID\=\"(\d+)\",'
            r'ChannelURL=\"igmp://(.+?)\".+?'
            r'TimeShift\=\"(\d+)\",'
            r'TimeShiftLength\=\"(\d+)\".+?,'
            r'TimeShiftURL\=\"(.+?)\".+?'
            r'FCCEnable\=\"(\d+)\",'
            r'ChannelFCCIP=\"(.*?)\",'
            r'ChannelFCCPort=\"(.*?)\",'
            r'ChannelFECPort=\"(.*?)\"'
        )
        channels = pattern.findall(response.text)

        def clean_channel_name(name):
            name = name.strip()
            for suffix in ("高清", "HD", "FHD", "UHD"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)].rstrip()
            name = name.replace("-", "")
            return name

        # 过滤掉包含"画中画"和"单音轨"的无效频道
        channels = [ch for ch in channels if "画中画" not in ch[1] and "单音轨" not in ch[1]]

        # ★ 新增映射字典：原代号 -> {显示名, tvg-id}
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

        formatted = []
        for ch in channels:
            orig_name = clean_channel_name(ch[1])
            display_name = orig_name
            tvg_id = orig_name
            
            # 如果命中映射字典，替换中文名并保留固定 tvg_id
            if orig_name in NAME_MAP:
                display_name = NAME_MAP[orig_name]["name"]
                tvg_id = NAME_MAP[orig_name]["tvg_id"]

            # 截断 TimeShiftURL
            rtsp_raw = ch[6]
            if rtsp_raw:
                if ".smil?" in rtsp_raw:
                    rtsp_raw = rtsp_raw.split(".smil?")[0] + ".smil"
                elif "?" in rtsp_raw:
                    rtsp_raw = rtsp_raw.split("?")[0]

            formatted.append({
                "channel_id": ch[0], "name": display_name, "tvg_id": tvg_id, # ★ 新增 tvg_id
                "user_channel_id": ch[2], "channel_url": ch[3],
                "timeshift": ch[4], "timeshift_length": ch[5], 
                "rtsp_url": rtsp_raw,
                "fcc_enable": ch[7], "fcc_ip": ch[8], "fcc_port": ch[9], "fec_port": ch[10],
            })

        result["success"] = True
        result["channels"] = formatted
        result["message"] = f"成功获取 {len(formatted)} 个频道"
        logger.info(f"获取到 {len(formatted)} 个频道")

    except Exception as e:
        result["message"] = f"获取频道列表失败: {e}"
        logger.error(f"获取频道列表失败: {e}")
    return result


# ════════════════════════════════════════════════════════════════
# 下载单个频道的 EPG
# ════════════════════════════════════════════════════════════════

def telecom_download_epg(host, cookies, user_token, stbid,
                         channel_id, channel_name, dates, config_data):
    result = {"success": False, "programs": [], "message": ""}
    try:
        epg_url = f"http://{host}/EPG/jsp/tools/aged/getTvodData.jsp?channelId={channel_id}"
        headers = {
            "User-Agent": config_data.get("UserAgent", "Mozilla/5.0"),
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"http://{host}/EPG/jsp/tools/aged/getTvodData.jsp",
            "X-Requested-With": "com.huawei.iptv",
        }
        data = {
            "conntype": "", "UserToken": user_token, "tempKey": "",
            "stbid": stbid, "SupportHD": "1",
            "UserID": config_data.get("UserID", ""), "Lang": "1",
        }
        # ★ 电信服务器偶发超时，单次失败直接丢节目。照认证流程一样做 3 次退避重试。
        resp = None
        for attempt in range(3):
            try:
                resp = requests.post(epg_url, headers=headers, cookies=cookies,
                                     data=data, timeout=30)
                resp.raise_for_status()
                break
            except requests.exceptions.RequestException:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
        if resp is None:
            result["message"] = "下载EPG失败: 重试耗尽"
            return result

        match = re.search(r'parent\.jsonBackLookStr\s*=\s*(\[.*?\]);', resp.text, re.DOTALL)
        if not match:
            result["message"] = "未找到EPG数据"
            return result
        epg_data = json.loads(match.group(1))
        if not isinstance(epg_data, list) or len(epg_data) < 2:
            result["message"] = "EPG数据格式错误"
            return result

        all_programs = []
        for programs in epg_data[1]:
            if not isinstance(programs, list):
                continue
            for p in programs:
                if not isinstance(p, dict):
                    continue
                begin = p.get("beginTimeFormat", "")
                end = p.get("endTimeFormat", "")
                if not begin or not end:
                    continue
                name = str(p.get("programName", "未知节目"))
                name = name.replace("<", "《").replace(">", "》").replace("&", "&amp;")
                all_programs.append({
                    "begin_time": begin, "end_time": end, "program_name": name,
                })

        result["success"] = True
        result["programs"] = all_programs
        result["message"] = f"成功下载 {len(all_programs)} 条节目"
    except Exception as e:
        result["message"] = f"下载EPG失败: {e}"
    return result


# ════════════════════════════════════════════════════════════════
# 时间格式化
# ════════════════════════════════════════════════════════════════

def format_xmltv_time(dt_str: str) -> str:
    if not dt_str:
        return ""
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(dt_str, fmt).strftime("%Y%m%d%H%M%S") + " +0800"
        except ValueError:
            continue
    return dt_str


# ════════════════════════════════════════════════════════════════
# rtp2httpd 的 r2h-token
# ════════════════════════════════════════════════════════════════

def append_r2h_token(url: str, token: str) -> str:
    """给 rtp2httpd 的地址追加 `r2h-token=<token>`。

    - token 留空 → 原样返回（不填就不加，符合“不填时 URL 不带 token”）
    - URL 自带 ?fcc=... 时用 & 连接，没有 ? 时用 ? 连接
    - 已经有 r2h-token= 的不重复加
    """
    token = str(token or "").strip()
    if not token or not url:
        return url
    if "r2h-token=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}r2h-token={quote(token, safe='')}"


# ════════════════════════════════════════════════════════════════
# 生成 M3U
# ════════════════════════════════════════════════════════════════

def telecom_generate_m3u(channels: List[Dict], config_data: Dict[str, Any]) -> str:
    rtp2httpd = config_data.get("RTP2HTTPD", "").rstrip("/")
    epg_server_url = str(config_data.get("epg_server_url", "") or "").rstrip("/")
    epgname = str(config_data.get("epgname", "") or "epg.xml").lstrip("/")
    logo_url = config_data.get("logo_url", "")
    # ★ 新增：r2h-token 配置
    r2h_token = str(config_data.get("r2h_token", "") or "").strip()

    # ★ 新增：提取开关和规则
    enable_group = config_data.get("enable_group", False)
    group_rules = config.get_group_rules() if enable_group else None

    lines = []
    epg_url = f"{epg_server_url}/{epgname}" if epg_server_url else ""
    lines.append(f'#EXTM3U name="电信IPTV" x-tvg-url="{epg_url}"')

    logo_base = (logo_url or epg_server_url or "").rstrip("/")

    for ch in channels:
        name = ch.get("name", "")
        channel_url = ch.get("channel_url", "")
        rtsp_raw = ch.get("rtsp_url", "").strip()

        play_url = f"{rtp2httpd}/rtp/{channel_url}" if rtp2httpd and channel_url else channel_url

        fcc_enable = ch.get("fcc_enable", "0")
        fec_port = ch.get("fec_port", "")
        if play_url and fcc_enable == "2":
            fcc_ip = ch.get("fcc_ip", "")
            fcc_p = ch.get("fcc_port", "")
            if fcc_ip:
                play_url = f"{play_url}?fcc={fcc_ip}:{fcc_p}"
            if fec_port and fec_port != "0":
                play_url = f"{play_url}&fec={fec_port}"

        # ★ 处理回看地址回看（catchup-source）：playseek + r2h-seek-mode + r2h-token
        rtsp_url = None
        if rtsp_raw.startswith("rtsp://"):
            if "?" in rtsp_raw:
                rtsp_raw = rtsp_raw.split("?")[0]
            prefix = f"{rtp2httpd}/rtsp/" if rtp2httpd else "rtsp://"
            rtsp_url = rtsp_raw.replace("rtsp://", prefix)
            rtsp_url += "?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"

        # ★ r2h-token：配了才加，没配原样
        play_url = append_r2h_token(play_url, r2h_token)
        if rtsp_url:
            rtsp_url = append_r2h_token(rtsp_url, r2h_token)

        tvg_logo = config.logo_link(logo_base, epg_server_url, name)
        attrs = f'tvg-logo="{tvg_logo}" tvg-id="{name}" tvg-name="{name}"'

        # ★ 新增：插入分组
        if enable_group:
            g = _apply_group_rules(name, group_rules)
            if g:
                attrs += f' group-title="{g}"'

        if rtsp_url:
            attrs += f' catchup="default" catchup-source="{rtsp_url}"'
            
        lines.append(f'#EXTINF:-1 {attrs}, {name}')
        lines.append(f"{play_url}")
        lines.append("")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 同步到本地 JSON
# ════════════════════════════════════════════════════════════════

def telecom_sync_to_server(epg_data, config_data):
    """把电信 EPG 数据写到 JSON_DIR/<safe_name>_telecom_<date>.json。

    文件名统一走 config.safe_filename()；写入用 tmp + replace 原子操作。
    """
    result = {"success": False, "message": "", "saved_files": 0}
    try:
        json_dir = config.JSON_DIR
        json_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for ch in epg_data.get("channels", []):
            name = ch.get("name", "")
            programs = ch.get("programs", [])
            if not programs or not name:
                continue

            date_progs = {}
            for p in programs:
                begin = p.get("begin_time", "")
                if len(begin) < 8:
                    continue
                date_progs.setdefault(begin[:8], []).append({
                    "name": name,
                    "start": format_xmltv_time(begin),
                    "stop": format_xmltv_time(p.get("end_time", "")),
                    "title": p.get("program_name", ""),
                    "desc": "",
                })

            safe = config.safe_filename(name)
            for date_str, progs in date_progs.items():
                fname = f"{safe}{config.TELECOM_EPG_SUFFIX}_{date_str}.json"
                path = json_dir / fname
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(
                    json.dumps({"programmes": progs, "source": "telecom"},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8")
                tmp.replace(path)
                saved += 1

        result.update({
            "success": True,
            "message": f"成功同步 {saved} 个电信EPG文件",
            "saved_files": saved,
        })
    except Exception as e:
        result["message"] = f"同步失败: {e}"
    return result


# ════════════════════════════════════════════════════════════════
# 生成 sctv.txt / sctv.m3u
# ════════════════════════════════════════════════════════════════

def generate_sctv_files(channels, config_data):
    result = {"success": False, "txt_path": "", "m3u_path": "", "message": ""}
    try:
        m3u_dir = config.M3U_DIR
        m3u_dir.mkdir(parents=True, exist_ok=True)
        RTP = config_data.get("RTP2HTTPD", "").rstrip("/")
        epg_server_url = str(config_data.get("epg_server_url", "") or "").rstrip("/")
        epgname = str(config_data.get("epgname", "") or "epg.xml").lstrip("/")
        logo_url = (config_data.get("logo_url") or epg_server_url).rstrip("/")
        r2h_token = str(config_data.get("r2h_token", "") or "").strip()

        # ★ 新增：提取开关和规则
        enable_group = config_data.get("enable_group", False)
        group_rules = config.get_group_rules() if enable_group else None

        epg_url = f"{epg_server_url}/{epgname}" if epg_server_url else ""
        txt_path = m3u_dir / "sctv.txt"
        m3u_path = m3u_dir / "sctv.m3u"

        count = 0
        with open(txt_path, "w", encoding="utf-8", newline="\n") as ftxt, \
             open(m3u_path, "w", encoding="utf-8", newline="\n") as fm3u:
            fm3u.write(f'#EXTM3U name="四川电信" x-tvg-url="{epg_url}"\n')
            for ch in channels:
                name = ch.get("name", "").strip()
                mcast = ch.get("channel_url", "").strip()
                rtsp_raw = ch.get("rtsp_url", "").strip()
                fcc_enable = ch.get("fcc_enable", "0")
                fec_port = ch.get("fec_port", "")
                if not name or not mcast:
                    continue

                url = f"{RTP}/rtp/{mcast}" if RTP else f"rtp://{mcast}"
                append = False
                if fcc_enable == "2":
                    ip = ch.get("fcc_ip", "")
                    pt = ch.get("fcc_port", "")
                    if ip:
                        url += f"?fcc={ip}:{pt}"
                        append = True
                if fec_port and fec_port != "0":
                    url += f"&fec={fec_port}" if append else f"?fec={fec_port}"

                rtsp_url = None
                if rtsp_raw.startswith("rtsp://"):
                    if "?" in rtsp_raw:
                        rtsp_raw = rtsp_raw.split("?")[0]
                    prefix = f"{RTP}/rtsp/" if RTP else "rtsp://"
                    rtsp_url = rtsp_raw.replace("rtsp://", prefix)
                    rtsp_url += "?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"

                # ★ r2h-token：配了才加（直播 + 回看都加），没配原样
                url = append_r2h_token(url, r2h_token)
                if rtsp_url:
                    rtsp_url = append_r2h_token(rtsp_url, r2h_token)

                # 计算分组
                group_str = ""
                if enable_group:
                    g = _apply_group_rules(name, group_rules)
                    group_str = f",{g}" if g else ""

                # 写入 TXT
                if rtsp_url:
                    ftxt.write(f"{name},{rtsp_url}#{url}{group_str}\n")
                else:
                    ftxt.write(f"{name},{url}{group_str}\n")

                # 写入 M3U
                tvg_logo = config.logo_link(logo_url, epg_server_url, name)
                tvg_id_str = ch.get("tvg_id", name) or name
                attrs = f'tvg-logo="{tvg_logo}" tvg-id="{tvg_id_str}" tvg-name="{name}"'
                
                if enable_group:
                    g = _apply_group_rules(name, group_rules)
                    if g:
                        attrs += f' group-title="{g}"'

                if rtsp_url:
                    attrs += f' catchup="default" catchup-source="{rtsp_url}"'
                fm3u.write(f"#EXTINF:-1 {attrs}, {name}\n{url}\n")
                count += 1

        result.update({
            "success": True,
            "txt_path": str(txt_path),
            "m3u_path": str(m3u_path),
            "count": count,
            "message": f"已生成 {count} 个频道",
        })
    except Exception as e:
        result["message"] = f"生成失败: {e}"
    return result