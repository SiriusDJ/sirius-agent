# Sirius-Agent（Agent Loop）Plan

## 架构概览

在 02-tools 的 config / providers / tools / session / agent / tui / cli 基础上，做一次贯穿全栈的异步化改造，并新增安全分类、停止条件、Plan Mode 三块能力：

- **providers 模块（异步化）**：`Provider.stream_chat` 从同步生成器改为异步生成器（`AsyncIterator[StreamEvent]`）；两个具体实现改用官方 SDK 的异步客户端（`AsyncAnthropic`/`AsyncOpenAI`），流式解析逻辑不变，新增在流结束时产出一次 `USAGE` 事件
- **tools 模块（异步化 + 安全分类）**：`Tool.execute` 改为 `async def`；每个工具新增 `safe: bool` 类属性标记是否只读无副作用；五个文件类工具内部把阻塞 I/O 包一层 `asyncio.to_thread`，`execute_command` 改用 `asyncio.create_subprocess_shell` 实现真正的异步子进程管理；`ToolRegistry` 新增按名判存在性、按安全性过滤工具列表的能力
- **agent 模块（重写为 Agent Loop）**：不再是"最多两次请求"的固定流程，而是循环往复的 `run_agent_loop()`，内部用一个 `StreamCollector` 做"实时转发 + 完整累积"的双路收集；每轮工具调用按安全性分成并发批次和串行批次分别执行；五种停止条件（正常结束、达到上限、用户取消、连续未知工具、流式错误）统一通过 `StopReason` 表达
- **tui 模块（异步化 + Plan Mode + 取消监听）**：主循环改为 `async def`，用 `prompt_toolkit` 的异步输入读取用户输入；识别 `/plan`、`/do` 两个指令切换"计划模式"状态；每轮驱动 Agent Loop 时起一个后台任务监听 Esc/Ctrl+C，设置取消信号
- **cli 入口（改造）**：`main()` 用 `asyncio.run()` 驱动整个异步 REPL

## 核心数据结构

```python
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Optional, Protocol


# ---- providers/base.py ----

@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int


class StreamEventType(Enum):
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    TOOL_CALL = "tool_call"
    USAGE = "usage"          # 新增：本次请求的 token 用量，流结束前产出
    DONE = "done"
    ERROR = "error"


@dataclass
class StreamEvent:
    type: StreamEventType
    text: Optional[str] = None
    error_message: Optional[str] = None
    tool_call: Optional["ToolCall"] = None
    usage: Optional[TokenUsage] = None      # 新增


class Provider(Protocol):
    def stream_chat(
        self, messages: list["Message"], tools: Optional[list["Tool"]] = None
    ) -> AsyncIterator[StreamEvent]:
        """改为异步生成器：发送带完整历史的对话请求，边收边产出归一化事件。"""
        ...


# ---- tools/base.py ----

class Tool(Protocol):
    name: str
    description: str
    parameters_schema: dict
    safe: bool
        # True=只读、无副作用（可在同一轮内与其他 safe 工具并发执行）
        # False=有副作用（写文件/改文件/执行命令，须与其他工具串行执行）

    async def execute(self, arguments: dict) -> ToolResult: ...


# ---- agent.py ----

class StopReason(Enum):
    COMPLETED = "completed"              # 模型不再请求工具，正常结束
    MAX_ITERATIONS = "max_iterations"    # 达到默认 20 轮迭代上限
    USER_CANCELLED = "user_cancelled"    # 用户按 Esc/Ctrl+C 取消
    UNKNOWN_TOOL = "unknown_tool"        # 连续两轮出现未知工具调用
    STREAM_ERROR = "stream_error"        # LLM 请求流式出错


class TurnEventType(Enum):
    THINKING_DELTA = "thinking_delta"
    TEXT_DELTA = "text_delta"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    USAGE = "usage"
    STOPPED = "stopped"     # 循环结束（唯一的终止事件，具体原因看 stop_reason）


@dataclass
class TurnEvent:
    type: TurnEventType
    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Optional[dict] = None
    tool_result: Optional["ToolResult"] = None
    usage: Optional[TokenUsage] = None
    stop_reason: Optional[StopReason] = None
    error_message: Optional[str] = None    # stop_reason=STREAM_ERROR 时携带
    iteration: int = 0                      # 当前第几轮（1-based），供界面展示进度
```

