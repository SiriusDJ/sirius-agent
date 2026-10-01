"""mcp/config.py：两层合并、字段校验、${VAR} 展开、坏文件容错。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sirius_agent.mcp.config import (
    HttpServerConfig,
    StdioServerConfig,
    load_mcp_server_configs,
    project_config_path,
    user_config_path,
)


class _Warnings(list):
    def __call__(self, message: str) -> None:
        self.append(message)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def warns() -> _Warnings:
    return _Warnings()


def test_config_paths(tmp_path):
    assert user_config_path() == Path.home() / ".sirius-agent" / "config.yaml"
    assert project_config_path(tmp_path) == tmp_path / ".sirius-agent.yaml"


def test_project_overrides_user_by_name_as_whole_entry(tmp_path, warns):
    user = _write(
        tmp_path / "user.yaml",
        """
mcp_servers:
  shared:
    type: stdio
    command: user-cmd
    args: [--user]
    env: {A: "1"}
  only_user:
    type: http
    url: http://user
""",
    )
    project = _write(
        tmp_path / "project.yaml",
        """
mcp_servers:
  shared:
    type: stdio
    command: project-cmd
  only_project:
    type: stdio
    command: p
""",
    )

    configs = load_mcp_server_configs(user, project, warns)

    assert set(configs) == {"shared", "only_user", "only_project"}
    # 完整覆盖：项目级没写 args/env，就不会继承用户级的
    assert configs["shared"] == StdioServerConfig(name="shared", command="project-cmd", args=[], env={})
    assert configs["only_user"] == HttpServerConfig(name="only_user", url="http://user", headers={})
    assert warns == []


def test_broken_project_entry_does_not_fall_back_to_user(tmp_path, warns):
    user = _write(tmp_path / "u.yaml", "mcp_servers:\n  s: {type: stdio, command: ok}\n")
    project = _write(tmp_path / "p.yaml", "mcp_servers:\n  s: {type: stdio}\n")

    configs = load_mcp_server_configs(user, project, warns)

    assert configs == {}
    assert any("command" in w for w in warns)


def test_missing_files_mean_no_servers(tmp_path, warns):
    assert load_mcp_server_configs(tmp_path / "nope1.yaml", tmp_path / "nope2.yaml", warns) == {}
    assert warns == []


def test_invalid_yaml_file_is_skipped_others_still_load(tmp_path, warns):
    user = _write(tmp_path / "u.yaml", "mcp_servers:\n  good: {type: stdio, command: ok}\n")
    project = _write(tmp_path / "p.yaml", "mcp_servers: [unclosed\n  : : :")

    configs = load_mcp_server_configs(user, project, warns)

    assert list(configs) == ["good"]
    assert len(warns) == 1 and "p.yaml" in warns[0]


@pytest.mark.parametrize(
    "text",
    ["", "other_key: 1\n", "mcp_servers:\n", "mcp_servers: {}\n"],
)
def test_empty_or_absent_section_means_no_servers(tmp_path, warns, text):
    project = _write(tmp_path / "p.yaml", text)
    assert load_mcp_server_configs(tmp_path / "none.yaml", project, warns) == {}
    assert warns == []


@pytest.mark.parametrize(
    "text",
    ["- a\n- b\n", "mcp_servers: [a, b]\n", "just a string\n"],
)
def test_wrong_structure_is_skipped_with_warning(tmp_path, warns, text):
    project = _write(tmp_path / "p.yaml", text)
    assert load_mcp_server_configs(tmp_path / "none.yaml", project, warns) == {}
    assert len(warns) == 1


@pytest.mark.parametrize(
    ("entry", "keyword"),
    [
        ("{type: stdio}", "command"),
        ("{type: stdio, command: ''}", "command"),
        ("{type: http}", "url"),
        ("{command: x}", "type"),
        ("{type: sse, url: http://x}", "sse"),
        ("{type: stdio, command: x, args: notalist}", "args"),
        ("{type: stdio, command: x, env: {A: 1}}", "env"),
        ("{type: http, url: http://x, headers: [a]}", "headers"),
        ("just-a-string", "map"),
    ],
)
def test_invalid_server_skipped_others_unaffected(tmp_path, warns, entry, keyword):
    project = _write(
        tmp_path / "p.yaml",
        f"mcp_servers:\n  bad: {entry}\n  good: {{type: http, url: http://ok}}\n",
    )

    configs = load_mcp_server_configs(tmp_path / "none.yaml", project, warns)

    assert list(configs) == ["good"]
    assert len(warns) == 1
    assert "bad" in warns[0] and keyword in warns[0]


def test_env_and_headers_expand_vars(tmp_path, warns, monkeypatch):
    monkeypatch.setenv("MEW_TEST_TOKEN", "s3cret-value")
    monkeypatch.delenv("MEW_TEST_UNDEFINED", raising=False)
    project = _write(
        tmp_path / "p.yaml",
        """
mcp_servers:
  local:
    type: stdio
    command: ${MEW_TEST_TOKEN}
    args: ["--token=${MEW_TEST_TOKEN}"]
    env:
      TOKEN: ${MEW_TEST_TOKEN}
      MISSING: pre-${MEW_TEST_UNDEFINED}-post
  remote:
    type: http
    url: http://x/${MEW_TEST_TOKEN}
    headers:
      Authorization: Bearer ${MEW_TEST_TOKEN}
""",
    )

    configs = load_mcp_server_configs(tmp_path / "none.yaml", project, warns)

    local = configs["local"]
    assert local.env == {"TOKEN": "s3cret-value", "MISSING": "pre--post"}
    # command / args / url 不展开
    assert local.command == "${MEW_TEST_TOKEN}"
    assert local.args == ["--token=${MEW_TEST_TOKEN}"]
    remote = configs["remote"]
    assert remote.headers == {"Authorization": "Bearer s3cret-value"}
    assert remote.url == "http://x/${MEW_TEST_TOKEN}"

    assert len(warns) == 1
    assert "MEW_TEST_UNDEFINED" in warns[0] and "local" in warns[0]
    # 告警不回显任何敏感值
    assert all("s3cret-value" not in w for w in warns)
