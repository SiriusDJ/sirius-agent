"""三级 YAML 规则文件的路径定位、加载、格式校验、追加写入。

用户级/项目级/本地级三个文件各自独立存放，读取时由上层（__main__.py）合并成一个
RuleSet；这里只负责单个文件的 I/O 和格式校验，不关心合并优先级。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from sirius_agent.permissions.types import Decision, Rule, RuleSource

_REQUIRED_FIELDS = ("tool", "pattern", "action")
_VALID_ACTIONS = {"allow", "ask", "deny"}


class RuleFileError(Exception):
    """规则文件格式非法（不是合法 YAML、字段缺失、action 不合法等）时抛出。"""


def user_rules_path() -> Path:
    """用户级规则文件路径：跨项目共享。"""

    return Path.home() / ".sirius-agent" / "rules.yaml"


def project_rules_path(workspace_root: Path) -> Path:
    """项目级规则文件路径：提交进 git，团队共享。"""

    return workspace_root / ".sirius-agent" / "rules.yaml"


def local_rules_path(workspace_root: Path) -> Path:
    """本地级规则文件路径：不提交，人在回路"永久允许"写这里。"""

    return workspace_root / ".sirius-agent" / "rules.local.yaml"


def load_rule_file(path: Path, source: RuleSource) -> list[Rule]:
    """加载并校验一个规则 YAML 文件，返回 Rule 列表。

    文件不存在时视为空规则集合；文件存在但格式不合法时抛 RuleFileError。
    """

    if not path.is_file():
        return []

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return []

    if not isinstance(raw, dict) or "rules" not in raw:
        raise RuleFileError(f"规则文件 '{path}' 需要包含顶层 'rules' 列表")

    entries = raw["rules"]
    if not isinstance(entries, list):
        raise RuleFileError(f"规则文件 '{path}' 的 'rules' 字段需要是一个列表")

    rules: list[Rule] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuleFileError(f"规则文件 '{path}' 中存在一个非法的规则条目：{entry!r}")

        missing = [field for field in _REQUIRED_FIELDS if field not in entry]
        if missing:
            raise RuleFileError(f"规则文件 '{path}' 中的条目缺少必需字段：{', '.join(missing)}（{entry!r}）")

        action = entry["action"]
        if action not in _VALID_ACTIONS:
            raise RuleFileError(
                f"规则文件 '{path}' 中的 action '{action}' 不合法，"
                f"只能是：{', '.join(sorted(_VALID_ACTIONS))}"
            )

        rules.append(
            Rule(
                tool=entry["tool"],
                pattern=entry["pattern"],
                action=Decision(action),
                source=source,
            )
        )

    return rules


def append_rule(path: Path, rule: Rule) -> None:
    """把一条规则追加写入指定的规则 YAML 文件（人在回路"永久允许"用）。"""

    existing: list[dict] = []
    if path.is_file():
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if isinstance(raw, dict) and isinstance(raw.get("rules"), list):
            existing = raw["rules"]

    existing.append({"tool": rule.tool, "pattern": rule.pattern, "action": rule.action.value})

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump({"rules": existing}, f, allow_unicode=True, sort_keys=False)
