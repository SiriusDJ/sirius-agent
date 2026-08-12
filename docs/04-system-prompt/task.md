# Sirius-Agent（System Prompt）Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/sirius_agent/prompt/__init__.py` | 空文件，声明包 |
| 新建 | `src/sirius_agent/prompt/environment.py` | `EnvironmentContext`、`gather_environment()` |
| 新建 | `src/sirius_agent/prompt/sections.py` | 七个固定模块 + 环境信息模块的文本函数 |
| 新建 | `src/sirius_agent/prompt/builder.py` | `SystemPromptBlock`、`build_system_prompt()` |
| 新建 | `src/sirius_agent/prompt/reminders.py` | `plan_mode_reminder()` |
| 修改 | `src/sirius_agent/providers/base.py` | `Message.role` 扩展、`TokenUsage` 扩展、`Provider.stream_chat` 签名扩展、`config` 属性 |
| 修改 | `src/sirius_agent/providers/anthropic_provider.py` | system/tools 缓存标记、role="system" 降级翻译、usage 缓存字段解析 |
| 修改 | `src/sirius_agent/providers/openai_provider.py` | system_prompt 前置为 system 消息 |
| 修改 | `src/sirius_agent/session.py` | `add_system_message` / `enter_plan_mode` / `next_plan_mode_round` |
| 修改 | `src/sirius_agent/tools/execute_command.py` | description 强化"优先专用工具" |
| 修改 | `src/sirius_agent/tools/edit_file.py` | description 强化"编辑前必读" |
| 修改 | `src/sirius_agent/agent.py` | `run_agent_loop` 接入 `gather_environment` + `build_system_prompt` |
| 修改 | `src/sirius_agent/tui.py` | 接入 workspace_root 透传、Plan Mode 生命周期接入 reminders |
| 修改 | `src/sirius_agent/__main__.py` | 把 workspace_root 传给 `run_repl` |
| 新建/修改 | `tests/test_prompt_environment.py` | T1 验证 |
| 新建/修改 | `tests/test_prompt_sections.py` | T2 验证 |
| 新建/修改 | `tests/test_prompt_builder.py` | T3 验证 |
| 新建/修改 | `tests/test_prompt_reminders.py` | T4 验证 |
| 修改 | `tests/test_anthropic_provider.py` | T6/T7 验证 |
| 修改 | `tests/test_openai_provider.py` | T8 验证 |
| 新建/修改 | `tests/test_session.py` | T9 验证 |
| 修改 | `tests/test_agent.py` | T11 验证 |
| 修改 | `tests/test_tui.py` | T12 验证 |

## T1: 创建 prompt 包骨架 + 环境信息采集

**文件：** `src/sirius_agent/prompt/__init__.py`（新建，留空）、`src/sirius_agent/prompt/environment.py`（新建）
**依赖：** 无
**步骤：**
1. 新建 `src/sirius_agent/prompt/__init__.py`，内容为空
2. 在 `environment.py` 定义 `EnvironmentContext` dataclass：`cwd: str`、`platform: str`、`date: str`、`provider_name: str`、`model_name: str`、`git_branch: Optional[str]`、`git_dirty: Optional[bool]`
3. 实现 `async def gather_environment(workspace_root: Path, provider_name: str, model_name: str) -> EnvironmentContext`：`cwd` 用 `str(workspace_root)`；`platform` 用 `sys.platform`；`date` 用当天日期字符串（如 `date.today().isoformat()`）；用 `asyncio.create_subprocess_exec("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=workspace_root, stdout=PIPE, stderr=PIPE)` 拿分支名，返回码非 0 时 `git_branch=None`
4. 分支采集成功后再跑 `git status --porcelain`，非空输出即 `git_dirty=True`，空输出 `git_dirty=False`；分支采集失败时 `git_dirty` 也置 `None`

**验证：** 新建 `tests/test_prompt_environment.py`：在 `sirius-agent` 仓库自身目录下调用 `gather_environment`，断言 `git_branch is not None`；用 `tmp_path`（非 git 目录）调用，断言 `git_branch is None and git_dirty is None`。`uv run pytest tests/test_prompt_environment.py` 通过

## T2: 创建七个固定模块 + 环境信息模块文本函数

