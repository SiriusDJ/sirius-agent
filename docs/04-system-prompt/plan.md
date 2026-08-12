# 系统提示工程化 Plan

## 架构概览

新增一个 `sirius_agent.prompt` 模块，独立于现有的 `agent`/`session`/`providers`/`tools`，职责是"给定环境信息，产出结构化的系统提示"。它不知道 Provider、不知道 Session，只做纯函数式的文本拼装。

系统提示分两条通路，对应 spec 里"稳定内容 vs 变化内容"的区分：

1. **稳定通路**：七个固定模块（+ 未来的可选模块）拼成一段文本，标记为可缓存
2. **变化通路**：环境信息（cwd、平台、日期、应用版本、当前模型、git 状态）每次请求现取现拼，不标记缓存

这两条通路的产物通过 `Provider.stream_chat` 的 `system_prompt` 参数传给各协议实现：Anthropic 走顶层 `system` 字段并显式挂 `cache_control`；OpenAI 把它拼成一条前置的 `system` 角色消息，不做任何显式缓存标记，靠端点自身的前缀缓存自动受益。

另有一条独立于系统提示的通路——**补充消息注入**（Plan Mode 提醒）。它按 **Agent Loop 轮次**（`run_agent_loop` 内部 `iteration` 计数，跨多次用户提交持续累加，只要 Plan Mode 没退出）决定这一轮要注入完整版还是精简版提醒；每一轮只要处于 Plan Mode 就一定会注入某个版本——不存在"这一轮什么都不注入"的情况。这条提醒**只拼进当次请求的消息列表，不写入 `ConversationSession` 的持久历史**：用统一 `Message` 模型的 `role="system"` 表示，在 `agent.py` 内部临时追加到 `session.get_messages()` 的拷贝上，永远不经过任何 `session.add_*` 方法。各 Provider 的翻译函数决定怎么把 `role="system"` 表达出来（OpenAI 原生支持；Anthropic 不允许，翻译时降级成带 `<system-reminder>` 标签的 `user` 消息）。

## 核心数据结构

### `SystemPromptBlock`（`sirius-agent/prompt/builder.py`）

```python
@dataclass
class SystemPromptBlock:
    text: str
    cacheable: bool = False
```
一段系统提示文本 + 是否应该被标记为可缓存。`build_system_prompt()` 固定返回两块：稳定模块合并成一块（`cacheable=True`），环境信息单独一块（`cacheable=False`）。

### `EnvironmentContext`（`sirius-agent/prompt/environment.py`）

```python
@dataclass
class EnvironmentContext:
    cwd: str
    platform: str
    date: str
    app_version: str
    model_name: str
    git_branch: str | None  # None = 不在 git 仓库中
    git_dirty: bool | None  # 与 git_branch 同步为 None
```
`app_version` 通过 `importlib.metadata.version("sirius-agent")` 读取 `pyproject.toml` 里声明的版本号，取不到时降级为 `"unknown"`。

### `Message` 扩展（`sirius-agent/providers/base.py`）

`role` 字段的取值集合从 `"user" | "assistant" | "tool"` 扩展为 `"user" | "assistant" | "tool" | "system"`。`role="system"` 的 `Message` 只在 `agent.py` 内部临时构造，用于这一次请求的补充指令注入，**不存在任何把它写入 `ConversationSession` 的接口**。

### `TokenUsage` 扩展（`sirius-agent/providers/base.py`）

```python
@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0  # 端点未返回缓存字段时按 0 处理
    cache_read_input_tokens: int = 0  # 端点未返回缓存字段时按 0 处理
```
两个字段用 `int` 默认 `0` 而不是 `Optional[int]`：两个协议都可能提供缓存信息，缺失时按 0 处理比用 `None` 表达"不适用"更符合 spec AC6/N6 的要求。

### `Provider.stream_chat` 签名扩展

```python
def stream_chat(
    self,
    messages: list[Message],
    tools: list[Tool] | None = None,
    system_prompt: list[SystemPromptBlock] | None = None,
) -> AsyncIterator[StreamEvent]: ...
```

新增 `config` 只读属性（`ProviderConfig`），供上层构建 `EnvironmentContext` 时读取当前模型名，不用另外传参。

## 模块设计

### `sirius-agent/prompt/sections.py`
**职责：** 提供七个固定模块 + 环境信息模块各自的纯函数，每个函数返回一段文本
**对外接口：**
```python
def identity_section() -> str: ...  # 身份
def system_constraints_section() -> str: ...  # 系统约束
def task_mode_section() -> str: ...  # 任务模式（含"可能收到运行时补充指令"的说明）
def action_execution_section() -> str: ...  # 动作执行
def tool_usage_section() -> str: ...  # 工具使用（含 F5 的两条强化规则）
def tone_style_section() -> str: ...  # 语气风格
def text_output_section() -> str: ...  # 文本输出
def environment_section(env: EnvironmentContext) -> str: ...  # 环境信息
```
**依赖：** 仅 `EnvironmentContext`，无其他模块依赖

