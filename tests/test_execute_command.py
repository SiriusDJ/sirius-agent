"""execute_command 工具改用 asyncio 子进程后的行为测试。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from sirius_agent.tools.execute_command import ExecuteCommandTool


async def test_success_command(tmp_path: Path):
    tool = ExecuteCommandTool(tmp_path)

    result = await tool.execute({"command": f'"{sys.executable}" -c "print(1)"'})

    assert result.ok is True
    assert "1" in result.content


async def test_nonzero_exit(tmp_path: Path):
    tool = ExecuteCommandTool(tmp_path)

    result = await tool.execute({"command": f'"{sys.executable}" -c "import sys; sys.exit(1)"'})

    assert result.ok is False


async def test_timeout(tmp_path: Path):
    tool = ExecuteCommandTool(tmp_path, timeout=0.1)

    start = time.monotonic()
    result = await tool.execute(
        {"command": f'"{sys.executable}" -c "import time; time.sleep(2)"'}
    )
    elapsed = time.monotonic() - start

    assert result.ok is False
    assert "超时" in result.content
    assert elapsed < 1.5
