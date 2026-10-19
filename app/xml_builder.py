"""XML 生成模块 - 构建酷9兼容的 EPG XML"""
import datetime
import json
import re
from pathlib import Path
from typing import Optional, Callable

from . import config
from . import epg_parser


def build_xml(
    progress_callback: Optional[Callable] = None,
    log_callback: Optional[Callable] = None,
) -> dict:
    """构建完整的 EPG XML"""
    result = {"success": False, "path": "", "channel_count": 0, "programme_count": 0, "error": ""}

    try:
        import time as _time
        _start = _time.time()
        if log_callback:
            log_callback("[XML] 开始生成 XML...")

        epg_channels = config.get_epg_channels()
        extracts = config.get_epg_extracts()
        download_channels = config.get_channels()

        today = datetime.date.today()
        date_range = [today + datetime.timedelta(days=i) for i in range(-6, 3)]

        if log_callback:
            log_callback(f"[XML] EPG 频道数: {len(epg_channels)}, 提取记录数: {len(extracts)}, 下载频道数: {len(download_channels)}")

        lines = []
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')
        lines.append(f'<tv generator-info-name="iptv-server EPG" generator-info-url="" source-info-name="自建EPG">')

        channel_count = 0
        programme_count = 0
        added_channels = set()
        ch_with_data = 0
        ch_without_data = 0
        json_found = 0
        json_parsed = 0
        json_errors = 0

        # === 处理 EPG 管理列表中的频道 ===
        total_epg = len(epg_channels)
        for idx, ch in enumerate(epg_channels, 1):
            name = ch.get("name", "")
            tvg_id = ch.get("tvg_id", "")
            enabled = ch.get("enabled", True)
            if not enabled:
                continue
            if not tvg_id:
                tvg_id = name
            if tvg_id in added_channels:
                continue
            added_channels.add(tvg_id)
            lines.append(f' <channel id="{tvg_id}">')
            lines.append(f'  <display-name lang="zh">{ch.get("tvg_name", name)}</display-name>')
            lines.append(f' </channel>')
            channel_count += 1

            ch_progs = 0
            ch_json_found = 0
            for d in date_range:
                date_str = d.strftime("%Y%m%d")
                json_path = config.JSON_DIR / f"{name}_{date_str}.json"
                if json_path.exists():
                    json_found += 1
                    ch_json_found += 1
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        # 兼容两种 JSON 格式：频道管理（schedules）和外部EPG提取（programmes）
                        prog_list = data.get("schedules", []) or data.get("programmes", [])
                        for prog in prog_list:
                            title = prog.get("title", "").strip()
                            if not title or title == '暂无节目单':
                                continue
                            start_time = prog.get("starttime", "") or prog.get("start", "")
                            end_time = prog.get("endtime", "") or prog.get("stop", "")
                            lines.append(f' <programme channel="{tvg_id}" start="{epg_parser.format_xmltv_time(start_time)}" stop="{epg_parser.format_xmltv_time(end_time)}">')
                            lines.append(f'  <title lang="zh">{_escape_xml(title)}</title>')
                            desc = str(prog.get("desc", "") or "").strip()
                            if desc:
                                lines.append(f'  <desc lang="zh">{_escape_xml(desc)}</desc>')
                            lines.append(f' </programme>')
                            programme_count += 1
                            ch_progs += 1
                        json_parsed += 1
                    except Exception as e:
                        json_errors += 1
                        msg = f"[XML] 解析 {json_path.name} 失败: {e}"
                        if log_callback:
                            log_callback(msg)

            if ch_progs > 0:
                ch_with_data += 1
            else:
                ch_without_data += 1
                # 无JSON数据的频道填入暂无节目单占位
                for d in date_range:
                    for hour in range(24):
                        start_dt = datetime.datetime.combine(d, datetime.time(hour, 0))
                        end_dt = start_dt + datetime.timedelta(minutes=30)
                        start_str = start_dt.strftime("%Y%m%d%H%M00") + " +0800"
                        end_str = end_dt.strftime("%Y%m%d%H%M00") + " +0800"
                        lines.append(f' <programme channel="{tvg_id}" start="{start_str}" stop="{end_str}">')
                        lines.append(f'  <title lang="zh">暂无节目单</title>')
                        lines.append(f' </programme>')
                        programme_count += 1

            if idx % 10 == 0 or idx == total_epg:
                if log_callback:
                    log_callback(f"[XML]   [{idx}/{total_epg}] 已处理 {ch_with_data} 个有数据的频道, {ch_without_data} 个无数据频道")

        if log_callback:
            log_callback(f"[XML] 频道处理完成: {ch_with_data} 个有节目, {ch_without_data} 个无数据, JSON文件: 找到{json_found}个, 解析成功{json_parsed}个, 失败{json_errors}个")

        # === 占位频道 9999 ===
        if "9999" not in added_channels:
            lines.append(' <channel id="9999">')
            lines.append('  <display-name lang="zh">暂无节目单</display-name>')
            lines.append(' </channel>')
            channel_count += 1

            for d in date_range:
                for hour in range(24):
                    start_dt = datetime.datetime.combine(d, datetime.time(hour, 0))
                    end_dt = start_dt + datetime.timedelta(minutes=30)
                    start_str = start_dt.strftime("%Y%m%d%H%M00") + " +0800"
                    end_str = end_dt.strftime("%Y%m%d%H%M00") + " +0800"
                    lines.append(f' <programme channel="9999" start="{start_str}" stop="{end_str}">')
                    lines.append(f'  <title lang="zh">暂无节目单</title>')
                    lines.append(f' </programme>')
                    programme_count += 1

        lines.append('</tv>')

        # 写入 XML
        # 原子化写入：先生成完整内容到临时文件，全部写完后用 os.replace 一步替换正式文件，
        # 避免下载时拿到写了一半的半成品；旧文件在被替换前始终完整存在。
        import os as _os
        xml_content = "\n".join(lines) + "\n"
        xml_path = config.XML_DIR / "epg.xml"

        # 先写临时文件（普通 .xml 与 .gz 各一份），完成后原子替换
        _tmp_xml = config.XML_DIR / "epg.xml.tmp"
        _tmp_gz = config.XML_DIR / "epg.xml.gz.tmp"

        # 临时文件写入（含 gzip 压缩到独立临时文件）
        with open(_tmp_xml, "w", encoding="utf-8", newline="\n") as f:
            f.write(xml_content)
        import io as _io_import, gzip as _gzip_import
        _gz_buf = _io_import.BytesIO()
        with _gzip_import.GzipFile(fileobj=_gz_buf, mode="wb", mtime=0) as _gz:
            _gz.write(xml_content.encode("utf-8"))
        _tmp_gz.write_bytes(_gz_buf.getvalue())

        # 全部写完后再原子替换（os.replace 覆盖即原子，旧文件在被替换前始终完整）
        _os.replace(str(_tmp_xml), str(xml_path))
        _os.replace(str(_tmp_gz), str(config.XML_DIR / "epg.xml.gz"))

        # 确保残留的临时文件被清理（正常路径下已被 replace 移走；异常时兜底）
        for _tmp in (_tmp_xml, _tmp_gz):
            try:
                if _tmp.exists():
                    _tmp.unlink()
            except OSError:
                pass

        share_path = config.SHARE_DIR / "epg.xml"
        try:
            share_path.parent.mkdir(parents=True, exist_ok=True)
            # 共享目录同样原子写入，避免共享下载拿到半成品
            _tmp_share = share_path.parent / "epg.xml.tmp"
            with open(_tmp_share, "w", encoding="utf-8", newline="\n") as f:
                f.write(xml_content)
            _os.replace(str(_tmp_share), str(share_path))
        except OSError:
            pass

        result["success"] = True
        result["path"] = str(xml_path)
        result["channel_count"] = channel_count
        result["programme_count"] = programme_count

        elapsed = int(_time.time() - _start)
        if log_callback:
            log_callback(f"[XML] 生成完成: {channel_count} 频道, {programme_count} 节目, 耗时 {elapsed}秒")
        if progress_callback:
            progress_callback(f"[XML] 完成: {channel_count} 频道, {programme_count} 节目, 耗时 {elapsed}秒")

    except Exception as e:
        result["error"] = str(e)
        import traceback
        result["traceback"] = traceback.format_exc()

    return result


def build_base_epg_xml():
    """生成基础 EPG XML（占位文件）"""
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<tv generator-info-name="iptv-server Base EPG">')
    lines.append(' <channel id="9999">')
    lines.append('  <display-name lang="zh">暂无节目单</display-name>')
    lines.append(' </channel>')

    today = datetime.date.today()
    date_range = [today + datetime.timedelta(days=i) for i in range(-6, 3)]

    for d in date_range:
        for hour in range(24):
            start_dt = datetime.datetime.combine(d, datetime.time(hour, 0))
            end_dt = start_dt + datetime.timedelta(minutes=30)
            start_str = start_dt.strftime("%Y%m%d%H%M00") + " +0800"
            end_str = end_dt.strftime("%Y%m%d%H%M00") + " +0800"
            lines.append(f' <programme channel="9999" start="{start_str}" stop="{end_str}">')
            lines.append(f'  <title lang="zh">暂无节目单</title>')
            lines.append(f' </programme>')

    lines.append('</tv>')
    content = "\n".join(lines) + "\n"

    with open(config.BASE_EPG_XML, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    return str(config.BASE_EPG_XML)


def _escape_xml(text: str) -> str:
    """转义 XML 特殊字符"""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("'", "&apos;")
    return text
