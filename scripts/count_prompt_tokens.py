"""一次性调试脚本：用 Anthropic 的 count_tokens 接口，实际统计当前
system_prompt（稳定块 + 环境信息块）与工具 schema 的真实 token 数。

不生成任何回复，几乎零成本。用来确认可缓存前缀是否达到 Anthropic 的
最小可缓存长度门槛（旧模型 1024 token / 新模型 512 token），
不达标时缓存标记会被静默忽略，cache_creation_input_tokens 永远是 0。

用法：uv run python scripts/count_prompt_tokens.py [--config sirius-agent.yaml] [--provider claude-prod]
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
    parser = argparse.ArgumentParser(description="统计当前 system_prompt + tools 的真实 token 数")
    parser.add_argument("--config", default="sirius-agent.yaml")
    parser.add_argument("--provider", default=None)
    args = parser.parse_args()

    try:
        configs = load_provider_configs(args.config)
        provider_config = select_provider_config(configs, args.provider)
    except ConfigError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)

    if provider_config.protocol != "anthropic":
        print(f"错误：count_tokens 是 Anthropic 专属接口，'{provider_config.name}' 的协议是 {provider_config.protocol}")
        sys.exit(1)

    workspace_root = Path.cwd()
    registry = _build_tool_registry(workspace_root)
    tools = registry.list_tools()
    tool_schema = to_anthropic_tool_schema(tools)
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

    # 只统计 system+tools 本身（不含用户消息），用一条最短的占位消息把请求填完整
    full_result = await client.messages.count_tokens(
        model=provider_config.model,
        system=system_blocks,
        tools=tool_schema,
        messages=[{"role": "user", "content": "hi"}],
    )
    system_only_result = await client.messages.count_tokens(
        model=provider_config.model,
        system=system_blocks,
        messages=[{"role": "user", "content": "hi"}],
    )
    tools_only_result = await client.messages.count_tokens(
        model=provider_config.model,
        tools=tool_schema,
        messages=[{"role": "user", "content": "hi"}],
    )

    print(f"供应商：{provider_config.name}（模型：{provider_config.model}）")
    print(f"system 稳定块字符数：{len(system_prompt[0].text)}")
    print(f"system_prompt + tools + 占位用户消息 合计 token 数：{full_result.input_tokens}")
    print(f"仅 system_prompt + 占位用户消息 token 数：{system_only_result.input_tokens}")
    print(f"仅 tools + 占位用户消息 token 数：{tools_only_result.input_tokens}")
    print("（用户消息本身极短，可近似认为上面两个数字里绝大部分是 system/tools 自身贡献的）")


if __name__ == "__main__":
    asyncio.run(main())
