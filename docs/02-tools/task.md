# Sirius-Agent（工具系统）Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `tools/base.py` | Tool Protocol、ToolCall、ToolResult |
| 新建 | `tools/paths.py` | resolve_safe_path、PathOutsideWorkspaceError |
| 新建 | `tools/read_file.py` | ReadFileTool |
| 新建 | `tools/write_file.py` | WriteFileTool |
| 新建 | `tools/edit_file.py` | EditFileTool |
| 新建 | `tools/execute_command.py` | ExecuteCommandTool |
| 新建 | `tools/glob_files.py` | GlobFilesTool |
| 新建 | `tools/grep_content.py` | GrepContentTool |
| 新建 | `tools/schema.py` | to_anthropic_tool_schema / to_openai_tool_schema |
| 新建 | `tools/registry.py` | ToolRegistry |
| 修改 | `providers/base.py` | StreamEvent 加 TOOL_CALL；stream_chat 签名加 tools 参数 |
| 修改 | `providers/anthropic_provider.py` | 组装工具 schema、翻译带工具的 Message、解析 tool_use 流式块 |
| 修改 | `providers/openai_provider.py` | 组装工具 schema、翻译带工具的 Message、解析 tool_calls 流式分片 |
| 修改 | `session.py` | 新增 add_assistant_tool_call_message / add_tool_result_message |
| 新建 | `agent.py` | TurnEvent / TurnEventType / run_turn() |
| 修改 | `tui.py` | 改为消费 TurnEvent 渲染 |
| 修改 | `__main__.py` | 组装 ToolRegistry 并注册六个工具 |

## T1: tools/base.py — Tool 协议与基础数据类型

**文件：** `src/sirius_agent/tools/base.py`
**依赖：** 无
**步骤：**
1. 定义 `Tool(Protocol)`：属性 `name: str`、`description: str`、`parameters_schema: dict`；方法 `execute(self, arguments: dict) -> ToolResult`
2. 定义 `@dataclass ToolCall`：`id: str`、`name: str`、`arguments: dict`
3. 定义 `@dataclass ToolResult`：`ok: bool`、`content: str`

**验证：** `uv run python -c "from sirius_agent.tools.base import Tool, ToolCall, ToolResult"` 无报错

## T2: tools/paths.py — 工作目录路径安全校验

**文件：** `src/sirius_agent/tools/paths.py`
**依赖：** 无
**步骤：**
1. 定义 `PathOutsideWorkspaceError(Exception)`
2. 实现 `resolve_safe_path(workspace_root: Path, user_path: str) -> Path`：把 `user_path` 相对 `workspace_root` 解析为绝对路径并 `.resolve()`；用 `resolve_path.is_relative_to(workspace_root.resolve())` 判断是否越界，越界则抛 `PathOutsideWorkspaceError`
3. 实现一个内部小工具 `is_within_workspace(workspace_root: Path, candidate: Path) -> bool`（`glob_files`/`grep_content` 过滤搜索结果时复用，`resolve_safe_path` 内部也可以复用它）

**验证：** 写脚本测试三种输入：`"a.txt"`（合法）、`"../a.txt"`（越界，抛异常）、绝对路径但落在 workspace 外（越界，抛异常），行为符合预期

## T3: tools/read_file.py — ReadFileTool

**文件：** `src/sirius_agent/tools/read_file.py`
**依赖：** T1, T2
**步骤：**
1. `ReadFileTool.__init__(self, workspace_root: Path)`
2. 设置 `name="read_file"`、`description`（中文，说明读取工作目录内某文件全文）、`parameters_schema`（`path: string`，必填）
3. `execute`：调用 `resolve_safe_path`；捕获 `PathOutsideWorkspaceError` → `ToolResult(ok=False, "路径超出工作目录范围：...")`；文件不存在 → `ok=False`；是目录 → `ok=False`；否则 UTF-8 读取全文 → `ok=True`

