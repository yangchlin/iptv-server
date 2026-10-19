"""代理转发模块"""
import httpx as _httpx
from . import proxy_monitor as _pm


_proxy_standalone_client = None


def get_proxy_client_standalone():
    """获取不跟重定向的代理客户端"""
    global _proxy_standalone_client
    if _proxy_standalone_client is None:
        _proxy_standalone_client = _httpx.AsyncClient(
            timeout=None,
            follow_redirects=False,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            limits=_httpx.Limits(max_keepalive_connections=50, max_connections=100),
        )
    return _proxy_standalone_client


MAX_REDIRECTS = 10


async def proxy_forward(request, target_url: str):
    """代理转发核心逻辑 —— 自动追重定向，最终返回 200 的流

    返回 (status_code, headers, body_or_iterator, media_type)
    body_or_iterator 为 async generator 时流式返回
    """
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        target_url = "http://" + target_url

    if request.url.query:
        sep = "&" if "?" in target_url else "?"
        target_url += f"{sep}{request.url.query}"

    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    remote_addr = forwarded or request.client.host
    upstream_addr = request.headers.get("host", "")
    req_head_size = len(request.method) + len(target_url) + sum(len(k) + len(v) + 4 for k, v in request.headers.items()) if request.headers else 200
    _pm.register(remote_addr, target_url, upstream_addr, bytes_sent=req_head_size)

    fwd_headers = dict(request.headers)
    fwd_headers.pop("host", None)
    fwd_headers.pop("connection", None)
    fwd_headers.pop("accept-encoding", None)

    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    client = get_proxy_client_standalone()

    redirect_count = 0
    current_url = target_url
    resp = None

    try:
        while redirect_count <= MAX_REDIRECTS:
            async with client.stream(
                method=request.method,
                url=current_url,
                headers=fwd_headers,
                content=body,
            ) as r:
                resp = r
                status = resp.status_code
                up_addr = str(resp.url.host) + ":" + str(resp.url.port) if resp.url else upstream_addr

                if status not in [301, 302, 303, 307, 308]:
                    break

                redirect_count += 1
                loc = resp.headers.get("location", "")
                if not loc:
                    return 502, {}, "Redirect without Location header", "text/plain"

                from urllib.parse import urljoin
                current_url = urljoin(str(resp.url), loc)

                if redirect_count > MAX_REDIRECTS:
                    resp_headers = {
                        k: v for k, v in resp.headers.items()
                        if k.lower() not in ["content-encoding", "transfer-encoding", "content-length", "connection"]
                    }
                    return status, resp_headers, None, None

        if resp is None:
            return 502, {}, "No response from upstream", "text/plain"

        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in ["content-encoding", "transfer-encoding", "content-length", "connection"]
        }
        ctype = resp.headers.get("content-type")
        received = 0

        async def _stream():
            nonlocal received
            try:
                async for chunk in resp.aiter_bytes():
                    received += len(chunk)
                    yield chunk
            except Exception:
                pass
            finally:
                _pm.register(remote_addr, target_url, up_addr, bytes_received=received)

        return status, resp_headers, _stream(), ctype

    except Exception as e:
        return 502, {}, f"Proxy error: {str(e)}", "text/plain"
