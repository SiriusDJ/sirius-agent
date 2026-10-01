"""远端 MCP 工具 ↔ sirius-agent Tool 协议的适配层。

McpTool 对 Agent / provider 完全透明：名字、描述、参数 schema、safe 标记与内置工具同构，
execute 里把一切失败（远端报错、超时、连接断开）收敛成 ok=False 的 ToolResult，从不抛异常。
"""

from __future__ import annotations

import re
from typing import Protocol

from mcp_types import CallToolResult, TextContent
from mcp_types import Tool as RemoteTool

from sirius_agent.mcp.config import Warn
from sirius_agent.tools.base import ToolResult

TOOL_NAME_PREFIX = "mcp__"
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class McpConnectionLike(Protocol):
    """McpTool 对连接的最小依赖：只需要能按远端工具名发起调用。"""

    async def call_tool(self, tool: str, arguments: dict) -> CallToolResult: ...


def build_tool_name(server: str, tool: str) -> str:
    """远端工具在工具中心里的名字：mcp__<server>__<tool>，server/tool 原样保留。"""

    return f"{TOOL_NAME_PREFIX}{server}__{tool}"


def is_valid_tool_name(name: str) -> bool:
    """LLM 工具名只允许 [A-Za-z0-9_-]。"""

    return _TOOL_NAME_RE.fullmatch(name) is not None


def convert_call_result(result: CallToolResult, tool_name: str, warn: Warn) -> ToolResult:
    """文本块按顺序以换行拼接；非文本块丢弃并至多告警一次；远端 is_error 映射为 ok=False。"""

    texts: list[str] = []
    dropped: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            texts.append(block.text)
        else:
            dropped.append(getattr(block, "type", type(block).__name__))

    if dropped:
        warn(f"工具 {tool_name} 的返回中有 {len(dropped)} 个非文本内容块（{', '.join(dropped)}），已丢弃")

    return ToolResult(ok=not result.is_error, content="\n".join(texts))


class McpTool:
    """把一个远端工具包装成满足 sirius-agent Tool 协议的对象。"""

    def __init__(
        self, server_name: str, remote: RemoteTool, connection: McpConnectionLike, warn: Warn
    ) -> None:
        self.name = build_tool_name(server_name, remote.name)
        self.description = remote.description or f"来自 MCP server '{server_name}' 的工具 {remote.name}"
        self.parameters_schema: dict = dict(remote.input_schema)
        # 只有远端明确声明 readOnlyHint=True 才按只读处理，缺失/非法一律按有副作用（安全默认）
        self.safe = remote.annotations is not None and remote.annotations.read_only_hint is True
        self.server_name = server_name
        self.remote_name = remote.name
        self._connection = connection
        self._warn = warn

    async def execute(self, arguments: dict) -> ToolResult:
        try:
            result = await self._connection.call_tool(self.remote_name, arguments)
        except Exception as e:  # noqa: BLE001 - 超时/断连/协议错误统一回灌给模型，不中断 Agent Loop
            return ToolResult(ok=False, content=f"MCP 工具 {self.name} 调用失败：{e}")
        return convert_call_result(result, self.name, self._warn)
