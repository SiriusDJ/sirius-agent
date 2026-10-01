"""tui.py 的 /permission 模式切换与事件渲染测试（不测真实终端按键监听，那部分在 checklist 用 tmux 验证）。"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from rich.console import Console

from sirius_agent import tui
from sirius_agent.agent import StopReason, TurnEvent, TurnEventType
from sirius_agent.permissions.types import PermissionMode
from sirius_agent.providers.base import TokenUsage
from sirius_agent.session import ConversationSession
from sirius_agent.tools.base import ToolResult


class _FakePromptSession:
    """按顺序返回预设输入的假 PromptSession，替代真实终端读取。"""

    def __init__(self, inputs: list[str]) -> None:
        self._inputs = iter(inputs)

    async def prompt_async(self, _prompt: str) -> str:
        try:
            return next(self._inputs)
        except StopIteration:
            raise EOFError from None


class _RecordingGate:
    """PermissionGate 的替身：只记录模式切换，run_repl 会调用 set_ask_callback/set_mode。"""

    def __init__(self) -> None:
        self.mode = PermissionMode.DEFAULT

    def set_ask_callback(self, callback) -> None:
        self.callback = callback

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode


async def test_permission_command_switches_mode_seen_by_agent_loop(monkeypatch):
    recorded_modes: list[PermissionMode] = []

    async def _fake_run_agent_loop(
        provider, tool_registry, session, user_text, cancel_event, workspace_root, permission_gate
    ):
        recorded_modes.append(permission_gate.mode)
        yield TurnEvent(type=TurnEventType.STOPPED, stop_reason=StopReason.COMPLETED)

    async def _fake_watch_cancel_keys(cancel_event):
        await asyncio.sleep(0)

    monkeypatch.setattr(tui, "run_agent_loop", _fake_run_agent_loop)
    monkeypatch.setattr(tui, "_watch_cancel_keys", _fake_watch_cancel_keys)
    inputs = ["hello", "/permission plan", "hello again", "/permission default", "final", "/exit"]
    monkeypatch.setattr(tui, "PromptSession", lambda: _FakePromptSession(inputs))

    await tui.run_repl(
        provider=object(),
        tool_registry=object(),
        session=ConversationSession(),
        workspace_root=Path("."),
        permission_gate=_RecordingGate(),
    )

    assert recorded_modes == [PermissionMode.DEFAULT, PermissionMode.PLAN, PermissionMode.DEFAULT]


def test_render_covers_all_event_types_and_stop_reasons():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, no_color=True, width=100)

    tui._render(console, TurnEvent(type=TurnEventType.THINKING_DELTA, text="thinking..."))
    tui._render(console, TurnEvent(type=TurnEventType.TEXT_DELTA, text="hello"))
    tui._render(
        console,
        TurnEvent(type=TurnEventType.TOOL_STARTED, tool_name="read_file", tool_arguments={"path": "a.txt"}),
    )
    tui._render(
        console,
        TurnEvent(
            type=TurnEventType.TOOL_FINISHED,
            tool_name="read_file",
            tool_result=ToolResult(ok=True, content="ok"),
        ),
    )
    tui._render(
        console,
        TurnEvent(
            type=TurnEventType.TOOL_FINISHED,
            tool_name="write_file",
            tool_result=ToolResult(ok=False, content="失败原因"),
        ),
    )
    usage = TokenUsage(input_tokens=10, output_tokens=5)
    tui._render(console, TurnEvent(type=TurnEventType.USAGE, usage=usage))
    tui._render(console, TurnEvent(type=TurnEventType.STOPPED, stop_reason=StopReason.COMPLETED))
    tui._render(console, TurnEvent(type=TurnEventType.STOPPED, stop_reason=StopReason.MAX_ITERATIONS))
    tui._render(console, TurnEvent(type=TurnEventType.STOPPED, stop_reason=StopReason.USER_CANCELLED))
    tui._render(console, TurnEvent(type=TurnEventType.STOPPED, stop_reason=StopReason.UNKNOWN_TOOL))
    tui._render(
        console,
        TurnEvent(type=TurnEventType.STOPPED, stop_reason=StopReason.STREAM_ERROR, error_message="断网了"),
    )

    output = buffer.getvalue()

    assert "thinking..." in output
    assert "hello" in output
    assert "执行 read_file" in output
    assert "完成：ok" in output
    assert "失败：失败原因" in output
    assert "输入 10 / 输出 5" in output
    assert "已达到最大迭代轮数" in output
    assert "已取消" in output
    assert "模型连续请求未知工具" in output
    assert "错误：断网了" in output


def test_render_usage_shows_cache_fields_when_present():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, no_color=True, width=100)

    tui._render(
        console,
        TurnEvent(
            type=TurnEventType.USAGE,
            usage=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                cache_creation_input_tokens=100,
                cache_read_input_tokens=200,
            ),
        ),
    )

    output = buffer.getvalue()
    assert "缓存写入 100" in output
    assert "缓存命中 200" in output


def test_render_usage_shows_zero_cache_fields_when_absent():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, no_color=True, width=100)

    usage = TokenUsage(input_tokens=10, output_tokens=5)
    tui._render(console, TurnEvent(type=TurnEventType.USAGE, usage=usage))

    output = buffer.getvalue()
    assert "缓存写入 0" in output
    assert "缓存命中 0" in output
