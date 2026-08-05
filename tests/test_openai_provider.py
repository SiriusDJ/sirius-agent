"""OpenAIProvider 异步化后的流式解析与 usage 事件测试（mock AsyncOpenAI 的流）。"""

from __future__ import annotations

from types import SimpleNamespace

from sirius_agent.config import ProviderConfig
from sirius_agent.providers.base import Message, StreamEventType
from sirius_agent.providers.openai_provider import OpenAIProvider


class _FakeChatStream:
    def __init__(self, chunks: list) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for chunk in self._chunks:
            yield chunk


def _make_provider(chunks: list) -> OpenAIProvider:
    config = ProviderConfig(
        name="test", protocol="openai", model="gpt-test", base_url="https://x", api_key="k"
    )
    provider = OpenAIProvider(config)

    async def _fake_create(**kwargs):
        return _FakeChatStream(chunks)

    provider._client.chat.completions.create = _fake_create
    return provider


def _chunk(content: str | None = None, tool_calls=None, usage=None):
    return SimpleNamespace(
        choices=(
            []
            if content is None and tool_calls is None and usage is not None
            else [SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))]
        ),
        usage=usage,
    )


async def test_text_and_tool_calls_and_usage():
    chunks = [
        _chunk(content="Hello "),
        _chunk(content="world"),
        _chunk(
            tool_calls=[
                SimpleNamespace(
                    index=0,
                    id="call_1",
                    function=SimpleNamespace(name="read_file", arguments='{"path"'),
                )
            ]
        ),
        _chunk(
            tool_calls=[
                SimpleNamespace(index=0, id=None, function=SimpleNamespace(name=None, arguments=': "a.txt"}'))
            ]
        ),
        _chunk(usage=SimpleNamespace(prompt_tokens=8, completion_tokens=3)),
    ]
    provider = _make_provider(chunks)

    collected = [e async for e in provider.stream_chat([Message(role="user", content="hi")])]

    types_in_order = [e.type for e in collected]
    assert types_in_order == [
        StreamEventType.TEXT_DELTA,
        StreamEventType.TEXT_DELTA,
        StreamEventType.USAGE,
        StreamEventType.TOOL_CALL,
        StreamEventType.DONE,
    ]

    text_events = [e for e in collected if e.type == StreamEventType.TEXT_DELTA]
    assert "".join(e.text for e in text_events) == "Hello world"

    usage_event = next(e for e in collected if e.type == StreamEventType.USAGE)
    assert usage_event.usage.input_tokens == 8
    assert usage_event.usage.output_tokens == 3

    tool_call_event = next(e for e in collected if e.type == StreamEventType.TOOL_CALL)
    assert tool_call_event.tool_call.id == "call_1"
    assert tool_call_event.tool_call.name == "read_file"
    assert tool_call_event.tool_call.arguments == {"path": "a.txt"}


async def test_plain_text_without_tool_calls_no_regression():
    chunks = [
        _chunk(content="just chatting"),
        _chunk(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2)),
    ]
    provider = _make_provider(chunks)

    collected = [e async for e in provider.stream_chat([Message(role="user", content="hi")])]

    types_in_order = [e.type for e in collected]
    assert types_in_order == [StreamEventType.TEXT_DELTA, StreamEventType.USAGE, StreamEventType.DONE]
    assert collected[0].text == "just chatting"
