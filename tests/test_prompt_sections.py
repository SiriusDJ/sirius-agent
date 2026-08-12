"""七个固定模块 + 环境信息模块文本函数的基本断言。"""

from __future__ import annotations

from sirius_agent.prompt import sections
from sirius_agent.prompt.environment import EnvironmentContext

_FIXED_SECTION_FUNCS = [
    sections.identity_section,
    sections.system_constraints_section,
    sections.task_mode_section,
    sections.action_execution_section,
    sections.tool_usage_section,
    sections.tone_style_section,
    sections.text_output_section,
]


def test_all_fixed_sections_return_non_empty_text():
    for fn in _FIXED_SECTION_FUNCS:
        text = fn()
        assert isinstance(text, str)
        assert text.strip() != ""


def test_tool_usage_section_reinforces_key_rules():
    text = sections.tool_usage_section()
    assert "read_file" in text
    assert "专用工具" in text


def test_environment_section_reports_not_in_git_repo():
    env = EnvironmentContext(
        cwd="/tmp/x",
        platform="win32",
        date="2026-08-11",
        app_version="0.1.0",
        model_name="m",
        git_branch=None,
        git_dirty=None,
    )
    text = sections.environment_section(env)
    assert "不在 git 仓库中" in text


def test_environment_section_includes_all_fields():
    env = EnvironmentContext(
        cwd="/workspace",
        platform="win32",
        date="2026-08-11",
        app_version="0.1.0",
        model_name="my-model",
        git_branch="main",
        git_dirty=True,
    )
    text = sections.environment_section(env)
    assert "/workspace" in text
    assert "win32" in text
    assert "2026-08-11" in text
    assert "0.1.0" in text
    assert "my-model" in text
    assert "main" in text
