# Sirius-Agent（Agent Loop）Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml` | 新增 `pytest`/`pytest-asyncio` dev 依赖，配置 `asyncio_mode = "auto"` |
| 修改 | `tools/base.py` | `Tool` 协议新增 `safe: bool`；`execute` 改为 `async def` |
| 修改 | `tools/registry.py` | 新增 `has()`、`list_tools(only_safe)`；`execute` 改为 `async def`，兜底未知工具与未预期异常 |
| 修改 | `tools/read_file.py` | `execute` 改 `async def` + `asyncio.to_thread` 包裹阻塞读取；`safe = True` |
| 修改 | `tools/glob_files.py` | 同上，`safe = True` |
| 修改 | `tools/grep_content.py` | 同上，`safe = True` |
| 修改 | `tools/write_file.py` | `execute` 改 `async def` + `asyncio.to_thread` 包裹阻塞写入；`safe = False` |
| 修改 | `tools/edit_file.py` | 同上，`safe = False` |
| 修改 | `tools/execute_command.py` | 改用 `asyncio.create_subprocess_shell` + `asyncio.wait_for`；`safe = False` |
| 修改 | `providers/base.py` | 新增 `TokenUsage`、`StreamEventType.USAGE`、`StreamEvent.usage`；`Provider.stream_chat` 签名改 `AsyncIterator` |
| 修改 | `providers/anthropic_provider.py` | 改用 `AsyncAnthropic`；`get_final_message()` 取 usage 并产出 `USAGE` 事件 |
| 修改 | `providers/openai_provider.py` | 改用 `AsyncOpenAI`；`stream_options={"include_usage": True}`；修正空 `choices` 判断以拿到 usage chunk |
| 重写 | `agent.py` | `StopReason`/`TurnEventType`/`TurnEvent`/`StreamCollector`/`run_agent_loop()` |
| 修改 | `tui.py` | `async run_repl`；`/plan`/`/do`；取消监听任务；新事件渲染 |
| 修改 | `__main__.py` | `main()` 用 `asyncio.run()` 驱动 |
| 新建 | `tests/test_registry.py` | ToolRegistry 新能力测试 |
| 新建 | `tests/test_tools_file_ops.py` | 五个文件类工具异步化后行为等价测试 |
| 新建 | `tests/test_execute_command.py` | execute_command 异步子进程测试 |
| 新建 | `tests/test_anthropic_provider.py` | AnthropicProvider usage/流式解析测试 |
| 新建 | `tests/test_openai_provider.py` | OpenAIProvider usage/流式解析测试 |
| 新建 | `tests/test_agent.py` | StreamCollector + run_agent_loop 全部场景测试 |

## T1: pyproject.toml — 引入测试依赖

**文件：** `pyproject.toml`
**依赖：** 无
**步骤：**
1. 运行 `uv add --dev pytest pytest-asyncio`（或手工在 `[dependency-groups]` 下加 `dev = ["pytest>=8.3", "pytest-asyncio>=0.24"]`）
2. 新增 `[tool.pytest.ini_options]`，设置 `asyncio_mode = "auto"`（让 `async def test_...` 函数无需额外装饰器即可运行）

**验证：** `uv run pytest --collect-only` 成功执行（此时还没有测试文件，收集到 0 个用例但不报错）

## T2: tools/base.py — Tool 协议异步化 + 安全分类

**文件：** `src/sirius_agent/tools/base.py`
**依赖：** 无
**步骤：**
1. `Tool(Protocol)` 新增类属性 `safe: bool`（注释说明 True=只读无副作用可并发，False=有副作用须串行）
2. `Tool.execute` 签名改为 `async def execute(self, arguments: dict) -> ToolResult: ...`
3. `ToolCall`、`ToolResult` 两个 dataclass 不变

**验证：** `uv run python -c "from sirius_agent.tools.base import Tool, ToolCall, ToolResult"` 无报错

## T3: tools/registry.py — 扩展 has / list_tools(only_safe) / 异步 execute

