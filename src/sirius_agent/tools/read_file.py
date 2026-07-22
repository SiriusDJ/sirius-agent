"""read_file 工具：读取工作目录内某个文件的完整文本内容。"""

from __future__ import annotations

from pathlib import Path

from sirius_agent.tools.base import ToolResult
from sirius_agent.tools.paths import PathOutsideWorkspaceError, resolve_safe_path


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

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    def execute(self, arguments: dict) -> ToolResult:
        try:
            path = resolve_safe_path(self._workspace_root, arguments["path"])
        except PathOutsideWorkspaceError as e:
            return ToolResult(ok=False, content=str(e))

        if not path.exists():
            return ToolResult(ok=False, content=f"文件不存在：{arguments['path']}")
        if path.is_dir():
            return ToolResult(ok=False, content=f"这是一个目录，不是文件：{arguments['path']}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, content=f"文件不是可读的 UTF-8 文本：{arguments['path']}")

        return ToolResult(ok=True, content=content)
