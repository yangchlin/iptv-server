# -*- coding: utf-8 -*-
"""后台任务工具。

asyncio.create_task() 只被事件循环弱引用，返回的 Task 如果没人保存，
可能在执行途中被垃圾回收（日志里表现为任务莫名其妙“消失”）。
这里统一保存强引用，任务结束后再释放。
"""
import asyncio
from typing import Any, Coroutine, Set

_tasks: Set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """创建后台任务并保持引用，返回 Task。"""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def running_tasks() -> int:
    """当前还在跑的后台任务数（测试/排查用）。"""
    return len(_tasks)