**文件：** `src/sirius_agent/tools/registry.py`
**依赖：** T2
**步骤：**
1. 新增 `has(self, name: str) -> bool`：返回 `name in self._tools`
2. `list_tools` 改为 `list_tools(self, only_safe: bool = False) -> list[Tool]`：`only_safe=True` 时只返回 `tool.safe` 为真的工具
3. `execute` 改为 `async def execute(self, name: str, arguments: dict) -> ToolResult`：
   - 若 `not self.has(name)`，直接返回 `ToolResult(ok=False, content=f"未知的工具：{name}")`（不再抛 `ToolError`，因为 agent 层需要把这类失败当结构化结果处理并计数）
   - 否则 `await self.get(name).execute(arguments)`，`try/except Exception` 兜底未预期异常，转成 `ToolResult(ok=False, content=f"工具执行出现意外错误：{e}")`

**验证：** 新建 `tests/test_registry.py`：`has()` 对已注册/未注册工具名返回正确布尔值；`list_tools(only_safe=True)` 只返回 `safe=True` 的工具（用两个假工具类，一个 safe 一个不 safe）；`await execute(未注册名, {})` 返回 `ok=False` 且内容含"未知的工具"；`await execute(已注册但内部抛异常的假工具, {})` 返回 `ok=False` 且不向上抛出异常

## T4: tools/read_file.py — 异步化

**文件：** `src/sirius_agent/tools/read_file.py`
**依赖：** T2
**步骤：**
1. `execute` 改为 `async def execute(self, arguments: dict) -> ToolResult`
2. `path.read_text(encoding="utf-8")` 改为 `await asyncio.to_thread(path.read_text, encoding="utf-8")`
3. 新增类属性 `safe = True`
4. 其余逻辑（路径校验、不存在/是目录的错误分支）不变

**验证：** 新建 `tests/test_tools_file_ops.py`，加入 `test_read_file_success`（在 `tmp_path` 下建文件，`await tool.execute({"path": ...})` 返回 `ok=True` 且内容正确）和 `test_read_file_not_found`（`ok=False`）两个 `async def` 测试函数

## T5: tools/glob_files.py — 异步化

**文件：** `src/sirius_agent/tools/glob_files.py`
**依赖：** T2
**步骤：**
1. `execute` 改为 `async def`；把 `root.glob(pattern)` 及后续过滤遍历这段阻塞逻辑整体放进一个内部同步辅助函数，再用 `await asyncio.to_thread(该辅助函数, ...)` 调用
2. 新增类属性 `safe = True`

**验证：** 在 `tests/test_tools_file_ops.py` 追加 `test_glob_files_matches`：`tmp_path` 下建若干文件（含应被忽略目录下的文件），`await tool.execute({"pattern": "**/*.py"})` 返回预期的文件列表

## T6: tools/grep_content.py — 异步化

**文件：** `src/sirius_agent/tools/grep_content.py`
**依赖：** T2
**步骤：**
1. `execute` 改为 `async def`；把 `rglob` 遍历 + 逐行正则匹配这段阻塞逻辑放进内部同步辅助函数，`await asyncio.to_thread(...)` 调用
2. 新增类属性 `safe = True`

**验证：** 在 `tests/test_tools_file_ops.py` 追加 `test_grep_content_matches`：`tmp_path` 下建含关键字的文件，`await tool.execute({"pattern": ...})` 返回正确的文件名/行号/内容

## T7: tools/write_file.py — 异步化

**文件：** `src/sirius_agent/tools/write_file.py`
**依赖：** T2
**步骤：**
1. `execute` 改为 `async def`；`path.parent.mkdir(...)` + `path.write_bytes(data)` 这段包一层 `await asyncio.to_thread(...)`（可以用一个内部同步辅助函数封装这两步）
2. 新增类属性 `safe = False`

**验证：** 在 `tests/test_tools_file_ops.py` 追加 `test_write_file_creates_and_overwrites`：写入新文件（含自动建父目录）和覆盖已存在文件两种场景，磁盘内容与返回结果都符合预期

## T8: tools/edit_file.py — 异步化

**文件：** `src/sirius_agent/tools/edit_file.py`
**依赖：** T2
**步骤：**
1. `execute` 改为 `async def`；读取、`count`/替换判断、写回这几步阻塞 I/O 包一层 `await asyncio.to_thread(...)`（判断逻辑本身是纯内存计算，可以留在协程里，只把 `read_text`/`write_text` 两次真正的文件 I/O 分别 `to_thread`）
2. 新增类属性 `safe = False`

