"""Streamable HTTP 传输：进程内 uvicorn 跑测试 server，外包一层中间件记录每个请求的方法与请求头。"""

from __future__ import annotations

import socket
import sys
import threading
import time
from functools import partial
from pathlib import Path

import pytest
import uvicorn

from sirius_agent.mcp.config import HttpServerConfig
from sirius_agent.mcp.connection import McpConnection, McpConnectionError, default_client_factory

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "mcp"))
from modern_server import build_server  # noqa: E402


class _HeaderRecorder:
    """ASGI 中间件：记录每个 HTTP 请求的 (method, headers)，其余原样转发。"""

    def __init__(self, app) -> None:
        self.app = app
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            self.requests.append((scope["method"], headers))
        await self.app(scope, receive, send)


@pytest.fixture(scope="module")
def http_server():
    recorder = _HeaderRecorder(build_server().streamable_http_app())
    server = uvicorn.Server(uvicorn.Config(recorder, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "测试 HTTP server 未能在 10s 内启动"
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/mcp", recorder
    server.should_exit = True
    thread.join(timeout=10)


def _connection(config: HttpServerConfig, **kwargs) -> McpConnection:
    return McpConnection(config, partial(default_client_factory, cwd=Path.cwd()), **kwargs)


async def test_http_lists_and_calls_tools_with_injected_headers(http_server):
    url, recorder = http_server
    recorder.requests.clear()
    conn = _connection(
        HttpServerConfig(name="remote", url=url, headers={"Authorization": "Bearer test-token"})
    )
    try:
        tools = await conn.start()
        assert "add" in {t.name for t in tools}

        result = await conn.call_tool("add", {"a": 20, "b": 22})
        assert result.content[0].text == "42"
    finally:
        await conn.close()

    posts = [headers for method, headers in recorder.requests if method == "POST"]
    assert posts, "没有记录到任何 POST 请求"
    for headers in posts:
        assert headers.get("authorization") == "Bearer test-token"
        assert headers.get("mcp-protocol-version")
        assert headers.get("mcp-method")

    call_requests = [h for h in posts if h.get("mcp-method") == "tools/call"]
    assert len(call_requests) == 1
    assert call_requests[0].get("mcp-name") == "add"
    # 新协议下只走 POST：不开 GET 通道
    assert all(method != "GET" for method, _ in recorder.requests)


async def test_http_unreachable_url_fails_with_connection_error():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # 拿到一个此刻无人监听的端口
    conn = _connection(HttpServerConfig(name="down", url=f"http://127.0.0.1:{port}/mcp"), start_timeout=10)
    try:
        with pytest.raises(McpConnectionError):
            await conn.start()
    finally:
        await conn.close()
