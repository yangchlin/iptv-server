# -*- coding: utf-8 -*-
"""中兴平台 RTSP 代理（回看用）

来源：原始备份里的 rtspproxy.py，整理成可启停的常驻服务：
- 监听一个本地端口（默认 554），把播放器的 RTSP 请求转发给真实服务器
- 自动跟随服务器的 302 / Location 重定向，改连到边缘节点
- 把中兴回看地址里的 PLTV/smil + playseek 翻译成服务器要的 live/mpg +
  programbegin/programend 形式
- 把响应里的 rtsp:// 地址改写回本代理地址，避免播放器直连内网 CDN

说明：这是「尽力而为」的协议转换代理，只处理 RTSP 握手阶段的改写；
握手完成后按原样双向转发（含 RTP 交织数据）。
"""
import re
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .logger import logger

DEFAULT_UA = "Lavf62.1.100"
MAX_HEADER = 65536
LOG_MAX = 300


def _now() -> str:
    return time.strftime("%H:%M:%S")


class RtspProxy:
    """可启停的 RTSP 代理。所有方法线程安全（用锁保护状态）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._server_socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_evt = threading.Event()
        self._connections: List[socket.socket] = []
        self._logs: List[Dict[str, str]] = []
        self._cfg: Dict[str, Any] = {}
        self._stats = {
            "accepted": 0,
            "active": 0,
            "errors": 0,
            "started_at": "",
        }
        self._last_error = ""

    # ── 状态 / 日志 ──
    def log(self, msg: str) -> None:
        line = str(msg)
        with self._lock:
            self._logs.append({"t": _now(), "msg": line})
            if len(self._logs) > LOG_MAX:
                del self._logs[:len(self._logs) - LOG_MAX]
        try:
            logger.log(f"[中兴代理] {line}", category="电信EPG")
        except Exception:
            pass

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "port": self._cfg.get("port", 0),
                "upstream": self._cfg.get("upstream", ""),
                "upstream_port": self._cfg.get("upstream_port", 554),
                "accepted": self._stats["accepted"],
                "active": self._stats["active"],
                "errors": self._stats["errors"],
                "started_at": self._stats["started_at"],
                "last_error": self._last_error,
                "logs": list(self._logs[-80:]),
            }

    # ── 启停 ──
    def start(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """启动代理。已运行且端口一致时直接返回成功。"""
        host = str(cfg.get("host") or "0.0.0.0")
        raw_port = cfg.get("port", 554)
        try:
            port = int(raw_port) if raw_port not in (None, "") else 554
        except (TypeError, ValueError):
            port = 554
        if port < 0 or port > 65535:
            port = 554
        upstream = str(cfg.get("upstream") or "").strip()
        try:
            upstream_port = int(cfg.get("upstream_port") or 554)
        except (TypeError, ValueError):
            upstream_port = 554
        bad_server = str(cfg.get("bad_server") or "").strip()

        if not upstream:
            msg = "未填写真实 RTSP 服务器地址（rtsp_upstream），代理无法启动"
            with self._lock:
                self._last_error = msg
            return {"success": False, "message": msg}

        with self._lock:
            if self._running and self._cfg.get("port") == port:
                return {"success": True, "message": f"RTSP 代理已在运行（端口 {port}）"}
            if self._running:
                self._stop_locked()

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                sock.listen(32)
            except OSError as e:
                try:
                    sock.close()
                except OSError:
                    pass
                msg = f"监听端口 {port} 失败: {e}（端口被占用或权限不足）"
                self._last_error = msg
                return {"success": False, "message": msg}

            # port 传 0 时由系统分配，取实际端口（测试 / 端口冲突时的兜底）
            try:
                actual_port = int(sock.getsockname()[1])
            except OSError:
                actual_port = port
            port = actual_port

            sock.settimeout(1.0)
            self._server_socket = sock
            self._cfg = {
                "host": host,
                "port": port,
                "upstream": upstream,
                "upstream_port": upstream_port,
                "bad_server": bad_server,
            }
            self._stop_evt.clear()
            self._running = True
            self._stats.update({"accepted": 0, "active": 0, "errors": 0,
                                "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            self._last_error = ""
            self._thread = threading.Thread(
                target=self._serve, name="zte-rtsp-proxy", daemon=True)
            self._thread.start()

        self.log(f"已启动：监听 {host}:{port}，转发到 {upstream}:{upstream_port}"
                 + (f"，丢弃节点 {bad_server}" if bad_server else ""))
        return {"success": True, "message": f"RTSP 代理已启动（端口 {port}）"}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            was_running = self._running
            self._stop_locked()
        if was_running:
            self.log("已停止")
        return {"success": True, "message": "RTSP 代理已停止" if was_running else "RTSP 代理本来就没运行"}

    def _stop_locked(self) -> None:
        self._stop_evt.set()
        self._running = False
        for s in (self._server_socket,):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        self._server_socket = None
        for c in list(self._connections):
            try:
                c.close()
            except OSError:
                pass
        self._connections.clear()
        self._stats["active"] = 0
        self._thread = None

    # ── 主循环 ──
    def _serve(self) -> None:
        sock = self._server_socket
        while not self._stop_evt.is_set() and sock is not None:
            try:
                client, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                self._stats["accepted"] += 1
                self._stats["active"] += 1
                self._connections.append(client)
            threading.Thread(
                target=self._handle, args=(client, addr), daemon=True,
                name="zte-rtsp-conn").start()

    def _handle(self, client: socket.socket, addr) -> None:
        upstream_sock: Optional[socket.socket] = None
        try:
            self.log(f"连接 {addr[0]}:{addr[1]}")
            upstream_sock = socket.create_connection(
                (self._cfg.get("upstream"), int(self._cfg.get("upstream_port", 554))),
                timeout=10)
            upstream_sock = self._handshake(client, upstream_sock, addr) or upstream_sock
        except Exception as e:
            with self._lock:
                self._stats["errors"] += 1
            self.log(f"连接失败: {e}")
        finally:
            for s in (client, upstream_sock):
                try:
                    if s:
                        s.close()
                except OSError:
                    pass
            with self._lock:
                self._stats["active"] = max(0, self._stats["active"] - 1)
                if client in self._connections:
                    self._connections.remove(client)

    # ── 握手阶段 ──
    # ── 握手阶段 ──
    def _handshake(self, client: socket.socket, upstream: socket.socket,
                   addr) -> Optional[socket.socket]:
        location = ""
        target_host = str(self._cfg.get("upstream"))
        target_port = int(self._cfg.get("upstream_port", 554))
        bad_server = str(self._cfg.get("bad_server") or "")
        client_host, client_port = "", 554

        for round_no in range(1, 13):
            req = _recv_message(client)
            if not req:
                return upstream
            
            method = req.split(b" ", 1)[0].strip().upper()
            client_host, client_port = _url_host_port(req, default_port=client_port)

            # 如果已经知道了边缘节点地址，之后的请求（SETUP, PLAY）直接发给边缘节点
            if location:
                req_text = req.decode("utf-8", "replace")
                m = re.search(r'playseek=(\d{14})-(\d{14})', req_text)
                if m:
                    start, end = m.group(1), m.group(2)
                    time_params = f"programbegin={start}+08&programend={end}+08"
                else:
                    time_params = ""
                
                sep = "&" if "?" in location else "?"
                new_url = f"{location}{sep}{time_params}" if time_params else location
                req = _set_request_url(req, new_url)
                req = _rewrite_host(req, f"rtsp://{target_host}:{target_port}")
            else:
                # 第一次请求（DESCRIBE）：发给中心服务器
                req = _rewrite_host(req, f"rtsp://{target_host}:{target_port}")
                req = _translate_request(req)

            resp = _exchange(upstream, req)
            if not resp:
                self.log("服务器无响应，关闭连接")
                return upstream

            # ★ 核心修复：如果在中心服务器收到了 302 重定向
            if not location and resp.startswith(b"RTSP/1.0 302"):
                location = _get_location(resp)
                th, tp = _url_host_port_from_text(location)
                if th and (not bad_server or th != bad_server):
                    self.log(f"收到重定向，改连边缘节点 {th}:{tp}")
                    target_host, target_port = th, tp
                    try:
                        upstream.close()
                        # 立刻连接到边缘节点
                        upstream = socket.create_connection((target_host, target_port), timeout=10)
                        
                        # 把刚才的请求组装上时间参数，重新发给边缘节点
                        req_text = req.decode("utf-8", "replace")
                        m = re.search(r'playseek=(\d{14})-(\d{14})', req_text)
                        if m:
                            start, end = m.group(1), m.group(2)
                            time_params = f"programbegin={start}+08&programend={end}+08"
                        else:
                            time_params = ""
                        sep = "&" if "?" in location else "?"
                        new_url = f"{location}{sep}{time_params}" if time_params else location
                        
                        # 替换 URL 和 Host 发给边缘节点
                        new_req = _set_request_url(req, new_url)
                        new_req = _rewrite_host(new_req, f"rtsp://{target_host}:{target_port}")
                        
                        # 获取边缘节点的 200 OK 响应
                        resp = _exchange(upstream, new_req)
                        if not resp:
                            self.log("边缘节点无响应")
                            return upstream
                    except OSError as e:
                        self.log(f"连接边缘节点失败: {e}")
                        return upstream

            # 把最终的 200 OK 响应发给客户端
            to_client = _rewrite_response(resp, client_host, client_port)
            client.sendall(to_client)

            # PLAY 的响应发完，就可以开始透传数据了
            if method == b"PLAY" and resp.startswith(b"RTSP/1.0 200"):
                break

        self.log("握手完成，开始转发流数据")
        _relay(client, upstream, self)
        return upstream

    def _follow_redirect(self, upstream: socket.socket, first_req: bytes,
                         bad_server: str):
        """首次响应不含 Session 时，主动发 DESCRIBE 拿重定向地址并连过去。"""
        describe = _to_describe(first_req)
        resp = _exchange(upstream, describe)
        if not resp:
            return None
        location = _get_location(resp)
        target_host, target_port = _url_host_port_from_text(location or resp.decode("utf-8", "replace"))
        if not target_host:
            self.log("响应里没有可用的重定向地址，继续使用原服务器")
            return None
        if bad_server and target_host == bad_server:
            self.log(f"重定向目标 {target_host} 在黑名单里，放弃")
            return None
        try:
            new_sock = socket.create_connection((target_host, target_port), timeout=10)
        except OSError as e:
            self.log(f"连接边缘节点失败: {e}")
            return None
        options_req = (
            f"OPTIONS {location} RTSP/1.0\r\n"
            f"CSeq: 1\r\n"
            f"User-Agent: {DEFAULT_UA}\r\n\r\n"
        ).encode("utf-8")
        try:
            new_resp = _exchange(new_sock, options_req)
        except OSError as e:
            self.log(f"边缘节点请求失败: {e}")
            new_resp = b""
        if not new_resp:
            try:
                new_sock.close()
            except OSError:
                pass
            return None
        try:
            upstream.close()
        except OSError:
            pass
        return new_sock, new_resp, target_host, target_port, location


# ════════════════════════════════════════════════════════════════
# 协议工具
# ════════════════════════════════════════════════════════════════

def _parse_headers(message: bytes) -> Dict[str, str]:
    head = message.split(b"\r\n\r\n", 1)[0].decode("utf-8", "replace")
    headers: Dict[str, str] = {}
    for line in head.split("\r\n")[1:]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return headers


def _recv_message(sock: socket.socket, timeout: float = 15.0) -> bytes:
    """精确读取一条 RTSP 消息（头 + Content-Length 指定的正文），不吞掉后续 RTP。"""
    sock.settimeout(timeout)
    buf = b""
    try:
        # 跳过前面可能出现的 RTP 交织帧（$ + channel + 2字节长度）
        while True:
            head = _peek(sock, 1)
            if head != b"$":
                break
            prefix = _recv_exact(sock, 4)
            if len(prefix) < 4:
                return b""
            length = int.from_bytes(prefix[2:4], "big")
            _recv_exact(sock, length)

        while not buf.endswith(b"\r\n\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                return b""
            buf += chunk
            if len(buf) > MAX_HEADER:
                return b""
    except (socket.timeout, OSError):
        return buf if buf.endswith(b"\r\n\r\n") else b""

    headers = _parse_headers(buf)
    try:
        length = int(headers.get("content-length", "0") or 0)
    except ValueError:
        length = 0
    if length > 0:
        buf += _recv_exact(sock, length)
    return buf


def _peek(sock: socket.socket, n: int) -> bytes:
    try:
        return sock.recv(n, socket.MSG_PEEK)
    except (AttributeError, OSError):
        return b""


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _exchange(sock: socket.socket, request: bytes) -> bytes:
    sock.sendall(request)
    return _recv_message(sock)


_URL_RE = re.compile(r'rtsp://[^\s/]+', re.IGNORECASE)
_REQUEST_LINE_RE = re.compile(
    r'(?m)^(OPTIONS|DESCRIBE|SETUP|PLAY|PAUSE|TEARDOWN|GET_PARAMETER|SET_PARAMETER|ANNOUNCE|RECORD)'
    r'\s+\S+')


def _rewrite_host(message: bytes, new_base: str) -> bytes:
    """只替换 rtsp://host:port 部分，保留路径（new_base 形如 rtsp://1.2.3.4:554）。"""
    text = message.decode("utf-8", "replace")
    text = _URL_RE.sub(new_base.rstrip("/"), text)
    return text.encode("utf-8")


