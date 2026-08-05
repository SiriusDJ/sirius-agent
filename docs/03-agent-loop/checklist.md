# Sirius-Agent（Agent Loop）Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。核心循环逻辑（停止条件、并发/串行分批、Plan Mode 过滤、usage 解析）用 pytest 自动化验证；涉及真实终端交互（按键取消、真实 LLM 多步任务、协议切换）的场景用 tmux 端到端手动验证，呼应 CLAUDE.md 的验收方式。

## 实现完整性

- [ ] 六个工具（read_file/glob_files/grep_content/write_file/edit_file/execute_command）`execute` 均改为 `async def` 并正确声明 `safe` 属性（验证：`tests/test_tools_file_ops.py`、`tests/test_execute_command.py` 全部通过）
- [ ] `ToolRegistry` 新增 `has()`/`list_tools(only_safe)`，`execute` 改为异步且正确兜底未知工具名与工具内部未预期异常（验证：`tests/test_registry.py` 通过）
- [ ] `AnthropicProvider`、`OpenAIProvider` 均改为异步流式，且都能正确产出 `USAGE` 事件（含 OpenAI 最后一个空 `choices` 但带 `usage` 的 chunk 不被跳过）（验证：`tests/test_anthropic_provider.py`、`tests/test_openai_provider.py` 通过）
- [ ] `StreamCollector` 正确做到"实时转发 + 完整累积"双路收集，遇到 `ERROR` 提前停止消费（验证：`tests/test_agent.py` 中 `test_stream_collector_*` 用例通过）
- [ ] `run_agent_loop` 完整实现五种停止条件（正常结束/达到上限/用户取消/连续未知工具/流式错误）、按 `safe` 分批的并发与串行执行、Plan Mode 的只读工具过滤（验证：`tests/test_agent.py` 全部用例通过）
- [ ] `tui.py` 支持 `/plan`/`/do` 模式切换、Esc/Ctrl+C 取消监听任务、按 `TurnEventType`（含 `USAGE`、统一的 `STOPPED`）渲染（验证：T17/T19 的手动脚本检查 + 代码审查）
- [ ] `__main__.main()` 用 `asyncio.run()` 驱动整个异步 REPL（验证：`uv run python -c "import sirius_agent.__main__"` 无报错）

## 集成

- [ ] `tui.run_repl` 完整消费 `agent.run_agent_loop` 产出的事件流并正确渲染，不直接感知 Agent 内部状态（验证：审查代码确认 `tui.py` 只依赖 `TurnEvent`/`TurnEventType`/`StopReason`，不 import agent 内部私有函数）
- [ ] `__main__.main()` 完整串起 config → provider → ToolRegistry → session → 异步 tui（验证：`uv run sirius-agent` 实际启动进入 REPL，Ctrl+D 正常退出）
- [ ] 新增一个工具后无需改动 `agent.py`/`tui.py`，只需实现 `Tool` 协议并声明 `safe`（验证：审查代码，确认 `agent.py`/`tui.py` 不出现具体工具名字符串或按工具名分支的专属逻辑）
- [ ] Plan Mode 的只读限制是协议层面的（工具列表过滤），而不是 prompt 文字约束（验证：`tests/test_agent.py` 的 `test_plan_mode_only_exposes_safe_tools` 通过）

## 编译与测试

- [ ] `uv add --dev pytest pytest-asyncio` 成功安装，无报错
- [ ] `uv run python -c "import sirius_agent"` 无 ImportError
- [ ] `uv run pytest` 全部用例通过，无失败/跳过用例

## 端到端场景（对应 spec.md AC1-AC12）

- [ ] 场景1（AC1）：tmux 中输入一个需要多步操作的真实任务（如"读取 test.txt 的内容，根据内容创建 summary.txt 并写入一句总结，再执行命令确认 summary.txt 存在"），观察 Agent Loop 无需用户催促、自动连续完成"请求→工具→结果"多轮直到给出最终回复
- [ ] 场景2（AC2）：`tests/test_agent.py::test_run_agent_loop_max_iterations` 通过（用假 Provider 精确控制模型永远请求工具，验证跑满 20 轮后停止并给出 `MAX_ITERATIONS`）
- [ ] 场景3（AC3）：tmux 中让模型执行一个多步任务，任务进行到一半时按 Esc 或 Ctrl+C，观察当前步骤跑完后循环立即停止、回到 `>` 输入提示符、整个程序未退出，且可以继续下一轮正常输入
- [ ] 场景4（AC4）：`tests/test_agent.py::test_unknown_tool_two_consecutive_rounds_stops` 与 `test_unknown_tool_single_occurrence_does_not_stop` 均通过
- [ ] 场景5（AC5）：tmux 中临时把配置里的 API key 改错（或断网）触发一次流式错误，观察循环立即终止、界面展示清晰错误信息（不是 Python 堆栈）、程序不崩溃，且改回正确配置后下一轮对话恢复正常
- [ ] 场景6（AC6）：`tests/test_agent.py::test_safe_tools_run_concurrently` 通过（两个 `safe=True` 假工具各耗时 0.2 秒，验证总耗时明显小于 0.4 秒）
- [ ] 场景7（AC7）：`tests/test_agent.py::test_unsafe_tools_run_serially` 通过（两个 `safe=False` 假工具各耗时 0.2 秒，验证总耗时接近 0.4 秒）
- [ ] 场景8（AC8）：tmux 中进行一次正常对话，观察每一轮 LLM 请求结束后界面上打印出的 token 用量信息（输入/输出 tokens）
- [ ] 场景9（AC9）：tmux 中输入 `/plan` 后要求模型写文件，观察模型只能给出计划性文字回复、磁盘上未产生对应文件（因为只读工具列表里没有 write_file）；输入 `/do` 后再次提出同样要求，观察这次能正常写入文件
- [ ] 场景10（AC10）：tmux 中输入 `/plan` 后连续进行两三轮讨论（不输入 `/do`），观察每一轮都保持只读工具限制，不会中途自动恢复全工具模式
- [ ] 场景11（AC11）：把 `sirius-agent.yaml` 的 `protocol` 从 `anthropic` 切到 `openai`（或反之），重跑场景1、场景6/7 对应的手动尝试、场景9，观察 Agent Loop 相关行为在两种协议下一致，不改代码只改配置
- [ ] 场景12（AC12）：tmux 中提出一个一轮工具调用即可完成的简单任务（如"读一下 README.md 内容"），观察模型给出最终回复后循环立即在下一轮判定"无新工具调用"并正常结束，没有多余的空转请求
