"""AnthropicProvider 异步化后的流式解析与 usage 事件测试（mock AsyncAnthropic 的流）。"""

from __future__ import annotations

from types import SimpleNamespace

from sirius_agent.config import ProviderConfig
from sirius_agent.providers.anthropic_provider import AnthropicProvider
from sirius_agent.providers.base import Message, StreamEventType


class _FakeMessageStream:
    """模拟 client.messages.stream(...) 返回的异步上下文管理器。"""

    def __init__(self, events: list, final_message) -> None:
        self._events = events
        self._final_message = final_message

    async def __aenter__(self) -> "_FakeMessageStream":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for event in self._events:
            yield event

    async def get_final_message(self):
        return self._final_message


def _make_provider(events: list, final_message) -> AnthropicProvider:
    config = ProviderConfig(
        name="test", protocol="anthropic", model="claude-test", base_url="https://x", api_key="k"
    )
    provider = AnthropicProvider(config)
    provider._client.messages.stream = lambda **kwargs: _FakeMessageStream(events, final_message)
    return provider


_FINAL_MESSAGE = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5))


async def test_text_and_tool_use_and_usage():
    events = [
        SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text")),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="Hello "),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="world"),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="tool_1", name="read_file"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"path"'),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json=': "a.txt"}'),
        ),
        SimpleNamespace(type="content_block_stop", index=1),
    ]
    provider = _make_provider(events, _FINAL_MESSAGE)

    collected = [e async for e in provider.stream_chat([Message(role="user", content="hi")])]

    types_in_order = [e.type for e in collected]
    assert types_in_order == [
        StreamEventType.TEXT_DELTA,
        StreamEventType.TEXT_DELTA,
        StreamEventType.TOOL_CALL,
        StreamEventType.USAGE,
        StreamEventType.DONE,
    ]

    text_events = [e for e in collected if e.type == StreamEventType.TEXT_DELTA]
    assert "".join(e.text for e in text_events) == "Hello world"

    tool_call_event = next(e for e in collected if e.type == StreamEventType.TOOL_CALL)
    assert tool_call_event.tool_call.id == "tool_1"
    assert tool_call_event.tool_call.name == "read_file"
    assert tool_call_event.tool_call.arguments == {"path": "a.txt"}

    usage_event = next(e for e in collected if e.type == StreamEventType.USAGE)
    assert usage_event.usage.input_tokens == 10
    assert usage_event.usage.output_tokens == 5


async def test_plain_text_without_tool_use_no_regression():
    events = [
        SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text")),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="just chatting"),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    provider = _make_provider(events, _FINAL_MESSAGE)

    collected = [e async for e in provider.stream_chat([Message(role="user", content="hi")])]

    types_in_order = [e.type for e in collected]
    assert types_in_order == [StreamEventType.TEXT_DELTA, StreamEventType.USAGE, StreamEventType.DONE]
    assert collected[0].text == "just chatting"
