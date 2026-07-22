"""工作目录路径安全校验：文件类工具统一走这里，禁止路径穿越到工作目录之外。"""

from __future__ import annotations

from pathlib import Path

IGNORED_DIR_NAMES = {".git", "__pycache__", ".venv", "node_modules"}


class PathOutsideWorkspaceError(Exception):
    """请求的路径解析后落在工作目录之外。"""


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


def resolve_safe_path(workspace_root: Path, user_path: str) -> Path:
    """把 user_path 相对 workspace_root 解析为绝对路径。

    解析结果不在 workspace_root 之内时抛 PathOutsideWorkspaceError。
    """

    candidate = (workspace_root / user_path).resolve()
    if not is_within_workspace(workspace_root, candidate):
        raise PathOutsideWorkspaceError(f"路径超出工作目录范围：{user_path}")
    return candidate
