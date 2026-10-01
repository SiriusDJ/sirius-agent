"""mcp/manager.py：并发启动、失败隔离、命名校验与去重、注册、关闭兜底；以及真实子进程集成。"""

from __future__ import annotations

import asyncio
import sys
import time
from functools import partial
from pathlib import Path

from mcp_types import CallToolResult, TextContent
from mcp_types import Tool as RemoteTool

from sirius_agent.mcp.config import StdioServerConfig
from sirius_agent.mcp.connection import McpConnection, McpConnectionError, default_client_factory
from sirius_agent.mcp.manager import McpManager
from sirius_agent.tools.registry import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
_SCHEMA = {"type": "object", "properties": {}}


class _Warnings(list):
    def __call__(self, message: str) -> None:
        self.append(message)


class _FakeConnection:
    """假连接：按预设返回工具、失败或卡住；记录是否被关闭。"""

    def __init__(self, name, tools=(), error=None, start_delay=0.0, close_delay=0.0):
        self.name = name
        self._tools = [RemoteTool(name=t, input_schema=_SCHEMA) for t in tools]
        self._error = error
        self._start_delay = start_delay
        self._close_delay = close_delay
        self.closed = False

    async def start(self):
        await asyncio.sleep(self._start_delay)
        if self._error is not None:
            raise self._error
        return self._tools

    async def call_tool(self, tool, arguments):
        return CallToolResult(content=[TextContent(type="text", text=f"{self.name}:{tool}")])

    async def close(self):
        await asyncio.sleep(self._close_delay)
        self.closed = True


def _manager(fakes: list[_FakeConnection], warns) -> McpManager:
    by_name = {f.name: f for f in fakes}
    configs = {f.name: StdioServerConfig(name=f.name, command="unused") for f in fakes}
    return McpManager(configs, connection_factory=lambda c: by_name[c.name], warn=warns)


async def test_failed_server_is_isolated_and_reported():
    warns = _Warnings()
    good = _FakeConnection("good", tools=["a", "b"])
    bad = _FakeConnection("bad", error=McpConnectionError("FileNotFoundError: no such command"))
    manager = _manager([good, bad], warns)

    statuses = await manager.start_all()
    registry = ToolRegistry()
    names = manager.register_into(registry)

    assert [(s.name, s.ok, s.tool_count) for s in statuses] == [("good", True, 2), ("bad", False, 0)]
    assert names == ["mcp__good__a", "mcp__good__b"]
    assert registry.has("mcp__good__a") and registry.has("mcp__good__b")
    assert len(warns) == 1 and "bad" in warns[0] and "no such command" in warns[0]


async def test_same_tool_name_on_different_servers_do_not_collide():
    manager = _manager(
        [_FakeConnection("s1", tools=["search"]), _FakeConnection("s2", tools=["search"])], _Warnings()
    )
    await manager.start_all()
    registry = ToolRegistry()
    manager.register_into(registry)

    r1 = await registry.execute("mcp__s1__search", {})
    r2 = await registry.execute("mcp__s2__search", {})

    assert (r1.content, r2.content) == ("s1:search", "s2:search")


async def test_invalid_names_skipped_and_duplicates_keep_last():
    warns = _Warnings()
    manager = _manager([_FakeConnection("srv", tools=["ok", "bad name", "a.b", "dup", "dup"])], warns)

    statuses = await manager.start_all()

    assert [t.name for t in manager.tools] == ["mcp__srv__ok", "mcp__srv__dup"]
    assert statuses[0].tool_count == 2
    assert sum("bad name" in w for w in warns) == 1
    assert sum("a.b" in w for w in warns) == 1
    assert sum("重名" in w for w in warns) == 1


async def test_invalid_server_name_skips_all_its_tools():
    warns = _Warnings()
    manager = _manager([_FakeConnection("my server", tools=["x"])], warns)
    await manager.start_all()
    assert manager.tools == []
    assert len(warns) == 1


async def test_servers_start_concurrently():
    fakes = [_FakeConnection(f"s{i}", tools=["t"], start_delay=0.4) for i in range(3)]
    manager = _manager(fakes, _Warnings())

    started = time.monotonic()
    await manager.start_all()

    assert time.monotonic() - started < 0.9


async def test_close_all_closes_every_connection_including_failed():
    good = _FakeConnection("good", tools=["a"])
    bad = _FakeConnection("bad", error=McpConnectionError("x"))
    manager = _manager([good, bad], _Warnings())
    await manager.start_all()

    await manager.close_all()

    assert good.closed and bad.closed


async def test_close_all_gives_up_after_timeout():
    warns = _Warnings()
    stuck = _FakeConnection("stuck", tools=["a"], close_delay=3600)
    manager = _manager([stuck], warns)
    await manager.start_all()

    started = time.monotonic()
    await manager.close_all(timeout=0.3)

    assert time.monotonic() - started < 1.5
    assert any("0.3s" in w for w in warns)


async def test_close_all_without_servers_is_noop():
    await McpManager({}, connection_factory=lambda c: None).close_all()


async def test_real_subprocesses_modern_legacy_and_broken():
    warns = _Warnings()
    configs = {
        "modern": StdioServerConfig(
            name="modern", command=sys.executable, args=[str(FIXTURES / "modern_server.py")]
        ),
        "legacy": StdioServerConfig(
            name="legacy", command=sys.executable, args=[str(FIXTURES / "legacy_server.py")]
        ),
        "broken": StdioServerConfig(name="broken", command="sirius-agent-no-such-command-xyz"),
    }
    factory = partial(default_client_factory, cwd=Path.cwd())
    manager = McpManager(configs, connection_factory=lambda c: McpConnection(c, factory), warn=warns)
    try:
        statuses = {s.name: s for s in await manager.start_all()}
        registry = ToolRegistry()
        manager.register_into(registry)

        assert statuses["modern"].ok and statuses["legacy"].ok and not statuses["broken"].ok
        assert any("broken" in w for w in warns)

        added = await registry.execute("mcp__modern__add", {"a": 1, "b": 1})
        echoed = await registry.execute("mcp__legacy__echo", {"text": "yo"})
        assert (added.ok, added.content) == (True, "2")
        assert (echoed.ok, echoed.content) == (True, "legacy:yo")
        assert registry.get("mcp__modern__echo").safe is True
        assert registry.get("mcp__modern__add").safe is False
    finally:
        await manager.close_all()
