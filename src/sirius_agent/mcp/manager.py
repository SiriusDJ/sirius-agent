"""多个 MCP server 的统一管理：并发启动、失败隔离、工具命名校验与注册、统一关闭。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from sirius_agent.mcp.adapter import McpTool, is_valid_tool_name
from sirius_agent.mcp.config import McpServerConfig, Warn, default_warn
from sirius_agent.mcp.connection import McpConnection
from sirius_agent.tools.registry import ToolRegistry

CLOSE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ServerStatus:
    """一个 server 的启动结果，供入口打印摘要。"""

    name: str
    ok: bool
    tool_count: int
    error: str | None = None


class McpManager:
    """持有全部连接（即连接缓存）：启动后常驻复用，退出时统一关闭。"""

    def __init__(
        self,
        configs: dict[str, McpServerConfig],
        connection_factory: Callable[[McpServerConfig], McpConnection],
        warn: Warn = default_warn,
    ) -> None:
        self._configs = configs
        self._connection_factory = connection_factory
        self._warn = warn
        self._connections: list[McpConnection] = []
        self._tools: list[McpTool] = []

    async def start_all(self) -> list[ServerStatus]:
        """并发连接全部 server；单个失败只跳过它自己，失败原因走 warn。"""

        connections = [self._connection_factory(config) for config in self._configs.values()]
        self._connections = connections
        results = await asyncio.gather(*(conn.start() for conn in connections), return_exceptions=True)

        statuses: list[ServerStatus] = []
        for conn, result in zip(connections, results, strict=True):
            if isinstance(result, BaseException):
                self._warn(f"server '{conn.name}' 启动失败，已跳过：{result}")
                statuses.append(ServerStatus(name=conn.name, ok=False, tool_count=0, error=str(result)))
                continue
            tools = self._adapt_tools(conn, result)
            self._tools.extend(tools)
            statuses.append(ServerStatus(name=conn.name, ok=True, tool_count=len(tools)))
        return statuses

    def _adapt_tools(self, conn: McpConnection, remote_tools: list) -> list[McpTool]:
        by_name: dict[str, McpTool] = {}
        for remote in remote_tools:
            tool = McpTool(conn.name, remote, conn, self._warn)
            if not is_valid_tool_name(tool.name):
                self._warn(f"工具名 '{tool.name}' 含有 [A-Za-z0-9_-] 以外的字符，已跳过")
                continue
            if tool.name in by_name:
                self._warn(f"server '{conn.name}' 报告了重名工具 '{remote.name}'，保留后出现的定义")
            by_name[tool.name] = tool
        return list(by_name.values())

    @property
    def tools(self) -> list[McpTool]:
        return list(self._tools)

    def register_into(self, registry: ToolRegistry) -> list[str]:
        """把全部成功适配的 MCP 工具注册进工具中心，返回注册的工具名。"""

        for tool in self._tools:
            registry.register(tool)
        return [tool.name for tool in self._tools]

    async def close_all(self, timeout: float = CLOSE_TIMEOUT_SECONDS) -> None:
        """并发关闭全部连接（含启动失败的）；超过 timeout 不再等待，放弃剩余连接。"""

        if not self._connections:
            return
        closing = asyncio.gather(*(conn.close() for conn in self._connections), return_exceptions=True)
        try:
            await asyncio.wait_for(closing, timeout)
        except TimeoutError:
            self._warn(f"部分 MCP 连接未能在 {timeout:g}s 内关闭，已放弃等待")
