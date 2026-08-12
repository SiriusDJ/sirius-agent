"""Agent Loop 核心逻辑测试：StreamCollector 双路收集 + run_agent_loop 各种场景。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from sirius_agent.agent import StopReason, StreamCollector, TurnEventType, run_agent_loop
from sirius_agent.providers.base import StreamEvent, StreamEventType, TokenUsage
from sirius_agent.session import ConversationSession
from sirius_agent.tools.base import ToolCall, ToolResult
from sirius_agent.tools.registry import ToolRegistry

_WORKSPACE_ROOT = Path(".")
_FAKE_CONFIG = SimpleNamespace(name="fake-provider", model="fake-model")


class _FakeKnownTool:
    name = "fake_known"
    description = "假的已知工具，总是成功"
    parameters_schema: dict = {}
    safe = True

    async def execute(self, arguments: dict) -> ToolResult:
        return ToolResult(ok=True, content="known-ok")


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_FakeKnownTool())
    return registry


async def _fake_stream_text_and_usage():
    yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="Hello ")
    yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="world")
    yield StreamEvent(type=StreamEventType.USAGE, usage=TokenUsage(input_tokens=1, output_tokens=2))
    yield StreamEvent(type=StreamEventType.DONE)


async def test_stream_collector_text_and_usage():
    collector = StreamCollector()
    events = [e async for e in collector.consume(_fake_stream_text_and_usage(), iteration=1)]

    text_events = [e for e in events if e.type == TurnEventType.TEXT_DELTA]
    assert [e.text for e in text_events] == ["Hello ", "world"]
    usage_events = [e for e in events if e.type == TurnEventType.USAGE]
    assert usage_events[0].usage.input_tokens == 1

    assert collector.text == "Hello world"
    assert collector.usage.input_tokens == 1
    assert collector.usage.output_tokens == 2
    assert collector.tool_calls == []
    assert collector.error_message is None


async def _fake_stream_with_error():
    yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="partial")
    yield StreamEvent(type=StreamEventType.ERROR, error_message="boom")
    # 生产者不会真的在 ERROR 之后继续产出，这里也不再 yield 更多事件


async def test_stream_collector_stops_on_error():
    collector = StreamCollector()
    events = [e async for e in collector.consume(_fake_stream_with_error(), iteration=1)]

    assert [e.type for e in events] == [TurnEventType.TEXT_DELTA]
    assert collector.error_message == "boom"


async def _fake_stream_with_tool_call():
    yield StreamEvent(
        type=StreamEventType.TOOL_CALL,
        tool_call=ToolCall(id="t1", name="read_file", arguments={"path": "a.txt"}),
    )
    yield StreamEvent(type=StreamEventType.DONE)


async def test_stream_collector_collects_tool_calls_without_yielding_them():
    collector = StreamCollector()
    events = [e async for e in collector.consume(_fake_stream_with_tool_call(), iteration=1)]

    assert events == []
    assert len(collector.tool_calls) == 1
    assert collector.tool_calls[0].name == "read_file"


# ---- run_agent_loop 骨架：正常结束 / 流式错误 / 迭代上限 ----


class _NoToolProvider:
    """模拟模型直接给出最终文字回复，不请求任何工具。"""

    config = _FAKE_CONFIG

    async def stream_chat(self, messages, tools=None, system_prompt=None):
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="done")
        yield StreamEvent(type=StreamEventType.DONE)


async def test_run_agent_loop_completes_without_tools():
    session = ConversationSession()
    events = [
        e
        async for e in run_agent_loop(
            _NoToolProvider(), _make_registry(), session, "hi", asyncio.Event(), _WORKSPACE_ROOT
        )
    ]

    stopped = events[-1]
    assert stopped.type == TurnEventType.STOPPED
    assert stopped.stop_reason == StopReason.COMPLETED

    messages = session.get_messages()
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "done"


class _StreamErrorProvider:
    config = _FAKE_CONFIG

    async def stream_chat(self, messages, tools=None, system_prompt=None):
        yield StreamEvent(type=StreamEventType.ERROR, error_message="network broke")


async def test_run_agent_loop_stream_error():
    session = ConversationSession()
    events = [
        e
        async for e in run_agent_loop(
            _StreamErrorProvider(), _make_registry(), session, "hi", asyncio.Event(), _WORKSPACE_ROOT
        )
    ]

    stopped = events[-1]
    assert stopped.type == TurnEventType.STOPPED
    assert stopped.stop_reason == StopReason.STREAM_ERROR
    assert stopped.error_message == "network broke"


class _AlwaysCallsToolProvider:
    """模拟模型永远请求同一个已知工具，永不给出最终回复——用来触发迭代上限。"""

    config = _FAKE_CONFIG

    async def stream_chat(self, messages, tools=None, system_prompt=None):
        yield StreamEvent(
            type=StreamEventType.TOOL_CALL,
            tool_call=ToolCall(id="t", name="fake_known", arguments={}),
        )
        yield StreamEvent(type=StreamEventType.DONE)


async def test_run_agent_loop_max_iterations():
    session = ConversationSession()
    events = [
        e
        async for e in run_agent_loop(
            _AlwaysCallsToolProvider(), _make_registry(), session, "hi", asyncio.Event(), _WORKSPACE_ROOT
        )
    ]

    stopped = events[-1]
    assert stopped.type == TurnEventType.STOPPED
    assert stopped.stop_reason == StopReason.MAX_ITERATIONS

    tool_started_events = [e for e in events if e.type == TurnEventType.TOOL_STARTED]
    assert len(tool_started_events) == 20
    assert tool_started_events[-1].iteration == 20


# ---- 未知工具识别 + 连续计数停止 ----


class _ScriptedProvider:
    """按调用顺序依次消费预设的事件脚本，每次 stream_chat 调用对应一轮脚本。"""

    config = _FAKE_CONFIG

    def __init__(self, scripts: list) -> None:
        self._scripts = scripts
        self.calls = 0

    async def stream_chat(self, messages, tools=None, system_prompt=None):
        events = self._scripts[self.calls]
        self.calls += 1
        for e in events:
            yield e


def _unknown_tool_call(call_id: str) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.TOOL_CALL,
        tool_call=ToolCall(id=call_id, name="does_not_exist", arguments={}),
    )


async def test_unknown_tool_two_consecutive_rounds_stops():
    provider = _ScriptedProvider(
        [
            [_unknown_tool_call("c1"), StreamEvent(type=StreamEventType.DONE)],
            [_unknown_tool_call("c2"), StreamEvent(type=StreamEventType.DONE)],
        ]
    )
    session = ConversationSession()

    events = [
        e
        async for e in run_agent_loop(
            provider, _make_registry(), session, "hi", asyncio.Event(), _WORKSPACE_ROOT
        )
    ]

    stopped = events[-1]
    assert stopped.type == TurnEventType.STOPPED
    assert stopped.stop_reason == StopReason.UNKNOWN_TOOL
    assert provider.calls == 2

    tool_results = [m for m in session.get_messages() if m.role == "tool"]
    assert len(tool_results) == 2
    assert all("未知的工具" in m.content for m in tool_results)


async def test_unknown_tool_single_occurrence_does_not_stop():
    provider = _ScriptedProvider(
        [
            [_unknown_tool_call("c1"), StreamEvent(type=StreamEventType.DONE)],
            [StreamEvent(type=StreamEventType.TEXT_DELTA, text="ok"), StreamEvent(type=StreamEventType.DONE)],
        ]
    )
    session = ConversationSession()

    events = [
        e
        async for e in run_agent_loop(
            provider, _make_registry(), session, "hi", asyncio.Event(), _WORKSPACE_ROOT
        )
    ]

    stopped = events[-1]
    assert stopped.type == TurnEventType.STOPPED
    assert stopped.stop_reason == StopReason.COMPLETED
    assert provider.calls == 2


# ---- 安全分批：只读并发 / 有副作用串行 ----


class _SlowTool:
    def __init__(self, name: str, safe: bool, delay: float) -> None:
        self.name = name
        self.description = "耗时假工具"
        self.parameters_schema: dict = {}
        self.safe = safe
        self._delay = delay

    async def execute(self, arguments: dict) -> ToolResult:
        await asyncio.sleep(self._delay)
        return ToolResult(ok=True, content=f"{self.name}-done")


def _two_tool_call_events() -> list:
    return [
        StreamEvent(
            type=StreamEventType.TOOL_CALL,
            tool_call=ToolCall(id="a", name="slow_a", arguments={}),
        ),
        StreamEvent(
            type=StreamEventType.TOOL_CALL,
            tool_call=ToolCall(id="b", name="slow_b", arguments={}),
        ),
        StreamEvent(type=StreamEventType.DONE),
    ]


async def test_safe_tools_run_concurrently():
    registry = ToolRegistry()
    registry.register(_SlowTool("slow_a", safe=True, delay=0.2))
    registry.register(_SlowTool("slow_b", safe=True, delay=0.2))
    provider = _ScriptedProvider([_two_tool_call_events(), [StreamEvent(type=StreamEventType.DONE)]])
    session = ConversationSession()

    start = time.monotonic()
    _ = [e async for e in run_agent_loop(provider, registry, session, "hi", asyncio.Event(), _WORKSPACE_ROOT)]
    elapsed = time.monotonic() - start

    assert elapsed < 0.35


async def test_unsafe_tools_run_serially():
    registry = ToolRegistry()
    registry.register(_SlowTool("slow_a", safe=False, delay=0.2))
    registry.register(_SlowTool("slow_b", safe=False, delay=0.2))
    provider = _ScriptedProvider([_two_tool_call_events(), [StreamEvent(type=StreamEventType.DONE)]])
    session = ConversationSession()

    start = time.monotonic()
    _ = [e async for e in run_agent_loop(provider, registry, session, "hi", asyncio.Event(), _WORKSPACE_ROOT)]
    elapsed = time.monotonic() - start

    assert elapsed >= 0.38


# ---- 用户取消 + Plan Mode ----


class _CancellingTool:
    """执行时把外部传入的 cancel_event 设置为已取消，模拟"工具跑的时候用户按了 Esc"。"""

    name = "cancelling_tool"
    description = "执行后触发取消的假工具"
    parameters_schema: dict = {}
    safe = True

    def __init__(self, cancel_event: asyncio.Event) -> None:
        self._cancel_event = cancel_event

    async def execute(self, arguments: dict) -> ToolResult:
        self._cancel_event.set()
        return ToolResult(ok=True, content="done")


async def test_user_cancel_stops_before_next_iteration():
    cancel_event = asyncio.Event()
    registry = ToolRegistry()
    registry.register(_CancellingTool(cancel_event))
    provider = _ScriptedProvider(
        [
            [
                StreamEvent(
                    type=StreamEventType.TOOL_CALL,
                    tool_call=ToolCall(id="c1", name="cancelling_tool", arguments={}),
                ),
                StreamEvent(type=StreamEventType.DONE),
            ],
            [
                StreamEvent(type=StreamEventType.TEXT_DELTA, text="should not run"),
                StreamEvent(type=StreamEventType.DONE),
            ],
        ]
    )
    session = ConversationSession()

    events = [
        e async for e in run_agent_loop(provider, registry, session, "hi", cancel_event, _WORKSPACE_ROOT)
    ]

    stopped = events[-1]
    assert stopped.type == TurnEventType.STOPPED
    assert stopped.stop_reason == StopReason.USER_CANCELLED
    assert provider.calls == 1


class _RecordingToolsProvider:
    """记录每次 stream_chat 收到的 messages/tools/system_prompt 参数，供 Plan Mode 相关断言使用。"""

    config = _FAKE_CONFIG

    def __init__(self) -> None:
        self.received_tools: list = []
        self.received_system_prompts: list = []
        self.received_messages: list = []

    async def stream_chat(self, messages, tools=None, system_prompt=None):
        self.received_tools = list(tools or [])
        self.received_system_prompts.append(system_prompt)
        self.received_messages.append(list(messages))
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="ok")
        yield StreamEvent(type=StreamEventType.DONE)


async def test_plan_mode_only_exposes_safe_tools():
    registry = ToolRegistry()
    registry.register(_SlowTool("safe_tool", safe=True, delay=0))
    registry.register(_SlowTool("unsafe_tool", safe=False, delay=0))
    provider = _RecordingToolsProvider()
    session = ConversationSession()

    _ = [
        e
        async for e in run_agent_loop(
            provider, registry, session, "hi", asyncio.Event(), _WORKSPACE_ROOT, tools_enabled=False
        )
    ]

    assert [t.name for t in provider.received_tools] == ["safe_tool"]


async def test_run_agent_loop_passes_system_prompt_each_iteration():
    registry = ToolRegistry()
    provider = _RecordingToolsProvider()
    session = ConversationSession()

    _ = [e async for e in run_agent_loop(provider, registry, session, "hi", asyncio.Event(), _WORKSPACE_ROOT)]

    assert len(provider.received_system_prompts) == 1
    assert provider.received_system_prompts[0] is not None
    assert len(provider.received_system_prompts[0]) == 2


async def test_plan_mode_reminder_injected_but_not_persisted():
    registry = ToolRegistry()
    provider = _RecordingToolsProvider()
    session = ConversationSession()
    session.enter_plan_mode()

    _ = [
        e
        async for e in run_agent_loop(
            provider, registry, session, "hi", asyncio.Event(), _WORKSPACE_ROOT, tools_enabled=False
        )
    ]

    last_request_messages = provider.received_messages[-1]
    assert last_request_messages[-1].role == "system"
    assert "计划模式" in last_request_messages[-1].content

    persisted_roles = [m.role for m in session.get_messages()]
    assert "system" not in persisted_roles


async def test_plan_mode_reminder_not_injected_when_tools_enabled():
    registry = ToolRegistry()
    provider = _RecordingToolsProvider()
    session = ConversationSession()

    _ = [
        e
        async for e in run_agent_loop(
            provider, registry, session, "hi", asyncio.Event(), _WORKSPACE_ROOT, tools_enabled=True
        )
    ]

    last_request_messages = provider.received_messages[-1]
    assert all(m.role != "system" for m in last_request_messages)


async def test_plan_mode_round_counter_advances_across_turns_and_alternates_full_brief():
    registry = ToolRegistry()
    provider = _RecordingToolsProvider()
    session = ConversationSession()
    session.enter_plan_mode()

    for _ in range(4):
        _ = [
            e
            async for e in run_agent_loop(
                provider, registry, session, "turn", asyncio.Event(), _WORKSPACE_ROOT, tools_enabled=False
            )
        ]

    reminders = [msgs[-1].content for msgs in provider.received_messages]
    assert "计划模式" in reminders[0] and reminders[0] != "[仍处于计划模式]"  # 第 1 轮：完整版
    assert reminders[1] == "[仍处于计划模式]"  # 第 2 轮：精简版
    assert reminders[2] == "[仍处于计划模式]"  # 第 3 轮：精简版
    assert "计划模式" in reminders[3] and reminders[3] != "[仍处于计划模式]"  # 第 4 轮：完整版重复
