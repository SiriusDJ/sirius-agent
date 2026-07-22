"""配置加载：解析 YAML 供应商配置、展开环境变量占位符、选择目标供应商。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

_SUPPORTED_PROTOCOLS = {"anthropic", "openai"}
_REQUIRED_FIELDS = ("name", "protocol", "model", "base_url", "api_key")
_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


class ConfigError(Exception):
    """配置文件缺字段、协议不支持、文件不存在、name 匹配不到等场景统一抛这个。"""


@dataclass
class ProviderConfig:
    """单个 LLM 供应商的配置。"""

    name: str
    protocol: str
    model: str
    base_url: str
    api_key: str
    thinking: bool = False


def _expand_env_placeholders(value: str, *, provider_name: str) -> str:
    """把字符串中的 ${VAR} 占位符替换成对应环境变量的值。

    找不到对应环境变量时抛 ConfigError，报错信息里写清楚是哪个变量缺失。
    """

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name not in os.environ:
            raise ConfigError(
                f"供应商 '{provider_name}' 的 api_key 引用了环境变量 '{var_name}'，"
                f"但该环境变量未设置"
            )
        return os.environ[var_name]

    return _ENV_PLACEHOLDER_RE.sub(_replace, value)


def load_provider_configs(path: str) -> list[ProviderConfig]:
    """加载并解析 YAML 配置文件，返回供应商配置列表。"""

    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在：{path}")

    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"配置文件 '{path}' 需要是一个非空的供应商配置列表")

    configs: list[ProviderConfig] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"配置文件 '{path}' 中存在一个非法的供应商条目：{entry!r}")

        entry_name = entry.get("name", "<unnamed>")

        missing = [field for field in _REQUIRED_FIELDS if field not in entry]
        if missing:
            raise ConfigError(
                f"供应商 '{entry_name}' 缺少必需字段：{', '.join(missing)}"
            )

        protocol = entry["protocol"]
        if protocol not in _SUPPORTED_PROTOCOLS:
            raise ConfigError(
                f"供应商 '{entry_name}' 的 protocol '{protocol}' 不受支持，"
                f"目前只支持：{', '.join(sorted(_SUPPORTED_PROTOCOLS))}"
            )

        api_key = _expand_env_placeholders(str(entry["api_key"]), provider_name=entry_name)

        configs.append(
            ProviderConfig(
                name=entry["name"],
                protocol=protocol,
                model=entry["model"],
                base_url=entry["base_url"],
                api_key=api_key,
                thinking=bool(entry.get("thinking", False)),
            )
        )

    return configs


def select_provider_config(
    configs: list[ProviderConfig], name: Optional[str]
) -> ProviderConfig:
    """按 name 选出目标供应商配置；name 为 None 时返回第一个。"""

    if name is None:
        return configs[0]

    for config in configs:
        if config.name == name:
            return config

    available = ", ".join(c.name for c in configs)
    raise ConfigError(f"找不到名为 '{name}' 的供应商配置，可用的有：{available}")
