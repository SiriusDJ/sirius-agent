"""Sirius-Agent CLI 入口：解析参数，组装各模块并启动交互循环。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from functools import partial
from pathlib import Path

from sirius_agent.config import ConfigError, load_provider_configs, select_provider_config
from sirius_agent.mcp.config import load_mcp_server_configs, project_config_path, user_config_path
from sirius_agent.mcp.connection import McpConnection, default_client_factory
from sirius_agent.mcp.manager import McpManager
from sirius_agent.mcp.permission_bridge import (
    McpAwarePermissionEngine,
    mcp_pattern_generalizer,
    mcp_rule_matcher,
)
from sirius_agent.permissions.gate import PermissionGate
from sirius_agent.permissions.rule_store import (
    RuleFileError,
    load_rule_file,
    local_rules_path,
    project_rules_path,
    user_rules_path,
)
from sirius_agent.permissions.rules import RuleSet
from sirius_agent.permissions.types import PermissionMode, RuleSource
from sirius_agent.providers.base import Provider
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


def _build_permission_gate(
    workspace_root: Path, mode: PermissionMode, tool_registry: ToolRegistry
) -> PermissionGate:
    """加载用户级/项目级/本地级三个规则文件并合并，组装出 PermissionEngine + PermissionGate。

    MCP 工具通过 permission_bridge 的注入件接入：规则工具名支持 glob（mcp__github__*）、
    自报只读的 MCP 工具按 read 类兜底、人在回路对 MCP 工具建议整工具放行。
    """

    def _is_read_only(tool_name: str) -> bool:
        return tool_registry.has(tool_name) and tool_registry.get(tool_name).safe

    try:
        user_rules = load_rule_file(user_rules_path(), RuleSource.USER)
        project_rules = load_rule_file(project_rules_path(workspace_root), RuleSource.PROJECT)
        local_rules = load_rule_file(local_rules_path(workspace_root), RuleSource.LOCAL)
    except RuleFileError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)

    rule_set = RuleSet.merge(user_rules, project_rules, local_rules, matcher=mcp_rule_matcher)
    engine = McpAwarePermissionEngine(workspace_root, rule_set, mode, is_read_only=_is_read_only)
    return PermissionGate(
        engine, rule_set, local_rules_path(workspace_root), pattern_generalizer=mcp_pattern_generalizer
    )


async def _start_mcp(workspace_root: Path) -> McpManager:
    """读取两层 MCP 配置，并发连接全部 server；失败的只告警跳过，成功的打印摘要。"""

    configs = load_mcp_server_configs(user_config_path(), project_config_path(workspace_root))
    client_factory = partial(default_client_factory, cwd=workspace_root)
    manager = McpManager(configs, connection_factory=lambda config: McpConnection(config, client_factory))
    for status in await manager.start_all():
        if status.ok:
            print(f"[MCP] {status.name}：已连接，{status.tool_count} 个工具")
    return manager


async def _run(provider: Provider, mode: PermissionMode) -> None:
    """异步主流程：先同步连完全部 MCP server 再进入 TUI，退出时统一关闭连接。"""

    workspace_root = Path.cwd()
    mcp_manager = await _start_mcp(workspace_root)
    try:
        tool_registry = _build_tool_registry(workspace_root)
        mcp_manager.register_into(tool_registry)
        permission_gate = _build_permission_gate(workspace_root, mode, tool_registry)
        session = ConversationSession()
        await run_repl(provider, tool_registry, session, workspace_root, permission_gate)
    finally:
        await mcp_manager.close_all()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sirius-agent", description="终端流式 AI 对话助手")
    parser.add_argument(
        "--config", default="sirius-agent.yaml", help="YAML 配置文件路径（默认：./sirius-agent.yaml）"
    )
    parser.add_argument("--provider", default=None, help="要使用的供应商 name（默认：配置文件中的第一个）")
    parser.add_argument(
        "--permission-mode",
        default="default",
        choices=["default", "accept_edits", "plan", "bypass"],
        help="初始权限模式（默认：default）",
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

    asyncio.run(_run(provider, PermissionMode(args.permission_mode)))


if __name__ == "__main__":
    main()
