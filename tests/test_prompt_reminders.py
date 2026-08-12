"""plan_mode_reminder 的轮次判断测试。"""

from __future__ import annotations

from sirius_agent.prompt.reminders import plan_mode_reminder


def test_round_one_returns_full_reminder():
    text = plan_mode_reminder(1)
    assert "计划模式" in text


def test_rounds_two_and_three_return_brief_reminder():
    assert plan_mode_reminder(2) == "[仍处于计划模式]"
    assert plan_mode_reminder(3) == "[仍处于计划模式]"


def test_round_four_and_seven_return_full_reminder():
    assert "计划模式" in plan_mode_reminder(4)
    assert plan_mode_reminder(4) != "[仍处于计划模式]"
    assert plan_mode_reminder(7) != "[仍处于计划模式]"


def test_round_five_returns_brief_reminder():
    assert plan_mode_reminder(5) == "[仍处于计划模式]"


def test_every_round_returns_a_non_empty_string():
    for round_number in range(1, 11):
        assert plan_mode_reminder(round_number)
