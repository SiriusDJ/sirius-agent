"""五个文件类工具异步化后的行为等价测试（read/glob/grep/write/edit）。"""

from __future__ import annotations

from pathlib import Path

from sirius_agent.tools.edit_file import EditFileTool
from sirius_agent.tools.glob_files import GlobFilesTool
from sirius_agent.tools.grep_content import GrepContentTool
from sirius_agent.tools.read_file import ReadFileTool
from sirius_agent.tools.write_file import WriteFileTool


# ---- read_file ----


async def test_read_file_success(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    tool = ReadFileTool(tmp_path)

    result = await tool.execute({"path": "a.txt"})

    assert result.ok is True
    assert result.content == "hello world"


async def test_read_file_not_found(tmp_path: Path):
    tool = ReadFileTool(tmp_path)

    result = await tool.execute({"path": "missing.txt"})

    assert result.ok is False


# ---- glob_files ----


async def test_glob_files_matches(tmp_path: Path):
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")
    ignored_dir = tmp_path / ".venv"
    ignored_dir.mkdir()
    (ignored_dir / "ignored.py").write_text("", encoding="utf-8")

    tool = GlobFilesTool(tmp_path)
    result = await tool.execute({"pattern": "**/*.py"})

    assert result.ok is True
    assert result.content == "a.py\nb.py"


# ---- grep_content ----


async def test_grep_content_matches(tmp_path: Path):
    (tmp_path / "a.txt").write_text("line one\nmagic keyword here\nline three", encoding="utf-8")

    tool = GrepContentTool(tmp_path)
    result = await tool.execute({"pattern": "magic keyword"})

    assert result.ok is True
    assert result.content == "a.txt:2: magic keyword here"


# ---- write_file ----


async def test_write_file_creates_and_overwrites(tmp_path: Path):
    tool = WriteFileTool(tmp_path)

    result = await tool.execute({"path": "nested/new.txt", "content": "first"})
    assert result.ok is True
    assert (tmp_path / "nested" / "new.txt").read_text(encoding="utf-8") == "first"

    result2 = await tool.execute({"path": "nested/new.txt", "content": "second"})
    assert result2.ok is True
    assert (tmp_path / "nested" / "new.txt").read_text(encoding="utf-8") == "second"


# ---- edit_file ----


async def test_edit_file_unique_match(tmp_path: Path):
    path = tmp_path / "test.txt"
    path.write_text("the quick fox", encoding="utf-8")
    tool = EditFileTool(tmp_path)

    result = await tool.execute({"path": "test.txt", "old_text": "quick", "new_text": "slow"})

    assert result.ok is True
    assert path.read_text(encoding="utf-8") == "the slow fox"


async def test_edit_file_no_match(tmp_path: Path):
    path = tmp_path / "test.txt"
    path.write_text("the quick fox", encoding="utf-8")
    tool = EditFileTool(tmp_path)

    result = await tool.execute({"path": "test.txt", "old_text": "slow", "new_text": "fast"})

    assert result.ok is False
    assert path.read_text(encoding="utf-8") == "the quick fox"


async def test_edit_file_multiple_matches(tmp_path: Path):
    path = tmp_path / "test.txt"
    path.write_text("fox fox fox", encoding="utf-8")
    tool = EditFileTool(tmp_path)

    result = await tool.execute({"path": "test.txt", "old_text": "fox", "new_text": "cat"})

    assert result.ok is False
    assert path.read_text(encoding="utf-8") == "fox fox fox"