def _set_request_url(message: bytes, url: str) -> bytes:
    """把请求行里的完整 URL 换成 url（跟随 Location 重定向时用）。"""
    text = message.decode("utf-8", "replace")
    text = _REQUEST_LINE_RE.sub(lambda m: f"{m.group(1)} {url}", text, count=1)
    return text.encode("utf-8")


def _rewrite_response(resp: bytes, host: str, port: int) -> bytes:
    if not host:
        return resp
    return _rewrite_host(resp, f"rtsp://{host}:{port}")


def _url_host_port(message: bytes, default_port: int = 554) -> Tuple[str, int]:
    text = message.decode("utf-8", "replace")
    m = re.search(r'rtsp://([^/\s:]+)(?::(\d+))?', text, re.IGNORECASE)
    if not m:
        return "", default_port
    return m.group(1), int(m.group(2) or default_port)


def _url_host_port_from_text(text: str) -> Tuple[str, int]:
    m = re.search(r'rtsp://([^/\s:]+)(?::(\d+))?', text or "", re.IGNORECASE)
    if not m:
        return "", 554
    return m.group(1), int(m.group(2) or 554)


def _get_location(resp: bytes) -> str:
    headers = _parse_headers(resp)
    loc = headers.get("location", "")
    if loc:
        return loc
    # 有些服务器把重定向地址放在响应正文里
    body = resp.split(b"\r\n\r\n", 1)[-1].decode("utf-8", "replace")
    m = re.search(r'rtsp://[^\s"\'<>]+', body)
    return m.group(0) if m else ""


