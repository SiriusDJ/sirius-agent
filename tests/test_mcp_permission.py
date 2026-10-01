"""mcp/permission_bridge.py：MCP 工具的规则匹配、模式兜底、人在回路；内置工具判定不变。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sirius_agent.mcp.permission_bridge import (
    McpAwarePermissionEngine,
    mcp_pattern_generalizer,
    mcp_rule_matcher,
)
from sirius_agent.permissions.engine import PermissionEngine
from sirius_agent.permissions.gate import PermissionGate
from sirius_agent.permissions.rule_store import load_rule_file
from sirius_agent.permissions.rules import RuleSet
from sirius_agent.permissions.types import (
    Decision,
    HumanChoice,
    HumanDecision,
    PermissionMode,
    Rule,
    RuleSource,
)

_WORKSPACE = Path.cwd()
_READ_ONLY_MCP = {"mcp__github__list_issues", "mcp__fs__read"}


def _is_read_only(name: str) -> bool:
    return name in _READ_ONLY_MCP


def _engine(rules: list[Rule] = (), mode=PermissionMode.DEFAULT) -> McpAwarePermissionEngine:
    rule_set = RuleSet.merge(list(rules), matcher=mcp_rule_matcher)
    return McpAwarePermissionEngine(_WORKSPACE, rule_set, mode, is_read_only=_is_read_only)


def _rule(tool: str, action: Decision, pattern: str = "*") -> Rule:
    return Rule(tool=tool, pattern=pattern, action=action, source=RuleSource.PROJECT)


def test_wildcard_server_rule_applies_only_to_that_server():
    engine = _engine([_rule("mcp__github__*", Decision.ALLOW), _rule("mcp__slack__*", Decision.DENY)])

    assert engine.evaluate("mcp__github__create_issue", {"title": "x"}).decision == Decision.ALLOW
    assert engine.evaluate("mcp__slack__post", {}).decision == Decision.DENY
    # 其它 server 未命中规则 → 按模式兜底
    assert engine.evaluate("mcp__jira__create", {}).decision == Decision.ASK


def test_exact_deny_beats_wildcard_allow():
    engine = _engine(
        [_rule("mcp__github__*", Decision.ALLOW), _rule("mcp__github__delete_repo", Decision.DENY)]
    )

    verdict = engine.evaluate("mcp__github__delete_repo", {"repo": "x"})

    assert verdict.decision == Decision.DENY
    assert "mcp__github__delete_repo" in verdict.reason


@pytest.mark.parametrize(
    ("mode", "read_only_expected", "other_expected"),
    [
        (PermissionMode.DEFAULT, Decision.ALLOW, Decision.ASK),
        (PermissionMode.ACCEPT_EDITS, Decision.ALLOW, Decision.ASK),
        (PermissionMode.PLAN, Decision.ALLOW, Decision.ASK),
        (PermissionMode.BYPASS, Decision.ALLOW, Decision.ALLOW),
    ],
)
def test_mode_fallback_by_read_only_hint(mode, read_only_expected, other_expected):
    engine = _engine(mode=mode)

    assert engine.evaluate("mcp__github__list_issues", {}).decision == read_only_expected
    assert engine.evaluate("mcp__github__create_issue", {"title": "t"}).decision == other_expected


def test_mcp_arguments_never_trip_blacklist_or_sandbox():
    engine = _engine(mode=PermissionMode.BYPASS)

    # 参数里即使出现危险命令串/越界路径，也不会进入黑名单/沙箱（它们只对内置工具生效）
    verdict = engine.evaluate("mcp__shell__run", {"command": "rm -rf /", "path": "../../etc/passwd"})

    assert verdict.decision == Decision.ALLOW


@pytest.mark.parametrize(
    ("tool", "args", "mode"),
    [
        ("execute_command", {"command": "rm -rf /"}, PermissionMode.BYPASS),
        ("execute_command", {"command": "git status"}, PermissionMode.DEFAULT),
        ("execute_command", {"command": "npm install"}, PermissionMode.DEFAULT),
        ("write_file", {"path": "a.txt"}, PermissionMode.DEFAULT),
        ("write_file", {"path": "a.txt"}, PermissionMode.ACCEPT_EDITS),
        ("read_file", {"path": "../outside.txt"}, PermissionMode.DEFAULT),
        ("read_file", {"path": "a.txt"}, PermissionMode.DEFAULT),
        ("glob_files", {"pattern": "**/*.py"}, PermissionMode.PLAN),
    ],
)
def test_builtin_tools_verdicts_unchanged(tool, args, mode):
    rules = [_rule("execute_command", Decision.ALLOW, "npm *")]
    original = PermissionEngine(_WORKSPACE, RuleSet.merge(rules), mode)
    bridged = _engine(rules, mode)

    assert bridged.evaluate(tool, args) == original.evaluate(tool, args)


def test_mcp_rules_load_from_yaml(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n  - {tool: 'mcp__github__*', pattern: '*', action: deny}\n", encoding="utf-8")

    engine = _engine(load_rule_file(path, RuleSource.PROJECT))

    assert engine.evaluate("mcp__github__list_issues", {}).decision == Decision.DENY


def test_pattern_generalizer():
    assert mcp_pattern_generalizer("mcp__github__create_issue", "") == "*"
    assert mcp_pattern_generalizer("execute_command", "git commit -m x") == "git *"


async def test_session_allow_stops_asking_for_that_mcp_tool(tmp_path):
    rule_set = RuleSet.merge([], matcher=mcp_rule_matcher)
    engine = McpAwarePermissionEngine(
        _WORKSPACE, rule_set, PermissionMode.DEFAULT, is_read_only=_is_read_only
    )
    asked: list = []

    async def _ask(request):
        asked.append(request)
        return HumanDecision(choice=HumanChoice.ALLOW_SESSION, pattern=request.suggested_pattern)

    gate = PermissionGate(
        engine,
        rule_set,
        tmp_path / "rules.local.yaml",
        ask_callback=_ask,
        pattern_generalizer=mcp_pattern_generalizer,
    )

    first = await gate.check("mcp__github__create_issue", {"title": "a"})
    second = await gate.check("mcp__github__create_issue", {"title": "b"})
    other = await gate.check("mcp__github__close_issue", {"id": 1})

    assert first.allowed and second.allowed and other.allowed
    assert [r.tool_name for r in asked] == ["mcp__github__create_issue", "mcp__github__close_issue"]
    assert asked[0].suggested_pattern == "*"