**文件：** `src/sirius_agent/prompt/sections.py`（新建）
**依赖：** T1
**步骤：**
1. 定义 `identity_section() -> str`、`system_constraints_section() -> str`、`task_mode_section() -> str`、`action_execution_section() -> str`、`tool_usage_section() -> str`、`tone_style_section() -> str`、`text_output_section() -> str`，各自返回一段非空说明文字
2. `task_mode_section()` 的内容里说明"运行中可能收到系统级补充指令（如计划模式提醒），需按其中的限制行事"
3. `tool_usage_section()` 的内容里写入 F6 的两条规则："优先使用专用工具（read_file/grep_content/glob_files），不要用 execute_command 读文件、搜索内容或查找文件"和"编辑文件前必须先用 read_file 读过目标文件"
4. 定义 `environment_section(env: EnvironmentContext) -> str`，把 `env` 的各字段格式化进一段文字，`git_branch is None` 时显式写"不在 git 仓库中"

**验证：** 新建 `tests/test_prompt_sections.py`：对七个固定函数逐一断言返回非空字符串；断言 `tool_usage_section()` 同时包含 `"read_file"` 和 `"专用工具"`；构造一个 `EnvironmentContext(git_branch=None, ...)`，断言 `environment_section` 返回文本包含"不在 git 仓库中"。`uv run pytest tests/test_prompt_sections.py` 通过

## T3: 创建 SystemPromptBlock + build_system_prompt

**文件：** `src/sirius_agent/prompt/builder.py`（新建）
**依赖：** T2
**步骤：**
1. 定义 `@dataclass class SystemPromptBlock: text: str; cacheable: bool = False`
2. 在模块内定义 `_FIXED_SECTIONS = [identity_section, system_constraints_section, task_mode_section, action_execution_section, tool_usage_section, tone_style_section, text_output_section]`
3. 实现 `build_system_prompt(env: EnvironmentContext, optional_sections: Optional[list[str]] = None) -> list[SystemPromptBlock]`：把 `_FIXED_SECTIONS` 逐个调用后用两个换行拼接成 `stable_text`；若 `optional_sections` 非空，追加到 `stable_text` 后面；返回 `[SystemPromptBlock(stable_text, cacheable=True), SystemPromptBlock(environment_section(env), cacheable=False)]`

**验证：** 新建 `tests/test_prompt_builder.py`：构造一个 `EnvironmentContext`，调用 `build_system_prompt(env)`，断言返回长度为 2；`result[0].cacheable is True` 且文本包含 `identity_section()` 和 `tool_usage_section()` 的内容；`result[1].cacheable is False` 且文本等于 `environment_section(env)`。`uv run pytest tests/test_prompt_builder.py` 通过

## T4: 创建 Plan Mode 提醒轮次判断

**文件：** `src/sirius_agent/prompt/reminders.py`（新建）
**依赖：** 无
**步骤：**
1. 定义模块常量：完整版提醒文本（说明当前处于计划模式、只有只读工具可用、需先给出计划、用户会输入 /do 切回）、精简版提醒文本（如 `"[仍处于计划模式]"`）、重复间隔 `_REPEAT_INTERVAL = 3`
2. 实现 `plan_mode_reminder(round_number: int) -> Optional[str]`：`round_number == 1` 返回完整版；`round_number > 1 and (round_number - 1) % _REPEAT_INTERVAL == 0` 返回精简版；其余返回 `None`

**验证：** 新建 `tests/test_prompt_reminders.py`：断言 `plan_mode_reminder(1)` 非 `None` 且包含"计划模式"；断言 `plan_mode_reminder(2)` 和 `plan_mode_reminder(3)` 为 `None`；断言 `plan_mode_reminder(4)` 和 `plan_mode_reminder(7)` 非 `None`；断言 `plan_mode_reminder(5)` 为 `None`。`uv run pytest tests/test_prompt_reminders.py` 通过

## T5: 扩展 providers/base.py 的数据结构

