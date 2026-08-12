"""Plan Mode 会话中，按 Agent Loop 轮次决定这一轮注入完整版还是精简版提醒。"""

from __future__ import annotations

_REPEAT_INTERVAL = 3

_PLAN_MODE_FULL = (
    "当前处于计划模式，只有只读工具可用。请先调研并给出具体计划，不要尝试执行写操作；"
    "计划确认后用户会输入 /do 切回全工具模式。"
)
_PLAN_MODE_BRIEF = "[仍处于计划模式]"


def plan_mode_reminder(round_number: int) -> str:
    """round_number 从 1 开始计数（每次进入 Plan Mode 重新从 1 计），按 Agent Loop 轮次递增。

    第 1 轮、此后每隔 _REPEAT_INTERVAL 轮（第 4、7、10...轮）返回完整版；
    其余轮次返回精简版——只要处于计划模式，每一轮都会注入某个版本的提醒。
    """

    if round_number == 1 or (round_number - 1) % _REPEAT_INTERVAL == 0:
        return _PLAN_MODE_FULL
    return _PLAN_MODE_BRIEF
