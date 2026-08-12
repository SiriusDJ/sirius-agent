"""build_system_prompt 的拼装规则测试。"""

from __future__ import annotations

from sirius_agent.prompt import builder
from sirius_agent.prompt.builder import Section, build_system_prompt
from sirius_agent.prompt.environment import EnvironmentContext
from sirius_agent.prompt.sections import environment_section, identity_section, tool_usage_section

_ENV = EnvironmentContext(
    cwd="/workspace",
    platform="win32",
    date="2026-08-11",
    app_version="0.1.0",
    model_name="m",
    git_branch="main",
    git_dirty=False,
)


def test_build_system_prompt_returns_two_blocks():
    blocks = build_system_prompt(_ENV)

    assert len(blocks) == 2

    stable_block, env_block = blocks
    assert stable_block.cacheable is True
    assert identity_section() in stable_block.text
    assert tool_usage_section() in stable_block.text

    assert env_block.cacheable is False
    assert env_block.text == environment_section(_ENV)


def test_build_system_prompt_appends_optional_sections():
    blocks = build_system_prompt(_ENV, optional_sections=["额外指令 A"])

    stable_block = blocks[0]
    assert "额外指令 A" in stable_block.text


def test_assembly_order_is_driven_by_priority_not_list_position(monkeypatch):
    """故意把优先级数字更大（更靠后）的 Section 写在列表前面，
    验证真正决定拼接顺序的是 priority 字段，而不是它在列表里的书写位置。"""

    fake_sections = [
        Section(name="second", priority=20, content_fn=lambda: "SECOND"),
        Section(name="first", priority=10, content_fn=lambda: "FIRST"),
    ]
    monkeypatch.setattr(builder, "_FIXED_SECTIONS", fake_sections)

    blocks = build_system_prompt(_ENV)

    stable_text = blocks[0].text
    assert stable_text.index("FIRST") < stable_text.index("SECOND")