要点说明：
- `TurnEventType` 用单一 `STOPPED` 事件替代原来的 `DONE`/`ERROR` 两种，具体终止原因统一走 `stop_reason`（对应 F11：五种停止路径都要说清楚原因）；这样界面只需一个分支按 `stop_reason` 细分展示文案，不必再区分"正常结束"和"出错"两条渲染路径
- `iteration` 字段承载"进度"信息（第几轮/共 20 轮），不再单独设一个 PROGRESS 事件类型（YAGNI）
- `TokenUsage` 统一了 Anthropic（`input_tokens`/`output_tokens`）和 OpenAI（`prompt_tokens`/`completion_tokens`）两家不同的字段命名，各 Provider 内部负责映射

## 模块设计

### tools 模块（扩展）

**职责变化：** `Tool.execute` 改为 `async def`；新增 `safe` 类属性；`ToolRegistry` 扩展按名判存在、按安全性过滤

**对外接口：**
```python
# tools/registry.py
class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...
    def has(self, name: str) -> bool:
        """新增：判断某个工具名是否已注册，供 agent 判断"未知工具"用"""
    def list_tools(self, only_safe: bool = False) -> list[Tool]:
        """only_safe=True 时只返回 safe=True 的工具（供 Plan Mode 使用）"""
    async def execute(self, name: str, arguments: dict) -> ToolResult:
        """按名查找并 await 执行；找不到工具（ToolError）或工具内部未预期异常，
        都统一兜底转成 ok=False 的 ToolResult，不向上抛出"""
```

**六个具体工具的改造：**

| 工具名 | `safe` | 改造要点 |
|---|---|---|
| `read_file` | `True` | `execute` 改 `async def`，`path.read_text(...)` 包一层 `await asyncio.to_thread(...)` |
| `glob_files` | `True` | 同上，`workspace_root.glob(pattern)` 的遍历包一层 `to_thread` |
| `grep_content` | `True` | 同上，`rglob` + 逐行正则匹配的遍历包一层 `to_thread` |
| `write_file` | `False` | `execute` 改 `async def`，写入操作包一层 `to_thread` |
| `edit_file` | `False` | 同上，读+替换+写包一层 `to_thread` |
| `execute_command` | `False` | 改用 `asyncio.create_subprocess_shell(...)` + `asyncio.wait_for(proc.communicate(), timeout=...)`；超时时 `proc.kill()` 后 `await proc.wait()` 避免僵尸进程 |

其余业务逻辑（路径安全校验、edit_file 的唯一匹配判断、错误信息文案）完全不变，只是执行方式从"直接同步调用"变成"await 一个内部用 to_thread/真异步子进程实现的协程"（对应 N1 行为等价）。

**依赖：** 标准库 `asyncio`（新增 `asyncio.to_thread`、`asyncio.create_subprocess_shell`、`asyncio.subprocess`）

### providers 模块（异步化）

**职责变化：** `stream_chat` 改为 `async def ... yield ...`（异步生成器）；内部改用异步 SDK 客户端；流结束前新增产出一次 `USAGE` 事件

**AnthropicProvider：**
- 构造时用 `anthropic.AsyncAnthropic(api_key=..., base_url=...)` 替代 `anthropic.Anthropic`
- `async with self._client.messages.stream(**request_kwargs) as stream: async for event in stream: ...`，内部识别 `content_block_start`/`content_block_delta`/`content_block_stop` 的逻辑不变（文本、thinking、tool_use 拼接逻辑照搬）
- 流正常消费完之后，`final_message = await stream.get_final_message()`，用 `TokenUsage(input_tokens=final_message.usage.input_tokens, output_tokens=final_message.usage.output_tokens)` 产出一次 `USAGE` 事件，再产出 `DONE`
- 异常捕获从 `anthropic.APIError` 改为在 `async with` 块外层捕获（异步客户端抛出的异常类型不变，只是捕获点在 `async def` 里）

