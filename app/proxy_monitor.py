"""代理连接内存追踪模块
实时记录每个客户端的代理请求流量，按客户端 IP 聚合。
"""
import time as _time
import datetime as _dt
import threading as _threading

_lock = _threading.Lock()

# 按客户端 IP 聚合：key = remote_addr
_client_pool: dict = {}


class _ClientEntry:
    __slots__ = (
        "target_url", "upstream_addr", "remote_addr",
        "bytes_sent", "bytes_received",
        "first_seen", "last_seen",
        "count",
    )

    def __init__(self, remote_addr: str):
        self.remote_addr = remote_addr
        self.target_url = ""
        self.upstream_addr = ""
        self.bytes_sent = 0
        self.bytes_received = 0
        self.first_seen = _dt.datetime.now()
        self.last_seen = self.first_seen
        self.count = 0


def register(remote_addr: str, target_url: str, upstream_addr: str,
             bytes_sent: int = 0, bytes_received: int = 0):
    """注册一次代理请求，按客户端 IP 聚合"""
    now = _dt.datetime.now()
    with _lock:
        entry = _client_pool.get(remote_addr)
        if entry is None:
            entry = _ClientEntry(remote_addr)
            _client_pool[remote_addr] = entry

        entry.bytes_sent += bytes_sent
        entry.bytes_received += bytes_received
        entry.count += 1
        entry.last_seen = now

        # 保留 index.m3u8 的 URL 作为展示地址
        if "index.m3u8" in target_url:
            entry.target_url = target_url
            entry.upstream_addr = upstream_addr


def get_connections() -> list:
    """获取当前所有客户端连接快照"""
    now = _dt.datetime.now()
    result = []
    stale_keys = []
    with _lock:
        for remote_addr, entry in _client_pool.items():
            elapsed = (now - entry.first_seen).total_seconds()
            bw_sent = entry.bytes_sent / elapsed if elapsed > 0.5 else 0
            bw_recv = entry.bytes_received / elapsed if elapsed > 0.5 else 0
            result.append({
                "target_url": entry.target_url,
                "remote_addr": entry.remote_addr,
                "upstream_addr": entry.upstream_addr,
                "count": entry.count,
                "bytes_sent": entry.bytes_sent,
                "bytes_received": entry.bytes_received,
                "bandwidth_sent": round(bw_sent, 2),
                "bandwidth_received": round(bw_recv, 2),
                "last_seen": entry.last_seen.isoformat(),
            })
            # 超过 30 秒无更新的客户端标记为过期
            if (now - entry.last_seen).total_seconds() > 30:
                stale_keys.append(remote_addr)
        for k in stale_keys:
            del _client_pool[k]

    result.sort(key=lambda x: x["last_seen"], reverse=True)
    return result


def get_total_bytes() -> tuple:
    """获取所有客户端的总字节数"""
    total_sent = 0
    total_recv = 0
    with _lock:
        for entry in _client_pool.values():
            total_sent += entry.bytes_sent
            total_recv += entry.bytes_received
    return total_sent, total_recv
