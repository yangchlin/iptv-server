"""定时任务调度模块

要点：
- 频道名统一用 config.safe_filename()，与 xml_builder 读取一致
- 同步重活（build_xml / cleanup）走 asyncio.to_thread
- _parse_hm 容错，非法时间不抛异常
"""
import asyncio
import datetime
import json
from collections import defaultdict
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config
from . import downloader
from . import xml_builder
from . import epg_parser
from .logger import logger


def _parse_hm(t: str) -> Optional[tuple]:
    """安全解析 'HH:MM'，返回 (hour, minute) 或 None。"""
    try:
        h, m = str(t).split(":")[:2]
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, AttributeError):
        pass
    return None


class TaskScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.current_task = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._setup_jobs()
        self.scheduler.start()
        print("[调度] 调度器已启动")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self._running = False
        print("[调度] 调度器已停止")

    def _setup_jobs(self):
        s = config.get_settings()

        if s.get("cron_enabled", True):
            hm = _parse_hm(s.get("cron_time", "11:00"))
            if hm:
                self.scheduler.add_job(
                    self.daily_task,
                    CronTrigger(hour=hm[0], minute=hm[1], timezone="Asia/Shanghai"),
                    id="daily_epg_task", replace_existing=True, name="外部EPG更新",
                )
            else:
                print(f"[调度] cron_time 非法，跳过: {s.get('cron_time')!r}")

        if s.get("telecom_channels_cron_enabled", False):
            hm = _parse_hm(s.get("telecom_channels_cron_time", "02:00"))
            if hm:
                self.scheduler.add_job(
                    self.telecom_channels_task,
                    CronTrigger(hour=hm[0], minute=hm[1], timezone="Asia/Shanghai"),
                    id="telecom_channels_task", replace_existing=True,
                    name=f"电信频道({s.get('telecom_channels_cron_mode','daily')})",
                )
                print(f"[调度] 电信频道任务: {hm[0]:02d}:{hm[1]:02d}")
            else:
                print("[调度] telecom_channels_cron_time 非法，跳过")

        if s.get("telecom_epg_cron_enabled", False):
            hm = _parse_hm(s.get("telecom_epg_cron_time", "03:00"))
            if hm:
                self.scheduler.add_job(
                    self.telecom_epg_task,
                    CronTrigger(hour=hm[0], minute=hm[1], timezone="Asia/Shanghai"),
                    id="telecom_epg_task", replace_existing=True,
                    name=f"电信EPG({s.get('telecom_epg_cron_mode','daily')})",
                )
                print(f"[调度] 电信EPG任务: {hm[0]:02d}:{hm[1]:02d}")
            else:
                print("[调度] telecom_epg_cron_time 非法，跳过")

    def reschedule(self):
        if not self._running:
            return
        for jid in ("daily_epg_task", "telecom_channels_task", "telecom_epg_task"):
            try:
                self.scheduler.remove_job(jid)
            except Exception:
                pass
        self._setup_jobs()

    def _should_run_now(self, prefix: str) -> bool:
        s = config.get_settings()
        if s.get(f"{prefix}_cron_mode", "daily") != "interval":
            return True
        try:
            n = max(1, int(s.get(f"{prefix}_cron_interval", 3)))
        except (TypeError, ValueError):
            n = 3
        last = s.get(f"{prefix}_last_run", "")
        if not last:
            return True
        try:
            delta = datetime.datetime.now() - datetime.datetime.fromisoformat(last)
            return delta.days >= n
        except Exception:
            s[f"{prefix}_last_run"] = ""
            config.save_settings(s)
            logger.log(f"[调度] {prefix}_last_run 格式非法，已重置", category="失败")
            return True

    def _mark_last_run(self, prefix: str):
        s = config.get_settings()
        s[f"{prefix}_last_run"] = datetime.datetime.now().isoformat()
        config.save_settings(s)

    def _auto_merge_into_epg(self, channels: list):
        """把电信频道自动并入「EPG 管理」，让电信节目能进入主 epg.xml。"""
        try:
            if not config.get_settings().get("telecom_auto_add_to_epg", True):
                return
            merged = config.merge_telecom_channels_into_epg(channels)
            if merged.get("added"):
                logger.log(
                    f"[任务] 已自动加入 {merged['added']} 个电信频道到「EPG 管理」，"
                    f"当前共 {merged['total']} 个",
                    category="电信频道",
                )
        except Exception as e:
            logger.log(f"[任务] 自动并入 EPG 管理失败: {e}", category="失败")

    # ════════════════════════════════════════════════════════════
    # 每日 EPG 任务
    # ════════════════════════════════════════════════════════════

    async def daily_task(self):
        if self.current_task and not self.current_task.done():
            print("[调度] 上一个任务仍在运行，跳过")
            return
        self.current_task = asyncio.create_task(self._run_daily())

    async def _run_daily(self):
        logger.log("[任务] ====== 开始每日 EPG 更新 ======", category="外部EPG")
        try:
            logger.log("[任务] 下载外部 EPG...", category="下载")
            await downloader.download_all_epg_sources(
                log_callback=lambda msg: logger.log(msg)
            )

            logger.log("[任务] 从 EPG 缓存提取节目...", category="EPG")
            extracts = config.get_epg_extracts()
            epg_cache = config.CONFIG_DIR / "epg_cache"
            today = datetime.date.today()
            # ★ 与 xml_builder 保持一致：前 6 天 + 今天 + 后 2 天（共 9 天）
            extract_dates = [
                (today + datetime.timedelta(days=i)).strftime("%Y%m%d")
                for i in range(-6, 3)
            ]
            json_dir = config.JSON_DIR
            extract_count = 0
            fail_count = 0

            epg_name_map = {}
            for ch in config.get_epg_channels():
                lid = ch.get("logic_id", "")
                if lid:
                    epg_name_map[lid] = ch.get("name", "")

            for idx, ext in enumerate(extracts, 1):
                name = ext.get("name", "")
                rule = ext.get("rule", "auto")
                if not name:
                    continue

                save_name = (epg_name_map.get(str(ext.get("target_logic_id", "") or ""))
                             or epg_name_map.get(str(ext.get("logic_id", "") or ""))
                             or name)

                programmes = []
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
                for prog in programmes:
                    start = prog.get("start", "")
                    d = start[:8] if len(start) >= 8 else ""
                    if d in extract_dates:
                        date_progs[d].append(prog)

                got_dates = sorted(set(d for d in date_progs if date_progs[d]))
                if not programmes:
                    fail_count += 1
                    logger.log(f"[任务]   [{idx}/{len(extracts)}] {name}: ❌ 未提取到节目", category="EPG")
                else:
                    logger.log(f"[任务]   [{idx}/{len(extracts)}] {name}: 已提取（{', '.join(got_dates)}）", category="EPG")

                safe = config.safe_filename(save_name)
                for d, progs in date_progs.items():
                    save_path = json_dir / f"{safe}_{d}.json"
                    prog_list = [
                        {"name": name, "start": p.get("start", ""),
                         "stop": p.get("stop", ""), "title": p.get("title", ""),
                         "desc": p.get("desc", "")}
                        for p in progs
                    ]
                    try:
                        json_dir.mkdir(parents=True, exist_ok=True)
                        tmp = save_path.with_name(save_path.name + ".tmp")
                        with open(tmp, "w", encoding="utf-8") as f:
                            json.dump({"programmes": prog_list}, f, ensure_ascii=False)
                        tmp.replace(save_path)
                        extract_count += 1
                    except Exception as e:
                        logger.log(f"[任务] 保存 {save_path.name} 失败: {e}", category="失败")

            logger.log(f"[任务] 提取完成: 新增{extract_count}个JSON, 失败{fail_count}条", category="EPG")

            dates = [
                today.strftime("%Y%m%d"),
                (today + datetime.timedelta(days=1)).strftime("%Y%m%d"),
                (today + datetime.timedelta(days=2)).strftime("%Y%m%d"),
            ]
            logger.log(f"[任务] 下载频道 JSON: {dates}", category="下载")
            await downloader.download_all_channels(
                dates, log_callback=lambda msg: logger.log(msg),
            )

            logger.log("[任务] 生成 XML...", category="EPG")
            xml_result = await asyncio.to_thread(
                xml_builder.build_xml,
                log_callback=lambda msg: logger.log(msg),
            )
            if xml_result["success"]:
                logger.log(
                    f"[任务] XML 生成完成: {xml_result['channel_count']} 频道, "
                    f"{xml_result['programme_count']} 节目", category="EPG")
            else:
                logger.log(f"[任务] XML 生成失败: {xml_result.get('error', '')}", category="EPG")

            logger.log("[任务] 清理过期文件...", category="清理")
            await asyncio.to_thread(downloader.cleanup_old_files)
            logger.log("[任务] 清理完成", category="清理")

            logger.log("[任务] ====== 每日 EPG 更新完成 ======", category="外部EPG")
        except Exception as e:
            logger.log(f"[任务] 错误: {str(e)}", category="失败")
            import traceback
            logger.log(traceback.format_exc(), category="失败")

    # ════════════════════════════════════════════════════════════
    # 合并（同步方法，外层用 to_thread）
    # ════════════════════════════════════════════════════════════

    def run_merge(self) -> bool:
        logger.log("[合并] ====== 开始 EPG 合并 ======", category="EPG")
        try:
            import time as _time
            t1 = _time.time()

            logger.log("[合并] 从 EPG 缓存提取节目...", category="EPG")
            extracts = config.get_epg_extracts()
            epg_cache = config.CONFIG_DIR / "epg_cache"
            today = datetime.date.today()
            # ★ 与 xml_builder 保持一致：前 6 天 + 今天 + 后 2 天（共 9 天）
            extract_dates = [
                (today + datetime.timedelta(days=i)).strftime("%Y%m%d")
                for i in range(-6, 3)
            ]
            json_dir = config.JSON_DIR
            extract_count = 0
            fail_count = 0

            epg_name_map = {}
            for ch in config.get_epg_channels():
                lid = ch.get("logic_id", "")
                if lid:
                    epg_name_map[lid] = ch.get("name", "")

            for idx, ext in enumerate(extracts, 1):
                name = ext.get("name", "")
                rule = ext.get("rule", "auto")
                if not name:
                    continue

                save_name = (epg_name_map.get(str(ext.get("target_logic_id", "") or ""))
                             or epg_name_map.get(str(ext.get("logic_id", "") or ""))
                             or name)

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
                for prog in programmes:
                    start = prog.get("start", "")
                    d = start[:8] if len(start) >= 8 else ""
                    if d in extract_dates:
                        date_progs[d].append(prog)

                got_dates = sorted(set(d for d in date_progs if date_progs[d]))
                if not programmes:
                    fail_count += 1
                    logger.log(f"[合并]   [{idx}/{len(extracts)}] {name}: ❌ 未提取到节目", category="EPG")
                else:
                    logger.log(f"[合并]   [{idx}/{len(extracts)}] {name}: 已提取（{', '.join(got_dates)}）", category="EPG")

                safe = config.safe_filename(save_name)
                for d, progs in date_progs.items():
                    save_path = json_dir / f"{safe}_{d}.json"
                    prog_list = [
                        {"name": name, "start": p.get("start", ""),
                         "stop": p.get("stop", ""), "title": p.get("title", ""),
                         "desc": p.get("desc", "")}
                        for p in progs
                    ]
                    try:
                        json_dir.mkdir(parents=True, exist_ok=True)
                        tmp = save_path.with_name(save_path.name + ".tmp")
                        with open(tmp, "w", encoding="utf-8") as f:
                            json.dump({"programmes": prog_list}, f, ensure_ascii=False)
                        tmp.replace(save_path)
                        extract_count += 1
                    except Exception:
                        pass

            logger.log(f"[合并] 提取完成: 新增{extract_count}个JSON, 失败{fail_count}条, "
                       f"耗时{int(_time.time()-t1)}秒", category="EPG")

            logger.log("[合并] 生成 XML...", category="EPG")
            xml_result = xml_builder.build_xml(log_callback=lambda msg: logger.log(msg))
            if xml_result["success"]:
                logger.log(
                    f"[合并] XML 生成完成: {xml_result['channel_count']} 频道, "
                    f"{xml_result['programme_count']} 节目", category="EPG")
            else:
                logger.log(f"[合并] XML 生成失败: {xml_result.get('error', '')}", category="EPG")

            logger.log("[合并] ====== EPG 合并完成 ======", category="EPG")
            return xml_result["success"]
        except Exception as e:
            logger.log(f"[合并] 错误: {str(e)}", category="失败")
            import traceback
            logger.log(traceback.format_exc(), category="失败")
            return False

    # ════════════════════════════════════════════════════════════
    # 电信频道 / EPG 任务
    # ════════════════════════════════════════════════════════════

    async def telecom_channels_task(self):
        from . import telecom_epg
        if not self._should_run_now("telecom_channels"):
            logger.log("[任务] 频道刷新: 未到间隔周期，跳过", category="电信频道")
            return

        logger.log("[任务] ====== 开始刷新电信频道 ======", category="电信频道")
        try:
            cfg = telecom_epg.get_telecom_config()
            if not cfg.get("enabled"):
                logger.log("[任务] 电信未启用，跳过", category="电信频道")
                return

            auth = await asyncio.to_thread(telecom_epg.telecom_auth, cfg)
            if not auth["success"]:
                logger.log(f"[任务] 认证失败: {auth['message']}", category="失败")
                return

            r = await asyncio.to_thread(
                telecom_epg.telecom_get_channels,
                host=auth["host"], cookies=auth["cookies"],
                user_token=auth["user_token"], stbid=auth["stbid"], config_data=cfg,
            )
            if r["success"]:
                config.save_telecom_channels(r["channels"])
                self._auto_merge_into_epg(r["channels"])
                self._mark_last_run("telecom_channels")
                logger.log(f"[任务] 频道已保存: {len(r['channels'])} 个", category="EPG")
            else:
                logger.log(f"[任务] 获取频道失败: {r['message']}", category="失败")
        except Exception as e:
            logger.log(f"[任务] 频道刷新异常: {e}", category="失败")

    async def telecom_epg_task(self):
        from . import telecom_epg
        if not self._should_run_now("telecom_epg"):
            logger.log("[任务] EPG下载: 未到间隔周期，跳过", category="电信EPG")
            return

        logger.log("[任务] ====== 开始电信EPG下载 ======", category="电信EPG")
        try:
            cfg = telecom_epg.get_telecom_config()
            if not cfg.get("enabled"):
                logger.log("[任务] 电信未启用，跳过", category="电信EPG")
                return

            auto_fetch = config.get_settings().get("telecom_epg_auto_fetch_channels", True)
            channels = config.get_telecom_channels().get("channels", [])
            auth = None

            if not channels:
                if not auto_fetch:
                    logger.log("[任务] 频道缓存空且未开自动抓取，跳过", category="电信EPG")
                    return
                logger.log("[任务] 频道缓存空，自动获取频道", category="EPG")
                auth = await asyncio.to_thread(telecom_epg.telecom_auth, cfg)
                if not auth["success"]:
                    logger.log(f"[任务] 认证失败: {auth['message']}", category="失败")
                    return
                r = await asyncio.to_thread(
                    telecom_epg.telecom_get_channels,
                    host=auth["host"], cookies=auth["cookies"],
                    user_token=auth["user_token"], stbid=auth["stbid"], config_data=cfg,
                )
                if not r["success"]:
                    logger.log(f"[任务] 获取频道失败: {r['message']}", category="失败")
                    return
                channels = r["channels"]
                config.save_telecom_channels(channels)
                self._auto_merge_into_epg(channels)

            if auth is None:
                auth = await asyncio.to_thread(telecom_epg.telecom_auth, cfg)
                if not auth["success"]:
                    logger.log(f"[任务] 认证失败: {auth['message']}", category="失败")
                    return

            today = datetime.date.today()
            dates = [(today + datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]
            all_epg = {"channels": [], "total": len(channels)}
            for ch in channels:
                r = await asyncio.to_thread(
                    telecom_epg.telecom_download_epg,
                    host=auth["host"], cookies=auth["cookies"],
                    user_token=auth["user_token"], stbid=auth["stbid"],
                    channel_id=ch.get("channel_id", ""), channel_name=ch.get("name", ""),
                    dates=dates, config_data=cfg,
                )
                if r["success"]:
                    all_epg["channels"].append({
                        "name": ch.get("name", ""),
                        "channel_id": ch.get("channel_id", ""),
                        "programs": r["programs"],
                    })
                await asyncio.sleep(0.5)

            sync = await asyncio.to_thread(telecom_epg.telecom_sync_to_server, all_epg, cfg)
            logger.log(f"[任务] EPG同步: {sync.get('message','')}", category="EPG")
            await asyncio.to_thread(telecom_epg.generate_sctv_files, channels, cfg)

            await asyncio.to_thread(xml_builder.build_xml, log_callback=lambda m: logger.log(m))

            # 电信专用 XML（data/xml/epg_telecom.xml）
            tr = await asyncio.to_thread(
                xml_builder.build_telecom_xml,
                log_callback=lambda m: logger.log(m, category="电信EPG"),
            )
            if tr.get("success"):
                logger.log(
                    f"[任务] 电信XML: {tr['channel_count']} 频道 / {tr['programme_count']} 节目",
                    category="电信EPG")
            else:
                logger.log(f"[任务] 电信XML 跳过: {tr.get('error', '')}", category="电信EPG")

            self._mark_last_run("telecom_epg")
            logger.log("[任务] ====== 电信EPG下载完成 ======", category="定时任务")
        except Exception as e:
            logger.log(f"[任务] EPG下载异常: {e}", category="失败")


scheduler = TaskScheduler()