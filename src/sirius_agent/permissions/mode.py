"""权限模式（default/accept_edits/plan/bypass）在规则未命中时的兜底默认值表。

按 read/write/command 三类工具分别给兜底值；read 类工具在四档下都是 allow。
"""

from __future__ import annotations

from sirius_agent.permissions.types import Decision, PermissionMode

_READ_TOOLS = {"read_file", "glob_files", "grep_content"}
_WRITE_TOOLS = {"write_file", "edit_file"}
# execute_command 以及未来新增但未归类的工具，一律按 command 类处理——
# 三类里最严格的一类，宁可多问一句也不要默认放行陌生工具。

_FALLBACK_TABLE: dict[PermissionMode, dict[str, Decision]] = {
    PermissionMode.DEFAULT: {"read": Decision.ALLOW, "write": Decision.ASK, "command": Decision.ASK},
    PermissionMode.ACCEPT_EDITS: {
        "read": Decision.ALLOW,
        "write": Decision.ALLOW,
        "command": Decision.ASK,
    },
    PermissionMode.PLAN: {"read": Decision.ALLOW, "write": Decision.ASK, "command": Decision.ASK},
    PermissionMode.BYPASS: {
        "read": Decision.ALLOW,
        "write": Decision.ALLOW,
        "command": Decision.ALLOW,
    },
}


def _category(tool_name: str) -> str:
    if tool_name in _READ_TOOLS:
        return "read"
    if tool_name in _WRITE_TOOLS:
        return "write"
    return "command"


def fallback_decision(tool_name: str, mode: PermissionMode) -> Decision:
    """规则集合未命中任何规则时，按当前权限模式给出的兜底默认判定。"""

    return _FALLBACK_TABLE[mode][_category(tool_name)]
