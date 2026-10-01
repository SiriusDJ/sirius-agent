# Sirius-Agent

我正在构建一个终端 AI 编程助手（类似 Claude Code），项目名叫 Sirius-Agent，使用 Python 实现。

## 文档
各章节的 spec / plan / task / checklist 不放在本仓库，统一放在 `../sirius-agent-doc/<章节>/`（如 `../sirius-agent-doc/06-mcp/`）。

## 语言
中文回答，中文注释。

## 测试

开发完功能后，用 tmux 做端到端测试：

1. 在 tmux 中启动 Sirius-Agent
2. 输入一段真实的对话请求
3. 观察 Sirius-Agent 是否正确调用工具、生成回复
4. 对照 `../sirius-agent-doc/<章节>/checklist.md` 逐项验收
