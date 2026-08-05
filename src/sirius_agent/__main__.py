"""Sirius-Agent CLI 入口：解析参数，组装各模块并启动交互循环。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sirius_agent.config import ConfigError, load_provider_configs, select_provider_config
from sirius_agent.providers.factory import create_provider
from sirius_agent.session import ConversationSession
from sirius_agent.tools.edit_file import EditFileTool
from sirius_agent.tools.execute_command import ExecuteCommandTool
from sirius_agent.tools.glob_files import GlobFilesTool
from sirius_agent.tools.grep_content import GrepContentTool
from sirius_agent.tools.read_file import ReadFileTool
from sirius_agent.tools.registry import ToolRegistry
from sirius_agent.tools.write_file import WriteFileTool
from sirius_agent.tui import run_repl


def _build_tool_registry(workspace_root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace_root))
    registry.register(WriteFileTool(workspace_root))
    registry.register(EditFileTool(workspace_root))
    registry.register(ExecuteCommandTool(workspace_root))
    registry.register(GlobFilesTool(workspace_root))
    registry.register(GrepContentTool(workspace_root))
    return registry


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sirius-agent", description="终端流式 AI 对话助手")
    parser.add_argument(
        "--config", default="sirius-agent.yaml", help="YAML 配置文件路径（默认：./sirius-agent.yaml）"
    )
    parser.add_argument(
        "--provider", default=None, help="要使用的供应商 name（默认：配置文件中的第一个）"
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args(sys.argv[1:])

    try:
        configs = load_provider_configs(args.config)
        provider_config = select_provider_config(configs, args.provider)
        provider = create_provider(provider_config)
    except ConfigError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)

    tool_registry = _build_tool_registry(Path.cwd())
    session = ConversationSession()
    asyncio.run(run_repl(provider, tool_registry, session))


if __name__ == "__main__":
    main()
