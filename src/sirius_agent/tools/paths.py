"""工作目录路径安全校验：文件类工具统一走这里，禁止路径穿越到工作目录之外。"""

from __future__ import annotations

from pathlib import Path

IGNORED_DIR_NAMES = {".git", "__pycache__", ".venv", "node_modules"}


def is_ignored(relative_path: Path) -> bool:
    """判断相对路径是否落在默认忽略的目录（.git/__pycache__/.venv/node_modules）下。"""

    return any(part in IGNORED_DIR_NAMES for part in relative_path.parts)


def is_within_workspace(workspace_root: Path, candidate: Path) -> bool:
    """判断 candidate 是否落在 workspace_root 之内（含自身）。"""

    try:
        candidate.resolve().relative_to(workspace_root.resolve())
        return True
    except ValueError:
        return False