def _to_describe(request: bytes) -> bytes:
    text = request.decode("utf-8", "replace")
    lines = text.split("\r\n")
    if lines:
        lines[0] = lines[0].replace("OPTIONS", "DESCRIBE", 1)
    out = []
    inserted = False
    for i, line in enumerate(lines):
        if i == 1 and not inserted:
            out.append("Accept: application/sdp")
            inserted = True
        out.append(line)
    # 把 CSeq 改成 2，避免和客户端请求的序号冲突
    # ★ 只能用 [ \t]，不能用 \s：后者会连行尾的 \r\n 一起吃进替换内容，导致请求头被截断
    text2 = "\r\n".join(out)
    text2 = re.sub(r'(?m)^CSeq:[ \t]*\d+[ \t]*\r?$', 'CSeq: 2', text2, count=1)
    if not text2.endswith("\r\n\r\n"):
        text2 = text2.rstrip("\r\n") + "\r\n\r\n"
    return text2.encode("utf-8")


def _translate_request(request: bytes) -> bytes:
    """中兴回看 / 直播地址翻译（与原始 rtspsproxy.py 的 Translator 一致）。"""
    text = request.decode("utf-8", "replace")
    if "playseek" in text:
        m = re.search(r'playseek=\d{14}-\d{14}', text)
        seek = m.group(0) if m else ""
        if seek:
            program = seek.replace("playseek", "programbegin").replace("-", "+08&programend=") + "+08"
            text = (text.replace("TVOD", "live")
                        .replace("smil?", "mpg?vcdnid=001&boid=001&contname=&")
                        .replace(seek, program))
    else:
        text = text.replace("PLTV", "live").replace("smil", "sdp?vcdnid=001")
    return text.encode("utf-8")


