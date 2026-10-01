"""测试用旧协议 MCP server：手写逐行 JSON-RPC，只懂 initialize 握手，拒绝 server/discover。

用来验证客户端探测失败后在同一连接回退到 initialize + notifications/initialized。
"""

from __future__ import annotations

import json
import sys


def _reply(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


_TOOLS = [
    {
        "name": "echo",
        "description": "legacy 回显",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    }
]


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        msg_id = msg.get("id")
        if msg_id is None:
            continue  # 通知（如 notifications/initialized）一律忽略

        if method == "initialize":
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "legacy-test-server", "version": "0.0.1"},
                    },
                }
            )
        elif method == "tools/list":
            _reply({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _TOOLS}})
        elif method == "tools/call":
            text = msg["params"]["arguments"].get("text", "")
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": [{"type": "text", "text": f"legacy:{text}"}], "isError": False},
                }
            )
        elif method == "ping":
            _reply({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        else:
            _reply(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )


if __name__ == "__main__":
    main()