**文件：** `src/sirius_agent/providers/base.py`
**依赖：** 无
**步骤：**
1. 更新 `Message` 的 `role` 字段注释，说明取值扩展为 `"user" | "assistant" | "tool" | "system"`（`role` 本身是 `str` 类型，无需改类型标注，只改文档字符串）
2. 在 `TokenUsage` dataclass 新增两个字段：`cache_creation_input_tokens: Optional[int] = None`、`cache_read_input_tokens: Optional[int] = None`
3. `Provider` Protocol 的 `stream_chat` 方法签名新增参数 `system_prompt: Optional[list["SystemPromptBlock"]] = None`（用字符串前向引用避免循环导入，或直接从 `sirius_agent.prompt.builder` 导入）
4. `Provider` Protocol 新增只读属性声明：`config: ProviderConfig`

**验证：** `uv run python -c "from sirius_agent.providers.base import Message, TokenUsage; Message(role='system', content='x'); TokenUsage(input_tokens=1, output_tokens=1)"` 无报错；`uv run pytest` 全量跑一遍确认未破坏现有测试

## T6: AnthropicProvider 接入 system_prompt 与工具缓存标记

**文件：** `src/sirius_agent/providers/anthropic_provider.py`
**依赖：** T3、T5
**步骤：**
1. 新增 `config` 属性，返回 `self._config`
2. `stream_chat` 签名新增 `system_prompt: Optional[list[SystemPromptBlock]] = None` 参数
3. `system_prompt` 非空时，构造 `request_kwargs["system"]`：对每个 block 生成 `{"type": "text", "text": block.text}`，`block.cacheable` 为真时额外加 `"cache_control": {"type": "ephemeral", "ttl": "5m"}`
4. `tools` 非空时，`to_anthropic_tool_schema(tools)` 的结果里，给最后一项加 `"cache_control": {"type": "ephemeral", "ttl": "5m"}`

**验证：** 在 `tests/test_anthropic_provider.py` 新增用例：调用 `stream_chat` 时传入两块 `SystemPromptBlock`（一块 `cacheable=True` 一块 `False`）和一个 tools 列表，通过给 `_make_provider` 增加记录 `request_kwargs` 的桩函数，断言 `system` 字段第一项带 `cache_control`、第二项不带；`tools` 最后一项带 `cache_control`。`uv run pytest tests/test_anthropic_provider.py` 通过

## T7: AnthropicProvider 的 role="system" 降级与 usage 缓存字段解析

**文件：** `src/sirius_agent/providers/anthropic_provider.py`
**依赖：** T5
**步骤：**
1. `_to_anthropic_messages` 新增分支：`m.role == "system"` 时，追加 `{"role": "user", "content": f"<system-reminder>\n{m.content}\n</system-reminder>"}`（放在 `m.role == "tool"` 分支之后、`assistant` 分支之前，保证不被其他分支提前匹配）
2. `stream_chat` 里解析 `final_message.usage` 时，读取 `cache_creation_input_tokens` / `cache_read_input_tokens`（用 `getattr(..., None)` 兜底字段不存在的情况），填进 `TokenUsage`

**验证：** 在 `tests/test_anthropic_provider.py` 新增用例：`_to_anthropic_messages([Message(role="system", content="reminder")])` 返回 `[{"role": "user", "content": "<system-reminder>\nreminder\n</system-reminder>"}]`；`_FINAL_MESSAGE` 的 `usage` 加上 `cache_creation_input_tokens=100, cache_read_input_tokens=200` 后，产出的 `USAGE` 事件里 `usage.cache_creation_input_tokens == 100` 且 `usage.cache_read_input_tokens == 200`。`uv run pytest tests/test_anthropic_provider.py` 通过

## T8: OpenAIProvider 接入 system_prompt

**文件：** `src/sirius_agent/providers/openai_provider.py`
**依赖：** T3、T5
**步骤：**
1. 新增 `config` 属性，返回 `self._config`
2. `stream_chat` 签名新增 `system_prompt: Optional[list[SystemPromptBlock]] = None` 参数
3. `system_prompt` 非空时，把各 block 的 `text` 用两个换行拼接成一段，构造 `{"role": "system", "content": combined_text}`，插到 `_to_openai_messages(messages)` 结果列表最前面

