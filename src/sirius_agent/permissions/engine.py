"""权限判定引擎：串联黑名单、路径沙箱、规则匹配、权限模式兜底，产出只读的判定结果。

不涉及任何 I/O 或用户交互——需要人在回路确认时，由上层 PermissionGate 负责。
"""

from __future__ import annotations

from pathlib import Path

from sirius_agent.permissions.blacklist import check_blacklist
from sirius_agent.permissions.mode import fallback_decision
from sirius_agent.permissions.rules import RuleSet
from sirius_agent.permissions.types import Decision, PermissionMode, PermissionVerdict
from sirius_agent.tools.paths import is_within_workspace

_PATH_TOOLS = {"read_file", "write_file", "edit_file"}

# 每个工具用来匹配规则/沙箱的参数字段名
_MATCH_ARG = {
    "execute_command": "command",
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "glob_files": "pattern",
    "grep_content": "pattern",
}


class PermissionEngine:
    """只读判定：给定一次工具调用，产出 allow/ask/deny 及其依据。"""

    def __init__(self, workspace_root: Path, rule_set: RuleSet, mode: PermissionMode) -> None:
        self._workspace_root = workspace_root
        self._rule_set = rule_set
        self._mode = mode

    def set_mode(self, mode: PermissionMode) -> None:
        self._mode = mode

    def evaluate(self, tool_name: str, arguments: dict) -> PermissionVerdict:
        match_text = arguments[_MATCH_ARG[tool_name]]

        if tool_name == "execute_command":
            blacklist_hit = check_blacklist(match_text)
            if blacklist_hit is not None:
                return PermissionVerdict(
                    decision=Decision.DENY,
                    reason=f"命中黑名单（{blacklist_hit.category}）",
                    match_text=match_text,
                )

        if tool_name in _PATH_TOOLS:
            candidate = (self._workspace_root / match_text).resolve()
            if not is_within_workspace(self._workspace_root, candidate):
                rule_hit = self._rule_set.evaluate(tool_name, match_text)
                if rule_hit is not None and rule_hit[0] == Decision.ALLOW:
                    return PermissionVerdict(
                        decision=Decision.ALLOW,
                        reason=f"越界路径，命中显式规则 {tool_name}({rule_hit[1].pattern})：allow",
                        match_text=match_text,
                    )
                if self._mode == PermissionMode.PERMISSIVE:
                    return PermissionVerdict(
                        decision=Decision.ALLOW,
                        reason="越界路径，当前为放行模式",
                        match_text=match_text,
                    )
                return PermissionVerdict(
                    decision=Decision.DENY,
                    reason="路径超出工作目录范围，且没有匹配的放行规则",
                    match_text=match_text,
                )

        rule_hit = self._rule_set.evaluate(tool_name, match_text)
        if rule_hit is not None:
            decision, rule = rule_hit
            return PermissionVerdict(
                decision=decision,
                reason=f"命中规则 {tool_name}({rule.pattern})：{decision.value}",
                match_text=match_text,
            )

        fallback = fallback_decision(tool_name, self._mode)
        return PermissionVerdict(
            decision=fallback,
            reason=f"未命中任何规则，按 {self._mode.value} 模式兜底为 {fallback.value}",
            match_text=match_text,
        )
