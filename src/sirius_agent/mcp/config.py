"""MCP server 配置：定位用户级/项目级两个 YAML 文件，读取 mcp_servers 段，校验、展开 ${VAR}、合并。

所有"坏文件/坏条目"都只告警并跳过，从不抛异常——配置问题不能让 sirius-agent 启动失败。
告警文本只包含文件路径、server 名、字段名、变量名，绝不包含展开后的值（避免泄漏凭据）。
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")
_SERVERS_KEY = "mcp_servers"

Warn = Callable[[str], None]


def default_warn(message: str) -> None:
    """默认告警出口：写到 stderr。"""

    print(f"[MCP] 警告：{message}", file=sys.stderr)


@dataclass(frozen=True)
class StdioServerConfig:
    """本地子进程 server：以 command + args 启动，通过 stdin/stdout 通信。"""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)  # 值已展开 ${VAR}


@dataclass(frozen=True)
class HttpServerConfig:
    """远程 server：走 Streamable HTTP，headers 注入每次请求。"""

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)  # 值已展开 ${VAR}


McpServerConfig = StdioServerConfig | HttpServerConfig


def user_config_path() -> Path:
    """用户级配置文件路径：跨项目共享。"""

    return Path.home() / ".sirius-agent" / "config.yaml"


def project_config_path(workspace_root: Path) -> Path:
    """项目级配置文件路径：同名 server 完整覆盖用户级。"""

    return workspace_root / ".sirius-agent.yaml"


def expand_env_value(value: str, context: str, warn: Warn) -> str:
    """把 value 中的 ${VAR} 替换成宿主环境变量的值；未定义的变量展开为空串并告警。"""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name not in os.environ:
            warn(f"{context} 引用的环境变量 '{var_name}' 未定义，已展开为空串")
            return ""
        return os.environ[var_name]

    return _ENV_PLACEHOLDER_RE.sub(_replace, value)


def _read_servers_section(path: Path, warn: Warn) -> dict:
    """读取一个配置文件的 mcp_servers 段（原始条目）；文件缺失或格式非法都视为空。"""

    if not path.is_file():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        warn(f"配置文件 '{path}' 读取失败，已跳过：{e}")
        return {}

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        warn(f"配置文件 '{path}' 顶层需要是一个 map，已跳过")
        return {}

    servers = raw.get(_SERVERS_KEY)
    if servers is None:
        return {}
    if not isinstance(servers, dict):
        warn(f"配置文件 '{path}' 的 {_SERVERS_KEY} 需要是一个 map（server 名 → 定义），已跳过")
        return {}
    return servers


def _is_str_map(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    )


def _expand_map(values: dict[str, str], context: str, warn: Warn) -> dict[str, str]:
    return {key: expand_env_value(value, f"{context} 的 '{key}'", warn) for key, value in values.items()}


def parse_server_entry(name: str, raw: object, warn: Warn) -> McpServerConfig | None:
    """校验并解析一个 server 定义；不合法时告警并返回 None（跳过该 server）。"""

    if not isinstance(raw, dict):
        warn(f"server '{name}' 的定义需要是一个 map，已跳过")
        return None

    server_type = raw.get("type")

    if server_type == "stdio":
        command = raw.get("command")
        if not isinstance(command, str) or not command:
            warn(f"server '{name}'（stdio）缺少必填字段 command，已跳过")
            return None
        args = raw.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            warn(f"server '{name}' 的 args 需要是字符串数组，已跳过")
            return None
        env = raw.get("env", {})
        if not _is_str_map(env):
            warn(f"server '{name}' 的 env 需要是字符串 map，已跳过")
            return None
        return StdioServerConfig(
            name=name,
            command=command,
            args=list(args),
            env=_expand_map(env, f"server '{name}' env", warn),
        )

    if server_type == "http":
        url = raw.get("url")
        if not isinstance(url, str) or not url:
            warn(f"server '{name}'（http）缺少必填字段 url，已跳过")
            return None
        headers = raw.get("headers", {})
        if not _is_str_map(headers):
            warn(f"server '{name}' 的 headers 需要是字符串 map，已跳过")
            return None
        return HttpServerConfig(
            name=name,
            url=url,
            headers=_expand_map(headers, f"server '{name}' headers", warn),
        )

    if server_type is None:
        warn(f"server '{name}' 缺少 type 字段（需要是 stdio 或 http），已跳过")
    else:
        warn(f"server '{name}' 的 type '{server_type}' 不合法（需要是 stdio 或 http），已跳过")
    return None


def load_mcp_server_configs(
    user_path: Path, project_path: Path, warn: Warn = default_warn
) -> dict[str, McpServerConfig]:
    """读取两层配置并按 server 名合并：项目级同名条目整条覆盖用户级，再逐条校验。

    先合并原始条目再校验，保证"完整覆盖"语义：项目级条目无论好坏都整条取代用户级，
    不会因为项目级写坏了而悄悄回退到用户级的定义。
    """

    merged_raw: dict = {}
    merged_raw.update(_read_servers_section(user_path, warn))
    merged_raw.update(_read_servers_section(project_path, warn))

    configs: dict[str, McpServerConfig] = {}
    for name, raw in merged_raw.items():
        config = parse_server_entry(str(name), raw, warn)
        if config is not None:
            configs[config.name] = config
    return configs
