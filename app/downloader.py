"""下载模块 - 处理 JSON 节目数据和外部 EPG 下载"""
import asyncio
import datetime
import gzip
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Optional, Callable

import httpx

from . import config
# 全局 HTTP 客户端（带连接池）
_client: Optional[httpx.AsyncClient] = None


def get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
            },
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _client


async def close_client():
    global _client
    if _client:
        await _client.aclose()
        _client = None


def replace_date_in_url(url: str, date_str: str) -> str:
    """将 URL 中的 {date} 或实际日期替换为目标日期"""
    # 先替换 {date} 占位符
    url = url.replace("{date}", date_str)
    # 再匹配 _20260720.json 样式的日期并替换
    url = re.sub(r'_(\d{8})\.json$', f'_{date_str}.json', url)
    return url


def extract_channel_name_from_url(url: str) -> str:
    """从 URL 中提取频道名"""
    # 从保存路径推断：XXX_20260726.json -> XXX
    match = re.search(r'/([^/]+?)_\d{8}\.json$', url)
    if match:
        return match.group(1)
    return "unknown"


async def download_json(url: str, save_path: Path) -> dict:
    """下载单个 JSON 文件，返回结果状态"""
    result = {"success": False, "url": url, "path": str(save_path), "error": ""}
    try:
        client = get_client()
        resp = await client.get(url)
        resp.raise_for_status()
        content = resp.text
        # 验证是否为有效 JSON
        data = json.loads(content)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        # 验证写入
        if save_path.exists() and save_path.stat().st_size > 0:
            result["success"] = True
            result["size"] = save_path.stat().st_size
        else:
            result["error"] = "文件写入后为空"
    except httpx.HTTPStatusError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except httpx.RequestError as e:
        result["error"] = f"请求失败: {str(e)}"
    except json.JSONDecodeError as e:
        result["error"] = f"JSON 解析失败: {str(e)}"
    except Exception as e:
        result["error"] = f"未知错误: {str(e)}"
    return result


async def download_channel_json(
    channel: dict,
    dates: list,
    progress_callback: Optional[Callable] = None,
    log_callback: Optional[Callable] = None,
) -> list:
    """下载某个频道的指定日期 JSON，返回结果列表"""
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

        # 今天/明天/后天：覆盖下载；过往日期：跳过
        today = datetime.date.today()
        try:
            file_date = datetime.datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            file_date = None
        is_recent = (file_date is not None and file_date >= today)

        if save_path.exists() and not is_recent:
            msg = f"[下载] 跳过 {name} {date_str} 已存在"
            results.append({"success": True, "skipped": True, "url": url, "path": str(save_path), "message": msg})
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

        # 延时
        await asyncio.sleep(config.DOWNLOAD_DELAY)

    return results


async def download_all_channels(dates: list, progress_callback=None, log_callback=None) -> list:
    """下载所有已启用频道"""
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


async def download_epg_source(source: dict, progress_callback=None, log_callback=None, index: int = 0, total: int = 1) -> Optional[dict]:
    """下载外部 EPG 源文件"""
    url = source.get("url", "")
    rule = source.get("rule", "auto")
    if not url:
        return None

    result = {"url": url, "rule": rule, "success": False, "error": "", "path": ""}

    try:
        # 生成安全的文件名
        safe_name = re.sub(r'[^\w\-_.]', '_', url.split("/")[-1])
        if not safe_name:
            safe_name = f"epg_source_{index}.xml"
        # 如果是 .gz 文件
        if url.endswith(".gz"):
            save_name = safe_name
        else:
            save_name = safe_name if safe_name.endswith(".xml") else safe_name + ".xml"

        msg = f"[下载] EPG源({index}/{total}) {url}"
        if log_callback:
            log_callback(msg)
        if progress_callback:
            await progress_callback(msg)

        client = get_client()
        resp = await client.get(url)
        resp.raise_for_status()

        content = resp.content
        # 如果是 gz 压缩
        if url.endswith(".gz") or content[:2] == b'\x1f\x8b':
            try:
                buf = io.BytesIO(content)
                with gzip.GzipFile(fileobj=buf) as gz:
                    content = gz.read()
                save_name = save_name.replace(".gz", "")
            except Exception:
                pass  # 不是有效的 gzip

        xml_text = content.decode("utf-8", errors="replace")
        xml_text = xml_text.replace("\r\n", "\n")

        save_path = config.CONFIG_DIR / "epg_cache" / save_name
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # 直接覆盖保存，不合并旧数据
        with open(save_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(xml_text)

        result["success"] = True
        result["path"] = str(save_path)
        result["content"] = xml_text  # 返回内容用于解析

        msg2 = f"[下载] EPG源({index}/{total}) 完成 {url} ✓"
        if log_callback:
            log_callback(msg2)
        if progress_callback:
            await progress_callback(msg2)

    except httpx.HTTPStatusError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except httpx.RequestError as e:
        result["error"] = f"请求失败: {str(e)}"
    except Exception as e:
        result["error"] = f"未知错误: {str(e)}"

    if result.get("error"):
        msg3 = f"[EPG] ({index}/{total}) 失败: {url} ✗ {result['error']}"
        if log_callback:
            log_callback(msg3)
        if progress_callback:
            await progress_callback(msg3)

    return result


async def download_all_epg_sources(progress_callback=None, log_callback=None) -> list:
    """下载所有已启用外部 EPG 源"""
    sources = config.get_epg_sources()
    results = []
    enabled = [s for s in sources if s.get("enabled", True)]
    total = len(enabled)

    for idx, src in enumerate(enabled, 1):
        result = await download_epg_source(src, progress_callback, log_callback, idx, total)
        if result:
            results.append(result)

    return results


def cleanup_old_files(log_callback=None):
    """清理过期文件（按文件名中的日期判断，保留过去6天+今天+明天=8天）

    Args:
        log_callback: 可选的回调函数，每删除一条文件时调用
    """
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=6)  # 保留过去6天的文件
    cutoff_str = cutoff.strftime("%Y%m%d")
    deleted = []

    def do_log(msg):
        if log_callback:
            log_callback(msg)

    # 清理 JSON（按文件名日期）
    for f in config.JSON_DIR.glob("*.json"):
        try:
            # 从文件名中提取日期: xxx_20260726.json -> 20260726
            m = re.search(r"_(\d{8})\.json$", f.name)
            if m:
                file_date = m.group(1)
                if file_date < cutoff_str:
                    f.unlink()
                    msg = f"[清理] 删除过期 JSON: {f.name} (日期 {file_date})"
                    do_log(msg)
                    deleted.append(msg)
            # 如果文件名无法匹配日期格式，用mtime判断
            else:
                if time.time() - f.stat().st_mtime > config.JSON_RETENTION_DAYS * 86400:
                    f.unlink()
                    msg = f"[清理] 删除过期 JSON(按mtime): {f.name}"
                    do_log(msg)
                    deleted.append(msg)
        except OSError:
            pass

    # 清理日志（保留7天）
    for f in config.LOG_DIR.glob("*.log"):
        try:
            if time.time() - f.stat().st_mtime > config.LOG_RETENTION_DAYS * 86400:
                f.unlink()
                msg = f"[清理] 删除过期日志: {f.name}"
                do_log(msg)
                deleted.append(msg)
        except OSError:
            pass

    # 清理 EPG 缓存中过期的合并文件
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


def get_json_files():
    """获取所有已下载的 JSON 文件信息"""
    files = []
    for f in sorted(config.JSON_DIR.glob("*.json")):
        try:
            files.append({
                "name": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "mtime": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except OSError:
            pass
    return files