**验证：** 在 `tests/test_tools_file_ops.py` 追加 `test_edit_file_unique_match`、`test_edit_file_no_match`、`test_edit_file_multiple_matches` 三个测试：分别验证唯一匹配替换成功、0 次匹配报错且文件未改动、多次匹配报错且文件未改动

## T9: tools/execute_command.py — 改用 asyncio 子进程

**文件：** `src/sirius_agent/tools/execute_command.py`
**依赖：** T2
**步骤：**
1. `execute` 改为 `async def`
2. 用 `proc = await asyncio.create_subprocess_shell(command, cwd=self._workspace_root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)` 启动子进程
3. `try: stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)`；捕获 `asyncio.TimeoutError` → `proc.kill()` 后 `await proc.wait()`，返回 `ToolResult(ok=False, content=f"命令执行超时（{self._timeout:g} 秒）：{command}")`
4. 正常完成时把 `stdout`/`stderr` 从 bytes `decode("utf-8", errors="replace")`，拼装 `content` 字符串（沿用原有格式：退出码 + stdout + stderr），`ok = (proc.returncode == 0)`
5. 新增类属性 `safe = False`

**验证：** 新建 `tests/test_execute_command.py`：`test_success_command` 跑一个必然成功的命令（如 `python -c "print(1)"`）验证 `ok=True` 且 stdout 含 `"1"`；`test_nonzero_exit` 跑一个必然非零退出的命令验证 `ok=False`；`test_timeout` 用 `timeout=0.1` 构造一个短 `ExecuteCommandTool` 实例，跑一个必然超过 0.1 秒的命令（如 `python -c "import time; time.sleep(2)"`），验证返回超时错误且测试本身没有卡死（用 `pytest-asyncio` 的默认超时或断言实际耗时接近 0.1 秒）

## T10: providers/base.py — 新增 TokenUsage / USAGE 事件 / 异步签名

**文件：** `src/sirius_agent/providers/base.py`
**依赖：** T2
**步骤：**
1. 新增 `@dataclass TokenUsage`：`input_tokens: int`、`output_tokens: int`
2. `StreamEventType` 新增 `USAGE = "usage"`
3. `StreamEvent` 新增字段 `usage: Optional[TokenUsage] = None`
4. `Provider.stream_chat` 签名改为 `def stream_chat(self, messages: list[Message], tools: Optional[list[Tool]] = None) -> AsyncIterator[StreamEvent]`（导入 `AsyncIterator` 替代 `Iterator`）

**验证：** `uv run python -c "from sirius_agent.providers.base import TokenUsage, StreamEventType, StreamEvent"` 无报错，`StreamEventType.USAGE` 存在

## T11: providers/anthropic_provider.py — 异步化 + usage

**文件：** `src/sirius_agent/providers/anthropic_provider.py`
**依赖：** T10
**步骤：**
1. 构造函数改用 `anthropic.AsyncAnthropic(api_key=..., base_url=...)`
2. `stream_chat` 改为 `async def stream_chat(...) -> AsyncIterator[StreamEvent]`
3. `with self._client.messages.stream(**kwargs) as stream:` 改为 `async with ...`；内层 `for event in stream:` 改为 `async for event in stream:`；`content_block_start`/`content_block_delta`/`content_block_stop` 的识别与拼接逻辑保持不变
4. 消费完流之后（`async with` 块内，紧跟在 `async for` 循环结束处），`final_message = await stream.get_final_message()`，产出 `StreamEvent(type=StreamEventType.USAGE, usage=TokenUsage(input_tokens=final_message.usage.input_tokens, output_tokens=final_message.usage.output_tokens))`
5. 再产出 `StreamEvent(type=StreamEventType.DONE)`
6. 异常捕获仍是 `except anthropic.APIError as e:`，包住整个 `async with` 块

**验证：** 新建 `tests/test_anthropic_provider.py`：构造一个假的异步流对象（模拟 `content_block_start`(text)/`content_block_delta`(text_delta)×N/`content_block_stop`、以及一段 `tool_use` 的 start/delta/stop 序列），`monkeypatch` 掉 `self._client.messages.stream` 让其返回这个假的异步上下文管理器（`get_final_message` 返回一个带 `usage.input_tokens`/`usage.output_tokens` 的假对象），断言 `stream_chat` 产出的事件序列里文本、`TOOL_CALL`（参数正确解析）、`USAGE`（数值正确）、`DONE` 都按预期顺序出现；重跑一遍不带 `tool_use` 块的纯文本序列，确认无回归