def _relay(client: socket.socket, upstream: socket.socket, proxy: RtspProxy) -> None:
    def pipe(src: socket.socket, dst: socket.socket):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    t1 = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
    t2 = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


# ════════════════════════════════════════════════════════════════
# 与配置联动
# ════════════════════════════════════════════════════════════════

def proxy_config_from_settings() -> Dict[str, Any]:
    cfg = config.get_platform_config("zte")
    try:
        port = int(cfg.get("rtsp_proxy_port") or 554)
    except (TypeError, ValueError):
        port = 554
    try:
        upstream_port = int(cfg.get("rtsp_upstream_port") or 554)
    except (TypeError, ValueError):
        upstream_port = 554
    return {
        "host": "0.0.0.0",
        "port": port,
        "upstream": str(cfg.get("rtsp_upstream", "") or "").strip(),
        "upstream_port": upstream_port,
        "bad_server": str(cfg.get("rtsp_proxy_bad_server", "") or "").strip(),
    }


def ensure_proxy_from_settings() -> Dict[str, Any]:
    """按配置启动 / 停止代理（服务启动时和保存配置后调用）。"""
    cfg = config.get_platform_config("zte")
    if bool(cfg.get("rtsp_proxy_enabled", False)):
        return rtsp_proxy.start(proxy_config_from_settings())
    if rtsp_proxy.status().get("running"):
        return rtsp_proxy.stop()
    return {"success": True, "message": "代理未启用"}


rtsp_proxy = RtspProxy()
