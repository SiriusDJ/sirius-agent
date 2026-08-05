"""Provider 层的统一接口与基础数据类型。

各协议实现（Anthropic、OpenAI）都要把自己的原始 SSE 事件
翻译成这里定义的 StreamEvent，上层 TUI 只认这套统一事件，
不需要关心底层具体是哪家 API。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional, Protocol

from sirius_agent.tools.base import Tool, ToolCall


@dataclass
class TokenUsage:
    """一次 LLM 请求的 token 用量，各 Provider 内部负责把协议自己的字段名映射过来。"""

    input_tokens: int
    output_tokens: int


class StreamEventType(Enum):
    """归一化后的流式事件类型。"""

    TEXT_DELTA = "text_delta"          # 正式回答的文本增量
    THINKING_DELTA = "thinking_delta"  # extended thinking 的文本增量（仅 Anthropic 会产出）
    TOOL_CALL = "tool_call"            # 模型请求一次工具调用，参数已拼接、解析完整
    USAGE = "usage"                    # 本次请求的 token 用量，流结束前产出
    DONE = "done"                      # 本轮回复正常结束
    ERROR = "error"                    # 请求过程中出错


@dataclass
class StreamEvent:
    """一个归一化的流式事件。"""

    type: StreamEventType
    text: Optional[str] = None          # TEXT_DELTA / THINKING_DELTA 时携带增量内容
    error_message: Optional[str] = None  # ERROR 时携带可读错误信息
    tool_call: Optional[ToolCall] = None  # TOOL_CALL 时携带完整的工具调用请求
    usage: Optional[TokenUsage] = None    # USAGE 时携带本次请求的 token 用量


@dataclass
class Message:
    """一条对话消息。"""

    role: str                # "user" | "assistant" | "tool"
    content: str = ""         # 纯文本内容
    tool_calls: list[ToolCall] = field(default_factory=list)
        # role="assistant" 且模型请求了工具调用时使用
    tool_call_id: Optional[str] = None
        # role="tool" 时必填，标识这是对哪一次 ToolCall 的结果响应


class Provider(Protocol):
    """所有供应商协议实现都要满足的统一接口。"""

    def stream_chat(
        self, messages: list[Message], tools: Optional[list[Tool]] = None
    ) -> AsyncIterator[StreamEvent]:
        """发送带完整历史的对话请求；tools 非空时把工具描述一并发给模型，
        并在流式响应中识别、拼接、产出 TOOL_CALL 事件。异步生成器：
        边收边产出 StreamEvent，结束前产出一次 USAGE 事件。"""
        ...
