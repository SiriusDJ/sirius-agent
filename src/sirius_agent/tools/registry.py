"""工具注册中心：按名注册/查找/执行工具，对未预期异常统一兜底。"""

from __future__ import annotations

from sirius_agent.tools.base import Tool, ToolResult


class ToolError(Exception):
    """工具注册/查找相关的错误（如按名找不到工具）。"""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"未知的工具：{name}") from None

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def execute(self, name: str, arguments: dict) -> ToolResult:
        tool = self.get(name)
        try:
            return tool.execute(arguments)
        except Exception as e:  # noqa: BLE001 - 工具执行的意外异常统一兜底转成结构化结果
            return ToolResult(ok=False, content=f"工具执行出现意外错误：{e}")