## T12: providers/openai_provider.py — 异步化 + usage

**文件：** `src/sirius_agent/providers/openai_provider.py`
**依赖：** T10
**步骤：**
1. 构造函数改用 `openai.AsyncOpenAI(api_key=..., base_url=...)`
2. `stream_chat` 改为 `async def stream_chat(...) -> AsyncIterator[StreamEvent]`
3. `request_kwargs` 新增 `stream_options={"include_usage": True}`
4. `stream = await self._client.chat.completions.create(**request_kwargs)`；`for chunk in stream:` 改为 `async for chunk in stream:`
5. **修正空 choices 判断**：原来的 `if not chunk.choices: continue` 挪到检查 `chunk.usage` 之后——先 `if chunk.usage is not None: yield StreamEvent(type=StreamEventType.USAGE, usage=TokenUsage(input_tokens=chunk.usage.prompt_tokens, output_tokens=chunk.usage.completion_tokens))`，再 `if not chunk.choices: continue`，剩余文本/工具调用分片解析逻辑不变
6. 流结束后仍按原逻辑把 `tool_call_buffers` 里的每一项 `yield StreamEvent(TOOL_CALL, ...)`，最后 `yield StreamEvent(DONE)`

**验证：** 新建 `tests/test_openai_provider.py`：构造一个假的异步 chunk 序列（含普通文本 delta 的 chunk、分片的 `tool_calls` delta chunk、以及最后一个 `choices=[]` 但 `usage` 非空的 chunk），`monkeypatch` 掉 `self._client.chat.completions.create` 让其返回这个假异步迭代器，断言产出的事件序列里文本、`TOOL_CALL`、`USAGE`（数值正确映射 `prompt_tokens`→`input_tokens`、`completion_tokens`→`output_tokens`）、`DONE` 都按预期出现，且最后的 usage-only chunk 没有被空 `choices` 判断误跳过

## T13: agent.py（第一部分）— StreamCollector

**文件：** `src/sirius_agent/agent.py`
**依赖：** T10
**步骤：**
1. 定义 `StopReason` 枚举（`COMPLETED`/`MAX_ITERATIONS`/`USER_CANCELLED`/`UNKNOWN_TOOL`/`STREAM_ERROR`）
2. 定义 `TurnEventType` 枚举（`THINKING_DELTA`/`TEXT_DELTA`/`TOOL_STARTED`/`TOOL_FINISHED`/`USAGE`/`STOPPED`）
3. 定义 `@dataclass TurnEvent`（字段见 plan.md：`type`/`text`/`tool_name`/`tool_arguments`/`tool_result`/`usage`/`stop_reason`/`error_message`/`iteration`）
4. 实现 `StreamCollector` 类：`__init__` 初始化 `text=""`、`tool_calls=[]`、`usage=None`、`error_message=None`；`async def consume(self, stream, iteration) -> AsyncIterator[TurnEvent]` 按 plan.md 描述逐个消费 `StreamEvent` 并 yield 对应 `TurnEvent`，消费完毕后 `self.text`/`self.tool_calls`/`self.usage`/`self.error_message` 可读

**验证：** 新建 `tests/test_agent.py`，加入 `test_stream_collector_text_and_usage`（构造一个假的 `async def _fake_stream()` 依次产出 `TEXT_DELTA`×2、`USAGE`、`DONE`，`async for turn_event in collector.consume(_fake_stream(), iteration=1)` 收集所有产出的 `TurnEvent`，断言实时转发的文本增量正确、`collector.text` 是拼接后的完整文本、`collector.usage` 正确）和 `test_stream_collector_stops_on_error`（流中途产出 `ERROR` 事件，断言 `collector.error_message` 被设置且后续事件不再被消费）

## T14: agent.py（第二部分）— run_agent_loop 骨架（正常结束 / 流式错误 / 迭代上限）

