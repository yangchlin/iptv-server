"""XML 生成模块 - 构建酷9兼容的 EPG XML

要点：
- 频道名统一用 config.safe_filename() 转安全文件名
- 读取 JSON 时同时尝试"新规则"与"历史原始名"两个路径，保证旧数据可读
- _escape_xml 剔除 XML 1.0 非法控制字符
- 保持函数同步（调用方用 asyncio.to_thread 包装）
"""
import datetime
import gzip
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from . import epg_parser

# XML 1.0 非法控制字符（\t \n \r 合法，保留）
_ILLEGAL_XML_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def _candidate_json_paths(name: str, suffix: str, date_str: str) -> list:
    """返回 JSON 文件候选路径（新规则优先，旧原始名兜底）。

    - 新规则：config.safe_filename(name) + suffix + _date.json
    - 旧兜底：直接用 name 拼接，但要求结果仍在 JSON_DIR 内（防 ../）
    """
    safe = config.safe_filename(name)
    primary = config.JSON_DIR / f"{safe}{suffix}_{date_str}.json"
    candidates = [primary]
    if safe != name:
        try:
            base = config.JSON_DIR.resolve()
            raw = (config.JSON_DIR / f"{name}{suffix}_{date_str}.json").resolve()
            raw.relative_to(base)          # 越界会抛 ValueError
            candidates.append(raw)
        except (ValueError, OSError):
            pass
    return candidates


def _find_json_path(name: str, suffix: str, date_str: str) -> Path:
    """返回第一个存在的候选路径；都不存在时返回首选路径（用于 exists() 判断）。"""
    candidates = _candidate_json_paths(name, suffix, date_str)
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _escape_xml(text: str) -> str:
    """XML 转义：特殊字符 + 剔除非法控制字符。"""
    text = _ILLEGAL_XML_CHARS.sub("", str(text or ""))
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("'", "&apos;")
    return text


