"""采集当前运行环境信息，供系统提示的环境信息模块使用。"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from dataclasses import dataclass
from datetime import date
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_PACKAGE_NAME = "sirius-agent"
_GIT_TIMEOUT = 5.0


def _app_version() -> str:
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return "unknown"


@dataclass
class EnvironmentContext:
    """一次请求发起时的环境快照。"""

    cwd: str
    platform: str
    date: str
    app_version: str
    model_name: str
    git_branch: str | None = None  # None = 不在 git 仓库中
    git_dirty: bool | None = None  # 与 git_branch 同步为 None


async def _run_git(workspace_root: Path, *args: str) -> str | None:
    """在 workspace_root 下跑一条 git 命令，成功返回 stdout（已 strip），失败或超时返回 None。

    超时保护是必须的：这个函数每轮 Agent Loop 迭代都会被调用，git 一旦卡住
    （凭据交互、异常挂载的网络盘等）就会拖住整个请求，而不只是拖住这一次采集。
    """

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=workspace_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return None

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()
        return None

    if proc.returncode != 0:
        return None
    return stdout.decode("utf-8", errors="replace").strip()


async def gather_environment(workspace_root: Path, model_name: str) -> EnvironmentContext:
    """采集 cwd/平台/日期/应用版本与 git 分支、改动状态。git 相关信息在不可用时置 None，不抛异常。"""

    git_branch = await _run_git(workspace_root, "rev-parse", "--abbrev-ref", "HEAD")
    git_dirty: bool | None = None
    if git_branch is not None:
        status_output = await _run_git(workspace_root, "status", "--porcelain")
        git_dirty = bool(status_output) if status_output is not None else None

    return EnvironmentContext(
        cwd=str(workspace_root),
        platform=sys.platform,
        date=date.today().isoformat(),
        app_version=_app_version(),
        model_name=model_name,
        git_branch=git_branch,
        git_dirty=git_dirty,
    )
