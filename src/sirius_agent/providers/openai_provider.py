"""OpenAI 协议的 Provider 实现，封装官方 openai SDK 的流式调用。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import openai

from sirius_agent.config import ProviderConfig
from sirius_agent.prompt.builder import SystemPromptBlock
from sirius_agent.providers.base import Message, StreamEvent, StreamEventType, TokenUsage
from sirius_agent.tools.base import Tool, ToolCall
from sirius_agent.tools.schema import to_openai_tool_schema


def _to_openai_messages(messages: list[Message]) -> list[dict]:
    """把统一的 Message 列表翻译成 OpenAI Chat Completions API 要求的请求体。"""

    result: list[dict] = []
    for m in messages:
        if m.role == "tool":
            result.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            continue

        if m.role == "assistant" and m.tool_calls:
            item: dict = {
                "role": "assistant",
                "content": m.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ],
            }
        else:
            item = {"role": m.role, "content": m.content}

        # DeepSeek 思考模式：带 tools 的请求必须原样回传历史中每条 assistant 的 reasoning_content，
        # 否则返回 400。只有端点真的返回过思考内容才会带上，标准 OpenAI 端点不受影响。
        if m.role == "assistant" and m.reasoning_content:
            item["reasoning_content"] = m.reasoning_content
            if item["content"] is None:
                item["content"] = ""
        result.append(item)
    return result


class OpenAIProvider:
    """基于 OpenAI Chat Completions API 的 Provider 实现。"""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._client = openai.AsyncOpenAI(
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
        openai_messages = _to_openai_messages(messages)
        if system_prompt:
            combined_text = "\n\n".join(block.text for block in system_prompt)
            openai_messages = [{"role": "system", "content": combined_text}] + openai_messages

        request_kwargs: dict = dict(
            model=self._config.model,
            messages=openai_messages,
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            request_kwargs["tools"] = to_openai_tool_schema(tools)

        # 按 index 累积分片返回的 tool_calls：id/name 只在首个分片出现，
        # arguments 需要把每个分片的字符串片段依次拼接
        tool_call_buffers: dict[int, dict] = {}

        try:
            stream = await self._client.chat.completions.create(**request_kwargs)
            async for chunk in stream:
                # 开启 stream_options.include_usage 后，最后一个 chunk 的 choices 为空、
                # 只带 usage；必须先检查 usage 再判断 choices 是否为空，否则会漏掉这个事件
                if chunk.usage is not None:
                    details = getattr(chunk.usage, "prompt_tokens_details", None)
                    yield StreamEvent(
                        type=StreamEventType.USAGE,
                        usage=TokenUsage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cache_creation_input_tokens=getattr(details, "cache_write_tokens", 0) or 0,
                            cache_read_input_tokens=getattr(details, "cached_tokens", 0) or 0,
                        ),
                    )

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # DeepSeek 等兼容端点把思考过程放在非标准字段 reasoning_content 里
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield StreamEvent(type=StreamEventType.THINKING_DELTA, text=reasoning)

                if delta.content:
                    yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=delta.content)

                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        buf = tool_call_buffers.setdefault(
                            tc_delta.index, {"id": None, "name": None, "arguments_buffer": ""}
                        )
                        if tc_delta.id:
                            buf["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            buf["name"] = tc_delta.function.name
                        if tc_delta.function and tc_delta.function.arguments:
                            buf["arguments_buffer"] += tc_delta.function.arguments
        except openai.APIError as e:
            yield StreamEvent(type=StreamEventType.ERROR, error_message=str(e))
            return

        for buf in tool_call_buffers.values():
            arguments = json.loads(buf["arguments_buffer"] or "{}")
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL,
                tool_call=ToolCall(id=buf["id"], name=buf["name"], arguments=arguments),
            )

        yield StreamEvent(type=StreamEventType.DONE)