def build_xml(
    progress_callback: Optional[Callable] = None,
    log_callback: Optional[Callable] = None,
) -> dict:
    """构建完整的 EPG XML。

    同步重活：调用方需用 await asyncio.to_thread(build_xml, ...) 包装。
    """
    result = {"success": False, "path": "", "channel_count": 0,
              "programme_count": 0, "error": ""}

    try:
        _start = time.time()
        if log_callback:
            log_callback("[XML] 开始生成 XML...")

        epg_channels = config.get_epg_channels()
        extracts = config.get_epg_extracts()
        download_channels = config.get_channels()

        today = datetime.date.today()
        date_range = [today + datetime.timedelta(days=i) for i in range(-6, 3)]

        if log_callback:
            log_callback(
                f"[XML] EPG 频道数: {len(epg_channels)}, "
                f"提取记录数: {len(extracts)}, 下载频道数: {len(download_channels)}"
            )

        lines = []
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')
        lines.append('<tv generator-info-name="iptv-server EPG" generator-info-url="" source-info-name="自建EPG">')

        channel_count = 0
        programme_count = 0
        added_channels = set()
        ch_with_data = 0
        ch_without_data = 0
        json_found = 0
        json_parsed = 0
        json_errors = 0

        total_epg = len(epg_channels)
        for idx, ch in enumerate(epg_channels, 1):
            name = ch.get("name", "")
            tvg_id = ch.get("tvg_id", "") or name
            if not ch.get("enabled", True):
                continue
            if tvg_id in added_channels:
                continue
            added_channels.add(tvg_id)

            lines.append(f' <channel id="{_escape_xml(tvg_id)}">')
            # ★ 修改：支持逗号/斜杠分隔的多个频道名
            tvg_name_str = str(ch.get("tvg_name", name) or "")
            for dn in re.split(r'[,，|/]', tvg_name_str):
                dn = dn.strip()
                if dn:
                    lines.append(f'  <display-name lang="zh">{_escape_xml(dn)}</display-name>')
            lines.append(' </channel>')
            channel_count += 1

            ch_progs = 0
            for d in date_range:
                date_str = d.strftime("%Y%m%d")
                # 优先读电信版，兜底普通版；两个路径都尝试新规则 + 历史原始名
                telecom_path = _find_json_path(name, config.TELECOM_EPG_SUFFIX, date_str)
                normal_path = _find_json_path(name, "", date_str)
                json_path = telecom_path if telecom_path.exists() else normal_path

                # ★ 新增：如果带 4K 的频道没拉到数据，自动去借用同名（去掉4K）普通频道的节目单
                if not json_path.exists() and name.upper().endswith("4K"):
                    fallback_name = name[:-2].strip()
                    telecom_path = _find_json_path(fallback_name, config.TELECOM_EPG_SUFFIX, date_str)
                    normal_path = _find_json_path(fallback_name, "", date_str)
                    json_path = telecom_path if telecom_path.exists() else normal_path

                if json_path.exists():
                    json_found += 1
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        prog_list = data.get("schedules", []) or data.get("programmes", [])
                        for prog in prog_list:
                            title = str(prog.get("title", "") or "").strip()
                            if not title or title == "暂无节目单":
                                continue
                            start_time = prog.get("starttime", "") or prog.get("start", "")
                            end_time = prog.get("endtime", "") or prog.get("stop", "")
                            lines.append(
                                f' <programme channel="{_escape_xml(tvg_id)}" '
                                f'start="{epg_parser.format_xmltv_time(start_time)}" '
                                f'stop="{epg_parser.format_xmltv_time(end_time)}">'
                            )
                            lines.append(f'  <title lang="zh">{_escape_xml(title)}</title>')
                            desc = str(prog.get("desc", "") or "").strip()
                            if desc:
                                lines.append(f'  <desc lang="zh">{_escape_xml(desc)}</desc>')
                            lines.append(' </programme>')
                            programme_count += 1
                            ch_progs += 1
                        json_parsed += 1
                    except Exception as e:
                        json_errors += 1
                        if log_callback:
                            log_callback(f"[XML] 解析 {json_path.name} 失败: {e}")

            if ch_progs > 0:
                ch_with_data += 1
            else:
                ch_without_data += 1

            if idx % 10 == 0 or idx == total_epg:
                if log_callback:
                    log_callback(
                        f"[XML]   [{idx}/{total_epg}] 已处理 {ch_with_data} 个有数据的频道, "
                        f"{ch_without_data} 个无数据频道"
                    )

        if log_callback:
            log_callback(
                f"[XML] 频道处理完成: {ch_with_data} 个有节目, {ch_without_data} 个无数据, "
                f"JSON文件: 找到{json_found}个, 解析成功{json_parsed}个, 失败{json_errors}个"
            )

        lines.append('</tv>')

        # 原子写入：普通 xml + gzip 各一份
        xml_content = "\n".join(lines) + "\n"
        xml_path = config.XML_DIR / "epg.xml"
        tmp_xml = config.XML_DIR / "epg.xml.tmp"
        tmp_gz = config.XML_DIR / "epg.xml.gz.tmp"

        with open(tmp_xml, "w", encoding="utf-8", newline="\n") as f:
            f.write(xml_content)
        gz_buf = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
            gz.write(xml_content.encode("utf-8"))
        tmp_gz.write_bytes(gz_buf.getvalue())

        os.replace(str(tmp_xml), str(xml_path))
        os.replace(str(tmp_gz), str(config.XML_DIR / "epg.xml.gz"))

        for tmp in (tmp_xml, tmp_gz):
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

        # 备份到 SHARE_DIR
        share_path = config.SHARE_DIR / "epg.xml"
        try:
            share_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_share = share_path.parent / "epg.xml.tmp"
            with open(tmp_share, "w", encoding="utf-8", newline="\n") as f:
                f.write(xml_content)
            os.replace(str(tmp_share), str(share_path))
        except OSError:
            pass

        result["success"] = True
        result["path"] = str(xml_path)
        result["channel_count"] = channel_count
        result["programme_count"] = programme_count

        elapsed = int(time.time() - _start)
        if log_callback:
            log_callback(f"[XML] 生成完成: {channel_count} 频道, {programme_count} 节目, 耗时 {elapsed}秒")
        if progress_callback:
            progress_callback(f"[XML] 完成: {channel_count} 频道, {programme_count} 节目, 耗时 {elapsed}秒")

    except Exception as e:
        result["error"] = str(e)
        import traceback
        result["traceback"] = traceback.format_exc()

    return result


