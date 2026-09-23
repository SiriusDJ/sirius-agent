"""权限系统的公共类型：枚举、数据结构、回调签名。

这些类型被 blacklist/rules/rule_store/mode/engine/gate 各模块共享，
单独放在一个模块里避免相互 import 造成循环依赖。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum


class Decision(Enum):
    """一次判定的结果三态。"""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionMode(Enum):
    """整体权限模式，作为规则未命中时按 read/write/command 三类工具给出的兜底默认值。

    PLAN 模式额外接管了"只暴露只读工具给模型"的职责（原来 tools_enabled 那套），
    不再是一个跟权限系统平行的独立开关。
    """

    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    BYPASS = "bypass"


class RuleSource(Enum):
    """规则来源，只用于展示判定依据，不参与优先级计算。"""

    USER = "user"  # 用户级 YAML（跨项目共享）
    PROJECT = "project"  # 项目级 YAML（提交进 git，团队共享）
    LOCAL = "local"  # 本地级 YAML（不提交，人在回路"永久允许"写这里）
    SESSION = "session"  # 人在回路"本会话允许"产生，只存在进程内存中


class HumanChoice(Enum):
    """人在回路时用户可以选择的四种放行/拒绝方式。"""

    ALLOW_ONCE = "allow_once"  # 仅允许这一次，不生成任何规则
    DENY_ONCE = "deny_once"  # 仅拒绝这一次
    ALLOW_SESSION = "allow_session"  # 生成一条只存在本会话内存里的规则
    ALLOW_PERMANENT = "allow_permanent"  # 生成一条规则并写入本地级 YAML 文件


@dataclass(frozen=True)
class Rule:
    """一条具体规则：工具名精确匹配 + 参数模式（支持 glob）+ 判定结果 + 来源。"""

    tool: str
    pattern: str
    action: Decision
    source: RuleSource


@dataclass(frozen=True)
class PermissionVerdict:
    """PermissionEngine.evaluate() 的返回值：纯数据，不涉及任何 I/O 或副作用。"""

    decision: Decision
    reason: str  # 人类可读的判定依据，供展示给用户/反馈给模型
    match_text: str  # 本次用于匹配规则的字符串（如 command 或 path），供人在回路生成规则时使用


@dataclass(frozen=True)
class PermissionRequest:
    """需要人在回路确认时，传给终端交互回调的请求内容。"""

    tool_name: str
    arguments: dict
    reason: str
    suggested_pattern: str  # 系统泛化建议的 glob 规则，供用户确认或修改


@dataclass(frozen=True)
class HumanDecision:
    """人在回路终端交互回调的返回值。"""

    choice: HumanChoice
    pattern: str | None = None  # 用户确认/修改后的 glob 规则，仅 ALLOW_SESSION/ALLOW_PERMANENT 时有意义


@dataclass(frozen=True)
class PermissionOutcome:
    """PermissionGate.check() 的最终返回值，供 Agent Loop 直接使用。"""

    allowed: bool
    reason: str  # 被拒绝时作为工具失败结果的 content 内容


# 由 tui.py 实现具体的终端交互（展示 PermissionRequest、读取用户选择）
AskPermissionCallback = Callable[[PermissionRequest], Awaitable[HumanDecision]]