**OpenAIProvider：**
- 构造时用 `openai.AsyncOpenAI(...)` 替代 `openai.OpenAI`
- 请求参数新增 `stream_options={"include_usage": True}`
- `stream = await self._client.chat.completions.create(**request_kwargs)`；`async for chunk in stream: ...`
- **关键修正**：原来 `if not chunk.choices: continue` 会跳过最后一个只带 usage、`choices` 为空的 chunk；改为先检查 `chunk.usage is not None`（命中就产出 `USAGE` 事件，`TokenUsage(input_tokens=chunk.usage.prompt_tokens, output_tokens=chunk.usage.completion_tokens)`），再判断 `choices` 是否为空决定要不要继续处理文本/工具调用增量
- 其余文本增量、`tool_calls` 分片拼接逻辑不变

**依赖：** `anthropic`/`openai` SDK 的异步客户端（同一个包，不需要新依赖）

### agent 模块（重写）

**职责：** 编排完整的 ReAct 循环——反复"发起 LLM 请求 → 双路收集响应 → 识别工具调用（按已知/未知、按安全性分类）→ 执行 → 写回历史"，直到五种停止条件之一触发；对外只产出 `TurnEvent` 异步事件流

**对外接口：**
```python
_MAX_ITERATIONS = 20
_MAX_CONSECUTIVE_UNKNOWN_TOOL_ROUNDS = 2

class StreamCollector:
    """把一次 stream_chat 的事件双路处理：一路实时转发成 TurnEvent，
    一路在内部累积出这一次请求的完整文本/工具调用/用量，供循环下一步判断使用。"""

    def __init__(self) -> None:
        self.text: str = ""
        self.tool_calls: list[ToolCall] = []
        self.usage: Optional[TokenUsage] = None
        self.error_message: Optional[str] = None

    async def consume(
        self, stream: AsyncIterator[StreamEvent], iteration: int
    ) -> AsyncIterator[TurnEvent]:
        """逐个消费 stream 的事件：THINKING_DELTA/TEXT_DELTA 实时 yield 并累积文本；
        TOOL_CALL 累积进 tool_calls；USAGE 记录并 yield；ERROR 记录 error_message 后停止消费；
        DONE 结束消费。消费完毕后 self.text/tool_calls/usage/error_message 可供调用方读取。"""
        ...


async def run_agent_loop(
    provider: Provider,
    tool_registry: ToolRegistry,
    session: ConversationSession,
    user_text: str,
    cancel_event: "asyncio.Event",
    tools_enabled: bool = True,
) -> AsyncIterator[TurnEvent]:
    """把 user_text 加入历史；循环最多 _MAX_ITERATIONS 轮：
    每轮开始前检查 cancel_event → 触发则 STOPPED(USER_CANCELLED)；
    用 StreamCollector 消费一次 stream_chat（tools_enabled=False 时只传只读工具列表，对应 Plan Mode）；
    出错 → STOPPED(STREAM_ERROR)；无工具调用 → 写入最终回复，STOPPED(COMPLETED)；
    有工具调用 → 写入 assistant 工具调用消息，逐个区分已知/未知工具：
      未知的立即产出错误结果并写回历史，计入本轮"未知工具"标记；
      已知的按 tool.safe 分成并发批次（asyncio.gather）和串行批次（for 循环 await）分别执行并写回历史；
    若本轮出现未知工具，连续计数 +1，达到 2 → STOPPED(UNKNOWN_TOOL)；否则清零计数，继续下一轮；
    循环跑满 _MAX_ITERATIONS 轮仍未停止 → STOPPED(MAX_ITERATIONS)"""
    ...
```

