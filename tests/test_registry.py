"""ToolRegistry 的 has / list_tools(only_safe) / 异步 execute 行为测试。"""

from __future__ import annotations

from sirius_agent.tools.base import ToolResult
from sirius_agent.tools.registry import ToolRegistry


class _FakeSafeTool:
    name = "fake_safe"
    description = "假的只读工具"
    parameters_schema: dict = {}
    safe = True

    async def execute(self, arguments: dict) -> ToolResult:
        return ToolResult(ok=True, content="safe-ok")


class _FakeUnsafeTool:
    name = "fake_unsafe"
    description = "假的有副作用工具"
    parameters_schema: dict = {}
    safe = False

    async def execute(self, arguments: dict) -> ToolResult:
        return ToolResult(ok=True, content="unsafe-ok")


class _FakeRaisingTool:
    name = "fake_raising"
    description = "执行时会抛异常的假工具"
    parameters_schema: dict = {}
    safe = True

    async def execute(self, arguments: dict) -> ToolResult:
        raise RuntimeError("boom")


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_FakeSafeTool())
    registry.register(_FakeUnsafeTool())
    return registry


def test_has_returns_correct_bool():
    registry = _build_registry()
    assert registry.has("fake_safe") is True
    assert registry.has("fake_unsafe") is True
    assert registry.has("does_not_exist") is False


def test_list_tools_only_safe_filters_by_safe_flag():
    registry = _build_registry()
    all_tools = registry.list_tools()
    assert {t.name for t in all_tools} == {"fake_safe", "fake_unsafe"}

    safe_only = registry.list_tools(only_safe=True)
    assert [t.name for t in safe_only] == ["fake_safe"]


async def test_execute_unknown_tool_returns_error_result():
    registry = _build_registry()
    result = await registry.execute("does_not_exist", {})
    assert result.ok is False
    assert "未知的工具" in result.content


async def test_execute_known_tool_success():
    registry = _build_registry()
    result = await registry.execute("fake_safe", {})
    assert result.ok is True
    assert result.content == "safe-ok"


async def test_execute_catches_unexpected_exception():
    registry = ToolRegistry()
    registry.register(_FakeRaisingTool())
    result = await registry.execute("fake_raising", {})
    assert result.ok is False
    assert "意外错误" in result.content
