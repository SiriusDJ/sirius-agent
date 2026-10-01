"""单个 MCP server 的连接生命周期。

官方 SDK 的 Client 是 anyio 异步上下文管理器，要求进入与退出发生在同一个 task 里；
而我们在启动时并发连接、在程序退出时统一关闭，两处不在同一 task。所以每个连接起一个常驻
worker task：在 worker 内 `async with Client(...)` 完成协议探测/回退握手与列工具，
通知"就绪"后挂起等待关闭信号，收到后在同一 task 内退出上下文（SDK 负责关 stdin、
等待/终止子进程、关闭 HTTP 连接）。其它 task 通过已进入的 client 发起工具调用。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult, Implementation
from mcp_types import Tool as RemoteTool

from sirius_agent.mcp.config import McpServerConfig, StdioServerConfig

START_TIMEOUT_SECONDS = 30.0
CALL_TIMEOUT_SECONDS = 30.0

ClientFactory = Callable[[McpServerConfig], contextlib.AbstractAsyncContextManager[Client]]


class McpConnectionError(Exception):
    """连接失败、启动超时、调用超时、连接已断开统一用它；消息是人类可读的中文。"""


def _client_info() -> Implementation:
    try:
        sirius_agent_version = version("sirius-agent")
    except PackageNotFoundError:
        sirius_agent_version = "0.0.0"
    return Implementation(name="sirius-agent", version=sirius_agent_version)


@contextlib.asynccontextmanager
async def default_client_factory(config: McpServerConfig, cwd: Path) -> AsyncIterator[Client]:
    """按传输类型构造并进入 SDK Client（mode="auto"：先 server/discover 探测，失败回退 initialize）。"""

    if isinstance(config, StdioServerConfig):
        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env or None,  # SDK 会把它合并到宿主默认环境之上
            cwd=cwd,
        )
        async with Client(params, mode="auto", client_info=_client_info()) as client:
            yield client
        return

    # SDK 只关闭它自己创建的 http client，外部传入的由这里负责关闭
    async with httpx2.AsyncClient(headers=config.headers, timeout=httpx2.Timeout(30.0, read=300.0)) as http:
        transport = streamable_http_client(config.url, http_client=http)
        async with Client(transport, mode="auto", client_info=_client_info()) as client:
            yield client


def _describe(error: BaseException) -> str:
    """把（可能嵌套的）异常组展开成一行可读原因。"""

    if isinstance(error, BaseExceptionGroup):
        return "；".join(_describe(e) for e in error.exceptions)
    text = str(error)
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


class McpConnection:
    """一个 server 一条连接：start() 建连并列出工具，call_tool() 复用连接调用，close() 收尾。"""

    def __init__(
        self,
        config: McpServerConfig,
        client_factory: ClientFactory,
        start_timeout: float = START_TIMEOUT_SECONDS,
        call_timeout: float = CALL_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config
        self.name = config.name
        self._client_factory = client_factory
        self._start_timeout = start_timeout
        self._call_timeout = call_timeout
        self._client: Client | None = None
        self._worker: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[list[RemoteTool]] | None = None
        self._close_event = asyncio.Event()

    @property
    def alive(self) -> bool:
        return self._client is not None

    async def start(self) -> list[RemoteTool]:
        """建立连接、完成协议协商并列出全部工具；失败或超时抛 McpConnectionError。"""

        self._ready = asyncio.get_running_loop().create_future()
        self._worker = asyncio.create_task(self._run(), name=f"mcp-server-{self.name}")
        try:
            return await asyncio.wait_for(asyncio.shield(self._ready), self._start_timeout)
        except TimeoutError:
            self._ready.cancel()
            self._worker.cancel()
            raise McpConnectionError(f"启动超时（{self._start_timeout:g}s）") from None

    async def _run(self) -> None:
        assert self._ready is not None
        try:
            async with self._client_factory(self.config) as client:
                tools = await self._list_all_tools(client)
                if self._ready.done():  # start() 已超时放弃
                    return
                self._client = client
                self._ready.set_result(tools)
                await self._close_event.wait()
        except asyncio.CancelledError:
            self._fail_ready("连接被取消")
            raise
        except Exception as e:  # noqa: BLE001 - 连接/协商/列工具/运行期断开的任何错误都只影响本 server
            self._fail_ready(_describe(e))
        finally:
            self._client = None

    def _fail_ready(self, reason: str) -> None:
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(McpConnectionError(reason))

    @staticmethod
    async def _list_all_tools(client: Client) -> list[RemoteTool]:
        tools: list[RemoteTool] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                return tools

    async def call_tool(self, tool: str, arguments: dict) -> CallToolResult:
        """通过已建立的连接调用远端工具；未连接/已断开/超时抛 McpConnectionError。"""

        client = self._client
        if client is None:
            raise McpConnectionError(f"server '{self.name}' 未连接或连接已断开")
        try:
            return await asyncio.wait_for(client.call_tool(tool, arguments), self._call_timeout)
        except TimeoutError:
            raise McpConnectionError(f"调用超时（{self._call_timeout:g}s）") from None
        except Exception as e:  # noqa: BLE001 - SDK 的协议错误/连接关闭等统一成一种异常
            raise McpConnectionError(f"调用失败：{_describe(e)}") from e

    async def close(self) -> None:
        """通知 worker 退出上下文并等待它结束；本身不设超时，整体兜底由 McpManager 负责。"""

        self._close_event.set()
        if self._worker is not None:
            await asyncio.gather(self._worker, return_exceptions=True)
