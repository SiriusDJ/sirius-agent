"""测试用 MCP server：基于官方 SDK 的 MCPServer，同时支持新协议（server/discover）与旧协议握手。

用法：
  python modern_server.py            # stdio
  python modern_server.py --http N   # streamable-http，监听 127.0.0.1:N/mcp
进程内 HTTP 测试直接 import build_server()。
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ImageContent, TextContent, ToolAnnotations

_PNG_1PX = base64.b64encode(bytes.fromhex("89504e470d0a1a0a")).decode()


def build_server() -> MCPServer:
    server = MCPServer("sirius-agent-test-server")

    @server.tool(description="原样回显 text", annotations=ToolAnnotations(read_only_hint=True))
    def echo(text: str) -> str:
        return text

    @server.tool(description="计算 a + b")
    def add(a: int, b: int) -> str:
        return str(a + b)

    @server.tool(description="总是失败")
    def fail() -> str:
        raise ToolError("boom from server")

    @server.tool(description="返回文本与图片混合内容", structured_output=False)
    def mixed() -> list[TextContent | ImageContent]:
        return [
            TextContent(type="text", text="first"),
            ImageContent(type="image", data=_PNG_1PX, mime_type="image/png"),
            TextContent(type="text", text="second"),
        ]

    @server.tool(description="睡 seconds 秒后返回")
    async def slow(seconds: float) -> str:
        await asyncio.sleep(seconds)
        return "done"

    @server.tool(description="读取 server 进程的环境变量", annotations=ToolAnnotations(read_only_hint=True))
    def get_env(name: str) -> str:
        return os.environ.get(name, "<unset>")

    @server.tool(description="返回 server 进程 pid", annotations=ToolAnnotations(read_only_hint=True))
    def get_pid() -> str:
        return str(os.getpid())

    @server.tool(description="让 server 进程立即退出（模拟运行期崩溃）")
    def crash() -> str:
        os._exit(1)

    return server


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--http":
        build_server().run("streamable-http", port=int(sys.argv[2]))
    else:
        build_server().run()