**依赖：** providers 模块（`Provider`、`StreamEvent`、`TokenUsage`）、tools 模块（`ToolRegistry`、`ToolCall`、`ToolResult`）、session 模块、标准库 `asyncio`

### tui 模块（异步化 + Plan Mode + 取消监听）

**职责变化：** 主循环改为 `async def`；识别 `/plan`/`/do` 维护计划模式状态；每轮驱动 Agent Loop 前后起停一个后台按键监听任务；按新的 `TurnEventType`（含 `USAGE`、统一的 `STOPPED`）渲染

**取消监听实现方式：** 用 `prompt_toolkit.input.create_input()` 打开一个跨平台（含 Windows）的原始按键输入源，配合 `input.attach(callback)` 在后台异步任务里监听 `Esc`/`Ctrl+C` 按键，命中后 `cancel_event.set()`；每次用户提交一条消息、开始跑 Agent Loop 时启动这个监听任务，Loop 结束（无论什么 stop_reason）后取消该任务。选择 prompt_toolkit 而不是手写 termios/msvcrt，是因为项目已经依赖它做输入，它自带跨平台的异步按键读取能力，不用再引入新依赖或平台分支代码。

**对外接口：**
```python
async def run_repl(
    provider: Provider, tool_registry: ToolRegistry, session: ConversationSession
) -> None:
    """交互式主循环：
    - 读到 /exit 或 Ctrl+D → 结束程序
    - 读到 /plan → 进入计划模式（之后每轮调用 run_agent_loop 时 tools_enabled=False）
    - 读到 /do → 切回全工具模式（tools_enabled=True）
    - 其他非空输入 → 起取消监听任务 + 消费 run_agent_loop 产出的 TurnEvent 并渲染，
      Loop 结束后停掉监听任务，回到输入提示符
    渲染规则：
      THINKING_DELTA/TEXT_DELTA → 沿用现有流式打印样式
      TOOL_STARTED/TOOL_FINISHED → 沿用现有工具名/参数/结果摘要样式
      USAGE → 打印本轮 token 用量（输入/输出），dim 样式
      STOPPED → 按 stop_reason 分支：
        COMPLETED      → 正常换行结束，不额外提示
        MAX_ITERATIONS → 提示"已达到最大迭代轮数（20），本次任务未必完成"
        USER_CANCELLED → 提示"已取消"
        UNKNOWN_TOOL   → 提示"模型连续请求未知工具，已停止"
        STREAM_ERROR   → 用 error_message 提示清晰错误
    """
```

### cli 入口（改造）

**职责变化：** `main()` 用 `asyncio.run()` 驱动 `run_repl`；`_build_tool_registry` 组装逻辑不变（六个工具各自在类定义里声明了 `safe` 属性，入口不需要额外区分）

## 模块交互

**启动阶段（不变的部分省略）：**
```
__main__.main()
  → 组装 provider / tool_registry / session（同 02-tools，不变）
  → asyncio.run(tui.run_repl(provider, tool_registry, session))
```

**每一轮用户输入（tui.run_repl 内部循环）：**
```
await prompt_session.prompt_async("> ")
  → /exit 或 EOF → 结束循环
  → /plan → plan_mode = True，打印提示，回到循环顶部
  → /do   → plan_mode = False，打印提示，回到循环顶部
  → 其他非空文本：
      cancel_event = asyncio.Event()
      watcher_task = asyncio.create_task(_watch_cancel_keys(cancel_event))
      try:
          async for turn_event in run_agent_loop(
              provider, tool_registry, session, text,
              cancel_event, tools_enabled=not plan_mode,
          ):
              渲染 turn_event（见上）
      finally:
          watcher_task.cancel()
```

