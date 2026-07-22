"""grep_content 工具：在工作目录内的文件里搜索匹配某正则模式的内容。"""

from __future__ import annotations

import re
from pathlib import Path

from sirius_agent.tools.base import ToolResult
from sirius_agent.tools.paths import is_ignored, is_within_workspace


class GrepContentTool:
    name = "grep_content"
    description = "在工作目录内按正则表达式搜索文件内容，返回匹配的文件名、行号与行内容"
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "要搜索的正则表达式"},
            "file_glob": {
                "type": "string",
                "description": "限定搜索的文件范围，如 *.py，默认 *（全部文件）",
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    def execute(self, arguments: dict) -> ToolResult:
        pattern = arguments["pattern"]
        file_glob = arguments.get("file_glob") or "*"
        root = self._workspace_root

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(ok=False, content=f"正则表达式无效：{e}")

        hits: list[str] = []
        for candidate in root.rglob(file_glob):
            if not candidate.is_file():
                continue
            if not is_within_workspace(root, candidate):
                continue
            relative = candidate.resolve().relative_to(root.resolve())
            if is_ignored(relative):
                continue

            try:
                text = candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{relative.as_posix()}:{line_no}: {line}")

        if not hits:
            return ToolResult(ok=True, content="未找到匹配")
        return ToolResult(ok=True, content="\n".join(hits))