**验证：** 在 `tests/test_openai_provider.py` 新增用例：传入两块 `SystemPromptBlock`，断言实际请求 `messages[0] == {"role": "system", "content": "<两块拼接后的文本>"}`；另断言 `_to_openai_messages([Message(role="system", content="reminder")])` 直接透传为 `{"role": "system", "content": "reminder"}`（验证现有 `else` 分支已覆盖，不需要新增代码）。`uv run pytest tests/test_openai_provider.py` 通过

## T9: session.py 新增 Plan Mode 状态与 system 消息写入

**文件：** `src/sirius_agent/session.py`
**依赖：** T5
**步骤：**
1. `__init__` 新增 `self._plan_mode_round: int = 0`
2. 新增 `def add_system_message(self, content: str) -> None`，追加 `Message(role="system", content=content)`
3. 新增 `def enter_plan_mode(self) -> None`，把 `self._plan_mode_round` 置 0
4. 新增 `def next_plan_mode_round(self) -> int`，`self._plan_mode_round += 1` 后返回新值

**验证：** 新建 `tests/test_session.py`：`add_system_message("x")` 后 `get_messages()[-1].role == "system"`；`enter_plan_mode()` 后连续调用 `next_plan_mode_round()` 三次得到 `1, 2, 3`；再次 `enter_plan_mode()` 后 `next_plan_mode_round()` 重新从 `1` 开始。`uv run pytest tests/test_session.py` 通过

## T10: 工具描述文案强化

**文件：** `src/sirius_agent/tools/execute_command.py`、`src/sirius_agent/tools/edit_file.py`
**依赖：** 无
**步骤：**
1. `ExecuteCommandTool.description` 追加"不要用它来读文件、搜索内容或查找文件，这些场景请优先使用 read_file / grep_content / glob_files"
2. `EditFileTool.description` 追加"编辑前必须先用 read_file 读过目标文件，不得在未读过内容的情况下直接编辑"

**验证：** `uv run python -c "from sirius_agent.tools.execute_command import ExecuteCommandTool as E; from sirius_agent.tools.edit_file import EditFileTool as F; assert 'read_file' in E.description; assert 'read_file' in F.description"` 无报错；`uv run pytest tests/test_execute_command.py tests/test_tools_file_ops.py` 确认原有行为测试不受影响

## T11: agent.py 接入系统提示构建

**文件：** `src/sirius_agent/agent.py`
**依赖：** T1、T3、T6、T8
**步骤：**
1. `run_agent_loop` 签名新增 `workspace_root: Path` 参数
2. 每轮迭代（`for iteration in range(...)` 循环内、构造 `collector` 之前）调用 `env = await gather_environment(workspace_root, provider.config.name, provider.config.model)` 和 `system_prompt = build_system_prompt(env)`
3. 把 `system_prompt` 传给 `provider.stream_chat(session.get_messages(), tools=active_tools, system_prompt=system_prompt)`

**验证：** 在 `tests/test_agent.py` 新增一个记录调用参数的假 provider（`stream_chat` 记录每次收到的 `system_prompt` 是否非 `None`），调用 `run_agent_loop(..., workspace_root=tmp_path)` 跑一轮，断言记录到的 `system_prompt` 非空；调整原有测试用例的 `run_agent_loop` 调用点补上 `workspace_root=tmp_path` 参数。`uv run pytest tests/test_agent.py` 通过

## T12: tui.py 接入 Plan Mode 生命周期

**文件：** `src/sirius_agent/tui.py`
**依赖：** T4、T9、T11
**步骤：**
1. `run_repl` 签名新增 `workspace_root: Path` 参数，调用 `run_agent_loop` 时传入
2. 处理 `/plan` 命令的分支里调用 `session.enter_plan_mode()`
3. 在正常对话轮次（非命令、`plan_mode` 为真时），调用 `run_agent_loop` 之前：`round_number = session.next_plan_mode_round()`；`reminder = plan_mode_reminder(round_number)`；`reminder` 非 `None` 时调用 `session.add_system_message(reminder)`
4. `/do` 分支不需要新增逻辑（不再调用上面两步即可）

