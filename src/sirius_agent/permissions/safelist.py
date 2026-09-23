"""execute_command 的已知安全命令白名单：命中即直接放行，跳过 ask/模式兜底。

跟黑名单一样硬编码在代码里，不接受配置；但优先级低于规则引擎——用户如果显式
为某条命令配了 deny/ask 规则，规则引擎会先命中，轮不到这里。

命中前会先做一次 shell 元字符检查：即使基础命令在白名单里，只要整条命令里出现
管道/分号/重定向/命令替换这类可以拼接出别的命令的字符，一律不算安全。
"""

from __future__ import annotations

_SHELL_METACHARACTERS = ("|", ";", "&", ">", "<", "$(", "`", "\n")

# 允许在命令后面接任意参数——前提是这个基础命令不存在任何已知的破坏性 flag，
# 不管用户加什么参数，语义上都还是"只读查看"。
_SAFE_PREFIX_COMMANDS = frozenset(
    {
        "pwd",
        "ls",
        "dir",
        "cat",
        "type",
        "head",
        "tail",
        "wc",
        "echo",
        "whoami",
        "hostname",
        "git status",
        "git log",
        "git diff",
        "git show",
    }
)

# 只允许精确匹配，不允许接任何后缀参数——这类命令本身可能存在别的、有副作用的
# 用法（比如 npm 不一定在 -v 处就停止解析后续参数），所以不给"前缀+任意参数"的口子。
_SAFE_EXACT_COMMANDS = frozenset(
    {
        "git --version",
        "git remote -v",
        "python --version",
        "python -V",
        "python3 --version",
        "python3 -V",
        "node --version",
        "node -v",
        "npm --version",
        "npm -v",
        "java -version",
        "java --version",
        "uv --version",
        "ruff --version",
    }
)


def is_safe_command(command: str) -> bool:
    """判断 command 是否命中安全命令白名单。"""

    trimmed = command.strip()
    if not trimmed:
        return False
    if any(ch in trimmed for ch in _SHELL_METACHARACTERS):
        return False
    if trimmed in _SAFE_EXACT_COMMANDS:
        return True
    return any(trimmed == safe or trimmed.startswith(safe + " ") for safe in _SAFE_PREFIX_COMMANDS)
