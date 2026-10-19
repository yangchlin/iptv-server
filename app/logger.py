"""日志模块 - 支持分类和全局日志"""
import datetime
import re
from pathlib import Path
from typing import Optional, List

from . import config


class Logger:
    """每日日志管理器（支持分类）"""

    # 分类固定顺序（下载->EPG->清理->定时任务->其他->失败->全部）
    CATEGORY_ORDER = ["下载", "EPG", "清理", "定时任务", "其他", "失败", "全部"]

    # 中英文别名 -> 标准分类
    _CATEGORY_ALIAS = {
        "download": "下载", "下载": "下载",
        "epg": "EPG", "EPG": "EPG",
        "clean": "清理", "清理": "清理", "cleanup": "清理",
        "schedule": "定时任务", "定时任务": "定时任务", "task": "定时任务", "定时": "定时任务", "调度": "定时任务",
        "other": "其他", "其他": "其他", "misc": "其他",
        "fail": "失败", "失败": "失败", "error": "失败", "错误": "失败",
        "all": "全部", "全部": "全部",
    }

    # 关键词兑底检测映射（显式指定优先，仅当 category 为空时用）
    CATEGORY_MAP = {
        "失败": ["✗", "失败", "错误", "出错", "异常", "Exception", "❌"],
        "下载": ["下载", "开始下载", "APK", "进度", "Download"],
        "EPG": ["EPG", "EPG源", "提取", "节目", "合并XML", "XML生成"],
        "清理": ["清理", "过期", "删除"],
        "定时任务": [],
        "其他": [],
    }

    def __init__(self, max_global: int = 1000):
        self._logs = {}  # date -> list of messages
        self._global_buffer = []  # ring buffer of recent entries
        self._max_global = max_global

    def _get_log_path(self, date_str: str = None) -> Path:
        if date_str is None:
            date_str = datetime.datetime.now().strftime("%Y%m%d")
        return config.LOG_DIR / f"download_{date_str}.log"

    def _resolve_category(self, category: str, message: str) -> str:
        """解析分类：显式分类优先，否则按关键词兑底检测"""
        if category:
            # 支持中英文别名
            cat = self._CATEGORY_ALIAS.get(str(category).strip(), str(category).strip())
            if cat in self.CATEGORY_ORDER:
                return cat
        # 键词兑底检测（失败优先）
        for kw in self.CATEGORY_MAP.get("失败", []):
            if kw in message:
                return "失败"
        for cat, kws in self.CATEGORY_MAP.items():
            if cat == "失败":
                continue
            for kw in kws:
                if kw in message:
                    return cat
        return "其他"

    def log(self, message: str, category: str = "", date_str: str = None):
        """写入日志

        Args:
            message: 日志内容
            category: 显式分类（"下载"/"EPG"/"清理"/"定时任务"/"其他"/"失败"），
                      为空时按关键词自动检测
            date_str: 日志日期
        """
        if date_str is None:
            date_str = datetime.datetime.now().strftime("%Y%m%d")
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        category = self._resolve_category(category, message)
        log_line = f"[{timestamp}] {message}"

        # 内存缓存（按日期）
        if date_str not in self._logs:
            self._logs[date_str] = []
        self._logs[date_str].append(log_line)

        # 全局环形缓冲区
        entry = {
            "timestamp": timestamp,
            "message": message,
            "category": category,
            "date": date_str,
            "line": log_line,
        }
        self._global_buffer.append(entry)
        if len(self._global_buffer) > self._max_global:
            self._global_buffer.pop(0)

        # 写入文件
        log_path = self._get_log_path(date_str)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(log_line + "\n")
        except OSError as e:
            print(f"[日志] 写入失败: {e}")

    def get_global(self, category: str = "", limit: int = 200) -> list:
        """获取全局日志，可按分类筛选"""
        entries = list(reversed(self._global_buffer))  # 最新的在前
        if category and category != "全部":
            entries = [e for e in entries if e["category"] == category]
        return entries[:limit]

    def get_categories(self) -> list:
        """获取所有分类（固定顺序）"""
        return list(self.CATEGORY_ORDER)

    def get_logs(self, date_str: str = None) -> list:
        """获取指定日期的日志"""
        if date_str is None:
            date_str = datetime.datetime.now().strftime("%Y%m%d")

        if date_str in self._logs:
            return self._logs[date_str]

        log_path = self._get_log_path(date_str)
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    return [line.strip() for line in f if line.strip()]
            except OSError:
                pass
        return []

    def get_log_files(self) -> list:
        """获取所有日志文件"""
        files = []
        for f in sorted(config.LOG_DIR.glob("*.log"), reverse=True):
            try:
                files.append({
                    "name": f.name,
                    "path": str(f),
                    "size": f.stat().st_size,
                    "date": f.name.replace("download_", "").replace(".log", ""),
                })
            except OSError:
                pass
        return files


    def info(self, msg, category=""):
        self.log(msg, category=category)
    def error(self, msg, category="失败"):
        self.log(msg, category=category)
    def warning(self, msg, category=""):
        self.log(msg, category=category)

# 全局日志实例
logger = Logger()
