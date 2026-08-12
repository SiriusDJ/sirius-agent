"""execute_command 工具：在工作目录下执行一条 shell 命令，带超时限制。"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from sirius_agent.tools.base import ToolResult

_DEFAULT_TIMEOUT = 30.0


class ExecuteCommandTool:
    name = "execute_command"
    description = (
        "在工作目录下执行一条 shell 命令，返回 stdout/stderr/退出码。"
        "不要用它来读文件、搜索内容或查找文件，这些场景请优先使用 read_file / grep_content / glob_files"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的完整命令行"},
        },
        "required": ["command"],
    }
    safe = False

    def __init__(self, workspace_root: Path, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._workspace_root = workspace_root
        self._timeout = timeout

    async def execute(self, arguments: dict) -> ToolResult:
        command = arguments["command"]
        extra_kwargs: dict = {} if sys.platform == "win32" else {"start_new_session": True}
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self._workspace_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **extra_kwargs,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            await self._kill_process_tree(proc)
            return ToolResult(ok=False, content=f"命令执行超时（{self._timeout:g} 秒）：{command}")

        summary = (
            f"退出码：{proc.returncode}\n"
            f"stdout:\n{stdout.decode('utf-8', errors='replace')}\n"
            f"stderr:\n{stderr.decode('utf-8', errors='replace')}"
        )
        return ToolResult(ok=proc.returncode == 0, content=summary)

    async def _kill_process_tree(self, proc: asyncio.subprocess.Process) -> None:
        """杀掉整个进程树，而不只是 shell 包装进程本身。

        Windows 上 create_subprocess_shell 实际是 `cmd /c <command>`，
        真正干活的子进程是 cmd.exe 的子进程；只 kill() cmd.exe 本身，
        它派生出的子进程仍会继续跑、继续占着 stdout/stderr 管道，
        导致 proc.wait() 一直等到那个子进程自然结束才返回，超时形同虚设。
        用 taskkill /T 连带子进程一起杀掉才能让 wait() 立刻返回。
        """
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            # POSIX：命令以新会话（新进程组）启动，killpg 才能连带子进程一起杀掉
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await proc.wait()