**验证：** 单元测试覆盖：读取已存在文件成功返回正确内容；读取不存在文件返回 `ok=False`

## T4: tools/write_file.py — WriteFileTool

**文件：** `src/sirius_agent/tools/write_file.py`
**依赖：** T1, T2
**步骤：**
1. `WriteFileTool.__init__(self, workspace_root: Path)`
2. 设置 `name="write_file"`、`description`、`parameters_schema`（`path`、`content`，均必填）
3. `execute`：`resolve_safe_path` 校验；自动创建缺失的父目录（`mkdir(parents=True, exist_ok=True)`）；UTF-8 写入（覆盖已有内容）；返回 `ok=True, content=f"已写入 {字节数} 字节到 {path}"`

**验证：** 单元测试覆盖：写入不存在的新文件（含自动建父目录）；覆盖已存在文件内容

## T5: tools/edit_file.py — EditFileTool

**文件：** `src/sirius_agent/tools/edit_file.py`
**依赖：** T1, T2
**步骤：**
1. `EditFileTool.__init__(self, workspace_root: Path)`
2. 设置 `name="edit_file"`、`description`、`parameters_schema`（`path`、`old_text`、`new_text`，均必填）
3. `execute`：`resolve_safe_path` 校验；读取全文；`count = content.count(old_text)`；`count == 0` → `ok=False, "未找到匹配的文本"`；`count > 1` → `ok=False, f"匹配到 {count} 处，无法确定替换位置，请提供更长/更唯一的上下文"`；`count == 1` → `content.replace(old_text, new_text, 1)` 写回，返回 `ok=True`

**验证：** 单元测试覆盖：唯一匹配替换成功且文件内容正确；0 次匹配报错且文件未改动；多次匹配报错且文件未改动

## T6: tools/execute_command.py — ExecuteCommandTool

**文件：** `src/sirius_agent/tools/execute_command.py`
**依赖：** T1
**步骤：**
1. `ExecuteCommandTool.__init__(self, workspace_root: Path, timeout: float = 30.0)`
2. 设置 `name="execute_command"`、`description`、`parameters_schema`（`command`，必填）
3. `execute`：`subprocess.run(command, shell=True, cwd=workspace_root, capture_output=True, text=True, timeout=self._timeout)`；捕获 `subprocess.TimeoutExpired` → `ok=False, f"命令执行超时（{timeout}秒）"`；正常返回时拼装 `content` 包含 stdout/stderr/exit code；`returncode != 0` → `ok=False`（但仍附带 stdout/stderr）；`returncode == 0` → `ok=True`

**验证：** 单元测试覆盖：成功命令返回 stdout 与 `ok=True`；非零退出码命令返回 `ok=False` 且带错误上下文；用一个必然超时的命令（或 mock `subprocess.run` 抛 `TimeoutExpired`）验证超时分支

## T7: tools/glob_files.py — GlobFilesTool

**文件：** `src/sirius_agent/tools/glob_files.py`
**依赖：** T1, T2
**步骤：**
1. `GlobFilesTool.__init__(self, workspace_root: Path)`
2. 设置 `name="glob_files"`、`description`、`parameters_schema`（`pattern`，必填，如 `"**/*.py"`）
3. `execute`：`workspace_root.glob(pattern)`；用 T2 的 `is_within_workspace` 过滤结果；跳过路径中含 `.git`/`__pycache__`/`.venv`/`node_modules` 的条目；按相对路径字符串排序后拼成多行文本返回；无匹配 → `ok=True, content="未找到匹配文件"`

**验证：** 单元测试覆盖：在临时目录构造几个文件（含被忽略目录下的文件），验证返回列表只包含预期文件且顺序稳定

## T8: tools/grep_content.py — GrepContentTool

