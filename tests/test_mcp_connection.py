"""mcp/connection.py：真实子进程下的新协议、旧协议回退、env 注入、超时、失败与退出清理。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from functools import partial
from pathlib import Path

import pytest

from sirius_agent.mcp.config import StdioServerConfig
from sirius_agent.mcp.connection import McpConnection, McpConnectionError, default_client_factory

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
MODERN = str(FIXTURES / "modern_server.py")
LEGACY = str(FIXTURES / "legacy_server.py")


def _stdio(name: str, script: str, env: dict | None = None) -> StdioServerConfig:
    return StdioServerConfig(name=name, command=sys.executable, args=[script], env=env or {})


def _connection(config, **kwargs) -> McpConnection:
    return McpConnection(config, partial(default_client_factory, cwd=Path.cwd()), **kwargs)


def process_alive(pid: int) -> bool:
    """跨平台判断进程是否仍在运行（Windows 用 OpenProcess + GetExitCodeProcess）。"""

    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        await asyncio.sleep(0.1)
    return False


async def test_modern_stdio_server_lists_and_calls_tools():
    conn = _connection(_stdio("modern", MODERN))
    try:
        tools = await conn.start()
        assert conn.alive
        assert {t.name for t in tools} == {
            "echo",
            "add",
            "fail",
            "mixed",
            "slow",
            "get_env",
            "get_pid",
            "crash",
        }

        result = await conn.call_tool("add", {"a": 2, "b": 3})
        assert result.is_error is False
        assert result.content[0].text == "5"

        failed = await conn.call_tool("fail", {})
        assert failed.is_error is True
    finally:
        await conn.close()


async def test_legacy_server_falls_back_to_initialize_handshake():
    conn = _connection(_stdio("legacy", LEGACY))
    try:
        tools = await conn.start()
        assert [t.name for t in tools] == ["echo"]

        result = await conn.call_tool("echo", {"text": "hi"})
        assert result.content[0].text == "legacy:hi"
    finally:
        await conn.close()


async def test_env_is_injected_and_host_env_preserved(monkeypatch):
    monkeypatch.setenv("MEW_HOST_ONLY", "from-host")
    monkeypatch.setenv("MEW_OVERRIDDEN", "host-value")
    conn = _connection(
        _stdio("envsrv", MODERN, env={"MEW_INJECTED": "abc", "MEW_OVERRIDDEN": "server-value"})
    )
    try:
        await conn.start()
        injected = await conn.call_tool("get_env", {"name": "MEW_INJECTED"})
        overridden = await conn.call_tool("get_env", {"name": "MEW_OVERRIDDEN"})
        assert injected.content[0].text == "abc"
        assert overridden.content[0].text == "server-value"
    finally:
        await conn.close()


async def test_missing_command_fails_with_connection_error():
    conn = _connection(StdioServerConfig(name="ghost", command="sirius-agent-no-such-command-xyz"))
    try:
        with pytest.raises(McpConnectionError):
            await conn.start()
        assert not conn.alive
    finally:
        await conn.close()


async def test_server_that_exits_immediately_fails_with_connection_error():
    config = StdioServerConfig(name="dies", command=sys.executable, args=["-c", "import sys; sys.exit(3)"])
    conn = _connection(config, start_timeout=10)
    try:
        with pytest.raises(McpConnectionError):
            await conn.start()
    finally:
        await conn.close()


async def test_start_timeout_gives_up_and_close_returns():
    @contextlib.asynccontextmanager
    async def _hanging_factory(config):
        await asyncio.sleep(3600)
        yield None

    conn = McpConnection(_stdio("hang", MODERN), _hanging_factory, start_timeout=0.3)

    started = time.monotonic()
    with pytest.raises(McpConnectionError, match="启动超时"):
        await conn.start()
    assert time.monotonic() - started < 2

    await asyncio.wait_for(conn.close(), 2)


async def test_call_timeout_becomes_connection_error_and_connection_survives():
    conn = _connection(_stdio("slowsrv", MODERN), call_timeout=0.5)
    try:
        await conn.start()
        with pytest.raises(McpConnectionError, match="调用超时"):
            await conn.call_tool("slow", {"seconds": 5})
        # 超时只影响那一次调用，连接仍然可用
        result = await conn.call_tool("echo", {"text": "still here"})
        assert result.content[0].text == "still here"
    finally:
        await conn.close()


async def test_close_terminates_subprocess_and_later_calls_fail():
    conn = _connection(_stdio("pidsrv", MODERN))
    await conn.start()
    pid = int((await conn.call_tool("get_pid", {})).content[0].text)
    assert process_alive(pid)

    await asyncio.wait_for(conn.close(), 10)

    assert not conn.alive
    assert await _wait_dead(pid), f"server 子进程 {pid} 在关闭后仍然存活"
    with pytest.raises(McpConnectionError, match="未连接"):
        await conn.call_tool("echo", {"text": "x"})


async def test_close_is_safe_without_start():
    conn = _connection(_stdio("never", MODERN))
    await conn.close()


async def test_server_crash_mid_session_fails_fast_without_hanging():
    conn = _connection(_stdio("crashy", MODERN))
    try:
        await conn.start()
        started = time.monotonic()
        with pytest.raises(McpConnectionError):
            await conn.call_tool("crash", {})
        # 之后的调用立即失败，而不是等到 30s 调用超时
        with pytest.raises(McpConnectionError):
            await conn.call_tool("echo", {"text": "x"})
        assert time.monotonic() - started < 10
    finally:
        await asyncio.wait_for(conn.close(), 10)
