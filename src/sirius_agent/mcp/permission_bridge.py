"""让 MCP 工具复用已有权限链路，且 permissions 包源码零修改。

原权限系统有三处默认行为不认识 MCP 工具：
  1. PermissionEngine.evaluate 按内置工具名查"匹配参数"，未知工具会 KeyError；
  2. 模式兜底按内置工具名单归类，MCP 只读工具会被当成 command 类；
  3. 规则的工具名是精确比较，写不了 mcp__github__* 这样的通配。
这里只借助 permissions 包已公开的注入点（检查链、RuleSet matcher、兜底函数、规则泛化器）
解决这三处，由 __main__.py 组装点注入。
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from pathlib import Path

from sirius_agent.mcp.adapter import TOOL_NAME_PREFIX
from sirius_agent.permissions.engine import (
    BlacklistChecker,
    ModeFallbackChecker,
    PermissionChecker,
    PermissionEngine,
    RuleChecker,
    SafelistChecker,
    SandboxChecker,
)
from sirius_agent.permissions.mode import fallback_decision
from sirius_agent.permissions.rules import RuleSet, generalize_pattern
from sirius_agent.permissions.types import Decision, PermissionMode, PermissionVerdict, Rule

# 借用内置工具名来取 read / command 两类的兜底值，不复制兜底表
_READ_CATEGORY_TOOL = "read_file"
_COMMAND_CATEGORY_TOOL = "execute_command"


def is_mcp_tool(tool_name: str) -> bool:
    return tool_name.startswith(TOOL_NAME_PREFIX)


def mcp_rule_matcher(rule: Rule, tool_name: str, match_text: str) -> bool:
    """规则的工具名与参数模式都按 glob 匹配；内置工具名不含通配符，对它们等价于原来的精确匹配。"""

    return fnmatch.fnmatchcase(tool_name, rule.tool) and fnmatch.fnmatchcase(match_text, rule.pattern)


def make_mcp_fallback(is_read_only: Callable[[str], bool]) -> Callable[[str, PermissionMode], Decision]:
    """MCP 工具：自报只读的归 read 类，其余归 command 类；内置工具原样委托。"""

    def _fallback(tool_name: str, mode: PermissionMode) -> Decision:
        if is_mcp_tool(tool_name):
            proxy = _READ_CATEGORY_TOOL if is_read_only(tool_name) else _COMMAND_CATEGORY_TOOL
            return fallback_decision(proxy, mode)
        return fallback_decision(tool_name, mode)

    return _fallback


def mcp_pattern_generalizer(tool_name: str, match_text: str) -> str:
    """人在回路"本会话/永久允许"时的建议规则：MCP 工具整工具放行（*），内置工具沿用原泛化逻辑。"""

    if is_mcp_tool(tool_name):
        return "*"
    return generalize_pattern(tool_name, match_text)


class McpAwarePermissionEngine(PermissionEngine):
    """内置工具走原检查链（行为不变）；MCP 工具只走「规则 → 模式兜底」，匹配文本恒为空串。

    黑名单/沙箱/安全命令白名单只对内置工具有意义，MCP 工具天然不经过它们。
    """

    def __init__(
        self,
        workspace_root: Path,
        rule_set: RuleSet,
        mode: PermissionMode,
        is_read_only: Callable[[str], bool],
    ) -> None:
        fallback = ModeFallbackChecker(make_mcp_fallback(is_read_only))
        builtin_checkers: list[PermissionChecker] = [
            BlacklistChecker(),
            SandboxChecker(workspace_root, rule_set),
            RuleChecker(rule_set),
            SafelistChecker(),
            fallback,
        ]
        super().__init__(workspace_root, rule_set, mode, checkers=builtin_checkers)
        self._mcp_checkers: list[PermissionChecker] = [RuleChecker(rule_set), fallback]

    def evaluate(self, tool_name: str, arguments: dict) -> PermissionVerdict:
        if not is_mcp_tool(tool_name):
            return super().evaluate(tool_name, arguments)

        for checker in self._mcp_checkers:
            verdict = checker.check(tool_name, arguments, "", self.mode)
            if verdict is not None:
                return verdict
        raise AssertionError("MCP 检查链未给出任何结论——ModeFallbackChecker 应该总是兜底返回一个结果")
