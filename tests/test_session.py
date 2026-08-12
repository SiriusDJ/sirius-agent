"""ConversationSession 的 Plan Mode 轮次计数测试。"""

from __future__ import annotations

from sirius_agent.session import ConversationSession


def test_plan_mode_round_counter_increments_from_one():
    session = ConversationSession()

    session.enter_plan_mode()

    assert session.next_plan_mode_round() == 1
    assert session.next_plan_mode_round() == 2
    assert session.next_plan_mode_round() == 3


def test_re_entering_plan_mode_resets_round_counter():
    session = ConversationSession()
    session.enter_plan_mode()
    session.next_plan_mode_round()
    session.next_plan_mode_round()

    session.enter_plan_mode()

    assert session.next_plan_mode_round() == 1
