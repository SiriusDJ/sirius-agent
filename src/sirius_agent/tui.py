"""交互式对话主循环：读取用户输入、驱动 agent 编排一轮对话、流式渲染事件。"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from rich.console import Console

from sirius_agent.agent import TurnEventType, run_turn
from sirius_agent.providers.base import Provider
from sirius_agent.session import ConversationSession
from sirius_agent.tools.registry import ToolRegistry

_EXIT_COMMANDS = {"/exit"}
_THINKING_STYLE = "dim italic"
_TOOL_STARTED_STYLE = "cyan"
_TOOL_SUCCESS_STYLE = "dim"
_TOOL_FAILURE_STYLE = "bold red"


def run_repl(provider: Provider, tool_registry: ToolRegistry, session: ConversationSession) -> None:
    """进入交互式对话循环，直到用户输入退出指令或按 Ctrl+D。"""

    prompt_session: PromptSession = PromptSession()
    console = Console()

    while True:
        try:
            text = prompt_session.prompt("> ")
        except KeyboardInterrupt:
            continue
        except EOFError:
            break

        if text.strip() in _EXIT_COMMANDS:
            break
        if not text.strip():
            continue

        was_thinking = False
        for turn_event in run_turn(provider, tool_registry, session, text):
            if turn_event.type == TurnEventType.THINKING_DELTA:
                console.print(
                    turn_event.text, end="", style=_THINKING_STYLE, markup=False, highlight=False
                )
                was_thinking = True
            elif turn_event.type == TurnEventType.TEXT_DELTA:
                if was_thinking:
                    console.print()
                    console.print()
                    was_thinking = False
                console.print(turn_event.text, end="", markup=False, highlight=False)
            elif turn_event.type == TurnEventType.TOOL_STARTED:
                console.print()
                console.print(
                    f"→ 执行 {turn_event.tool_name}({turn_event.tool_arguments})",
                    style=_TOOL_STARTED_STYLE,
                    markup=False,
                    highlight=False,
                )
            elif turn_event.type == TurnEventType.TOOL_FINISHED:
                result = turn_event.tool_result
                style = _TOOL_SUCCESS_STYLE if result.ok else _TOOL_FAILURE_STYLE
                status = "完成" if result.ok else "失败"
                console.print(
                    f"  {status}：{result.content}", style=style, markup=False, highlight=False
                )
            elif turn_event.type == TurnEventType.ERROR:
                console.print()
                console.print(f"错误：{turn_event.error_message}", style="bold red")
            elif turn_event.type == TurnEventType.DONE:
                console.print()

    console.print("再见！")
