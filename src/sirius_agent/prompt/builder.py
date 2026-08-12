"""把固定模块、可选模块、环境信息组装成结构化的系统提示。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sirius_agent.prompt.environment import EnvironmentContext
from sirius_agent.prompt.sections import (
    action_execution_section,
    environment_section,
    identity_section,
    system_constraints_section,
    task_mode_section,
    text_output_section,
    tone_style_section,
    tool_usage_section,
)


@dataclass
class SystemPromptBlock:
    """一段系统提示文本，cacheable 标记它是否应该被 Provider 标记为可缓存内容。"""

    text: str
    cacheable: bool = False


@dataclass
class Section:
    """一个系统提示模块：名称 + 优先级 + 生成内容的函数。

    数值越小优先级越高、装配时越靠前；新增模块只需要挂一个 Section 进列表，
    实际拼接顺序由 priority 决定，不依赖它在列表里的书写位置。
    """

    name: str
    priority: int
    content_fn: Callable[[], str]


_FIXED_SECTIONS: list[Section] = [
    Section("身份", 10, identity_section),
    Section("系统约束", 20, system_constraints_section),
    Section("任务模式", 30, task_mode_section),
    Section("动作执行", 40, action_execution_section),
    Section("工具使用", 50, tool_usage_section),
    Section("语气风格", 60, tone_style_section),
    Section("文本输出", 70, text_output_section),
]


def build_system_prompt(
    env: EnvironmentContext, optional_sections: list[str] | None = None
) -> list[SystemPromptBlock]:
    """产出两块：固定模块（按 priority 排序后拼装 + 可选模块）拼成的稳定块，和不参与缓存的环境信息块。"""

    ordered = sorted(_FIXED_SECTIONS, key=lambda section: section.priority)
    stable_text = "\n\n".join(section.content_fn() for section in ordered)
    if optional_sections:
        stable_text += "\n\n" + "\n\n".join(optional_sections)

    return [
        SystemPromptBlock(text=stable_text, cacheable=True),
        SystemPromptBlock(text=environment_section(env), cacheable=False),
    ]
