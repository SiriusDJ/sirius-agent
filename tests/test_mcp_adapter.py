"""mcp/adapter.py：命名、描述兜底、schema 透传、只读映射、结果转换、异常回灌。"""

from __future__ import annotations

import pytest
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from mcp_types import Tool as RemoteTool

from sirius_agent.mcp.adapter import McpTool, build_tool_name, convert_call_result, is_valid_tool_name
from sirius_agent.tools.schema import to_anthropic_tool_schema, to_openai_tool_schema

_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string", "description": "要回显的文本"}},
    "required": ["text"],
}


class _Warnings(list):
    def __call__(self, message: str) -> None:
        self.append(message)


class _FakeConnection:
    def __init__(self, result: CallToolResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool: str, arguments: dict) -> CallToolResult:
        self.calls.append((tool, arguments))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _remote(name="echo", description="回显", annotations=None) -> RemoteTool:
    return RemoteTool(name=name, description=description, input_schema=_SCHEMA, annotations=annotations)


def _text_result(*texts: str, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=t) for t in texts], is_error=is_error)


def test_build_and_validate_tool_name():
    assert build_tool_name("github", "create_issue") == "mcp__github__create_issue"
    assert is_valid_tool_name("mcp__github__create-issue_2")
    assert not is_valid_tool_name("mcp__git hub__x")
    assert not is_valid_tool_name("mcp__srv__a.b")
    assert not is_valid_tool_name("mcp__srv__工具")


def test_tool_metadata_is_adapted():
    tool = McpTool("srv", _remote(), _FakeConnection(), _Warnings())

    assert tool.name == "mcp__srv__echo"
    assert tool.description == "回显"
    assert tool.parameters_schema == _SCHEMA
    assert tool.remote_name == "echo"


def test_empty_description_falls_back_to_server_hint():
    tool = McpTool("srv", _remote(description=None), _FakeConnection(), _Warnings())
    assert tool.description and "srv" in tool.description and "echo" in tool.description


@pytest.mark.parametrize(
    ("annotations", "expected"),
    [
        (ToolAnnotations(read_only_hint=True), True),
        (ToolAnnotations(read_only_hint=False), False),
        (ToolAnnotations(), False),
        (None, False),
    ],
)
def test_read_only_hint_maps_to_safe(annotations, expected):
    assert McpTool("srv", _remote(annotations=annotations), _FakeConnection(), _Warnings()).safe is expected


def test_adapted_tool_fits_both_provider_schemas():
    tool = McpTool("srv", _remote(), _FakeConnection(), _Warnings())

    anthropic = to_anthropic_tool_schema([tool])[0]
    openai = to_openai_tool_schema([tool])[0]

    assert anthropic == {"name": "mcp__srv__echo", "description": "回显", "input_schema": _SCHEMA}
    assert openai["function"] == {"name": "mcp__srv__echo", "description": "回显", "parameters": _SCHEMA}


async def test_execute_forwards_remote_name_and_joins_text():
    conn = _FakeConnection(_text_result("line1", "line2"))
    tool = McpTool("srv", _remote(), conn, _Warnings())

    result = await tool.execute({"text": "hi"})

    assert conn.calls == [("echo", {"text": "hi"})]
    assert result.ok is True
    assert result.content == "line1\nline2"


async def test_remote_is_error_maps_to_failed_result():
    tool = McpTool("srv", _remote(), _FakeConnection(_text_result("boom", is_error=True)), _Warnings())
    result = await tool.execute({})
    assert result.ok is False and result.content == "boom"


async def test_exceptions_become_failed_result_not_raised():
    tool = McpTool("srv", _remote(), _FakeConnection(error=TimeoutError("调用超时（30s）")), _Warnings())

    result = await tool.execute({"text": "x"})

    assert result.ok is False
    assert "mcp__srv__echo" in result.content and "调用超时" in result.content


def test_non_text_blocks_dropped_and_warned_once():
    warns = _Warnings()
    result = CallToolResult(
        content=[
            TextContent(type="text", text="a"),
            ImageContent(type="image", data="AAAA", mime_type="image/png"),
            ImageContent(type="image", data="BBBB", mime_type="image/png"),
            TextContent(type="text", text="b"),
        ]
    )

    converted = convert_call_result(result, "mcp__srv__mixed", warns)

    assert converted.ok is True
    assert converted.content == "a\nb"
    assert len(warns) == 1
    assert "image" in warns[0] and "mcp__srv__mixed" in warns[0]
