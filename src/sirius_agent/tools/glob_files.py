"""glob_files 工具：按 glob 模式在工作目录内查找文件。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sirius_agent.tools.base import ToolResult
from sirius_agent.tools.paths import is_ignored, is_within_workspace


def _glob_sync(root: Path, pattern: str) -> list[str]:
    matches: list[str] = []
    for candidate in root.glob(pattern):
        if not candidate.is_file():
            continue
        if not is_within_workspace(root, candidate):
            continue
        relative = candidate.resolve().relative_to(root.resolve())
        if is_ignored(relative):
            continue
        matches.append(relative.as_posix())
    matches.sort()
    return matches


class GlobFilesTool:
    name = "glob_files"
    description = "按 glob 模式（如 **/*.py）在工作目录内查找文件"
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
        },
        "required": ["pattern"],
    }
    safe = True

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    async def execute(self, arguments: dict) -> ToolResult:
        matches = await asyncio.to_thread(_glob_sync, self._workspace_root, arguments["pattern"])
        if not matches:
            return ToolResult(ok=True, content="未找到匹配文件")
        return ToolResult(ok=True, content="\n".join(matches))
