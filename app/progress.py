"""下载进度管理 - 独立模块

供 main.py 与 telecom_api.py 共用，避免循环导入。
仅承载 DownloadProgress 类与全局实例，不依赖项目内其它模块。
"""
import datetime


class DownloadProgress:
    """进程级下载进度容器。

    字段前缀下划线表示"内部使用"，外部通过 get_messages / clear / set_progress
    以及 is_paused / is_stopped 方法访问。_running / _paused / _stopped 允许
    外部直接写，保持与原 main.py 代码完全一致。
    """

    def __init__(self):
        self._messages = []                                   # 最近的进度日志（时间前缀）
        self._running = False                                 # 是否有任务在跑
        self._paused = False                                  # 是否暂停
        self._stopped = False                                 # 是否被停止
        self._current = {"current": 0, "total": 0, "message": ""}

    async def add_message(self, msg: str):
        """追加一条进度消息（带 HH:MM:SS 时间戳）。"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self._messages.append(f"[{timestamp}] {msg}")
        self._current["message"] = msg

    def get_messages(self):
        """返回消息列表的副本（避免外部修改内部状态）。"""
        return list(self._messages)

    def clear(self):
        """清空消息与进度。"""
        self._messages.clear()
        self._current = {"current": 0, "total": 0, "message": ""}

    def set_progress(self, current: int, total: int):
        """更新当前/总数进度。"""
        self._current["current"] = current
        self._current["total"] = total

    def is_paused(self):
        return self._paused

    def is_stopped(self):
        return self._stopped


# 全局实例
download_progress = DownloadProgress()