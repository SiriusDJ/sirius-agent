"""交互式对话主循环：读取用户输入、驱动 Agent Loop、流式渲染事件。

支持 /plan、/do 两段式指令切换计划模式；循环执行期间可按 Esc/Ctrl+C 取消当前 Agent Loop。
"""

from __future__ import annotations

import asyncio
import contextlib

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_input
from prompt_toolkit.keys import Keys
from rich.console import Console

from sirius_agent.agent import StopReason, TurnEventType, run_agent_loop
from sirius_agent.providers.base import Provider
from sirius_agent.session import ConversationSession
from sirius_agent.tools.registry import ToolRegistry

_EXIT_COMMANDS = {"/exit"}
_PLAN_COMMAND = "/plan"
_DO_COMMAND = "/do"
_THINKING_STYLE = "dim italic"
_TOOL_STARTED_STYLE = "cyan"
_TOOL_SUCCESS_STYLE = "dim"
_TOOL_FAILURE_STYLE = "bold red"
_USAGE_STYLE = "dim"

_STOP_REASON_MESSAGES = {
    StopReason.MAX_ITERATIONS: "已达到最大迭代轮数（20），本次任务未必完成",
    StopReason.USER_CANCELLED: "已取消",
    StopReason.UNKNOWN_TOOL: "模型连续请求未知工具，已停止",
}


async def _watch_cancel_keys(cancel_event: asyncio.Event) -> None:
    """监听 Esc/Ctrl+C 按键，命中后设置 cancel_event。

    循环执行期间会一直占着 raw_mode 直到外部 cancel() 这个任务为止——不能在
    cancel_event 一被设置就自己退出：Loop 要等"当前步骤跑完"才会真正停止，
    这段等待期里如果提前退出 raw_mode，Windows 控制台的 Ctrl+C 默认处理（SIGINT）
    会恢复，用户这时再按一次 Ctrl+C 就会直接崩掉整个进程而不是被安全吸收。
    """

    input_ = create_input()

    def _keys_ready() -> None:
        for key_press in input_.read_keys():
            if key_press.key in (Keys.Escape, Keys.ControlC):
                cancel_event.set()

    with input_.raw_mode():
        with input_.attach(_keys_ready):
            while True:
                await asyncio.sleep(0.05)


def _render(console: Console, turn_event) -> None:
    """把一个 TurnEvent 渲染到终端；抽成独立函数方便脱离真实终端单独测试。"""

    if turn_event.type == TurnEventType.THINKING_DELTA:
        console.print(turn_event.text, end="", style=_THINKING_STYLE, markup=False, highlight=False)
    elif turn_event.type == TurnEventType.TEXT_DELTA:
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
        console.print(f"  {status}：{result.content}", style=style, markup=False, highlight=False)
    elif turn_event.type == TurnEventType.USAGE:
        usage = turn_event.usage
        console.print()
        console.print(
            f"[本轮用量：输入 {usage.input_tokens} / 输出 {usage.output_tokens} tokens]",
            style=_USAGE_STYLE,
            markup=False,
            highlight=False,
        )
    elif turn_event.type == TurnEventType.STOPPED:
        console.print()
        message = _STOP_REASON_MESSAGES.get(turn_event.stop_reason)
        if turn_event.stop_reason == StopReason.STREAM_ERROR:
            console.print(f"错误：{turn_event.error_message}", style="bold red")
        elif message is not None:
            console.print(message, style="bold yellow")
        # StopReason.COMPLETED：不额外打印，只换行结束


async def run_repl(provider: Provider, tool_registry: ToolRegistry, session: ConversationSession) -> None:
    """进入交互式对话循环，直到用户输入退出指令或按 Ctrl+D。"""

    prompt_session: PromptSession = PromptSession()
    console = Console()
    plan_mode = False

    while True:
        try:
            text = await prompt_session.prompt_async("> ")
        except KeyboardInterrupt:
            continue
        except EOFError:
            break

        stripped = text.strip()
        if stripped in _EXIT_COMMANDS:
            break
        if stripped == _PLAN_COMMAND:
            plan_mode = True
            console.print("[已进入计划模式，仅只读工具可用，输入 /do 切回全工具模式]", style="bold cyan")
            continue
        if stripped == _DO_COMMAND:
            plan_mode = False
            console.print("[已切回全工具模式]", style="bold cyan")
            continue
        if not stripped:
            continue

        cancel_event = asyncio.Event()
        watcher_task = asyncio.create_task(_watch_cancel_keys(cancel_event))
        try:
            async for turn_event in run_agent_loop(
                provider, tool_registry, session, text, cancel_event, tools_enabled=not plan_mode
            ):
                _render(console, turn_event)
        finally:
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task

    console.print("再见！")
