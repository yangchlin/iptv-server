"""定时任务调度模块"""
import asyncio
import datetime
import json
from collections import defaultdict
from typing import Optional, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config
from . import downloader
from . import xml_builder
from . import epg_parser
from .logger import logger


class TaskScheduler:
    """任务调度器"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.current_task = None
        self._running = False

    def start(self):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._setup_jobs()
        self.scheduler.start()
        print("[调度] 调度器已启动")

    def stop(self):
        """停止调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self._running = False
        print("[调度] 调度器已停止")

    def _setup_jobs(self):
        """设置定时任务"""
        settings = config.get_settings()
        cron_time = settings.get("cron_time", "11:00")
        cron_enabled = settings.get("cron_enabled", True)

        if cron_enabled and ":" in cron_time:
            hour, minute = cron_time.split(":")
            self.scheduler.add_job(
                self.daily_task,
                CronTrigger(hour=int(hour), minute=int(minute), timezone="Asia/Shanghai"),
                id="daily_epg_task",
                replace_existing=True,
                name="每日 EPG 更新",
            )
            print(f"[调度] 已设置每日任务: {cron_time}")

    def reschedule(self):
        """重新设置定时任务（时间更改后调用）"""
        if self._running:
            self.scheduler.remove_job("daily_epg_task")
            self._setup_jobs()

    async def daily_task(self):
        """每日定时任务"""
        if self.current_task and not self.current_task.done():
            print("[调度] 上一个任务仍在运行，跳过")
            return

        self.current_task = asyncio.create_task(self._run_daily())

    async def _run_daily(self):
        """执行每日更新"""
        import time as _time
        logger.log("[任务] ====== 开始每日 EPG 更新 ======", category="定时任务")
        try:
            # 1. 下载外部 EPG 源
            logger.log("[任务] 下载外部 EPG...", category="下载")
            t2 = _time.time()
            epg_results = await downloader.download_all_epg_sources(
                log_callback=lambda msg: logger.log(msg)
            )

            # 2. 遍历提取记录，从 EPG 缓存提取节目，保存到 json/（不覆盖已有）
            logger.log("[任务] 从 EPG 缓存提取节目...", category="EPG")
            extracts = config.get_epg_extracts()
            epg_cache = config.CONFIG_DIR / "epg_cache"
            today = datetime.date.today()
            extract_dates = [
                today.strftime("%Y%m%d"),
                (today + datetime.timedelta(days=1)).strftime("%Y%m%d"),
                (today + datetime.timedelta(days=2)).strftime("%Y%m%d"),
            ]
            json_dir = config.JSON_DIR
            extract_count = 0
            fail_count = 0
            # 缓存在 EPG 管理列表中查找 logic_id -> channel name 的映射
            epg_name_map = {}
            epg_channels = config.get_epg_channels()
            for ch in epg_channels:
                ch_logic_id = ch.get("logic_id", "")
                if ch_logic_id:
                    epg_name_map[ch_logic_id] = ch.get("name", "")

            for idx, ext in enumerate(extracts, 1):
                name = ext.get("name", "")
                rule = ext.get("rule", "auto")
                if not name:
                    continue
                # 根据 logic_id 查找 EPG 管理列表中的频道名称，用于保存 JSON
                save_name = name
                logic_id = ext.get("logic_id", "")
                if logic_id and logic_id in epg_name_map and epg_name_map[logic_id]:
                    save_name = epg_name_map[logic_id]
                programmes = []
                # 严格按提取记录指定的源(source_url)提取, 不兜底遍历
                for cf in epg_parser.resolve_cache_files(epg_cache, ext.get("source_url", "")):
                    try:
                        cache_text = cf.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    progs = epg_parser.extract_programmes(cache_text, rule, name)
                    if progs:
                        programmes.extend(progs)
                        break
                date_progs = defaultdict(list)
                if programmes:
                    for prog in programmes:
                        start = prog.get("start", "")
                        d = start[:8] if len(start) >= 8 else ""
                        if d in extract_dates:
                            date_progs[d].append(prog)

                # 只保留实际提取到的日期（在 extract_dates 范围内的），不再生成"暂无节目单"占位
                got_dates = sorted(set(d for d in date_progs if date_progs[d]))
                if not programmes:
                    fail_count += 1
                    logger.log(f"[任务]   [{idx}/{len(extracts)}] {name}: ❌ 未提取到节目（源内无该频道或源文件缺失）", category="EPG")
                else:
                    logger.log(f"[任务]   [{idx}/{len(extracts)}] {name}: 已提取到（{', '.join(got_dates)}）", category="EPG")

                for d, progs in date_progs.items():
                    save_filename = f"{save_name}_{d}.json"
                    save_path = json_dir / save_filename
                    prog_list = [{"name": name, "start": p.get("start", ""), "stop": p.get("stop", ""), "title": p.get("title", ""), "desc": p.get("desc", "")} for p in progs]
                    try:
                        json_dir.mkdir(parents=True, exist_ok=True)
                        with open(save_path, "w", encoding="utf-8") as f:
                            json.dump({"programmes": prog_list}, f, ensure_ascii=False)
                        extract_count += 1
                    except Exception:
                        pass
                if idx % 10 == 0 or idx == len(extracts):
                    logger.log(f"[任务]   [{idx}/{len(extracts)}] 成功{extract_count}个JSON, 失败{fail_count}条", category="EPG")
            logger.log(f"[任务] [第2步] 外部 EPG 提取完成: 新增{extract_count}个JSON, 失败{fail_count}条, 耗时{int(_time.time()-t2)}秒", category="EPG")

            # 3. 下载频道 JSON
            today = datetime.date.today()
            dates = [
                today.strftime("%Y%m%d"),
                (today + datetime.timedelta(days=1)).strftime("%Y%m%d"),
                (today + datetime.timedelta(days=2)).strftime("%Y%m%d"),
            ]
            logger.log(f"[任务] 下载频道 JSON: {dates}", category="下载")
            await downloader.download_all_channels(
                dates,
                log_callback=lambda msg: logger.log(msg),
            )

            # 5. 生成 XML
            logger.log("[任务] 生成 XML...", category="EPG")
            xml_result = xml_builder.build_xml(
                log_callback=lambda msg: logger.log(msg),
            )
            if xml_result["success"]:
                logger.log(f"[任务] XML 生成完成: {xml_result['channel_count']} 频道, {xml_result['programme_count']} 节目", category="EPG")
            else:
                logger.log(f"[任务] XML 生成失败: {xml_result.get('error', '')}", category="EPG")

            # 6. 最后清理过期文件
            logger.log("[任务] 清理过期文件...", category="清理")
            downloader.cleanup_old_files()
            logger.log("[任务] 清理完成", category="清理")

            logger.log("[任务] ====== 每日 EPG 更新完成 ======", category="定时任务")

        except Exception as e:
            logger.log(f"[任务] 错误: {str(e)}", category="失败")
            import traceback
            logger.log(traceback.format_exc(), category="失败")

    def run_merge(self):
        """合并 EPG：从已有缓存提取节目 + 生成 XML（不下载）"""
        logger.log("[合并] ====== 开始 EPG 合并 ======", category="EPG")
        try:
            import time as _time
            t1 = _time.time()

            # 1. 提取节目
            logger.log("[合并] 从 EPG 缓存提取节目...", category="EPG")
            extracts = config.get_epg_extracts()
            epg_cache = config.CONFIG_DIR / "epg_cache"
            today = datetime.date.today()
            extract_dates = [
                today.strftime("%Y%m%d"),
                (today + datetime.timedelta(days=1)).strftime("%Y%m%d"),
                (today + datetime.timedelta(days=2)).strftime("%Y%m%d"),
            ]
            json_dir = config.JSON_DIR
            extract_count = 0
            fail_count = 0

            epg_name_map = {}
            epg_channels = config.get_epg_channels()
            for ch in epg_channels:
                ch_logic_id = ch.get("logic_id", "")
                if ch_logic_id:
                    epg_name_map[ch_logic_id] = ch.get("name", "")

            for idx, ext in enumerate(extracts, 1):
                name = ext.get("name", "")
                rule = ext.get("rule", "auto")
                if not name:
                    continue
                save_name = name
                logic_id = ext.get("logic_id", "")
                if logic_id and logic_id in epg_name_map and epg_name_map[logic_id]:
                    save_name = epg_name_map[logic_id]

                programmes = []
                if epg_cache.exists():
                    for cf in sorted(epg_cache.glob("*.xml")):
                        try:
                            cache_text = cf.read_text(encoding="utf-8")
                        except Exception:
                            continue
                        progs = epg_parser.extract_programmes(cache_text, rule, name)
                        if progs:
                            programmes.extend(progs)
                            break

                date_progs = defaultdict(list)
                if programmes:
                    for prog in programmes:
                        start = prog.get("start", "")
                        d = start[:8] if len(start) >= 8 else ""
                        if d in extract_dates:
                            date_progs[d].append(prog)

                got_dates = sorted(set(d for d in date_progs if date_progs[d]))
                if not programmes:
                    fail_count += 1
                    logger.log(f"[合并]   [{idx}/{len(extracts)}] {name}: ❌ 未提取到节目（源内无该频道或源文件缺失）", category="EPG")
                else:
                    logger.log(f"[合并]   [{idx}/{len(extracts)}] {name}: 已提取到（{', '.join(got_dates)}）", category="EPG")

                for d, progs in date_progs.items():
                    save_filename = f"{save_name}_{d}.json"
                    save_path = json_dir / save_filename
                    prog_list = [{"name": name, "start": p.get("start", ""), "stop": p.get("stop", ""), "title": p.get("title", ""), "desc": p.get("desc", "")} for p in progs]
                    try:
                        json_dir.mkdir(parents=True, exist_ok=True)
                        with open(save_path, "w", encoding="utf-8") as f:
                            json.dump({"programmes": prog_list}, f, ensure_ascii=False)
                        extract_count += 1
                    except Exception:
                        pass

            logger.log(f"[合并] 提取完成: 新增{extract_count}个JSON, 失败{fail_count}条, 耗时{int(_time.time()-t1)}秒", category="EPG")

            # 2. 生成 XML
            logger.log("[合并] 生成 XML...", category="EPG")
            xml_result = xml_builder.build_xml(
                log_callback=lambda msg: logger.log(msg),
            )
            if xml_result["success"]:
                logger.log(f"[合并] XML 生成完成: {xml_result['channel_count']} 频道, {xml_result['programme_count']} 节目", category="EPG")
            else:
                logger.log(f"[合并] XML 生成失败: {xml_result.get('error', '')}", category="EPG")

            logger.log("[合并] ====== EPG 合并完成 ======", category="EPG")
            return xml_result["success"]

        except Exception as e:
            logger.log(f"[合并] 错误: {str(e)}", category="失败")
            import traceback
            logger.log(traceback.format_exc(), category="失败")
            return False


# 全局调度器实例
scheduler = TaskScheduler()
