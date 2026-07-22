"""对话历史管理：维护本次进程运行期间的多轮对话消息列表（纯内存，不持久化）。"""

from __future__ import annotations

from sirius_agent.providers.base import Message
from sirius_agent.tools.base import ToolCall


class ConversationSession:
    """维护一次运行期间的对话历史。"""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add_user_message(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        self._messages.append(Message(role="assistant", content=content))

    def add_assistant_tool_call_message(self, content: str, tool_calls: list[ToolCall]) -> None:
        """模型这一步请求了工具调用（可能同时带文字）。"""
        self._messages.append(Message(role="assistant", content=content, tool_calls=tool_calls))

    def add_tool_result_message(self, tool_call_id: str, content: str) -> None:
        """把某次 ToolCall 的执行结果写回历史。"""
        self._messages.append(Message(role="tool", content=content, tool_call_id=tool_call_id))

    def get_messages(self) -> list[Message]:
        """返回历史列表的拷贝，避免调用方误改内部状态。"""
        return list(self._messages)
