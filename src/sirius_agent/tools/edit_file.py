"""edit_file 工具：用唯一匹配替换的方式修改文件中的一段文本。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sirius_agent.tools.base import ToolResult


class EditFileTool:
    name = "edit_file"
    description = (
        "用唯一匹配替换的方式修改文件中的一段文本。"
        "编辑前必须先用 read_file 读过目标文件，不得在未读过内容的情况下直接编辑"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的文件路径"},
            "old_text": {"type": "string", "description": "要被替换的原文本，必须在文件中唯一出现"},
            "new_text": {"type": "string", "description": "替换后的新文本"},
        },
        "required": ["path", "old_text", "new_text"],
    }
    safe = False

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    async def execute(self, arguments: dict) -> ToolResult:
        # 是否越界已经由权限系统在调用这里之前判过；这里只负责实际编辑。
        path = (self._workspace_root / arguments["path"]).resolve()

        if not path.exists() or path.is_dir():
            return ToolResult(ok=False, content=f"文件不存在：{arguments['path']}")

        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        old_text = arguments["old_text"]
        count = content.count(old_text)

        if count == 0:
            return ToolResult(ok=False, content="未找到匹配的文本")
        if count > 1:
            return ToolResult(
                ok=False,
                content=f"匹配到 {count} 处，无法确定替换位置，请提供更长/更唯一的上下文",
            )

        new_content = content.replace(old_text, arguments["new_text"], 1)
        await asyncio.to_thread(path.write_text, new_content, encoding="utf-8")

        return ToolResult(ok=True, content=f"已完成替换：{arguments['path']}")