**验证：** 调整 `tests/test_tui.py` 里 `_fake_run_agent_loop` 的签名加上 `workspace_root` 参数，`run_repl` 调用点加上 `workspace_root=object()`（或 `Path(".")`），确认 `test_plan_do_toggle_controls_tools_enabled` 仍通过；新增用例：用一个真实的 `ConversationSession`（而非 `object()`）跑 `/plan` → 若干次输入 → `/do`，断言 `session.get_messages()` 里在第 1 轮出现完整版提醒、第 4 轮出现精简版提醒、`/do` 后不再新增 `role="system"` 消息。`uv run pytest tests/test_tui.py` 通过

## T13: __main__.py 透传 workspace_root

**文件：** `src/sirius_agent/__main__.py`
**依赖：** T12
**步骤：**
1. `main()` 里调用 `run_repl` 的位置，补上 `workspace_root=Path.cwd()`（复用已有的 `_build_tool_registry(Path.cwd())` 那个 `Path.cwd()`，可以先赋值给局部变量两处共用）

**验证：** `uv run python -m sirius-agent --help` 正常打印帮助、无报错；`uv run pytest` 全量跑一遍确认无回归

## T14: 全量回归

**文件：** 无新改动，仅验证
**依赖：** T1-T13 全部完成
**步骤：**
1. 跑一遍完整测试套件
2. 用 tmux 启动 Sirius-Agent，人工过一遍典型场景（真实系统提示是否发出、Plan Mode 提醒是否按预期出现、Anthropic 缓存字段是否非零）

**验证：** `uv run pytest` 全绿；tmux 端到端跑通至少一次多轮 Plan Mode 会话和一次 Anthropic 连续两轮对话（用于观察缓存命中）

## 执行顺序

```
T1 → T2 → T3 ─┬─→ T6 ─┐
              │       │
T5 ───────────┼─→ T7  │
              │       │
              ├─→ T8 ─┼─→ T11 → T12 → T13 → T14
              │       │          ↑
              └─→ T9 ─┘          │
                                 │
T4 ──────────────────────────────┘

T10（可随时并行，无强依赖）──────────────→ T14
```

---

## 修订记录：spec 替换为「系统提示工程化」后的增量任务（D1-D8）

T1-T14 完成后，spec.md 被整体替换为更严格的版本，其中几处是**语义级**修订，不只是措辞调整。以下任务在 T1-T14 已实现的基础上做增量修改，不是从头重做。

| 操作 | 文件 | 变化点 |
|------|------|--------|
| 修改 | `src/sirius_agent/prompt/environment.py` | `EnvironmentContext`/`gather_environment()` 去掉 `provider_name`，新增 `app_version`（`importlib.metadata.version("sirius-agent")`） |
| 修改 | `src/sirius_agent/prompt/sections.py` | `environment_section()` 呈现字段同步调整 |
| 修改 | `src/sirius_agent/prompt/reminders.py` | `plan_mode_reminder()` 返回类型从 `Optional[str]` 改为 `str`；频率规则改为"首轮与间隔轮完整、其余轮精简"（不再有"不注入"） |
| 修改 | `src/sirius_agent/providers/base.py` | `TokenUsage` 两个缓存字段从 `Optional[int]=None` 改为 `int=0` |
| 修改 | `src/sirius_agent/providers/anthropic_provider.py` | usage 缓存字段解析兜底值从 `None` 改为 `0` |
| 修改 | `src/sirius_agent/providers/openai_provider.py` | 新增对 `usage.prompt_tokens_details.cached_tokens`/`cache_write_tokens` 的解析（原 spec 明确排除，新 spec 明确要求） |
| 修改 | `src/sirius_agent/session.py` | 删除 `add_system_message`（不再提供任何持久化 system 消息的接口） |
| 修改 | `src/sirius_agent/agent.py` | Plan Mode 提醒的构造与轮次推进从 tui.py 移入 `run_agent_loop` 内部，按 Agent Loop 迭代计数；提醒只临时追加到发给 provider 的消息列表，不落进 session 历史 |
| 修改 | `src/sirius_agent/tui.py` | 移除提醒构造/注入逻辑（已内聚进 agent.py）；`_render` 的用量展示不再判断 `is not None`，缓存字段永远显示 |
| 新增 | `pyproject.toml` | 新增 `ruff` dev 依赖与 `[tool.ruff]` 配置 |

