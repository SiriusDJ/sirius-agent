"""AnthropicProvider 异步化后的流式解析与 usage 事件测试（mock AsyncAnthropic 的流）。"""

from __future__ import annotations

from types import SimpleNamespace

from sirius_agent.config import ProviderConfig
from sirius_agent.prompt.builder import SystemPromptBlock
from sirius_agent.providers.anthropic_provider import AnthropicProvider, _to_anthropic_messages
from sirius_agent.providers.base import Message, StreamEventType
from sirius_agent.tools.base import Tool


class _FakeMessageStream:
    """模拟 client.messages.stream(...) 返回的异步上下文管理器。"""

    def __init__(self, events: list, final_message) -> None:
        self._events = events
        self._final_message = final_message

    async def __aenter__(self) -> _FakeMessageStream:
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


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} 的描述"
        self.parameters_schema: dict = {"type": "object", "properties": {}}
        self.safe = True

    async def execute(self, arguments: dict) -> None:  # pragma: no cover - 未在这些测试里被调用
        raise NotImplementedError


def _make_provider_capturing_kwargs(events: list, final_message) -> tuple[AnthropicProvider, dict]:
    """跟 _make_provider 一样，但额外记录传给 client.messages.stream(**kwargs) 的 kwargs。"""

    config = ProviderConfig(
        name="test", protocol="anthropic", model="claude-test", base_url="https://x", api_key="k"
    )
    provider = AnthropicProvider(config)
    captured: dict = {}

    def _fake_stream(**kwargs):
        captured.update(kwargs)
        return _FakeMessageStream(events, final_message)

    provider._client.messages.stream = _fake_stream
    return provider, captured


_EMPTY_STREAM_EVENTS: list = []


async def test_system_prompt_cache_control_only_on_cacheable_block():
    provider, captured = _make_provider_capturing_kwargs(_EMPTY_STREAM_EVENTS, _FINAL_MESSAGE)
    system_prompt = [
        SystemPromptBlock(text="稳定内容", cacheable=True),
        SystemPromptBlock(text="环境信息", cacheable=False),
    ]

    _ = [
        e
        async for e in provider.stream_chat([Message(role="user", content="hi")], system_prompt=system_prompt)
    ]

    assert captured["system"] == [
        {"type": "text", "text": "稳定内容", "cache_control": {"type": "ephemeral", "ttl": "5m"}},
        {"type": "text", "text": "环境信息"},
    ]


async def test_tools_cache_control_on_last_tool_only():
    provider, captured = _make_provider_capturing_kwargs(_EMPTY_STREAM_EVENTS, _FINAL_MESSAGE)
    tools: list[Tool] = [_FakeTool("read_file"), _FakeTool("write_file")]  # type: ignore[list-item]

    _ = [e async for e in provider.stream_chat([Message(role="user", content="hi")], tools=tools)]

    sent_tools = captured["tools"]
    assert "cache_control" not in sent_tools[0]
    assert sent_tools[1]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}


def test_to_anthropic_messages_downgrades_system_role_to_tagged_user_message():
    result = _to_anthropic_messages([Message(role="system", content="reminder")])

    assert result == [{"role": "user", "content": "<system-reminder>\nreminder\n</system-reminder>"}]


async def test_usage_event_carries_cache_fields():
    final_message = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=100,
            cache_read_input_tokens=200,
        )
    )
    provider = _make_provider(_EMPTY_STREAM_EVENTS, final_message)

    collected = [e async for e in provider.stream_chat([Message(role="user", content="hi")])]

    usage_event = next(e for e in collected if e.type == StreamEventType.USAGE)
    assert usage_event.usage.cache_creation_input_tokens == 100
    assert usage_event.usage.cache_read_input_tokens == 200


async def test_usage_event_defaults_cache_fields_to_zero_when_absent():
    provider = _make_provider(_EMPTY_STREAM_EVENTS, _FINAL_MESSAGE)

    collected = [e async for e in provider.stream_chat([Message(role="user", content="hi")])]

    usage_event = next(e for e in collected if e.type == StreamEventType.USAGE)
    assert usage_event.usage.cache_creation_input_tokens == 0
    assert usage_event.usage.cache_read_input_tokens == 0