def build_telecom_xml(log_callback: Optional[Callable] = None,
                      progress_callback: Optional[Callable] = None) -> dict:
    """把电信 EPG 单独生成一份 epg_telecom.xml（+ .gz）。

    放在与 epg.xml 相同的目录（config.XML_DIR），所以外部 EPG 和电信 EPG
    的 XML 都在 data/xml/ 下：
        epg.xml          —— 全部频道（外部 + 电信，按 EPG 管理列表合并）
        epg_telecom.xml  —— 只含电信频道

    数据来源：
      频道：data/config/telecom_channels.json
      节目：data/json/<频道名>_telecom_<日期>.json（telecom_sync_to_server 写入）
    """
    result = {"success": False, "path": "", "channel_count": 0,
              "programme_count": 0, "error": ""}

    try:
        channels = config.get_telecom_channels().get("channels", [])
        if not channels:
            result["error"] = "没有电信频道数据（请先执行「获取频道列表」）"
            return result

        today = datetime.date.today()
        date_range = [today + datetime.timedelta(days=i) for i in range(-6, 3)]

        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<tv generator-info-name="iptv-server 电信EPG" source-info-name="电信IPTV">']
        channel_count = 0
        programme_count = 0
        placeholder_count = 0
        added = set()

        for ch in channels:
            name = str(ch.get("name", "") or "").strip()
            if not name or name in added:
                continue
            added.add(name)

            lines.append(f' <channel id="{_escape_xml(name)}">')
            # ★ 修改开始：支持多个别名
            for dn in re.split(r'[,，|/]', name):
                dn = dn.strip()
                if dn:
                    lines.append(f'  <display-name lang="zh">{_escape_xml(dn)}</display-name>')
            # ★ 修改结束
            lines.append(' </channel>')
            channel_count += 1

            ch_progs = 0
            for d in date_range:
                date_str = d.strftime("%Y%m%d")
                json_path = _find_json_path(name, config.TELECOM_EPG_SUFFIX, date_str)
                # ★ 新增：如果带 4K 的频道没拉到数据，自动去借用同名（去掉4K）普通频道的节目单
                if not json_path.exists() and name.upper().endswith("4K"):
                    fallback_name = name[:-2].strip()
                    json_path = _find_json_path(fallback_name, config.TELECOM_EPG_SUFFIX, date_str)

                if not json_path.exists():
                    continue
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as e:
                    if log_callback:
                        log_callback(f"[XML-电信] 解析 {json_path.name} 失败: {e}")
                    continue

                prog_list = data.get("programmes", []) or data.get("schedules", [])
                for prog in prog_list:
                    title = str(prog.get("title", "") or "").strip()
                    if not title or title == "暂无节目单":
                        continue
                    start_time = prog.get("starttime", "") or prog.get("start", "")
                    end_time = prog.get("endtime", "") or prog.get("stop", "")
                    lines.append(
                        f' <programme channel="{_escape_xml(name)}" '
                        f'start="{epg_parser.format_xmltv_time(start_time)}" '
                        f'stop="{epg_parser.format_xmltv_time(end_time)}">'
                    )
                    lines.append(f'  <title lang="zh">{_escape_xml(title)}</title>')
                    desc = str(prog.get("desc", "") or "").strip()
                    if desc:
                        lines.append(f'  <desc lang="zh">{_escape_xml(desc)}</desc>')
                    lines.append(' </programme>')
                    programme_count += 1
                    ch_progs += 1

            # 该频道一条节目都没有：补占位，避免播放器拿到空频道

        lines.append('</tv>')
        xml_content = "\n".join(lines) + "\n"

        xml_path = config.XML_DIR / "epg_telecom.xml"
        tmp_xml = config.XML_DIR / "epg_telecom.xml.tmp"
        tmp_gz = config.XML_DIR / "epg_telecom.xml.gz.tmp"

        with open(tmp_xml, "w", encoding="utf-8", newline="\n") as f:
            f.write(xml_content)
        gz_buf = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
            gz.write(xml_content.encode("utf-8"))
        tmp_gz.write_bytes(gz_buf.getvalue())

        os.replace(str(tmp_xml), str(xml_path))
        os.replace(str(tmp_gz), str(config.XML_DIR / "epg_telecom.xml.gz"))

        for tmp in (tmp_xml, tmp_gz):
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

        result["success"] = True
        result["path"] = str(xml_path)
        result["channel_count"] = channel_count
        result["programme_count"] = programme_count
        result["placeholder_count"] = placeholder_count

        if log_callback:
            log_callback(
                f"[XML-电信] 完成: {channel_count} 频道, {programme_count} 节目"
                f"（其中占位 {placeholder_count}）→ {xml_path.name}"
            )
        if progress_callback:
            progress_callback(f"[XML-电信] 完成: {channel_count} 频道 / {programme_count} 节目")

    except Exception as e:
        result["error"] = str(e)
        import traceback
        result["traceback"] = traceback.format_exc()

    return result