**文件：** `src/sirius_agent/tools/grep_content.py`
**依赖：** T1, T2
**步骤：**
1. `GrepContentTool.__init__(self, workspace_root: Path)`
2. 设置 `name="grep_content"`、`description`、`parameters_schema`（`pattern` 必填正则，`file_glob` 可选默认 `"*"`）
3. `execute`：遍历 `workspace_root.rglob(file_glob)`（同样过滤忽略目录），对能以 UTF-8 解码的文件逐行 `re.search(pattern, line)`，解码失败的文件直接跳过（视为二进制）；命中行汇总为 `相对路径:行号: 内容` 多行文本；无命中 → `ok=True, content="未找到匹配"`

**验证：** 单元测试覆盖：在临时目录构造含关键字的文件，验证命中行的文件名/行号/内容都正确；构造一个二进制文件确认不报错、被跳过

## T9: tools/schema.py — 工具描述转换

**文件：** `src/sirius_agent/tools/schema.py`
**依赖：** T1
**步骤：**
1. 实现 `to_anthropic_tool_schema(tools: list[Tool]) -> list[dict]`：每个工具转成 `{"name":..., "description":..., "input_schema": tool.parameters_schema}`
2. 实现 `to_openai_tool_schema(tools: list[Tool]) -> list[dict]`：每个工具转成 `{"type": "function", "function": {"name":..., "description":..., "parameters": tool.parameters_schema}}`

**验证：** 单元测试用几个 T3-T8 中的真实工具实例（或简单 stub）调用两个转换函数，断言输出结构和字段值符合各自协议要求的形状

## T10: tools/registry.py — ToolRegistry

**文件：** `src/sirius_agent/tools/registry.py`
**依赖：** T1
**步骤：**
1. `ToolRegistry.__init__`：内部维护 `dict[str, Tool]`
2. `register(self, tool: Tool) -> None`
3. `get(self, name: str) -> Tool`：找不到抛 `ToolError`（在本文件内定义这个异常类）
4. `list_tools(self) -> list[Tool]`
5. `execute(self, name: str, arguments: dict) -> ToolResult`：`get(name)` 后调用 `tool.execute(arguments)`；用 `try/except Exception` 包住，未预期异常转成 `ToolResult(ok=False, f"工具执行出现意外错误：{e}")`

**验证：** 单元测试覆盖：注册后能查到；执行已注册工具成功；执行不存在的工具名抛 `ToolError`；用一个内部会抛异常的假工具验证 registry 兜底捕获、不向上抛出

## T11: providers/base.py — 扩展支持工具调用

**文件：** `src/sirius_agent/providers/base.py`
**依赖：** T1
**步骤：**
1. `StreamEventType` 新增 `TOOL_CALL = "tool_call"`
2. `StreamEvent` 新增字段 `tool_call: Optional[ToolCall] = None`（从 `sirius_agent.tools.base` 导入 `ToolCall`）
3. `Provider.stream_chat` 签名改为 `stream_chat(self, messages: list[Message], tools: list[Tool] | None = None) -> Iterator[StreamEvent]`

**验证：** `uv run python -c "import sirius_agent.providers.base"` 无报错；重跑第一期 provider 相关的 mock 测试脚本确认无回归（`tools` 参数默认 `None` 时行为不变）

## T12: providers/anthropic_provider.py — 支持工具调用

