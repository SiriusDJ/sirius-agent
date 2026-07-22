"""Tool 层的统一接口与基础数据类型。

每个具体工具都要满足 Tool 协议：声明名称、描述、参数 JSON Schema，
并提供 execute 方法。可预期的业务失败（文件不存在、路径越界等）
由工具自己捕获并转成 ok=False 的 ToolResult，而不是抛异常。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ToolCall:
    """模型请求的一次工具调用（参数已拼接、解析完整）。"""

    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    """工具执行的结构化结果。"""

    ok: bool
    content: str


class Tool(Protocol):
    """所有具体工具都要满足的统一接口。"""

    name: str
    description: str
    parameters_schema: dict

    def execute(self, arguments: dict) -> ToolResult:
        """执行工具并返回结构化结果。"""
        ...
