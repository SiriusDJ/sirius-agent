"""对话历史管理：维护本次进程运行期间的多轮对话消息列表（纯内存，不持久化）。"""

from __future__ import annotations

from sirius_agent.providers.base import Message
from sirius_agent.tools.base import ToolCall


class ConversationSession:
    """维护一次运行期间的对话历史。"""

    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._plan_mode_round: int = 0

    def add_user_message(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))

    def enter_plan_mode(self) -> None:
        """进入 Plan Mode，轮次计数器归零。"""
        self._plan_mode_round = 0

    def next_plan_mode_round(self) -> int:
        """Plan Mode 轮次计数器 +1 并返回新值，供调用方判断这一轮要不要注入提醒。"""
        self._plan_mode_round += 1
        return self._plan_mode_round

    def add_assistant_message(self, content: str, reasoning_content: str | None = None) -> None:
        self._messages.append(Message(role="assistant", content=content, reasoning_content=reasoning_content))

    def add_assistant_tool_call_message(
        self, content: str, tool_calls: list[ToolCall], reasoning_content: str | None = None
    ) -> None:
        """模型这一步请求了工具调用（可能同时带文字和思考过程）。"""
        self._messages.append(
            Message(
                role="assistant", content=content, tool_calls=tool_calls, reasoning_content=reasoning_content
            )
        )

    def add_tool_result_message(self, tool_call_id: str, content: str) -> None:
        """把某次 ToolCall 的执行结果写回历史。"""
        self._messages.append(Message(role="tool", content=content, tool_call_id=tool_call_id))

    def get_messages(self) -> list[Message]:
        """返回历史列表的拷贝，避免调用方误改内部状态。"""
        return list(self._messages)