**run_agent_loop 内部（核心 ReAct 循环，每轮迭代）：**
```
session.add_user_message(user_text)   # 只在循环开始前执行一次
consecutive_unknown_rounds = 0

for iteration in 1..20:
    if cancel_event.is_set():
        yield STOPPED(USER_CANCELLED); return

    active_tools = tool_registry.list_tools(only_safe=not tools_enabled)
    collector = StreamCollector()
    async for turn_event in collector.consume(
        provider.stream_chat(session.get_messages(), tools=active_tools), iteration
    ):
        yield turn_event   # THINKING_DELTA / TEXT_DELTA / USAGE 实时转发

    if collector.error_message:
        yield STOPPED(STREAM_ERROR, error_message=...); return

    if not collector.tool_calls:
        session.add_assistant_message(collector.text)
        yield STOPPED(COMPLETED); return

    session.add_assistant_tool_call_message(collector.text, collector.tool_calls)

    known, round_has_unknown = [], False
    for tc in collector.tool_calls:
        if not tool_registry.has(tc.name):
            round_has_unknown = True
            result = ToolResult(ok=False, content=f"未知的工具：{tc.name}")
            yield TOOL_STARTED / TOOL_FINISHED（携带 result）
            session.add_tool_result_message(tc.id, result.content)
        else:
            known.append(tc)

    consecutive_unknown_rounds = consecutive_unknown_rounds + 1 if round_has_unknown else 0
    if consecutive_unknown_rounds >= 2:
        yield STOPPED(UNKNOWN_TOOL); return

    safe_calls   = [tc for tc in known if tool_registry.get(tc.name).safe]
    unsafe_calls = [tc for tc in known if not tool_registry.get(tc.name).safe]

    # 并发批次：先对每个 tc 各 yield 一次 TOOL_STARTED，再 asyncio.gather 并发执行，
    # 全部完成后逐个 yield TOOL_FINISHED 并写回历史
    # 串行批次：for 循环逐个 yield TOOL_STARTED → await 执行 → yield TOOL_FINISHED → 写回历史

yield STOPPED(MAX_ITERATIONS)   # 循环跑满 20 轮仍未返回
```

**异常兜底（延续 + 扩展）：** `ConfigError` 仍在 `main()` 最外层捕获（不变）；`run_agent_loop` 内部的流式错误转成 `STOPPED(STREAM_ERROR)` 事件（不再是裸的 `ERROR` 类型）；`ToolRegistry.execute` 对工具内部未预期异常兜底（不变，且现在同时兜底"未知工具名"这一路径，供 F4 的连续计数使用）；`tui.run_repl` 顶层仍用 `try/except KeyboardInterrupt` 兜底捕获意外没被按键监听任务吞掉的 Ctrl+C，避免整个 REPL 崩溃退出。

## 文件组织