**文件：** `src/sirius_agent/agent.py`
**依赖：** T13, T3（`ToolRegistry.execute` 异步签名）
**步骤：**
1. 定义模块级常量 `_MAX_ITERATIONS = 20`、`_MAX_CONSECUTIVE_UNKNOWN_TOOL_ROUNDS = 2`
2. 实现 `async def run_agent_loop(provider, tool_registry, session, user_text, cancel_event, tools_enabled=True) -> AsyncIterator[TurnEvent]`：
   - `session.add_user_message(user_text)`
   - `for iteration in range(1, _MAX_ITERATIONS + 1):` 循环体：用 `StreamCollector` 消费 `provider.stream_chat(session.get_messages(), tools=tool_registry.list_tools(only_safe=not tools_enabled))`，转发所有事件
   - `collector.error_message` 非空 → `yield TurnEvent(STOPPED, stop_reason=STREAM_ERROR, error_message=..., iteration=iteration); return`
   - `collector.tool_calls` 为空 → `session.add_assistant_message(collector.text)`（非空才写入）→ `yield TurnEvent(STOPPED, stop_reason=COMPLETED, iteration=iteration); return`
   - 有工具调用时：本任务先只做"全部当已知工具、不区分安全批次、逐个串行执行"的最简实现（`session.add_assistant_tool_call_message(...)` → for 循环逐个 `await tool_registry.execute(...)` 并 yield `TOOL_STARTED`/`TOOL_FINISHED`、写回历史），未知工具识别和安全分批留给 T15
   - 循环跑满 `_MAX_ITERATIONS` 轮仍未 return → `yield TurnEvent(STOPPED, stop_reason=MAX_ITERATIONS, iteration=_MAX_ITERATIONS)`

**验证：** 在 `tests/test_agent.py` 追加：`test_run_agent_loop_completes_without_tools`（假 `Provider.stream_chat` 直接产出文本+`DONE`，断言产出 `STOPPED(COMPLETED)` 且 `session` 里有对应的 assistant 消息）；`test_run_agent_loop_stream_error`（假 provider 产出 `ERROR`，断言 `STOPPED(STREAM_ERROR)`）；`test_run_agent_loop_max_iterations`（假 provider 每轮都产出同一个已知工具调用、永不结束，断言循环执行了恰好 20 轮后产出 `STOPPED(MAX_ITERATIONS)`）

## T15: agent.py（第三部分）— 未知工具识别 + 安全分批并发/串行

**文件：** `src/sirius_agent/agent.py`
**依赖：** T14
**步骤：**
1. 在处理工具调用的分支里，先遍历 `collector.tool_calls`：`tool_registry.has(tc.name)` 为假的立即产出 `ToolResult(ok=False, content=f"未知的工具：{tc.name}")` 对应的 `TOOL_STARTED`/`TOOL_FINISHED`、写回历史，并标记 `round_has_unknown = True`；为真的收进 `known_calls` 列表
2. 维护循环级变量 `consecutive_unknown_rounds`：本轮 `round_has_unknown` 为真则 `+= 1`，否则清零；达到 `_MAX_CONSECUTIVE_UNKNOWN_TOOL_ROUNDS` → `yield TurnEvent(STOPPED, stop_reason=UNKNOWN_TOOL, iteration=iteration); return`
3. 把 `known_calls` 按 `tool_registry.get(tc.name).safe` 分成 `safe_calls`/`unsafe_calls`
4. 实现两个内部协程辅助函数（模块级私有函数，不导出）：
   - `async def _run_safe_batch(tool_registry, calls, session, iteration) -> AsyncIterator[TurnEvent]`：先对每个 `tc` 各 yield 一次 `TOOL_STARTED`；`results = await asyncio.gather(*(tool_registry.execute(tc.name, tc.arguments) for tc in calls))`；再按顺序对每个 `(tc, result)` yield `TOOL_FINISHED` 并 `session.add_tool_result_message(...)`
   - `async def _run_unsafe_batch(tool_registry, calls, session, iteration) -> AsyncIterator[TurnEvent]`：for 循环逐个 `yield TOOL_STARTED` → `await tool_registry.execute(...)` → `yield TOOL_FINISHED` → 写回历史
5. `run_agent_loop` 依次 `async for ... in _run_safe_batch(...): yield ...` 和 `async for ... in _run_unsafe_batch(...): yield ...`

