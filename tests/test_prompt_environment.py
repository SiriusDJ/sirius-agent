"""gather_environment 的 git 分支/改动状态采集测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sirius_agent.prompt import environment as environment_module
from sirius_agent.prompt.environment import gather_environment

_REPO_ROOT = Path(__file__).resolve().parent.parent


async def test_gather_environment_in_git_repo():
    env = await gather_environment(_REPO_ROOT, model_name="test-model")

    assert env.git_branch is not None
    assert env.git_dirty is not None
    assert env.app_version != ""
    assert env.model_name == "test-model"
    assert env.cwd == str(_REPO_ROOT)


async def test_gather_environment_outside_git_repo(tmp_path):
    env = await gather_environment(tmp_path, model_name="test-model")

    assert env.git_branch is None
    assert env.git_dirty is None


class _HangingProcess:
    """模拟一个卡住不返回的 git 子进程，用来验证超时保护真的会终止等待并 kill 掉它。"""

    def __init__(self) -> None:
        self.killed = False

    async def communicate(self):
        await asyncio.sleep(10)
        return b"", b""

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        return None


async def test_run_git_times_out_and_kills_hanging_process(monkeypatch):
    hanging_process = _HangingProcess()

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return hanging_process

    monkeypatch.setattr(environment_module.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(environment_module, "_GIT_TIMEOUT", 0.05)

    result = await environment_module._run_git(Path("."), "status", "--porcelain")

    assert result is None
    assert hanging_process.killed is True
