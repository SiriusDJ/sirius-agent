"""交互式对话主循环：读取用户输入、驱动 Agent Loop、流式渲染事件。

支持 /plan、/do 两段式指令切换计划模式；循环执行期间可按 Esc/Ctrl+C 取消当前 Agent Loop。
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_input
from prompt_toolkit.keys import Keys
from rich.console import Console

from sirius_agent.agent import StopReason, TurnEventType, run_agent_loop
from sirius_agent.permissions.gate import PermissionGate
from sirius_agent.permissions.types import (
    AskPermissionCallback,
    HumanChoice,
    HumanDecision,
    PermissionMode,
    PermissionRequest,
)
from sirius_agent.providers.base import Provider
from sirius_agent.session import ConversationSession
from sirius_agent.tools.registry import ToolRegistry

_EXIT_COMMANDS = {"/exit"}
_PLAN_COMMAND = "/plan"
_DO_COMMAND = "/do"
_PERMISSION_COMMAND = "/permission"
_THINKING_STYLE = "dim italic"
_TOOL_STARTED_STYLE = "cyan"
_TOOL_SUCCESS_STYLE = "dim"
_TOOL_FAILURE_STYLE = "bold red"
_USAGE_STYLE = "dim"
_PERMISSION_STYLE = "bold magenta"

_PERMISSION_CHOICE_BY_INPUT = {
    "1": HumanChoice.ALLOW_ONCE,
    "2": HumanChoice.DENY_ONCE,
    "3": HumanChoice.ALLOW_SESSION,
    "4": HumanChoice.ALLOW_PERMANENT,
}

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


class _CancelWatcher:
    """管理 Esc/Ctrl+C 取消监听的启停。

    人在回路询问期间需要临时让出终端输入所有权给 prompt_session：
    _watch_cancel_keys 会一直占着 raw_mode 抢占所有按键事件，如果这时候
    prompt_session.prompt_async 也在读同一个终端输入，两边会互相打架、
    表现为按键要等好一会儿才有反应。
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._cancel_event: asyncio.Event | None = None

    def start(self, cancel_event: asyncio.Event) -> None:
        self._cancel_event = cancel_event
        self._task = asyncio.create_task(_watch_cancel_keys(cancel_event))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def pause(self) -> None:
        """人在回路询问开始前调用：临时停掉监听，让 prompt_session 独占终端输入。"""
        await self.stop()

    def resume(self) -> None:
        """人在回路询问结束后调用：重新开始监听 Esc/Ctrl+C。"""
        if self._cancel_event is not None:
            self.start(self._cancel_event)


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
            f"[本轮用量：输入 {usage.input_tokens} / 输出 {usage.output_tokens} tokens，"
            f"缓存写入 {usage.cache_creation_input_tokens} / "
            f"缓存命中 {usage.cache_read_input_tokens} tokens]",
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


def create_ask_callback(
    console: Console, prompt_session: PromptSession, watcher: _CancelWatcher
) -> AskPermissionCallback:
    """构造人在回路的终端交互回调：展示请求信息，读取用户在四个选项间的选择。

    读取过程中遇到 Ctrl+C/Ctrl+D 一律视为"拒绝本次"，不会让权限询问卡死整个程序。
    询问期间会临时暂停 Esc/Ctrl+C 取消监听，避免两边抢占同一个终端输入。
    """

    async def _ask(request: PermissionRequest) -> HumanDecision:
        await watcher.pause()
        try:
            console.print()
            console.print(
                f"[需要确认] {request.tool_name}({request.arguments})",
                style=_PERMISSION_STYLE,
                markup=False,
                highlight=False,
            )
            console.print(f"  依据：{request.reason}", style="dim", markup=False, highlight=False)
            console.print(
                "  1) 允许（仅本次）  2) 拒绝（仅本次）  3) 本会话内允许同类操作  4) 永久允许同类操作",
                markup=False,
                highlight=False,
            )

            while True:
                try:
                    choice_text = (await prompt_session.prompt_async("选择 [1-4]: ")).strip()
                except (KeyboardInterrupt, EOFError):
                    return HumanDecision(choice=HumanChoice.DENY_ONCE)

                choice = _PERMISSION_CHOICE_BY_INPUT.get(choice_text)
                if choice is not None:
                    break
                console.print("请输入 1-4 之间的数字", style="bold red")

            if choice not in (HumanChoice.ALLOW_SESSION, HumanChoice.ALLOW_PERMANENT):
                return HumanDecision(choice=choice)

            console.print(f"  建议规则：{request.tool_name}({request.suggested_pattern})", style="dim")
            try:
                custom = (
                    await prompt_session.prompt_async("直接回车采用建议规则，或输入自定义模式：")
                ).strip()
            except (KeyboardInterrupt, EOFError):
                return HumanDecision(choice=HumanChoice.DENY_ONCE)

            pattern = custom if custom else request.suggested_pattern
            return HumanDecision(choice=choice, pattern=pattern)
        finally:
            watcher.resume()

    return _ask


async def run_repl(
    provider: Provider,
    tool_registry: ToolRegistry,
    session: ConversationSession,
    workspace_root: Path,
    permission_gate: PermissionGate,
) -> None:
    """进入交互式对话循环，直到用户输入退出指令或按 Ctrl+D。"""

    prompt_session: PromptSession = PromptSession()
    console = Console()
    plan_mode = False
    watcher = _CancelWatcher()
    permission_gate.set_ask_callback(create_ask_callback(console, prompt_session, watcher))

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
            session.enter_plan_mode()
            console.print("[已进入计划模式，仅只读工具可用，输入 /do 切回全工具模式]", style="bold cyan")
            continue
        if stripped == _DO_COMMAND:
            plan_mode = False
            console.print("[已切回全工具模式]", style="bold cyan")
            continue
        if stripped.startswith(_PERMISSION_COMMAND):
            _handle_permission_command(stripped, permission_gate, console)
            continue
        if not stripped:
            continue

        cancel_event = asyncio.Event()
        watcher.start(cancel_event)
        try:
            async for turn_event in run_agent_loop(
                provider,
                tool_registry,
                session,
                text,
                cancel_event,
                workspace_root,
                permission_gate,
                tools_enabled=not plan_mode,
            ):
                _render(console, turn_event)
        finally:
            await watcher.stop()

    console.print("再见！")


def _handle_permission_command(stripped: str, permission_gate: PermissionGate, console: Console) -> None:
    """解析 `/permission strict|default|permissive` 并切换权限模式。"""

    parts = stripped.split()
    if len(parts) != 2 or parts[1] not in {"strict", "default", "permissive"}:
        console.print("用法：/permission strict|default|permissive", style="bold red")
        return

    mode = PermissionMode(parts[1])
    permission_gate.set_mode(mode)
    console.print(f"[已切换权限模式：{mode.value}]", style="bold cyan")