### D1: 环境信息字段调整
**依赖：** 无
**步骤：** `gather_environment(workspace_root, model_name)` 去掉 `provider_name` 形参；`EnvironmentContext` 新增 `app_version: str`；`environment_section()` 呈现"应用版本"而非"当前供应商"
**验证：** `tests/test_prompt_environment.py`、`tests/test_prompt_sections.py`、`tests/test_prompt_builder.py` 更新后全部通过

### D2: Plan Mode 提醒频率规则修订
**依赖：** 无
**步骤：** `plan_mode_reminder(round_number) -> str`：`round_number == 1 or (round_number - 1) % 3 == 0` 返回完整版，否则返回精简版——永不返回 `None`
**验证：** `tests/test_prompt_reminders.py`：round 1/4/7 完整版，2/3/5 精简版，1-10 每轮都非空

### D3: TokenUsage 缓存字段默认值改为 0
**依赖：** 无
**步骤：** `cache_creation_input_tokens: int = 0`、`cache_read_input_tokens: int = 0`；`AnthropicProvider` 解析时 `getattr(..., 0) or 0` 兜底
**验证：** `tests/test_anthropic_provider.py::test_usage_event_defaults_cache_fields_to_zero_when_absent` 通过

### D4: OpenAIProvider 解析 cached_tokens
**依赖：** D3
**步骤：** 从 `chunk.usage.prompt_tokens_details` 解析 `cached_tokens`（→ `cache_read_input_tokens`）与 `cache_write_tokens`（→ `cache_creation_input_tokens`），`prompt_tokens_details` 本身缺失时两者都按 0 处理
**验证：** `tests/test_openai_provider.py::test_usage_event_parses_cached_tokens_when_present`、`test_usage_event_defaults_cache_fields_to_zero_when_details_absent` 通过

### D5: 提醒消息改为每轮临时拼接、不持久化
**依赖：** D2
**步骤：**
1. `session.py` 删除 `add_system_message`，只保留 `enter_plan_mode`/`next_plan_mode_round`
2. `agent.py` 的 `run_agent_loop` 在每轮迭代内：`request_messages = session.get_messages()`；若 `not tools_enabled`，取 `round = session.next_plan_mode_round()`、`reminder = plan_mode_reminder(round)`，把 `Message(role="system", content=reminder)` 追加到 `request_messages`（这份局部拷贝，不影响 `session` 内部状态）；用 `request_messages` 而非 `session.get_messages()` 调用 `provider.stream_chat`
3. `tui.py` 删除原本在 `/plan` 输入后立即调用 `plan_mode_reminder` + `session.add_system_message` 的那段逻辑，只保留 `session.enter_plan_mode()`

**验证：** `tests/test_agent.py::test_plan_mode_reminder_injected_but_not_persisted`（注入到请求里但 `session.get_messages()` 里找不到）、`test_plan_mode_round_counter_advances_across_turns_and_alternates_full_brief`（跨多次 `run_agent_loop` 调用轮次持续递增、完整/精简交替）均通过

### D6: 引入 ruff 并做全仓库合规
**依赖：** 无
**步骤：** `uv add --dev ruff`；`pyproject.toml` 加 `[tool.ruff]`（`target-version="py314"`、`line-length=110`、`select=["E","F","I","UP"]`）；`ruff check . --fix` 自动修复可修复项；手工拆分剩余的 E501 长行（主要在 `agent.py`、`tui.py` 和几个测试文件里）；最后 `ruff format .` 统一格式化全仓库
**验证：** `uv run ruff check .` 与 `uv run ruff format --check .` 均输出"通过/无变更"；`uv run pytest` 全绿（确认格式化没有改变行为）

### D7: 更新 plan.md/task.md/checklist.md
**依赖：** D1-D6
**步骤：** 按新 spec 与实际实现重写 plan.md 的核心数据结构/模块设计/模块交互/技术决策；task.md 追加本节；checklist.md 按新 AC1-AC14 重写
**验证：** 三份文档与代码状态一致（人工比对）

### D8: 全量回归
**依赖：** D1-D7
**步骤：** `uv run pytest` + `uv run ruff check .` + `uv run ruff format --check .`
**验证：** 全部通过，无回归
```
