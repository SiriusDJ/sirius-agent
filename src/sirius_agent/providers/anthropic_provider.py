"""Anthropic 协议的 Provider 实现，封装官方 anthropic SDK 的流式调用。"""

from __future__ import annotations

import json
from typing import Iterator, Optional

import anthropic

from sirius_agent.config import ProviderConfig
from sirius_agent.providers.base import Message, StreamEvent, StreamEventType
from sirius_agent.tools.base import Tool, ToolCall
from sirius_agent.tools.schema import to_anthropic_tool_schema

_MAX_TOKENS = 8192


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
        elif m.role == "assistant" and m.tool_calls:
            content: list[dict] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            result.append({"role": "assistant", "content": content})
        else:
            result.append({"role": m.role, "content": m.content})
    return result


class AnthropicProvider:
    """基于 Anthropic Messages API 的 Provider 实现。"""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._client = anthropic.Anthropic(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    def stream_chat(
        self, messages: list[Message], tools: Optional[list[Tool]] = None
    ) -> Iterator[StreamEvent]:
        request_kwargs: dict = dict(
            model=self._config.model,
            max_tokens=_MAX_TOKENS,
            messages=_to_anthropic_messages(messages),
        )
        if self._config.thinking:
            request_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            request_kwargs["temperature"] = 1  # 开启 thinking 时官方要求 temperature 必须为 1
        if tools:
            request_kwargs["tools"] = to_anthropic_tool_schema(tools)

        # 按内容块 index 累积 tool_use 块的 id/name/JSON 参数碎片
        tool_use_blocks: dict[int, dict] = {}

        try:
            with self._client.messages.stream(**request_kwargs) as stream:
                for event in stream:
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
                            yield StreamEvent(
                                type=StreamEventType.THINKING_DELTA, text=delta.thinking
                            )
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
        except anthropic.APIError as e:
            yield StreamEvent(type=StreamEventType.ERROR, error_message=str(e))
            return

        yield StreamEvent(type=StreamEventType.DONE)
