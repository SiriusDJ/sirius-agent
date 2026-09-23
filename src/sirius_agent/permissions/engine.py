"""权限判定引擎：把黑名单、路径沙箱、规则匹配、安全命令白名单、权限模式兜底
组织成一条可注入的检查链，依次尝试直到有一层给出结论。

不涉及任何 I/O 或用户交互——需要人在回路确认时，由上层 PermissionGate 负责。

黑名单/白名单本身的具体条目仍然硬编码在 blacklist.py/safelist.py 里，不接受配置
（这是 spec 明确要求的：黑名单"不可被配置放开"）；这里"可注入"指的是检查链的
组成和顺序可以在构造 PermissionEngine 时替换，方便后续新增检查层（网络请求限制、
资源配额等）时不用改 evaluate() 本身，也方便测试时单独构造某个 checker。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from sirius_agent.permissions.blacklist import BlacklistRule, check_blacklist
from sirius_agent.permissions.mode import fallback_decision
from sirius_agent.permissions.rules import RuleSet
from sirius_agent.permissions.safelist import is_safe_command
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


class PermissionChecker(Protocol):
    """检查链里的一层。返回 None 表示这层没有意见、交给下一层；返回 PermissionVerdict 表示已有定论。"""

    def check(
        self, tool_name: str, arguments: dict, match_text: str, mode: PermissionMode
    ) -> PermissionVerdict | None: ...


class BlacklistChecker:
    """execute_command 的正则黑名单，命中即 DENY，不可被后面任何一层覆盖。

    黑名单具体条目仍然硬编码在 blacklist.py 里（spec 要求不可配置）；这里注入的
    是"判断函数"本身，默认就是 blacklist.check_blacklist，测试/未来扩展时可以换掉。
    """

    def __init__(self, blacklist_check: Callable[[str], BlacklistRule | None] = check_blacklist) -> None:
        self._blacklist_check = blacklist_check

    def check(
        self, tool_name: str, arguments: dict, match_text: str, mode: PermissionMode
    ) -> PermissionVerdict | None:
        if tool_name != "execute_command":
            return None
        hit = self._blacklist_check(match_text)
        if hit is None:
            return None
        return PermissionVerdict(
            decision=Decision.DENY, reason=f"命中黑名单（{hit.category}）", match_text=match_text
        )


class SandboxChecker:
    """路径类工具的越界判断；只有越界时才给出定论（显式 allow 规则或 bypass 模式放行，否则拒绝）。

    未越界时返回 None，把这次调用交给后面的 RuleChecker 走一般规则判断。
    """

    def __init__(
        self,
        workspace_root: Path,
        rule_set: RuleSet,
        workspace_check: Callable[[Path, Path], bool] = is_within_workspace,
    ) -> None:
        self._workspace_root = workspace_root
        self._rule_set = rule_set
        self._workspace_check = workspace_check

    def check(
        self, tool_name: str, arguments: dict, match_text: str, mode: PermissionMode
    ) -> PermissionVerdict | None:
        if tool_name not in _PATH_TOOLS:
            return None

        candidate = (self._workspace_root / match_text).resolve()
        if self._workspace_check(self._workspace_root, candidate):
            return None

        rule_hit = self._rule_set.evaluate(tool_name, match_text)
        if rule_hit is not None and rule_hit[0] == Decision.ALLOW:
            return PermissionVerdict(
                decision=Decision.ALLOW,
                reason=f"越界路径，命中显式规则 {tool_name}({rule_hit[1].pattern})：allow",
                match_text=match_text,
            )
        if mode == PermissionMode.BYPASS:
            return PermissionVerdict(
                decision=Decision.ALLOW, reason="越界路径，当前为 bypass 模式", match_text=match_text
            )
        return PermissionVerdict(
            decision=Decision.DENY,
            reason="路径超出工作目录范围，且没有匹配的放行规则",
            match_text=match_text,
        )


class RuleChecker:
    """用户级/项目级/本地级三个来源合并后的规则集合，命中即按结果（deny>ask>allow）返回。"""

    def __init__(self, rule_set: RuleSet) -> None:
        self._rule_set = rule_set

    def check(
        self, tool_name: str, arguments: dict, match_text: str, mode: PermissionMode
    ) -> PermissionVerdict | None:
        rule_hit = self._rule_set.evaluate(tool_name, match_text)
        if rule_hit is None:
            return None
        decision, rule = rule_hit
        return PermissionVerdict(
            decision=decision,
            reason=f"命中规则 {tool_name}({rule.pattern})：{decision.value}",
            match_text=match_text,
        )


class SafelistChecker:
    """execute_command 的安全命令白名单，命中即 ALLOW；优先级低于 RuleChecker，高于模式兜底。

    白名单具体条目仍然硬编码在 safelist.py 里；注入的是判断函数本身。
    """

    def __init__(self, safelist_check: Callable[[str], bool] = is_safe_command) -> None:
        self._safelist_check = safelist_check

    def check(
        self, tool_name: str, arguments: dict, match_text: str, mode: PermissionMode
    ) -> PermissionVerdict | None:
        if tool_name != "execute_command":
            return None
        if not self._safelist_check(match_text):
            return None
        return PermissionVerdict(decision=Decision.ALLOW, reason="命中安全命令白名单", match_text=match_text)


class ModeFallbackChecker:
    """兜底层：前面都没给出结论时，按当前权限模式给出默认判定。链上必须是最后一层，从不返回 None。"""

    def __init__(self, fallback: Callable[[str, PermissionMode], Decision] = fallback_decision) -> None:
        self._fallback = fallback

    def check(
        self, tool_name: str, arguments: dict, match_text: str, mode: PermissionMode
    ) -> PermissionVerdict | None:
        fallback = self._fallback(tool_name, mode)
        return PermissionVerdict(
            decision=fallback,
            reason=f"未命中任何规则，按 {mode.value} 模式兜底为 {fallback.value}",
            match_text=match_text,
        )


def _default_checkers(workspace_root: Path, rule_set: RuleSet) -> list[PermissionChecker]:
    """默认检查链，顺序即 spec.md F9 定义的判定顺序：黑名单 > 沙箱 > 规则 > 白名单 > 模式兜底。"""

    return [
        BlacklistChecker(),
        SandboxChecker(workspace_root, rule_set),
        RuleChecker(rule_set),
        SafelistChecker(),
        ModeFallbackChecker(),
    ]


class PermissionEngine:
    """只读判定：给定一次工具调用，依次跑检查链，产出 allow/ask/deny 及其依据。"""

    def __init__(
        self,
        workspace_root: Path,
        rule_set: RuleSet,
        mode: PermissionMode,
        checkers: list[PermissionChecker] | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._rule_set = rule_set
        self._mode = mode
        self._checkers = checkers if checkers is not None else _default_checkers(workspace_root, rule_set)

    @property
    def mode(self) -> PermissionMode:
        return self._mode

    def set_mode(self, mode: PermissionMode) -> None:
        self._mode = mode

    def evaluate(self, tool_name: str, arguments: dict) -> PermissionVerdict:
        match_text = arguments[_MATCH_ARG[tool_name]]

        for checker in self._checkers:
            verdict = checker.check(tool_name, arguments, match_text, self._mode)
            if verdict is not None:
                return verdict

        raise AssertionError("检查链未给出任何结论——ModeFallbackChecker 应该总是兜底返回一个结果")
