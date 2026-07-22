"""execute_command 工具：在工作目录下执行一条 shell 命令，带超时限制。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sirius_agent.tools.base import ToolResult

_DEFAULT_TIMEOUT = 30.0


class ExecuteCommandTool:
    name = "execute_command"
    description = "在工作目录下执行一条 shell 命令，返回 stdout/stderr/退出码"
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的完整命令行"},
        },
        "required": ["command"],
    }

    def __init__(self, workspace_root: Path, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._workspace_root = workspace_root
        self._timeout = timeout

    def execute(self, arguments: dict) -> ToolResult:
        command = arguments["command"]
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self._workspace_root,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, content=f"命令执行超时（{self._timeout:g} 秒）：{command}")

        summary = (
            f"退出码：{completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
        return ToolResult(ok=completed.returncode == 0, content=summary)