```
sirius-agent/
├── docs/
│   └── 03-agent-loop/
│       ├── spec.md / plan.md / task.md / checklist.md   — 本期文档
├── pyproject.toml                     — 不变（asyncio 为标准库，两家 SDK 已自带异步客户端）
├── src/
│   └── sirius-agent/
│       ├── __main__.py                 — 改造：main() 用 asyncio.run() 驱动 run_repl
│       ├── config.py                    — 不变
│       ├── session.py                    — 不变（纯内存同步操作，无需异步化）
│       ├── agent.py                      — 重写：StopReason / TurnEventType(含 USAGE/STOPPED) /
│       │                                          StreamCollector / run_agent_loop() / 并发+串行批次执行
│       ├── tui.py                         — 改造：async run_repl，/plan /do，取消监听任务，新事件渲染
│       └── providers/
│           ├── base.py                    — 扩展：TokenUsage、StreamEventType.USAGE、
│           │                                       Provider.stream_chat 签名改 AsyncIterator
│           ├── factory.py                  — 不变
│           ├── anthropic_provider.py       — 改造：AsyncAnthropic，get_final_message() 取 usage
│           └── openai_provider.py          — 改造：AsyncOpenAI，stream_options include_usage，
│                                                    修正"空 choices 判断"以免漏掉 usage chunk
│       └── tools/
│           ├── base.py                     — 扩展：Tool 新增 safe 属性，execute 改 async def
│           ├── registry.py                  — 扩展：has()、list_tools(only_safe)、execute 改 async
│           ├── schema.py                    — 不变
│           ├── paths.py                     — 不变
│           ├── read_file.py                 — 改造：execute 改 async，读取包 to_thread，safe=True
│           ├── glob_files.py                — 改造：同上，safe=True
│           ├── grep_content.py              — 改造：同上，safe=True
│           ├── write_file.py                — 改造：execute 改 async，写入包 to_thread，safe=False
│           ├── edit_file.py                 — 改造：同上，safe=False
│           └── execute_command.py           — 改造：asyncio.create_subprocess_shell + wait_for，safe=False
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 是否全面迁移 asyncio | 是——Provider、Tool、Agent、TUI 全链路 async | 用户明确要求"异步事件流"；只在局部加线程池模拟并发会导致"部分异步、部分同步"的架构割裂，取消/超时/并发三件事都需要真正的事件循环才能一致地表达 |
| 停止条件的事件建模 | 单一 `STOPPED` 事件 + `StopReason` 枚举，取代原来的 `DONE`/`ERROR` 两种类型 | 五种终止路径本质都是"循环不再继续"，只是原因不同；界面只需一处按 `stop_reason` 分支渲染，避免 `DONE`/`ERROR` 各自一套渲染逻辑造成重复 |
| 安全工具并发的实现方式 | 文件类工具内部用 `asyncio.to_thread` 包裹阻塞 I/O，Agent Loop 层用 `asyncio.gather` 并发调度 | 只把 `execute` 声明成 `async def` 而不包 `to_thread`，同批次的 `gather` 不会有任何一处让出控制权，退化成顺序执行；`to_thread` 把阻塞操作丢进线程池，才能让多个只读工具在 `gather` 下真正并发跑，满足 AC6 的可观测并发 |
| `execute_command` 的异步实现 | 直接用 `asyncio.create_subprocess_shell` + `asyncio.wait_for`，而非 `to_thread` 包裹 `subprocess.run` | 原生异步子进程支持在超时时干净地 `kill()` + `await proc.wait()`；`to_thread` 包裹的同步 `subprocess.run` 一旦超时，只能等线程里的调用自己抛 `TimeoutExpired`，无法被外层 `asyncio` 取消逻辑提前打断 |
| 未知工具的容忍粒度 | 按"轮"计数（一轮里只要出现过未知工具就计数 +1），而非按"次"计数 | 呼应 AC4"模型收到错误反馈并重试"的语义——重试发生在下一轮请求里；按轮计数才能表达"给一次基于反馈纠正的机会"，按次计数会在模型一次性并行请求多个未知工具名时误伤 |
| 取消信号的实现 | `asyncio.Event`，由 tui 层的按键监听任务设置，Agent Loop 在每轮迭代开始前检查 | 只在安全的检查点（下一轮开始前）响应取消，不强行打断正在执行中的工具调用，符合 F3"当前步骤跑完再停"的约定，避免半途中断 execute_command 或文件写入导致的不一致状态 |
| Plan Mode 的限制机制 | 通过"这一轮请求携带的工具列表"过滤（`list_tools(only_safe=True)`），而非依赖 prompt 文字约束模型 | 与协议层面禁止工具调用的原则一致（呼应 02-tools"追加请求不传 tools"的思路）；模型在协议层面就拿不到写类工具的 schema，杜绝"文字约束被忽略"的风险 |
| Token 用量的字段归一化 | 自定义 `TokenUsage(input_tokens, output_tokens)`，各 Provider 内部把 Anthropic 的 `input_tokens`/`output_tokens` 和 OpenAI 的 `prompt_tokens`/`completion_tokens` 都映射过来 | agent/tui 层只认一套字段名，不需要关心底层协议差异，延续"协议差异收敛在 Provider 内部"的既有原则 |
