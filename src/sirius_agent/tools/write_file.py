"""write_file 工具：在工作目录内创建或覆盖写入一个文件。"""

from __future__ import annotations

from pathlib import Path

from sirius_agent.tools.base import ToolResult
from sirius_agent.tools.paths import PathOutsideWorkspaceError, resolve_safe_path


class WriteFileTool:
    name = "write_file"
    description = "在工作目录内创建或覆盖写入一个文件"
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的文件路径"},
            "content": {"type": "string", "description": "要写入的完整文件内容"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    def execute(self, arguments: dict) -> ToolResult:
        try:
            path = resolve_safe_path(self._workspace_root, arguments["path"])
        except PathOutsideWorkspaceError as e:
            return ToolResult(ok=False, content=str(e))

        content = arguments["content"]
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        path.write_bytes(data)

        return ToolResult(ok=True, content=f"已写入 {len(data)} 字节到 {arguments['path']}")
