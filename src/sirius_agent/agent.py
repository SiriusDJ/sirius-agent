"""一轮对话的编排逻辑：首次请求 → 识别/执行工具调用 → 追加一次不带工具的请求拿最终回复。

对外只产出 TurnEvent 流，不直接碰终端渲染，方便脱离 TUI 单独测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional

from sirius_agent.providers.base import Provider, StreamEventType
from sirius_agent.session import ConversationSession
from sirius_agent.tools.base import ToolResult
from sirius_agent.tools.registry import ToolRegistry


class TurnEventType(Enum):
    THINKING_DELTA = "thinking_delta"
    TEXT_DELTA = "text_delta"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    ERROR = "error"
    DONE = "done"


@dataclass
class TurnEvent:
    type: TurnEventType
    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Optional[dict] = None
    tool_result: Optional[ToolResult] = None
    error_message: Optional[str] = None


def run_turn(
    provider: Provider,
    tool_registry: ToolRegistry,
    session: ConversationSession,
    user_text: str,
) -> Iterator[TurnEvent]:
    """处理一轮用户输入，必要时执行一次工具往返，最终产出一次完整回复。"""

    session.add_user_message(user_text)

    buffer_parts: list[str] = []
    tool_calls = []
    for event in provider.stream_chat(session.get_messages(), tools=tool_registry.list_tools()):
        if event.type == StreamEventType.THINKING_DELTA:
            yield TurnEvent(type=TurnEventType.THINKING_DELTA, text=event.text)
        elif event.type == StreamEventType.TEXT_DELTA:
            yield TurnEvent(type=TurnEventType.TEXT_DELTA, text=event.text)
            buffer_parts.append(event.text or "")
        elif event.type == StreamEventType.TOOL_CALL:
            tool_calls.append(event.tool_call)
        elif event.type == StreamEventType.ERROR:
            yield TurnEvent(type=TurnEventType.ERROR, error_message=event.error_message)
            return
        elif event.type == StreamEventType.DONE:
            break

    if not tool_calls:
        reply = "".join(buffer_parts)
        if reply:
            session.add_assistant_message(reply)
        yield TurnEvent(type=TurnEventType.DONE)
        return

    session.add_assistant_tool_call_message("".join(buffer_parts), tool_calls)

    for tc in tool_calls:
        yield TurnEvent(
            type=TurnEventType.TOOL_STARTED, tool_name=tc.name, tool_arguments=tc.arguments
        )
        result = tool_registry.execute(tc.name, tc.arguments)
        yield TurnEvent(type=TurnEventType.TOOL_FINISHED, tool_name=tc.name, tool_result=result)
        session.add_tool_result_message(tc.id, result.content)

    final_parts: list[str] = []
    for event in provider.stream_chat(session.get_messages(), tools=None):
        if event.type == StreamEventType.THINKING_DELTA:
            yield TurnEvent(type=TurnEventType.THINKING_DELTA, text=event.text)
        elif event.type == StreamEventType.TEXT_DELTA:
            yield TurnEvent(type=TurnEventType.TEXT_DELTA, text=event.text)
            final_parts.append(event.text or "")
        elif event.type == StreamEventType.ERROR:
            yield TurnEvent(type=TurnEventType.ERROR, error_message=event.error_message)
            return
        elif event.type == StreamEventType.DONE:
            break

    final_reply = "".join(final_parts)
    if final_reply:
        session.add_assistant_message(final_reply)
    yield TurnEvent(type=TurnEventType.DONE)
