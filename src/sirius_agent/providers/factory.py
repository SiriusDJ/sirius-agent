"""按 protocol 字符串创建对应的 Provider 实例。"""

from __future__ import annotations

from sirius_agent.config import ConfigError, ProviderConfig
from sirius_agent.providers.anthropic_provider import AnthropicProvider
from sirius_agent.providers.base import Provider
from sirius_agent.providers.openai_provider import OpenAIProvider


def create_provider(config: ProviderConfig) -> Provider:
    """按 config.protocol 创建对应的 Provider 实例。

    config.py 在加载配置时已经校验过 protocol 合法性，这里的报错分支是防御性兜底。
    """
    if config.protocol == "anthropic":
        return AnthropicProvider(config)
    if config.protocol == "openai":
        return OpenAIProvider(config)
    raise ConfigError(f"不支持的 protocol：{config.protocol}")
