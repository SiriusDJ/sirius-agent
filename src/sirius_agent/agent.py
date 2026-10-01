"""Agent Loop 编排逻辑：反复"发起 LLM 请求 → 识别工具调用 → 执行工具 → 结果写回历史"，
直到模型不再请求工具或触发某个停止条件。

对外只产出 TurnEvent 异步事件流，不直接碰终端渲染，方便脱离 TUI 单独测试。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from sirius_agent.permissions.gate import PermissionGate
from sirius_agent.permissions.types import PermissionMode
from sirius_agent.prompt.builder import build_system_prompt
from sirius_agent.prompt.environment import gather_environment
from sirius_agent.prompt.reminders import plan_mode_reminder
from sirius_agent.providers.base import Message, Provider, StreamEvent, StreamEventType, TokenUsage
from sirius_agent.session import ConversationSession
from sirius_agent.tools.base import ToolCall, ToolResult
from sirius_agent.tools.registry import ToolRegistry

_MAX_ITERATIONS = 20
_MAX_CONSECUTIVE_UNKNOWN_TOOL_ROUNDS = 2


class StopReason(Enum):
    COMPLETED = "completed"  # 模型不再请求工具，正常结束
    MAX_ITERATIONS = "max_iterations"  # 达到默认 20 轮迭代上限
    USER_CANCELLED = "user_cancelled"  # 用户按 Esc/Ctrl+C 取消
    UNKNOWN_TOOL = "unknown_tool"  # 连续两轮出现未知工具调用
    STREAM_ERROR = "stream_error"  # LLM 请求流式出错


class TurnEventType(Enum):
    THINKING_DELTA = "thinking_delta"
    TEXT_DELTA = "text_delta"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    USAGE = "usage"
    STOPPED = "stopped"  # 循环结束（唯一的终止事件，具体原因看 stop_reason）


@dataclass
class TurnEvent:
    type: TurnEventType
    text: str | None = None
    tool_name: str | None = None
    tool_arguments: dict | None = None
    tool_result: ToolResult | None = None
    usage: TokenUsage | None = None
    stop_reason: StopReason | None = None
    error_message: str | None = None  # stop_reason=STREAM_ERROR 时携带
    iteration: int = 0  # 当前第几轮（1-based），供界面展示进度


class StreamCollector:
    """把一次 stream_chat 的事件双路处理：一路实时转发成 TurnEvent，
    一路在内部累积出这一次请求的完整文本/工具调用/用量，供循环下一步判断使用。"""

    def __init__(self) -> None:
        self.text: str = ""
        self.thinking: str | None = None  # 本次请求的完整思考过程；没有思考输出时为 None
        self.tool_calls: list = []
        self.usage: TokenUsage | None = None
        self.error_message: str | None = None

    async def consume(self, stream: AsyncIterator[StreamEvent], iteration: int) -> AsyncIterator[TurnEvent]:
        parts: list[str] = []
        thinking_parts: list[str] = []
        async for event in stream:
            if event.type == StreamEventType.THINKING_DELTA:
                thinking_parts.append(event.text or "")
                yield TurnEvent(type=TurnEventType.THINKING_DELTA, text=event.text, iteration=iteration)
            elif event.type == StreamEventType.TEXT_DELTA:
                parts.append(event.text or "")
                yield TurnEvent(type=TurnEventType.TEXT_DELTA, text=event.text, iteration=iteration)
            elif event.type == StreamEventType.TOOL_CALL:
                self.tool_calls.append(event.tool_call)
            elif event.type == StreamEventType.USAGE:
                self.usage = event.usage
                yield TurnEvent(type=TurnEventType.USAGE, usage=event.usage, iteration=iteration)
            elif event.type == StreamEventType.ERROR:
                self.error_message = event.error_message
                return
            elif event.type == StreamEventType.DONE:
                break
        self.text = "".join(parts)
        self.thinking = "".join(thinking_parts) or None


async def run_agent_loop(
    provider: Provider,
    tool_registry: ToolRegistry,
    session: ConversationSession,
    user_text: str,
    cancel_event: asyncio.Event,
    workspace_root: Path,
    permission_gate: PermissionGate,
) -> AsyncIterator[TurnEvent]:
    """把 user_text 加入历史，反复"请求 → 工具 → 结果"直到触发某个停止条件。"""

    session.add_user_message(user_text)
    consecutive_unknown_rounds = 0

    for iteration in range(1, _MAX_ITERATIONS + 1):
        if cancel_event.is_set():
            yield TurnEvent(
                type=TurnEventType.STOPPED, stop_reason=StopReason.USER_CANCELLED, iteration=iteration
            )
            return

        env = await gather_environment(workspace_root, provider.config.model)
        system_prompt = build_system_prompt(env)

        plan_mode = permission_gate.mode == PermissionMode.PLAN

        # Plan Mode 提醒按 Agent Loop 轮次动态构造，只拼进这一次请求的消息列表，
        # 不落进 session 的持久历史——不影响后续轮次、也不污染可缓存的稳定内容。
        request_messages = session.get_messages()
        if plan_mode:
            reminder = plan_mode_reminder(session.next_plan_mode_round())
            request_messages.append(Message(role="system", content=reminder))

        active_tools = tool_registry.list_tools(only_safe=plan_mode)
        collector = StreamCollector()
        async for turn_event in collector.consume(
            provider.stream_chat(request_messages, tools=active_tools, system_prompt=system_prompt),
            iteration,
        ):
            yield turn_event

        if collector.error_message is not None:
            yield TurnEvent(
                type=TurnEventType.STOPPED,
                stop_reason=StopReason.STREAM_ERROR,
                error_message=collector.error_message,
                iteration=iteration,
            )
            return

        if not collector.tool_calls:
            if collector.text or collector.thinking:
                session.add_assistant_message(collector.text, reasoning_content=collector.thinking)
            yield TurnEvent(type=TurnEventType.STOPPED, stop_reason=StopReason.COMPLETED, iteration=iteration)
            return

        session.add_assistant_tool_call_message(
            collector.text, collector.tool_calls, reasoning_content=collector.thinking
        )

        known_calls = []
        round_has_unknown = False
        for tc in collector.tool_calls:
            if tool_registry.has(tc.name):
                known_calls.append(tc)
                continue
            round_has_unknown = True
            yield TurnEvent(
                type=TurnEventType.TOOL_STARTED,
                tool_name=tc.name,
                tool_arguments=tc.arguments,
                iteration=iteration,
            )
            result = ToolResult(ok=False, content=f"未知的工具：{tc.name}")
            yield TurnEvent(
                type=TurnEventType.TOOL_FINISHED,
                tool_name=tc.name,
                tool_result=result,
                iteration=iteration,
            )
            session.add_tool_result_message(tc.id, result.content)

        consecutive_unknown_rounds = consecutive_unknown_rounds + 1 if round_has_unknown else 0
        if consecutive_unknown_rounds >= _MAX_CONSECUTIVE_UNKNOWN_TOOL_ROUNDS:
            yield TurnEvent(
                type=TurnEventType.STOPPED, stop_reason=StopReason.UNKNOWN_TOOL, iteration=iteration
            )
            return

        allowed_calls: list[ToolCall] = []
        async for turn_event in _run_permission_checks(
            permission_gate, known_calls, session, iteration, allowed_calls
        ):
            yield turn_event

        safe_calls = [tc for tc in allowed_calls if tool_registry.get(tc.name).safe]
        unsafe_calls = [tc for tc in allowed_calls if not tool_registry.get(tc.name).safe]

        async for turn_event in _run_safe_batch(tool_registry, safe_calls, session, iteration):
            yield turn_event
        async for turn_event in _run_unsafe_batch(tool_registry, unsafe_calls, session, iteration):
            yield turn_event

        if cancel_event.is_set():
            yield TurnEvent(
                type=TurnEventType.STOPPED, stop_reason=StopReason.USER_CANCELLED, iteration=iteration
            )
            return

    yield TurnEvent(
        type=TurnEventType.STOPPED, stop_reason=StopReason.MAX_ITERATIONS, iteration=_MAX_ITERATIONS
    )


async def _run_permission_checks(
    permission_gate: PermissionGate,
    calls: list[ToolCall],
    session: ConversationSession,
    iteration: int,
    allowed_out: list[ToolCall],
) -> AsyncIterator[TurnEvent]:
    """对每个已知工具调用顺序跑一次权限判定（保证人在回路严格串行）。

    被允许的调用追加进 allowed_out 供后续 safe/unsafe 批处理执行；
    被拒绝的调用直接产出一对 TOOL_STARTED/TOOL_FINISHED 事件，并把失败结果写入历史。
    """

    for tc in calls:
        outcome = await permission_gate.check(tc.name, tc.arguments)
        if outcome.allowed:
            allowed_out.append(tc)
            continue

        yield TurnEvent(
            type=TurnEventType.TOOL_STARTED,
            tool_name=tc.name,
            tool_arguments=tc.arguments,
            iteration=iteration,
        )
        result = ToolResult(ok=False, content=outcome.reason)
        yield TurnEvent(
            type=TurnEventType.TOOL_FINISHED, tool_name=tc.name, tool_result=result, iteration=iteration
        )
        session.add_tool_result_message(tc.id, result.content)


async def _run_safe_batch(
    tool_registry: ToolRegistry, calls: list, session: ConversationSession, iteration: int
) -> AsyncIterator[TurnEvent]:
    """只读、无副作用的工具调用批次：并发执行。"""

    if not calls:
        return

    for tc in calls:
        yield TurnEvent(
            type=TurnEventType.TOOL_STARTED,
            tool_name=tc.name,
            tool_arguments=tc.arguments,
            iteration=iteration,
        )

    results = await asyncio.gather(*(tool_registry.execute(tc.name, tc.arguments) for tc in calls))

    for tc, result in zip(calls, results):
        yield TurnEvent(
            type=TurnEventType.TOOL_FINISHED, tool_name=tc.name, tool_result=result, iteration=iteration
        )
        session.add_tool_result_message(tc.id, result.content)


async def _run_unsafe_batch(
    tool_registry: ToolRegistry, calls: list, session: ConversationSession, iteration: int
) -> AsyncIterator[TurnEvent]:
    """有副作用的工具调用批次：逐个串行执行。"""

    for tc in calls:
        yield TurnEvent(
            type=TurnEventType.TOOL_STARTED,
            tool_name=tc.name,
            tool_arguments=tc.arguments,
            iteration=iteration,
        )
        result = await tool_registry.execute(tc.name, tc.arguments)
        yield TurnEvent(
            type=TurnEventType.TOOL_FINISHED, tool_name=tc.name, tool_result=result, iteration=iteration
        )
        session.add_tool_result_message(tc.id, result.content)