**文件：** `src/sirius_agent/providers/anthropic_provider.py`
**依赖：** T9, T11
**步骤：**
1. `stream_chat` 增加 `tools` 参数；非空时 `request_kwargs["tools"] = to_anthropic_tool_schema(tools)`
2. 把 `messages` 翻译成 Anthropic 请求体：`role="assistant"` 且有 `tool_calls` 时，组装 `content` 为多个 `{"type":"tool_use","id":tc.id,"name":tc.name,"input":tc.arguments}`（连同已有文字块，如果 `content` 非空）；`role="tool"` 的消息翻译成 `role="user"`、`content=[{"type":"tool_result","tool_use_id":m.tool_call_id,"content":m.content}]`
3. 处理流式响应：用 `dict[index, buffer]` 按内容块 index 累积；`content_block_start` 里 `type=="tool_use"` 时记录该 index 的 `id`/`name`；`content_block_delta` 里 `delta.type=="input_json_delta"` 时把 `delta.partial_json` 累加到对应 index 的字符串缓冲；`content_block_stop` 时若该 index 是 tool_use，`json.loads` 拼好的字符串得到 `arguments`，`yield StreamEvent(TOOL_CALL, tool_call=ToolCall(id, name, arguments))`；`type=="text"` 的块继续走原有 `text_delta` 逻辑

**验证：** 用 mock 的流式事件序列（模拟一段文字 + 一个 tool_use 块的 start/delta×N/stop）跑单元测试，断言产出的事件序列里出现正确的 `TOOL_CALL`（`arguments` 正确解析）且文字仍正常输出；重跑第一期的 mock 测试确认纯文本对话无回归

## T13: providers/openai_provider.py — 支持工具调用

**文件：** `src/sirius_agent/providers/openai_provider.py`
**依赖：** T9, T11
**步骤：**
1. `stream_chat` 增加 `tools` 参数；非空时 `tools=to_openai_tool_schema(tools)` 传给 `chat.completions.create`
2. 把 `messages` 翻译成 OpenAI 请求体：`role="assistant"` 且有 `tool_calls` 时，组装 `tool_calls=[{"id":tc.id,"type":"function","function":{"name":tc.name,"arguments":json.dumps(tc.arguments)}} for tc in ...]`；`role="tool"` 消息翻译成 `{"role":"tool","tool_call_id":m.tool_call_id,"content":m.content}`
3. 处理流式响应：用 `dict[index, {"id":..., "name":..., "arguments_buffer":...}]` 按 `chunk.choices[0].delta.tool_calls` 里每项的 `index` 累积（首个分片取 `id`/`function.name`，后续分片把 `function.arguments` 追加到 buffer）；流结束时对每个累积项 `json.loads(arguments_buffer)`，逐个 `yield StreamEvent(TOOL_CALL, tool_call=ToolCall(id, name, arguments))`

**验证：** 用 mock 的流式 chunk 序列（模拟分片的 `tool_calls` delta）跑单元测试，断言正确拼接出 `TOOL_CALL` 事件；重跑第一期的 mock 测试确认纯文本对话无回归

## T14: session.py — 扩展工具相关历史写入

**文件：** `src/sirius_agent/session.py`
**依赖：** T1
**步骤：**
1. 新增 `add_assistant_tool_call_message(self, content: str, tool_calls: list[ToolCall]) -> None`：追加 `Message(role="assistant", content=content, tool_calls=tool_calls)`
2. 新增 `add_tool_result_message(self, tool_call_id: str, content: str) -> None`：追加 `Message(role="tool", content=content, tool_call_id=tool_call_id)`

**验证：** 单元测试覆盖：调用两个新方法后 `get_messages()` 返回的列表里对应条目的 `role`/`content`/`tool_calls`/`tool_call_id` 字段均符合预期，且顺序正确

## T15: agent.py — 一轮对话编排

**文件：** `src/sirius_agent/agent.py`
**依赖：** T10, T11, T14
**步骤：**
1. 定义 `TurnEventType`（`THINKING_DELTA`/`TEXT_DELTA`/`TOOL_STARTED`/`TOOL_FINISHED`/`ERROR`/`DONE`）与 `@dataclass TurnEvent`
2. 实现 `run_turn(provider, tool_registry, session, user_text) -> Iterator[TurnEvent]`：按 plan.md「模块交互」一节描述的顺序——`add_user_message` → 首次 `stream_chat(messages, tools=tool_registry.list_tools())` → 转发 delta/收集 `TOOL_CALL` → 无工具调用则存历史后 `DONE` 结束；有工具调用则存历史、逐个 `yield TOOL_STARTED` → `registry.execute` → `yield TOOL_FINISHED` → 存结果到历史 → 追加一次 `stream_chat(messages, tools=None)` 拿最终回复、转发 delta、存历史、`yield DONE`
3. `ERROR` 事件出现时不写入历史、直接 `return`（不再继续后续步骤）

