"""read_file 工具：读取工作目录内某个文件的完整文本内容。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sirius_agent.tools.base import ToolResult


class ReadFileTool:
    name = "read_file"
    description = "读取工作目录内某个文件的完整文本内容"
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的文件路径"},
        },
        "required": ["path"],
    }
    safe = True

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    async def execute(self, arguments: dict) -> ToolResult:
        # 是否越界已经由权限系统在调用这里之前判过；这里只负责实际读取。
        path = (self._workspace_root / arguments["path"]).resolve()

        if not path.exists():
            return ToolResult(ok=False, content=f"文件不存在：{arguments['path']}")
        if path.is_dir():
            return ToolResult(ok=False, content=f"这是一个目录，不是文件：{arguments['path']}")

        try:
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, content=f"文件不是可读的 UTF-8 文本：{arguments['path']}")

        return ToolResult(ok=True, content=content)