**验证：** 在 `tests/test_agent.py` 追加：`test_unknown_tool_two_consecutive_rounds_stops`（假 provider 连续两轮都请求一个不存在的工具名，断言第二轮后 `STOPPED(UNKNOWN_TOOL)` 且历史里有两条"未知的工具"错误结果）；`test_unknown_tool_single_occurrence_does_not_stop`（第一轮未知工具、第二轮改为已知工具且无更多工具调用，断言循环正常 `STOPPED(COMPLETED)` 而不是提前终止）；`test_safe_tools_run_concurrently`（注册两个假的 `safe=True` 工具，`execute` 内部 `await asyncio.sleep(0.2)` 模拟耗时，断言 `run_agent_loop` 处理这一轮两个并发调用的总耗时明显小于 `0.4` 秒、接近 `0.2` 秒）；`test_unsafe_tools_run_serially`（同样两个 `await asyncio.sleep(0.2)` 但 `safe=False`，断言总耗时接近 `0.4` 秒）

## T16: agent.py（第四部分）— 用户取消 + Plan Mode 过滤

**文件：** `src/sirius_agent/agent.py`
**依赖：** T15
**步骤：**
1. 在 `for iteration in range(...)` 循环体最开头，`if cancel_event.is_set(): yield TurnEvent(STOPPED, stop_reason=USER_CANCELLED, iteration=iteration); return`
2. 在工具调用批次执行完、进入下一轮迭代之前，再检查一次 `cancel_event.is_set()`（同样的取消逻辑，确保工具批次执行完成后也能及时响应取消，而不必等到下一轮 LLM 请求发出后才检查）
3. 确认 `tools_enabled=False` 时 `tool_registry.list_tools(only_safe=True)` 被正确传给 `provider.stream_chat` 的 `tools` 参数（T14 已经这样写，这里补测试即可，不需要改代码）

**验证：** 在 `tests/test_agent.py` 追加：`test_user_cancel_stops_before_next_iteration`（`cancel_event` 在第一轮工具执行完后被外部设置，断言第二轮不会发起新的 `stream_chat` 请求、产出 `STOPPED(USER_CANCELLED)`）；`test_plan_mode_only_exposes_safe_tools`（`tools_enabled=False`，断言假 `Provider.stream_chat` 被调用时收到的 `tools` 参数只包含 `safe=True` 的工具，即使 `tool_registry` 里也注册了 `safe=False` 的工具）

## T17: tui.py（第一部分）— 异步框架 + /plan /do

**文件：** `src/sirius_agent/tui.py`
**依赖：** T16
**步骤：**
1. `run_repl` 改为 `async def run_repl(provider: Provider, tool_registry: ToolRegistry, session: ConversationSession) -> None`
2. 用户输入改为 `text = await prompt_session.prompt_async("> ")`（`PromptSession` 本身不变，只是调用异步方法）
3. 新增 `plan_mode: bool = False` 局部变量；输入为 `/plan` → 设 `True` 并打印提示"[已进入计划模式，仅只读工具可用，输入 /do 切回全工具模式]"、`continue`；输入为 `/do` → 设 `False` 并打印提示"[已切回全工具模式]"、`continue`
4. 调用 `run_agent_loop(..., tools_enabled=not plan_mode)`（取消监听部分留给 T18，本任务先用一个"从不取消"的占位 `cancel_event = asyncio.Event()`）

**验证：** `uv run python -c "import sirius_agent.tui"` 无报错；写一段临时脚本用假的 `run_agent_loop`（只产出 `STOPPED(COMPLETED)`）和假的标准输入序列（`/plan` → 一句话 → `/do` → `/exit`）跑一遍 `run_repl`，确认不抛异常、`plan_mode` 状态切换逻辑符合预期（可以用 `unittest.mock.patch` 打桩 `PromptSession.prompt_async` 返回预设的输入序列）

## T18: tui.py（第二部分）— Esc/Ctrl+C 取消监听任务

**文件：** `src/sirius_agent/tui.py`
**依赖：** T17
**步骤：**
1. 实现 `async def _watch_cancel_keys(cancel_event: asyncio.Event) -> None`：用 `prompt_toolkit.input.create_input()` 打开输入源，`input_.raw_mode()` + `input_.attach(callback)` 监听按键，`callback` 里遍历 `input_.read_keys()`，命中 `Keys.Escape` 或 `Keys.ControlC` 时 `cancel_event.set()`；用一个 `while not cancel_event.is_set(): await asyncio.sleep(0.05)` 保持任务存活直到取消或外部 `cancel()`
2. 在 `run_repl` 处理非空文本输入的分支里：`cancel_event = asyncio.Event()`；`watcher_task = asyncio.create_task(_watch_cancel_keys(cancel_event))`；`try: async for turn_event in run_agent_loop(..., cancel_event, ...): 渲染(turn_event) finally: watcher_task.cancel()`（用 `contextlib.suppress(asyncio.CancelledError)` 包住 `await watcher_task` 避免取消异常冒泡）

