"""把统一的 Tool 描述转换成 Anthropic / OpenAI 各自 API 要求的工具描述格式。"""

from __future__ import annotations

from sirius_agent.tools.base import Tool


def to_anthropic_tool_schema(tools: list[Tool]) -> list[dict]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters_schema,
        }
        for tool in tools
    ]


def to_openai_tool_schema(tools: list[Tool]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema,
            },
        }
        for tool in tools
    ]