### `sirius-agent/prompt/environment.py`
**职责：** 采集当前运行环境，产出 `EnvironmentContext`
**对外接口：**
```python
async def gather_environment(workspace_root: Path, model_name: str) -> EnvironmentContext: ...
```
内部通过 `asyncio.create_subprocess_exec("git", "rev-parse", "--abbrev-ref", "HEAD")` 和 `git status --porcelain` 采集分支与改动状态；命令失败（不在 git 仓库/git 不存在）或超过 `_GIT_TIMEOUT`（5 秒）未返回时，`git_branch`/`git_dirty` 置 `None`，不抛异常、不无限期挂住请求（N4）；`app_version` 通过 `importlib.metadata` 读取
**依赖：** 无（不依赖 prompt 包内其他模块）

### `sirius-agent/prompt/builder.py`
**职责：** 把固定模块 + 可选模块 + 环境信息组装成 `list[SystemPromptBlock]`
**对外接口：**
```python
@dataclass
class Section:
    name: str
    priority: int  # 数值越小越靠前
    content_fn: Callable[[], str]


def build_system_prompt(
    env: EnvironmentContext, optional_sections: list[str] | None = None
) -> list[SystemPromptBlock]: ...
```
七个固定模块各自包一个 `Section`（`priority` 取 10/20/.../70，留出插入空隙），存进 `_FIXED_SECTIONS` 列表；装配时先 `sorted(_FIXED_SECTIONS, key=lambda s: s.priority)` 再拼接——真正决定顺序的是 `priority` 字段，不是列表的书写顺序。新增模块只需要在 `sections.py` 里加一个函数、在 `_FIXED_SECTIONS` 里挂一个 `Section` 并给个 `priority` 数字，不用关心插在列表哪个位置——满足 N8 的"挂载"语义；`optional_sections` 为空时不追加多余空行
**依赖：** `sections.py`

### `sirius-agent/prompt/reminders.py`
**职责：** 根据 Plan Mode 已进行的 Agent Loop 轮次，决定这一轮注入完整版还是精简版提醒——每一轮都会返回一个版本，不返回"不注入"
**对外接口：**
```python
def plan_mode_reminder(round_number: int) -> str:
    """round_number 从 1 开始计数（每次进入 Plan Mode 重新从 1 计），按 Agent Loop 轮次递增。
    第 1 轮、此后每隔 3 轮（第 4、7、10...轮）返回完整版；其余轮次返回精简版。"""
```
**依赖：** 无

### `sirius-agent/session.py`（改动）
**新增职责：** 记录 Plan Mode 当前已进行的轮次；**不提供**任何写入 `role="system"` 消息的接口——保证提醒消息不可能被持久化（N3）
**新增接口：**
```python
def enter_plan_mode(self) -> None: ...  # 轮次计数器归零
def next_plan_mode_round(self) -> int: ...  # 计数器 +1 并返回新值
```
**依赖：** 无新增外部依赖

### `sirius-agent/providers/anthropic_provider.py`（改动）
**新增职责：** 把 `system_prompt` 翻译成顶层 `system` 字段（`cacheable=True` 的块带 `cache_control: {"type":"ephemeral","ttl":"5m"}`）；给 `tools` 数组最后一项加同样的 `cache_control`（缓存整个工具列表前缀）；`_to_anthropic_messages` 新增 `role="system"` 分支，降级为 `<system-reminder>` 包裹的 `user` 消息；解析 `usage.cache_creation_input_tokens` / `usage.cache_read_input_tokens`，缺失时按 0 处理
**依赖：** `sirius_agent.prompt.builder.SystemPromptBlock`

### `sirius-agent/providers/openai_provider.py`（改动）
**新增职责：** 把 `system_prompt` 各块文本拼接成一条 `{"role":"system","content":...}` 消息，前置到 `messages` 列表最前面；`_to_openai_messages` 无需改动（现有 `else` 分支已经能透传 `role="system"` 的历史消息）；从 `usage.prompt_tokens_details` 解析 `cached_tokens`（→ `cache_read_input_tokens`）与 `cache_write_tokens`（→ `cache_creation_input_tokens`），字段或 `prompt_tokens_details` 本身缺失时按 0 处理
**依赖：** `sirius_agent.prompt.builder.SystemPromptBlock`

