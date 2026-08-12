"""诊断脚本：连续发两次结构完全相同的真实请求（跟 AnthropicProvider.stream_chat
实际构造的 kwargs 一致，包括 thinking 配置），打印完整 usage，定位缓存到底卡在哪一步。

跟 count_tokens 不同，这个脚本会真的调用 messages.create、真的产生费用（但
max_tokens 很小，成本可以忽略），因为只有真实生成请求才会触发缓存的写入/命中。

用法：uv run python scripts/diagnose_cache.py [--config sirius-agent.yaml] [--provider claude-prod]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import anthropic

from sirius_agent.config import ConfigError, load_provider_configs, select_provider_config
from sirius_agent.prompt.builder import build_system_prompt
from sirius_agent.prompt.environment import gather_environment
from sirius_agent.tools.edit_file import EditFileTool
from sirius_agent.tools.execute_command import ExecuteCommandTool
from sirius_agent.tools.glob_files import GlobFilesTool
from sirius_agent.tools.grep_content import GrepContentTool
from sirius_agent.tools.read_file import ReadFileTool
from sirius_agent.tools.registry import ToolRegistry
from sirius_agent.tools.schema import to_anthropic_tool_schema
from sirius_agent.tools.write_file import WriteFileTool

_CACHE_CONTROL = {"type": "ephemeral", "ttl": "5m"}


def _build_tool_registry(workspace_root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace_root))
    registry.register(WriteFileTool(workspace_root))
    registry.register(EditFileTool(workspace_root))
    registry.register(ExecuteCommandTool(workspace_root))
    registry.register(GlobFilesTool(workspace_root))
    registry.register(GrepContentTool(workspace_root))
    return registry


async def main() -> None:
    parser = argparse.ArgumentParser(description="连续两次真实请求，诊断缓存写入/命中卡在哪")
    parser.add_argument("--config", default="sirius-agent.yaml")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--no-thinking", action="store_true", help="强制关闭 thinking，隔离变量")
    args = parser.parse_args()

    try:
        configs = load_provider_configs(args.config)
        provider_config = select_provider_config(configs, args.provider)
    except ConfigError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)

    if provider_config.protocol != "anthropic":
        print(f"错误：这个脚本是 Anthropic 专属的，'{provider_config.name}' 协议是 {provider_config.protocol}")
        sys.exit(1)

    workspace_root = Path.cwd()
    registry = _build_tool_registry(workspace_root)
    tool_schema = to_anthropic_tool_schema(registry.list_tools())
    tool_schema[-1] = {**tool_schema[-1], "cache_control": _CACHE_CONTROL}

    env = await gather_environment(workspace_root, provider_config.model)
    system_prompt = build_system_prompt(env)
    system_blocks = [
        {
            "type": "text",
            "text": block.text,
            **({"cache_control": _CACHE_CONTROL} if block.cacheable else {}),
        }
        for block in system_prompt
    ]

    client = anthropic.AsyncAnthropic(api_key=provider_config.api_key, base_url=provider_config.base_url)

    request_kwargs: dict = dict(
        model=provider_config.model,
        max_tokens=16,
        system=system_blocks,
        tools=tool_schema,
        messages=[{"role": "user", "content": "hi"}],
    )
    use_thinking = provider_config.thinking and not args.no_thinking
    if use_thinking:
        request_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        request_kwargs["temperature"] = 1

    print(f"供应商：{provider_config.name}（模型：{provider_config.model}，thinking={use_thinking}）\n")

    for i in (1, 2):
        response = await client.messages.create(**request_kwargs)
        usage = response.usage
        print(f"第 {i} 次请求 usage：{usage.model_dump_json()}")
        if i == 1:
            await asyncio.sleep(1)  # 确保两次请求不在同一毫秒内、便于观察真实两次独立调用


if __name__ == "__main__":
    asyncio.run(main())
