"""权限模式（严格/默认/放行）在规则未命中时的兜底默认值表。

只读工具三档统一兜底 allow，避免默认模式下每次读文件都要确认；
有副作用工具才按档位区分（严格=deny，默认=ask，放行=allow）。
"""

from __future__ import annotations

from sirius_agent.permissions.types import Decision, PermissionMode

_READONLY_TOOLS = {"read_file", "glob_files", "grep_content"}

_SIDE_EFFECT_FALLBACK = {
    PermissionMode.STRICT: Decision.DENY,
    PermissionMode.DEFAULT: Decision.ASK,
    PermissionMode.PERMISSIVE: Decision.ALLOW,
}


def fallback_decision(tool_name: str, mode: PermissionMode) -> Decision:
    """规则集合未命中任何规则时，按当前权限模式给出的兜底默认判定。"""

    if tool_name in _READONLY_TOOLS:
        return Decision.ALLOW
    return _SIDE_EFFECT_FALLBACK[mode]
