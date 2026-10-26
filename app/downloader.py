"""下载模块 - 处理 JSON 节目数据和外部 EPG 下载

设计要点：
- 全局 AsyncClient 单例，复用连接池
- download_json / download_epg_source 采用流式读取 + 大小上限 + 原子写
- 支持 SSRF 校验：由环境变量 IPTV_EPG_ALLOW_PRIVATE 控制
  （默认 "1" 允许内网，兼容家庭自建 EPG 源；设为 "0" 则阻断内网）
"""
import asyncio
import datetime
import gzip
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from . import config
from .logger import logger

# 单文件大小上限（防 OOM）
_JSON_MAX_BYTES = 50 * 1024 * 1024       # JSON：50MB
_EPG_MAX_BYTES = 200 * 1024 * 1024       # EPG XML（解压后）：200MB

# 内网/回环/元数据网段（严格模式下阻断）
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),      # 云元数据 169.254.169.254
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# 全局 HTTP 客户端
_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    """获取（或懒创建）全局 AsyncClient。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            # 连接超时 10s：源不可达时快速失败，不用干等 30s
            # 读取超时 60s：EPG 大文件下载留足时间
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
            },
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _client


async def close_client():
    """关闭全局客户端（应用 shutdown 时调用）。"""
    global _client
    if _client:
        await _client.aclose()
        _client = None


# ════════════════════════════════════════════════════════════════
# SSRF 校验
# ════════════════════════════════════════════════════════════════

def _is_safe_http_url(url: str) -> bool:
    """URL 安全校验。

    - 环境变量 IPTV_EPG_ALLOW_PRIVATE 默认 "1"：仅校验协议（兼容内网自建源）
    - 设为 "0"：解析 DNS，阻断指向内网/回环/元数据地址的 URL（严格 SSRF 防护）

    无论哪种模式，都只允许 http/https 协议。
    """
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https"):
            return False
        if not u.hostname:
            return False
    except Exception:
        return False

    # 默认允许内网：仅做协议校验
    if os.environ.get("IPTV_EPG_ALLOW_PRIVATE", "1") == "1":
        return True

    # 严格模式：解析所有 A/AAAA 记录，任一落在内网即拒绝
    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if any(ip in net for net in _PRIVATE_NETS):
                return False
        return True
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def replace_date_in_url(url: str, date_str: str) -> str:
    """将 URL 中的 {date} 占位符或 _YYYYMMDD.json 替换为目标日期。"""
    url = url.replace("{date}", date_str)
    url = re.sub(r'_(\d{8})\.json$', f'_{date_str}.json', url)
    return url


async def _read_limited(resp: httpx.Response, max_bytes: int) -> Optional[bytes]:
    """流式读取响应内容，超限返回 None。"""
    chunks = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def download_json(url: str, save_path: Path) -> dict:
    """下载单个 JSON 文件，返回结果状态。"""
    result = {"success": False, "url": url, "path": str(save_path), "error": ""}
    if not _is_safe_http_url(url):
        result["error"] = "URL 不允许（协议非法或指向内网）"
        return result

    try:
        client = get_client()
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content = await _read_limited(resp, _JSON_MAX_BYTES)
            if content is None:
                result["error"] = f"文件超过 {_JSON_MAX_BYTES // 1024 // 1024}MB 上限"
                return result

        text = content.decode("utf-8", errors="replace")
        # 校验 JSON 合法后再写盘
        json.loads(text)

        save_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = save_path.with_name(save_path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        tmp.replace(save_path)

        result["success"] = True
        result["size"] = save_path.stat().st_size
    except httpx.HTTPStatusError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except httpx.RequestError as e:
        result["error"] = f"请求失败: {e}"
    except json.JSONDecodeError as e:
        result["error"] = f"JSON 解析失败: {e}"
    except Exception as e:
        result["error"] = f"未知错误: {e}"
    return result


async def download_channel_json(
    channel: dict,
    dates: list,
    progress_callback: Optional[Callable] = None,
    log_callback: Optional[Callable] = None,
) -> list:
    """下载某频道的指定日期 JSON，返回结果列表。"""
    results = []
    name = channel.get("name", "未知")
    url_template = channel.get("url", "")
    enabled = channel.get("enabled", True)

    if not enabled:
        return results

    for date_str in dates:
        url = replace_date_in_url(url_template, date_str)
        save_name = f"{name}_{date_str}.json"
        save_path = config.JSON_DIR / save_name

        today = datetime.date.today()
        try:
            file_date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            file_date = None
        is_recent = (file_date is not None and file_date >= today)

        # 过往日期已存在则跳过
        if save_path.exists() and not is_recent:
            msg = f"[下载] 跳过 {name} {date_str} 已存在"
            results.append({"success": True, "skipped": True, "url": url,
                            "path": str(save_path), "message": msg})
            if log_callback:
                log_callback(msg)
            if progress_callback:
                await progress_callback(msg)
            continue

        msg = f"[下载] {name} {date_str}"
        if log_callback:
            log_callback(msg)
        if progress_callback:
            await progress_callback(msg)

        result = await download_json(url, save_path)
        result["channel_name"] = name
        result["date"] = date_str
        results.append(result)

        if result["success"]:
            msg2 = f"[下载] 完成 {name} {date_str} ✓"
        else:
            msg2 = f"[失败] {name} {date_str} ✗ {result.get('error', '')}"
        if log_callback:
            log_callback(msg2)
        if progress_callback:
            await progress_callback(msg2)

        await asyncio.sleep(config.DOWNLOAD_DELAY)

    return results


async def download_all_channels(dates: list,
                                progress_callback=None,
                                log_callback=None) -> list:
    """下载所有已启用频道的 JSON。"""
    channels = config.get_channels()
    all_results = []
    total = len(channels)
    for idx, ch in enumerate(channels, 1):
        if not ch.get("enabled", True):
            continue
        msg = f"[进度] ({idx}/{total}) 处理: {ch.get('name', '未知')}"
        if progress_callback:
            await progress_callback(msg)
        if log_callback:
            log_callback(msg)
        results = await download_channel_json(ch, dates, progress_callback, log_callback)
        all_results.extend(results)
    return all_results


def clean_cache_name(name: str) -> str:
    """把用户填的保存名清洗成安全的 .xml 文件名（空串表示没填）。"""
    name = re.sub(r'[^\w\-_.]', '_', str(name or '')).strip(' .')
    if name.endswith('.gz'):
        name = name[:-3]
    if name and not name.endswith('.xml'):
        name += '.xml'
    return name[:80]


def epg_cache_name(source: dict) -> str:
    """这个 EPG 源在 epg_cache 目录里的文件名（不会再“同名覆盖”）。

    - 源里填了 cache_name → 用它（用户自己负责别撞名）
    - 否则自动：`域名_文件名_短哈希.xml`
      例如 https://epg.gotonas.com/txml.gz → epg.gotonas.com_txml_1a2b3c.xml
      同样是 txml.gz 的另一个域名会得到另一个哈希，两个源不会互相覆盖；
      同一个 URL 每次都是同一个名字，重复下载只会原地更新。
    """
    url = str(source.get("url", "") or "")
    custom = clean_cache_name(source.get("cache_name", ""))
    if custom:
        return custom

    raw = url.split("/")[-1] or "epg.xml"
    raw = re.sub(r'[^\w\-_.]', '_', raw)
    if raw.endswith(".gz"):
        raw = raw[:-3]
    if not raw.lower().endswith(".xml"):
        raw += ".xml"
    stem = raw[:-4] or "epg"

    host = (urlparse(url).netloc or "source").replace(":", "_")
    host = re.sub(r'[^\w\-_.]', '_', host)[:40].strip('._') or "source"
    digest = hashlib.md5((url or stem).encode("utf-8")).hexdigest()[:6]
    return f"{host}_{stem}_{digest}.xml"[:120]


def remove_legacy_cache(url: str, keep_name: str) -> list:
    """下载成功后清掉旧版本按 URL basename 命名的缓存（如 txml.xml）。

    旧命名会让两个同名源共用一个文件，而且新旧同时存在时合并会把同一份
    数据算两遍，所以下到新名字后就地清掉。返回被删掉的文件名。
    """
    stem = (url or "").split("/")[-1].replace(".gz", "").replace(".xml", "")
    stem = re.sub(r'[^\w\-_.]', '_', stem).strip(' .')
    if not stem:
        return []
    if keep_name in (f"{stem}.xml", f"{stem}.xml.disabled"):
        return []
    removed = []
    for old in (config.CONFIG_DIR / "epg_cache").glob(f"{stem}.*xml"):
        if old.name in (keep_name, keep_name + ".disabled"):
            continue
        try:
            old.unlink()
            removed.append(old.name)
        except OSError:
            pass
    return removed


def set_source_cache_enabled(source: dict, enabled: bool) -> str:
    """启用/禁用源时，把它的缓存文件挂起或恢复。

    禁用 = 改名成 `<名字>.xml.disabled`：搜索频道、提取节目、合并 EPG 都是
    按 `*.xml` 扫目录的，改个后缀就彻底不参与，重新启用再改回来。
    不删数据，也不影响别的源。
    """
    name = epg_cache_name(source)
    cache_dir = config.CONFIG_DIR / "epg_cache"
    active = cache_dir / name
    parked = cache_dir / (name + ".disabled")
    try:
        if enabled:
            if parked.is_file() and not active.is_file():
                parked.replace(active)
            return str(active) if active.is_file() else ""
        if active.is_file():
            active.replace(parked)
            return str(parked)
        return str(parked) if parked.is_file() else ""
    except OSError as e:
        logger.warning(f"切换 EPG 源缓存状态失败: {e}")
        return ""


async def download_epg_source(source: dict,
                              progress_callback=None,
                              log_callback=None,
                              index: int = 0,
                              total: int = 1) -> Optional[dict]:
    """下载外部 EPG 源文件（支持 gzip 自动解压）。"""
    url = source.get("url", "")
    rule = source.get("rule", "auto")
    if not url:
        return None
    if not _is_safe_http_url(url):
        msg = f"[EPG] ({index}/{total}) URL 不允许: {url}"
        if log_callback:
            log_callback(msg)
        return {"url": url, "rule": rule, "success": False, "error": "URL 不允许", "path": ""}

    result = {"url": url, "rule": rule, "success": False, "error": "", "path": ""}

    try:
        # ★ 保存名：源里填了 cache_name 就用它，否则按「域名_文件名_短哈希」，
        #   两个源都叫 txml.gz 也不会互相覆盖（旧版本会直接盖掉）
        save_name = epg_cache_name(source)

        msg = f"[下载] EPG源({index}/{total}) {url} → {save_name}"
        if log_callback:
            log_callback(msg)
        if progress_callback:
            await progress_callback(msg)

        client = get_client()
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            raw = await _read_limited(resp, _EPG_MAX_BYTES)
            if raw is None:
                result["error"] = f"文件超过 {_EPG_MAX_BYTES // 1024 // 1024}MB 上限"
                return result

        # gzip 自动解压
        if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
            try:
                buf = io.BytesIO(raw)
                with gzip.GzipFile(fileobj=buf) as gz:
                    raw = gz.read()
            except Exception:
                pass  # 不是有效 gzip，按原样处理

        xml_text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")

        save_path = config.CONFIG_DIR / "epg_cache" / save_name
        save_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = save_path.with_name(save_path.name + ".tmp")
        tmp.write_text(xml_text, encoding="utf-8", newline="\n")
        tmp.replace(save_path)

        result["success"] = True
        result["path"] = str(save_path)
        result["content"] = xml_text

        # 清掉旧命名缓存（txml.xml 这种），避免合并时同一份数据算两遍
        legacy = remove_legacy_cache(url, save_name)
        if legacy and log_callback:
            log_callback(f"[下载] 已清理旧命名缓存: {', '.join(legacy)}")

        msg2 = f"[下载] EPG源({index}/{total}) 完成 {url} ✓"
        if log_callback:
            log_callback(msg2)
        if progress_callback:
            await progress_callback(msg2)

    except httpx.HTTPStatusError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except httpx.TimeoutException:
        result["error"] = "超时：服务器 30 秒内没有响应（网络不通或该源太慢）"
    except httpx.RequestError as e:
        detail = str(e) or type(e).__name__
        result["error"] = f"请求失败: {detail}"
    except Exception as e:
        result["error"] = f"未知错误: {e}"

    if result.get("error"):
        msg3 = f"[EPG] ({index}/{total}) 失败: {url} ✗ {result['error']}"
        if log_callback:
            log_callback(msg3)
        if progress_callback:
            await progress_callback(msg3)

    return result


async def download_all_epg_sources(progress_callback=None, log_callback=None) -> list:
    """下载所有已启用的外部 EPG 源。"""
    sources = config.get_epg_sources()
    results = []
    enabled = [s for s in sources if s.get("enabled", True)]
    total = len(enabled)
    for idx, src in enumerate(enabled, 1):
        r = await download_epg_source(src, progress_callback, log_callback, idx, total)
        if r:
            results.append(r)
    return results


def cleanup_old_files(log_callback=None) -> list:
    """清理过期文件（JSON 按文件名日期，日志/EPG 缓存按 mtime）。"""
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=6)
    cutoff_str = cutoff.strftime("%Y%m%d")
    deleted = []

    def do_log(msg):
        if log_callback:
            log_callback(msg)

    # JSON：优先按文件名日期，兜底按 mtime
    for f in config.JSON_DIR.glob("*.json"):
        try:
            m = re.search(r"_(\d{8})\.json$", f.name)
            if m:
                if m.group(1) < cutoff_str:
                    f.unlink()
                    msg = f"[清理] 删除过期 JSON: {f.name} (日期 {m.group(1)})"
                    do_log(msg)
                    deleted.append(msg)
            elif time.time() - f.stat().st_mtime > config.JSON_RETENTION_DAYS * 86400:
                f.unlink()
                msg = f"[清理] 删除过期 JSON(按mtime): {f.name}"
                do_log(msg)
                deleted.append(msg)
        except OSError:
            pass

    # 日志文件
    for f in config.LOG_DIR.glob("*.log"):
        try:
            if time.time() - f.stat().st_mtime > config.LOG_RETENTION_DAYS * 86400:
                f.unlink()
                msg = f"[清理] 删除过期日志: {f.name}"
                do_log(msg)
                deleted.append(msg)
        except OSError:
            pass

    # EPG 缓存
    epg_cache = config.CONFIG_DIR / "epg_cache"
    if epg_cache.exists():
        for f in epg_cache.glob("*.xml"):
            try:
                if time.time() - f.stat().st_mtime > 7 * 86400:
                    f.unlink()
                    msg = f"[清理] 删除过期EPG缓存: {f.name}"
                    do_log(msg)
                    deleted.append(msg)
            except OSError:
                pass

    if not deleted:
        do_log("[清理] 无需清理")

    return deleted