### `sirius-agent/agent.py`（改动）
**新增职责：** `run_agent_loop` 新增 `workspace_root: Path` 参数；每轮迭代发起请求前调用 `gather_environment` + `build_system_prompt`；当 `tools_enabled=False`（即 Plan Mode）时，调用 `session.next_plan_mode_round()` 取得这一轮的轮次号，用 `plan_mode_reminder()` 取得提醒文本，把它包成 `Message(role="system", ...)` **临时追加**到 `session.get_messages()` 的返回值（一份拷贝）上，作为这一次 `stream_chat` 调用的 `messages` 参数——不调用任何 `session.add_*`，因此这条消息不出现在下一轮的 `session.get_messages()` 里
**依赖：** `sirius_agent.prompt.environment`、`sirius_agent.prompt.builder`、`sirius_agent.prompt.reminders`

### `sirius-agent/tui.py`（改动）
**新增职责：** 接入 `workspace_root` 参数透传；`/plan` 时调用 `session.enter_plan_mode()` 重置轮次计数器；**不再**做任何提醒构造或注入——这部分逻辑已经内聚进 `agent.py`，tui.py 只负责传递 `tools_enabled=not plan_mode`
**依赖：** 无新增

### `sirius-agent/tools/execute_command.py`、`sirius-agent/tools/edit_file.py`（改动）
**新增职责：** `description` 文案分别追加"优先使用专用工具"和"编辑前必须先读"的强化措辞（F5）
**依赖：** 无

### 代码规范（新增，非模块）
项目新增 `ruff`（dev 依赖）与 `[tool.ruff]` 配置（`target-version = "py314"`、`line-length = 110`、`select = ["E", "F", "I", "UP"]`），`ruff check .` 与 `ruff format --check .` 都要求全仓库无告警（N7/AC14），而不只是本章新增文件。

## 模块交互

### 请求发起时（每轮 Agent Loop 迭代）

```
run_agent_loop（agent.py，第 iteration 轮）
  │
  ├─ gather_environment(workspace_root, provider.config.model)
  │     → EnvironmentContext（含一次 git 子进程调用 + importlib.metadata 读版本号）
  │
  ├─ build_system_prompt(env)
  │     → [SystemPromptBlock(稳定模块, cacheable=True),
  │        SystemPromptBlock(环境信息, cacheable=False)]
  │
  ├─ request_messages = session.get_messages()（拷贝）
  │     tools_enabled=False 时：
  │       round = session.next_plan_mode_round()
  │       reminder = plan_mode_reminder(round)   # 永远非空
  │       request_messages.append(Message(role="system", content=reminder))
  │       # 注意：只追加到这份拷贝，session 内部历史不变
  │
  └─ provider.stream_chat(request_messages, tools=active_tools, system_prompt=blocks)
        │
        ├─ AnthropicProvider：blocks → system=[...]（挂 cache_control）
        │                     tools  → 最后一项挂 cache_control
        │                     role="system" 消息 → 降级为带标签的 user 消息
        │                     响应 usage → 填 TokenUsage.cache_*（缺失按 0）
        │
        └─ OpenAIProvider：  blocks 拼接 → 前置 system 消息
                              role="system" 消息 → 原生透传
                              响应 usage.prompt_tokens_details → 填 TokenUsage.cache_*（缺失按 0）
```

### Plan Mode 轮次与提醒（跨越多次用户提交，只要没 `/do`）

```
tui.py: 用户输入 /plan
  → session.enter_plan_mode()   # 轮次计数器归零
  → plan_mode = True

用户第 1 次提交（tools_enabled=False）→ run_agent_loop 内第 1 次迭代
  → round=1 → 完整版提醒

用户第 2 次提交，这一轮任务只需 1 次迭代
  → round=2 → 精简版提醒

用户第 3 次提交，这一轮模型调用了一次只读工具、又请求了一次
  → 第一次迭代 round=3 → 精简版；第二次迭代 round=4 → 完整版（重复）
  # 轮次计数覆盖的是"Agent Loop 请求次数"，不是"用户提交次数"——
  # 一次用户提交可能对应多轮迭代，每轮都各自计数、各自决定注入哪个版本

tui.py: 用户输入 /do
  → plan_mode = False   # 之后的 run_agent_loop 调用 tools_enabled=True，不再触发注入
```

## 文件组织