**验证：** 用假 `Provider`（第一次 `stream_chat` 产出一段文字 + 一个 `TOOL_CALL` + `DONE`，第二次产出最终文字 + `DONE`）和假 `ToolRegistry` 跑单元测试，断言：事件顺序为 `TEXT_DELTA(可选)→TOOL_STARTED→TOOL_FINISHED→TEXT_DELTA→DONE`；断言第二次 `stream_chat` 调用时 `tools` 参数为 `None`；断言 `session.get_messages()` 最终包含完整的 user/assistant(tool_calls)/tool/assistant(最终文字) 四条历史

## T16: tui.py — 改为消费 TurnEvent

**文件：** `src/sirius_agent/tui.py`
**依赖：** T15
**步骤：**
1. `run_repl` 签名改为 `run_repl(provider: Provider, tool_registry: ToolRegistry, session: ConversationSession) -> None`
2. 主循环里把 `provider.stream_chat(...)` 换成 `agent.run_turn(provider, tool_registry, session, text)`
3. 按 `TurnEventType` 渲染：`THINKING_DELTA`/`TEXT_DELTA` 复用第一期样式；`TOOL_STARTED` 打印如 `→ 执行 read_file(path='a.txt')`（专属样式，如 `cyan`）；`TOOL_FINISHED` 按 `tool_result.ok` 用不同样式打印结果摘要（失败用 `bold red`，成功用普通样式）；`ERROR`/`DONE` 沿用原逻辑

**验证：** 用假的 `agent.run_turn`（`patch` 成一个产出固定 `TurnEvent` 序列的生成器）跑单元测试，检查各类型事件对应打印出的文本内容和调用的样式参数符合预期

## T17: __main__.py — 组装 ToolRegistry

**文件：** `src/sirius_agent/__main__.py`
**依赖：** T3, T4, T5, T6, T7, T8, T10
**步骤：**
1. `main()` 中在 `create_provider` 之后新建 `ToolRegistry()`，依次 `register` 六个工具实例（均用 `Path.cwd()` 作为 `workspace_root`）
2. 把 `tool_registry` 传给 `tui.run_repl(provider, tool_registry, session)`

**验证：** `uv run python -c "import sirius_agent.__main__"` 无报错；重跑第一期 CLI wiring 的 mock 测试（`patch.object` 打桩 `run_repl`）确认能正常组装并调用，且新增的 `tool_registry` 参数被正确传入

## 执行顺序

```
T1 ─┬─→ T3 ─┐
    │      T4 ─┤
T2 ─┴─→ T5 ─┼─→ T9 ─┬─→ T12 ─┐
    ├─→ T6 ─┤        ├─→ T13 ─┤
    ├─→ T7 ─┤        │        │
    └─→ T8 ─┘        │        │
                      │        │
T1 ────→ T11 ─────────┘        │
T1 ────→ T14 ───────────────────┼─→ T15 → T16 → T17
T1 ────→ T10 ───────────────────┘        （T17 还依赖 T3-T8）
```

简化说明：T1/T2 打底 → T3-T8（六个工具，可并行）+ T9（schema）+ T10（registry）+ T11（provider 基础扩展）+ T14（session 扩展）都只依赖 T1（部分依赖 T2）可并行推进 → T12/T13（两个 Provider 具体实现）依赖 T9+T11 → T15（agent 编排）依赖 T10+T11+T14 → T16（tui）依赖 T15 → T17（cli 组装）收尾，依赖 T3-T8 和 T10。
