"""Anthropic 协议的 Provider 实现，封装官方 anthropic SDK 的流式调用。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import anthropic

from sirius_agent.config import ProviderConfig
from sirius_agent.prompt.builder import SystemPromptBlock
from sirius_agent.providers.base import Message, StreamEvent, StreamEventType, TokenUsage
from sirius_agent.tools.base import Tool, ToolCall
from sirius_agent.tools.schema import to_anthropic_tool_schema

_MAX_TOKENS = 8192
_CACHE_CONTROL = {"type": "ephemeral", "ttl": "5m"}


def _to_anthropic_messages(messages: list[Message]) -> list[dict]:
    """把统一的 Message 列表翻译成 Anthropic Messages API 要求的请求体。"""

    result: list[dict] = []
    for m in messages:
        if m.role == "tool":
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id,
                            "content": m.content,
                        }
                    ],
                }
            )
        elif m.role == "system":
            # Anthropic Messages API 不接受 role="system" 出现在 messages 数组里，
            # 降级成带 <system-reminder> 标签的 user 消息，模型据此识别为非用户提问。
            result.append({"role": "user", "content": f"<system-reminder>\n{m.content}\n</system-reminder>"})
        elif m.role == "assistant" and m.tool_calls:
            content: list[dict] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            result.append({"role": "assistant", "content": content})
        else:
            result.append({"role": m.role, "content": m.content})
    return result


class AnthropicProvider:
    """基于 Anthropic Messages API 的 Provider 实现。"""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._client = anthropic.AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    @property
    def config(self) -> ProviderConfig:
        return self._config

    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        system_prompt: list[SystemPromptBlock] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        request_kwargs: dict = dict(
            model=self._config.model,
            max_tokens=_MAX_TOKENS,
            messages=_to_anthropic_messages(messages),
        )
        if self._config.thinking:
            request_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            request_kwargs["temperature"] = 1  # 开启 thinking 时官方要求 temperature 必须为 1
        if system_prompt:
            request_kwargs["system"] = [
                {
                    "type": "text",
                    "text": block.text,
                    **({"cache_control": _CACHE_CONTROL} if block.cacheable else {}),
                }
                for block in system_prompt
            ]
        if tools:
            tool_schema = to_anthropic_tool_schema(tools)
            # 缓存断点语义是"缓存这个断点之前的全部前缀"：挂在最后一项即可缓存整个工具列表
            tool_schema[-1] = {**tool_schema[-1], "cache_control": _CACHE_CONTROL}
            request_kwargs["tools"] = tool_schema

        # 按内容块 index 累积 tool_use 块的 id/name/JSON 参数碎片
        tool_use_blocks: dict[int, dict] = {}

        try:
            async with self._client.messages.stream(**request_kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            tool_use_blocks[event.index] = {
                                "id": block.id,
                                "name": block.name,
                                "json_buffer": "",
                            }
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=delta.text)
                        elif delta.type == "thinking_delta":
                            yield StreamEvent(type=StreamEventType.THINKING_DELTA, text=delta.thinking)
                        elif delta.type == "input_json_delta":
                            tool_use_blocks[event.index]["json_buffer"] += delta.partial_json
                    elif event.type == "content_block_stop":
                        block_info = tool_use_blocks.get(event.index)
                        if block_info is not None:
                            arguments = json.loads(block_info["json_buffer"] or "{}")
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL,
                                tool_call=ToolCall(
                                    id=block_info["id"],
                                    name=block_info["name"],
                                    arguments=arguments,
                                ),
                            )

                final_message = await stream.get_final_message()
                yield StreamEvent(
                    type=StreamEventType.USAGE,
                    usage=TokenUsage(
                        input_tokens=final_message.usage.input_tokens,
                        output_tokens=final_message.usage.output_tokens,
                        cache_creation_input_tokens=getattr(
                            final_message.usage, "cache_creation_input_tokens", 0
                        )
                        or 0,
                        cache_read_input_tokens=getattr(final_message.usage, "cache_read_input_tokens", 0)
                        or 0,
                    ),
                )
        except anthropic.APIError as e:
            yield StreamEvent(type=StreamEventType.ERROR, error_message=str(e))
            return

        yield StreamEvent(type=StreamEventType.DONE)
