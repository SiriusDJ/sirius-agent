"""execute_command 的危险命令黑名单：命中即拒绝，不接受任何配置放开。

覆盖类 Unix 和 Windows（PowerShell/cmd）两套命令变体，因为 Sirius-Agent 需要在
Windows 上运行（execute_command 工具本身已有 win32 专属的进程树清理逻辑）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (类别说明, 正则表达式) —— 正则用 re.IGNORECASE 编译，命中即视为高危操作
_BLACKLIST_PATTERNS: list[tuple[str, str]] = [
    # 删除系统关键目录
    (
        "删除系统关键目录",
        r"rm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\s+(/|~|/\*|C:\\|%SYSTEMROOT%)",
    ),
    ("删除系统关键目录", r"\brd\s+/s\s+/q\s+C:\\"),
    ("删除系统关键目录", r"\bdel\s+/f\s+/s\s+/q\s+C:\\"),
    ("删除系统关键目录", r"Remove-Item\s+.*-Recurse.*-Force.*C:\\\s*$"),
    # 磁盘格式化 / 写裸设备
    ("磁盘格式化或写裸设备", r"\bmkfs(\.\w+)?\b"),
    ("磁盘格式化或写裸设备", r"\bformat\s+[a-zA-Z]:"),
    ("磁盘格式化或写裸设备", r"\bdd\s+.*\bof=/dev/"),
    # 远程脚本直接执行
    ("远程脚本直接执行", r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(sh|bash|zsh)\b"),
    ("远程脚本直接执行", r"\b(iwr|invoke-webrequest|curl)\b[^\n|]*\|\s*(iex|invoke-expression)\b"),
    # fork 炸弹
    ("fork 炸弹", r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    # 关机 / 重启系统
    ("关机或重启系统", r"\bshutdown\b"),
    ("关机或重启系统", r"\breboot\b"),
    ("关机或重启系统", r"\bRestart-Computer\b"),
]


@dataclass(frozen=True)
class BlacklistRule:
    """一条黑名单规则：人类可读的类别 + 编译后的正则。"""

    category: str
    regex: re.Pattern[str]


_BLACKLIST: list[BlacklistRule] = [
    BlacklistRule(category=category, regex=re.compile(pattern, re.IGNORECASE))
    for category, pattern in _BLACKLIST_PATTERNS
]


def check_blacklist(command: str) -> BlacklistRule | None:
    """检查 command 是否命中黑名单，命中则返回对应规则，否则返回 None。"""

    for rule in _BLACKLIST:
        if rule.regex.search(command):
            return rule
    return None