```
src/sirius_agent/
├── prompt/
│   ├── __init__.py
│   ├── builder.py       — SystemPromptBlock、Section（含 priority）、build_system_prompt()
│   ├── sections.py      — 七个固定模块 + 环境信息模块的文本函数
│   ├── environment.py   — EnvironmentContext、gather_environment()
│   └── reminders.py     — plan_mode_reminder()
├── providers/
│   ├── base.py           （改）Message.role 扩展、TokenUsage 扩展（int=0）、stream_chat 签名扩展、config 属性
│   ├── anthropic_provider.py  （改）system/tools 缓存标记、role="system" 降级翻译、usage 缓存字段解析
│   └── openai_provider.py     （改）system 消息前置、usage.prompt_tokens_details 解析
├── session.py             （改）enter_plan_mode / next_plan_mode_round（不提供 add_system_message）
├── agent.py                （改）接入 gather_environment + build_system_prompt；Plan Mode 提醒的临时注入与轮次推进
├── tui.py                  （改）workspace_root 透传；/plan 时重置轮次计数器，不做提醒注入
└── tools/
    ├── execute_command.py  （改）description 强化
    └── edit_file.py         （改）description 强化

pyproject.toml               （改）新增 ruff dev 依赖与 [tool.ruff] 配置
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 系统提示是否存入 `ConversationSession` 历史 | 不存入，每轮现取现拼，作为独立参数传给 `stream_chat` | 环境信息（cwd/日期/git 状态）逐轮可能变化，且天然对应 Anthropic `system` 是顶层参数、不在 `messages` 数组里这一事实 |
| Plan Mode 提醒是否存入历史 | **不存入**，每轮临时构造、只拼进这一次请求的 `messages` 参数 | spec F6/N3 明确要求"每轮动态构造、不写入持久历史"；`ConversationSession` 不再提供任何持久化这类消息的接口，从根源上防止误用 |
| Plan Mode 轮次的计数口径 | 按 **Agent Loop 迭代次数**（`run_agent_loop` 内部 `iteration`），跨用户提交持续累加 | spec F7 明确写"按 Agent Loop 轮次控制"；一次用户提交可能触发多轮迭代，每轮都是一次独立的 LLM 请求，都需要独立判断这一轮的提醒详略 |
| Plan Mode 提醒频率规则 | 第 1、4、7...轮完整版，其余轮次精简版（不存在"不注入"） | spec F7/AC9 明确"首轮完整、间隔轮次重复完整、其余轮次精简"——精简版本身就是一种注入，不是跳过 |
| `role="system"` 在 Anthropic 下如何降级 | 翻译成 `role="user"`，内容包一层 `<system-reminder>` 标签 | Anthropic Messages API 硬性只接受 `user`/`assistant`，`<system-reminder>` 是模型训练中常见的"非用户提问、无需当作对话回应"的约定标签 |
| Anthropic 工具缓存断点放在哪 | `tools` 数组最后一项挂 `cache_control` | 缓存断点语义是"缓存这个断点之前的全部前缀"，挂在最后一项等价于缓存整个工具列表 |
| OpenAI 是否解析缓存字段 | 解析 `usage.prompt_tokens_details.cached_tokens` / `cache_write_tokens` | spec F4/AC6 明确要求 OpenAI 也解析缓存命中信息；端点不支持时字段缺失，按 0 处理不报错 |
| `TokenUsage` 缓存字段用 `int=0` 而非 `Optional[int]=None` | 是 | 两个协议现在都可能提供缓存信息，`0` 比 `None` 更直接地表达"这次没有缓存收益"，也符合 N6"未返回时按零处理"的措辞 |
| 环境信息新增 `app_version`、去掉 `provider_name` | 是 | 严格对齐新 spec F2/AC3 列出的字段清单 |
| 引入 `ruff` 作为唯一的代码规范工具 | 是，不引入 `mypy`（N7 里 mypy 是可选项） | `ruff check` + `ruff format` 一套工具覆盖 lint + 格式化，配置成本最低；`select = ["E","F","I","UP"]` 覆盖 PEP 8、未使用引用、import 排序、新语法升级 |
| `gather_environment` 放在 `agent.py` 每轮迭代内调用，而非会话开始时调用一次 | 是 | F2 要求环境信息反映"当前"状态；环境信息本来就不参与缓存（F3），逐轮重新采集不影响缓存策略 |
| 模块的"优先级"用显式 `Section(name, priority, content_fn)` 结构体 + 排序，而不是隐式列表顺序 | 是 | F1 原文"每个模块带名称、优先级、内容"、N8"新增模块只需挂载"都在描述一个带 `priority` 字段的结构；纯列表顺序虽然效果等价，但没有可检验、可独立于书写位置的"优先级"概念，不满足这两条的字面要求 |
| `_run_git` 加超时保护（`_GIT_TIMEOUT = 5.0`），而不是无限等待子进程 | 是 | N4 明确要求"快速且有界，不阻塞界面"；`gather_environment` 每轮迭代都会调用，git 一旦因为凭据交互/异常挂载卡住，没有超时就会拖住整个 Agent Loop 请求，不只是拖住环境采集本身 |
| 缓存命中数字拼进 `tui.py` 原有的"本轮用量"滚动输出行，不算违反"不进 TUI 状态栏" | 是（经与用户确认） | "不做的事"里的"状态栏"指常驻、固定位置的 UI 元素；现在的用量行跟对话内容一起向上滚动，不是固定在屏幕上的独立状态展示，形式上与 ch04 已有的 token 用量行一致，未新增这类常驻元素 |
