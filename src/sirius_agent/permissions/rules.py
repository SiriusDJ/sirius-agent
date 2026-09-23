"""规则的内存表示与匹配/合并逻辑。

精确匹配是无通配符 pattern 的自然特例，统一用 fnmatch 处理，不单独实现一套精确匹配。
多条规则同时命中时，只看结果严重度（deny > ask > allow），不管哪条更精确、写在哪个来源。
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from posixpath import dirname

from sirius_agent.permissions.types import Decision, Rule

# 结果严重度：数值越小越"重"，用于在多条命中规则里选出最终结果
_SEVERITY_ORDER = (Decision.DENY, Decision.ASK, Decision.ALLOW)

# 每个工具规则模式泛化时，判定它是否属于"路径类"工具（泛化到目录层级而不是命令首词）
_PATH_LIKE_TOOLS = {"read_file", "write_file", "edit_file"}

RuleMatcher = Callable[[Rule, str, str], bool]


def _matches(rule: Rule, tool_name: str, match_text: str) -> bool:
    """判断一条规则是否命中本次调用：工具名精确相等，且 pattern 按 glob 语法匹配 match_text。

    默认的匹配实现，RuleSet 构造时可以传别的 matcher 换掉它。
    """

    return rule.tool == tool_name and fnmatch.fnmatchcase(match_text, rule.pattern)


class RuleSet:
    """合并自多个来源（用户级/项目级/本地级 YAML + 会话内临时规则）的规则集合。"""

    def __init__(self, rules: list[Rule] | None = None, matcher: RuleMatcher = _matches) -> None:
        self._rules: list[Rule] = list(rules) if rules else []
        self._matcher = matcher

    @classmethod
    def merge(cls, *rule_lists: list[Rule], matcher: RuleMatcher = _matches) -> RuleSet:
        """把多个来源的规则列表合并成一个 RuleSet。"""

        merged: list[Rule] = []
        for rule_list in rule_lists:
            merged.extend(rule_list)
        return cls(merged, matcher=matcher)

    def evaluate(self, tool_name: str, match_text: str) -> tuple[Decision, Rule] | None:
        """找出所有命中的规则，按严重度（deny>ask>allow）取最终结果，连同命中的那条规则一起返回。

        没有任何规则命中时返回 None，交给调用方走模式兜底逻辑。
        """

        matched = [rule for rule in self._rules if self._matcher(rule, tool_name, match_text)]
        if not matched:
            return None

        for decision in _SEVERITY_ORDER:
            for rule in matched:
                if rule.action == decision:
                    return decision, rule
        # 理论上不可达：matched 非空时必然有某条规则的 action 属于 _SEVERITY_ORDER 之一
        raise AssertionError("命中规则但严重度匹配失败，这是一个内部错误")

    def add_session_rule(self, rule: Rule) -> None:
        """追加一条只存在本会话内存里的规则（人在回路"本会话允许"用）。"""

        self._rules.append(rule)


def generalize_pattern(tool_name: str, match_text: str) -> str:
    """把一次具体调用泛化成一条更宽的 glob 规则，供人在回路"本会话/永久允许"时展示给用户确认。

    execute_command 泛化成"子命令 + *"；路径类工具泛化到参数所在目录 + "/**"
    （没有目录部分时无法再泛化，原样返回）；其余工具原样返回，不做泛化。
    """

    if tool_name == "execute_command":
        first_word = match_text.strip().split(" ", 1)[0]
        return f"{first_word} *" if first_word else match_text

    if tool_name in _PATH_LIKE_TOOLS:
        directory = dirname(match_text.replace("\\", "/"))
        return f"{directory}/**" if directory else match_text

    return match_text
