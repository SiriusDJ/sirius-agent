"""Provider 层的统一接口与基础数据类型。

各协议实现（Anthropic、OpenAI）都要把自己的原始 SSE 事件
翻译成这里定义的 StreamEvent，上层 TUI 只认这套统一事件，
不需要关心底层具体是哪家 API。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from sirius_agent.config import ProviderConfig
from sirius_agent.prompt.builder import SystemPromptBlock
from sirius_agent.tools.base import Tool, ToolCall


@dataclass
class TokenUsage:
    """一次 LLM 请求的 token 用量，各 Provider 内部负责把协议自己的字段名映射过来。"""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0  # 端点未返回缓存字段时按 0 处理
    cache_read_input_tokens: int = 0  # 端点未返回缓存字段时按 0 处理


class StreamEventType(Enum):
    """归一化后的流式事件类型。"""

    TEXT_DELTA = "text_delta"  # 正式回答的文本增量
    THINKING_DELTA = "thinking_delta"  # extended thinking 的文本增量（仅 Anthropic 会产出）
    TOOL_CALL = "tool_call"  # 模型请求一次工具调用，参数已拼接、解析完整
    USAGE = "usage"  # 本次请求的 token 用量，流结束前产出
    DONE = "done"  # 本轮回复正常结束
    ERROR = "error"  # 请求过程中出错


@dataclass
class StreamEvent:
    """一个归一化的流式事件。"""

    type: StreamEventType
    text: str | None = None  # TEXT_DELTA / THINKING_DELTA 时携带增量内容
    error_message: str | None = None  # ERROR 时携带可读错误信息
    tool_call: ToolCall | None = None  # TOOL_CALL 时携带完整的工具调用请求
    usage: TokenUsage | None = None  # USAGE 时携带本次请求的 token 用量


@dataclass
class Message:
    """一条对话消息。"""

    role: str  # "user" | "assistant" | "tool" | "system"
    content: str = ""  # 纯文本内容
    tool_calls: list[ToolCall] = field(default_factory=list)
    # role="assistant" 且模型请求了工具调用时使用
    tool_call_id: str | None = None
    # role="tool" 时必填，标识这是对哪一次 ToolCall 的结果响应
    # role="system" 用于运行期补充指令注入（如 Plan Mode 提醒），
    # 各 Provider 的翻译函数负责按自己协议的约束把它表达出来


class Provider(Protocol):
    """所有供应商协议实现都要满足的统一接口。"""

    config: ProviderConfig

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        system_prompt: list[SystemPromptBlock] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """发送带完整历史的对话请求；tools 非空时把工具描述一并发给模型，
        system_prompt 非空时把结构化的系统提示（稳定模块 + 环境信息）一并发给模型，
        并在流式响应中识别、拼接、产出 TOOL_CALL 事件。异步生成器：
        边收边产出 StreamEvent，结束前产出一次 USAGE 事件。"""
        ...