**验证：** `uv run python -c "import sirius_agent.tui"` 无报错；本任务的实际按键响应行为难以用 pytest 可靠模拟终端原始输入，留到 checklist.md 阶段用 tmux 手动验证（对应 spec.md AC3）

## T19: tui.py（第三部分）— 新事件类型渲染

**文件：** `src/sirius_agent/tui.py`
**依赖：** T17
**步骤：**
1. 渲染分支增加 `TurnEventType.USAGE`：打印形如 `"[本轮用量：输入 {usage.input_tokens} / 输出 {usage.output_tokens} tokens]"`，`dim` 样式
2. 渲染分支把原来的 `ERROR`/`DONE` 替换成统一的 `TurnEventType.STOPPED`，按 `turn_event.stop_reason` 分支：
   - `COMPLETED` → 只换行，不额外打印
   - `MAX_ITERATIONS` → 打印"已达到最大迭代轮数（20），本次任务未必完成"
   - `USER_CANCELLED` → 打印"已取消"
   - `UNKNOWN_TOOL` → 打印"模型连续请求未知工具，已停止"
   - `STREAM_ERROR` → 用 `turn_event.error_message` 打印清晰错误（沿用原 `bold red` 样式）

**验证：** 写一段临时脚本，构造一组覆盖所有 `TurnEventType`（含五种 `stop_reason`）的 `TurnEvent` 列表，喂给渲染逻辑（可以把渲染部分抽成一个接受 `console` 和 `turn_event` 的小函数，方便直接调用验证），人工核对每种事件打印出的文案和样式符合上面的规则；真实终端效果留到 checklist.md 用 tmux 端到端确认

## T20: __main__.py — asyncio.run 驱动

**文件：** `src/sirius_agent/__main__.py`
**依赖：** T17
**步骤：**
1. `main()` 里原来直接调用 `run_repl(provider, tool_registry, session)` 的地方改为 `asyncio.run(run_repl(provider, tool_registry, session))`
2. 顶部新增 `import asyncio`
3. `_build_tool_registry` 与参数解析逻辑不变

**验证：** `uv run python -c "import sirius_agent.__main__"` 无报错；`uv run sirius-agent --config <一个可用的测试配置>` 能正常启动进入 REPL（手动 Ctrl+D 退出确认不抛异常）

## 执行顺序

```
T1（独立，工具链准备）

T2 ─┬─→ T3 ─────────────────────────────┐
    ├─→ T4 ─┐                            │
    ├─→ T5 ─┤                            │
    ├─→ T6 ─┼─→（T4-T9 各自往            │
    ├─→ T7 ─┤   tests/test_tools_*.py    │
    ├─→ T8 ─┤   追加用例）                │
    └─→ T9 ─┘                            │
                                          │
T2 ────→ T10 ─┬─→ T11                    │
              └─→ T12                    │
                                          │
T10 ───────────────────→ T13 ─→ T14 ←────┘（T14 依赖 T3 的异步 execute）
                                 │
                                 ▼
                                T15 ─→ T16 ─→ T17 ─┬─→ T18
                                                    └─→ T19
                                                          │
                                                          ▼
                                                         T20（还依赖 T4-T9 用于 _build_tool_registry 不变但要能正常 import）
```

简化说明：T1 独立先行 → T2 打底 → T3-T9（registry + 六个工具的异步化，除 T3→T4-T9 无直接依赖外可并行）→ T10（provider 基础类型）→ T11/T12（两个 Provider 具体实现，可并行）→ T13（StreamCollector，依赖 T10）→ T14（Loop 骨架，依赖 T13 和 T3）→ T15（未知工具+安全分批，依赖 T3 的 safe/has）→ T16（取消+Plan Mode）→ T17（tui 框架）→ T18/T19（tui 取消监听、渲染，可并行）→ T20（cli 收尾）。
