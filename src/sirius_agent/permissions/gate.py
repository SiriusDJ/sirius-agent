"""权限网关：包裹 PermissionEngine，在判定为 ask 时发起人在回路，
并根据用户选择更新会话内规则集合或落盘到本地规则文件。

是权限系统里唯一有副作用、需要异步等待用户输入的入口。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sirius_agent.permissions.engine import PermissionEngine
from sirius_agent.permissions.rule_store import append_rule
from sirius_agent.permissions.rules import RuleSet, generalize_pattern
from sirius_agent.permissions.types import (
    AskPermissionCallback,
    Decision,
    HumanChoice,
    PermissionMode,
    PermissionOutcome,
    PermissionRequest,
    Rule,
    RuleSource,
)


class PermissionGate:
    """权限系统的统一入口：check() 返回是否放行，需要时内部会触发人在回路。

    generalize_pattern/append_rule 默认指向 rules.py/rule_store.py 里的真实实现，
    构造时可以换成别的实现（比如测试里想观察泛化过程、或者未来想换一种泛化策略）。
    """

    def __init__(
        self,
        engine: PermissionEngine,
        rule_set: RuleSet,
        local_rules_path: Path,
        ask_callback: AskPermissionCallback | None = None,
        pattern_generalizer: Callable[[str, str], str] = generalize_pattern,
        rule_appender: Callable[[Path, Rule], None] = append_rule,
    ) -> None:
        self._engine = engine
        self._rule_set = rule_set
        self._local_rules_path = local_rules_path
        self._ask_callback = ask_callback
        self._pattern_generalizer = pattern_generalizer
        self._rule_appender = rule_appender

    @property
    def mode(self) -> PermissionMode:
        return self._engine.mode

    def set_mode(self, mode: PermissionMode) -> None:
        self._engine.set_mode(mode)

    def set_ask_callback(self, callback: AskPermissionCallback) -> None:
        self._ask_callback = callback

    async def check(self, tool_name: str, arguments: dict) -> PermissionOutcome:
        verdict = self._engine.evaluate(tool_name, arguments)

        if verdict.decision == Decision.ALLOW:
            return PermissionOutcome(allowed=True, reason=verdict.reason)
        if verdict.decision == Decision.DENY:
            return PermissionOutcome(allowed=False, reason=verdict.reason)

        # decision == ASK
        if self._ask_callback is None:
            return PermissionOutcome(allowed=False, reason="未配置人在回路交互，默认拒绝")

        request = PermissionRequest(
            tool_name=tool_name,
            arguments=arguments,
            reason=verdict.reason,
            suggested_pattern=self._pattern_generalizer(tool_name, verdict.match_text),
        )
        human = await self._ask_callback(request)

        if human.choice == HumanChoice.DENY_ONCE:
            return PermissionOutcome(allowed=False, reason="用户拒绝了本次操作")

        if human.choice == HumanChoice.ALLOW_ONCE:
            return PermissionOutcome(allowed=True, reason="用户已允许本次操作")

        pattern = human.pattern or verdict.match_text

        if human.choice == HumanChoice.ALLOW_SESSION:
            self._rule_set.add_session_rule(Rule(tool_name, pattern, Decision.ALLOW, RuleSource.SESSION))
            return PermissionOutcome(allowed=True, reason="用户已允许本会话内的同类操作")

        # ALLOW_PERMANENT
        rule = Rule(tool_name, pattern, Decision.ALLOW, RuleSource.LOCAL)
        self._rule_set.add_session_rule(rule)
        self._rule_appender(self._local_rules_path, rule)
        return PermissionOutcome(allowed=True, reason="用户已永久允许同类操作")
