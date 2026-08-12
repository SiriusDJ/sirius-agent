# Sirius-Agent（工具系统）Plan

## 架构概览

在第一期的 config / providers / session / tui / cli 五个模块基础上，新增两个模块，并对 providers、session、tui 做扩展：

- **tools 模块（新增）**：定义统一的 `Tool` 接口和参数 Schema 描述方式；六个具体工具实现；一个 `ToolRegistry` 注册中心，负责按名查找工具、把工具列表转换成 Anthropic/OpenAI 各自的工具描述格式；执行入口统一处理超时和异常，返回结构化 `ToolResult`
- **providers 模块（扩展）**：`Provider` 接口的 `stream_chat` 增加"可携带工具列表"的能力；`StreamEvent` 增加工具调用相关的事件类型；`AnthropicProvider`/`OpenAIProvider` 各自负责把统一的工具描述翻译成本协议的请求格式，并从流式响应里识别、拼接出完整的工具调用请求（工具名 + 参数 JSON）
- **session 模块（扩展）**：`Message` 需要能表达"助手请求了工具调用"和"工具执行结果"这两种新的历史条目，保证下一次请求把完整的工具调用/结果链路带给模型
- **agent 模块（新增）**：编排"一轮对话"的完整流程——首次请求、识别工具调用、执行工具、写回历史、自动发起不带工具的追加请求拿最终回复；对外产出一串可直接渲染的事件
- **tui 模块（扩展）**：主循环改为消费 agent 模块产出的事件并渲染到终端（工具名/参数/结果，区别于 thinking/正式回答样式）
- **cli 入口（扩展）**：组装阶段额外创建 `ToolRegistry` 并注册六个工具

## 核心数据结构

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Optional, Protocol


class StreamEventType(Enum):
    TEXT_DELTA = "text_delta"  # 正式回答的文本增量
    THINKING_DELTA = "thinking_delta"  # extended thinking 的文本增量
    TOOL_CALL = "tool_call"  # 新增：模型请求一次工具调用，参数已拼接、解析完整
    DONE = "done"  # 本轮流式响应结束
    ERROR = "error"  # 请求过程中出错


@dataclass
class ToolCall:
    id: str  # 本次调用的唯一标识（供应商返回，用于把结果关联回去）
    name: str  # 工具名，如 "read_file"
    arguments: dict  # 已解析好的参数字典（不再是 JSON 碎片）


@dataclass
class StreamEvent:
    type: StreamEventType
    text: Optional[str] = None  # TEXT_DELTA / THINKING_DELTA 时携带增量内容
    error_message: Optional[str] = None  # ERROR 时携带可读错误信息
    tool_call: Optional[ToolCall] = None  # TOOL_CALL 时携带完整的工具调用请求


@dataclass
class ToolResult:
    ok: bool  # 工具是否执行成功
    content: str  # 成功时是工具产出内容（文件内容/命令输出/匹配列表等）；
    # 失败时是清晰的错误描述（对应 N1），供模型和终端展示复用同一份文本


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str = ""  # 纯文本内容（user/assistant 文本消息使用）
    tool_calls: list[ToolCall] = field(default_factory=list)
    # role="assistant" 且模型请求了工具调用时使用；文本回复用 content，工具调用请求用这个字段，
    # 二者可能同时非空（模型一边说话一边调用工具）
    tool_call_id: Optional[str] = None
    # role="tool" 时必填，标识这是对哪一次 ToolCall 的结果响应


class Tool(Protocol):
    name: str  # 工具名，供模型和注册中心按名引用
    description: str  # 给模型看的自然语言描述，说明这个工具是干什么的
    parameters_schema: dict  # JSON Schema，描述 execute 期望的 arguments 结构

    def execute(self, arguments: dict) -> ToolResult:
        """执行工具；工具自身负责把可预期的失败（文件不存在、匹配失败等）转成
        ok=False 的 ToolResult，而不是抛异常"""
        ...


class Provider(Protocol):
    def stream_chat(self, messages: list[Message], tools: list[Tool] | None = None) -> Iterator[StreamEvent]:
        """发送带完整历史的对话请求；tools 非空时把工具描述一并发给模型，
        并在流式响应中识别、拼接、产出 TOOL_CALL 事件"""
        ...
```

要点说明：
- `Provider.stream_chat` 新增可选的 `tools` 参数，向后兼容——不传时行为与第一期完全一致（对应 N4）
- `TOOL_CALL` 事件携带的 `ToolCall.arguments` 已经是拼接、解析完的字典，Provider 内部负责处理协议各自的 JSON 碎片流；这个拼接细节不会泄漏给 tui/session 等上层模块
- `Message` 用一个类型承载三种历史条目（user 文本、assistant 文本+/工具调用、tool 结果），具体怎么翻译成 Anthropic/OpenAI 各自的请求体格式，是各 Provider 实现内部的事——session/tui 层不需要关心协议差异
- `Tool.execute` 约定：可预期的业务失败（文件不存在、路径越界、edit 匹配失败/多次等）由工具自己捕获并转成 `ToolResult(ok=False, ...)`；只有真正意外的异常才会冒泡到 registry 层被兜底捕获（对应 N2/AC13 的两道防线）

## 模块设计

### tools 模块（`tools/`，新增）

**职责：** 定义 Tool 协议、六个具体工具实现、注册中心（按名查找/执行/统一异常兜底）、协议描述转换、共享的工作目录路径安全校验

**对外接口：**
```python
# tools/base.py
class Tool(Protocol):
    name: str
    description: str
    parameters_schema: dict

    def execute(self, arguments: dict) -> ToolResult: ...


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    ok: bool
    content: str


# tools/registry.py
class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool:
        """找不到抛 ToolError"""

    def list_tools(self) -> list[Tool]: ...
    def execute(self, name: str, arguments: dict) -> ToolResult:
        """按名查找并执行；工具内部未捕获的异常在这里兜底捕获，
        转成 ok=False 的 ToolResult（对应 N2/AC13 的第二道防线）"""


# tools/schema.py
def to_anthropic_tool_schema(tools: list[Tool]) -> list[dict]: ...
def to_openai_tool_schema(tools: list[Tool]) -> list[dict]: ...


# tools/paths.py
class PathOutsideWorkspaceError(Exception): ...


def resolve_safe_path(workspace_root: Path, user_path: str) -> Path:
    """把 user_path 相对 workspace_root 解析成绝对路径并 resolve()；
    解析结果不在 workspace_root 之内时抛 PathOutsideWorkspaceError（对应 F8）"""
```

**六个具体工具**（均在构造时注入 `workspace_root: Path`，五个文件类工具通过 `resolve_safe_path` 统一做路径校验）：

| 工具名 | 参数 Schema（必填字段） | 行为要点 |
|---|---|---|
| `read_file` | `path: string` | 读取文件全文；不存在/是目录 → `ok=False` |
| `write_file` | `path: string`, `content: string` | 新建/覆盖写入，自动创建缺失的父目录 |
| `edit_file` | `path: string`, `old_text: string`, `new_text: string` | 统计 `old_text` 在文件中出现次数：0 次 → "未找到匹配"；>1 次 → "匹配到 N 处，无法确定替换位置"；恰好 1 次才真正替换写回（对应 F7/AC3-AC5） |
| `execute_command` | `command: string` | `subprocess.run(command, shell=True, cwd=workspace_root, capture_output=True, text=True, timeout=30)`；捕获 `TimeoutExpired` → 超时错误；非零退出码 → `ok=False` 但仍附带 stdout/stderr 供模型判断原因（对应 F9/N3/AC6-AC7） |
| `glob_files` | `pattern: string` | `workspace_root.glob(pattern)`，跳过 `.git`/`__pycache__`/`.venv`/`node_modules`，按相对路径排序返回 |
| `grep_content` | `pattern: string`（正则）, `file_glob: string`（可选，默认 `*`） | 遍历 `workspace_root.rglob(file_glob)`（跳过同上忽略目录、非文本/解码失败的文件），逐行 `re.search`，命中返回 `相对路径:行号: 内容` |

**依赖：** 标准库（`pathlib`、`subprocess`、`re`）；不引入额外第三方包

### providers 模块（扩展）

**职责变化：** `stream_chat` 增加可选 `tools` 参数；两个具体 Provider 各自负责（1）把 `Tool` 列表通过 `tools/schema.py` 转成协议要求的格式随请求发出；（2）把 `Message` 列表（含 assistant 的 tool_calls、role="tool" 的结果）翻译成协议要求的历史格式；（3）从流式响应中识别工具调用意图，按协议各自的分片方式拼接参数 JSON，拼完整后 `yield StreamEvent(TOOL_CALL, tool_call=...)`

**Anthropic 侧拼接方式：** 用 `content_block_start`（`type=="tool_use"` 时记下该 index 的 `id`/`name`）+ 多个 `content_block_delta`（`delta.type=="input_json_delta"` 时把 `delta.partial_json` 按 index 累加到一个字符串缓冲区）+ `content_block_stop`（该 index 的 JSON 字符串拼接完成，`json.loads` 得到 `arguments`，产出 `TOOL_CALL` 事件）。文本内容块（`type=="text"`）继续走原来的 `text_delta` 逻辑，二者按 index 互不干扰。

**OpenAI 侧拼接方式：** 每个 `chunk.choices[0].delta.tool_calls` 是按 `index` 分片的增量：首个分片带 `id` 和 `function.name`，后续分片持续追加 `function.arguments` 字符串片段。用一个以 `index` 为 key 的字典累积，流结束时对每个累积项 `json.loads(arguments_buffer)`，逐个产出 `TOOL_CALL` 事件，再产出 `DONE`。

**依赖：** 官方 `anthropic`/`openai` SDK（不变，两者的流式事件本身已包含工具调用相关字段，不用手写 SSE 解析）

### session 模块（扩展）

**职责变化：** 新增两类历史条目的写入方法，`get_messages()` 不变

```python
class ConversationSession:
    def add_user_message(self, content: str) -> None: ...
    def add_assistant_message(self, content: str) -> None: ...
    def add_assistant_tool_call_message(self, content: str, tool_calls: list[ToolCall]) -> None: ...
        """模型这一步请求了工具调用（可能同时带文字）"""
    def add_tool_result_message(self, tool_call_id: str, content: str) -> None: ...
        """把某次 ToolCall 的执行结果写回历史，role="tool" """
    def get_messages(self) -> list[Message]: ...
```

### agent 模块（`agent.py`，新增）

**职责：** 编排"一轮对话"的完整流程——首次请求、识别工具调用、执行工具、写回历史、自动发起不带工具的追加请求拿最终回复；对外只产出一串可直接渲染的事件，不直接碰 `Console`

**对外接口：**
```python
class TurnEventType(Enum):
    THINKING_DELTA = "thinking_delta"
    TEXT_DELTA = "text_delta"
    TOOL_STARTED = "tool_started"  # 携带 tool_name、tool_arguments
    TOOL_FINISHED = "tool_finished"  # 携带 tool_name、tool_result
    ERROR = "error"
    DONE = "done"


@dataclass
class TurnEvent:
    type: TurnEventType
    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Optional[dict] = None
    tool_result: Optional[ToolResult] = None
    error_message: Optional[str] = None


def run_turn(
    provider: Provider,
    tool_registry: ToolRegistry,
    session: ConversationSession,
    user_text: str,
) -> Iterator[TurnEvent]:
    """把 user_text 加入历史；调用一次带 tools 的 stream_chat；
    把 THINKING_DELTA/TEXT_DELTA/ERROR 原样转发成 TurnEvent；
    收集期间出现的 TOOL_CALL；流结束后：
      - 没有工具调用 → 把回复文字存入历史，yield DONE，结束
      - 有工具调用 → 把 assistant 的工具调用消息存入历史；
        逐个执行工具（yield TOOL_STARTED → registry.execute → yield TOOL_FINISHED），
        每个结果存入历史为 role="tool" 消息；
        再调用一次 stream_chat（这次不传 tools，从协议层面保证不会再产生新的工具调用）；
        转发这次的 THINKING_DELTA/TEXT_DELTA/ERROR，把最终文字存入历史，yield DONE"""
```

**依赖：** providers 模块（`Provider`、`StreamEvent`）、tools 模块（`ToolRegistry`、`ToolCall`、`ToolResult`）、session 模块

### tui 模块（简化）

**职责变化：** 不再直接消费 `StreamEventType`，改为对每次用户输入调用 `agent.run_turn(...)`，按 `TurnEventType` 把内容渲染到终端（`TOOL_STARTED`/`TOOL_FINISHED` 用专门样式打印工具名/参数/结果摘要，区别于 thinking/正式回答）；`/exit`、Ctrl+D、空输入等既有逻辑不变

### cli 入口（扩展）

**职责变化：** 组装阶段额外创建 `ToolRegistry`，实例化并注册六个工具（`workspace_root=Path.cwd()`），把 registry 一并传给后续的对话循环

## 模块交互

**启动阶段（扩展）：**
```
__main__.main()
  → argparse 解析 --config / --provider（不变）
  → config.load_provider_configs(path) / select_provider_config(...)（不变）
  → providers.factory.create_provider(config)（不变）
  → 新建 ToolRegistry，register 六个工具实例（workspace_root=Path.cwd()）
  → session.ConversationSession()（不变）
  → tui.run_repl(provider, tool_registry, session)
```

**每一轮对话（tui.run_repl 内部循环）：**
```
prompt_toolkit 读取一行用户输入
  → 退出指令 → 结束循环
  → for turn_event in agent.run_turn(provider, tool_registry, session, text):
        THINKING_DELTA / TEXT_DELTA → 沿用现有流式打印样式
        TOOL_STARTED   → 打印"→ 执行 {tool_name}({tool_arguments})"
        TOOL_FINISHED  → 打印结果摘要（成功/失败 + tool_result.content，失败用醒目样式）
        ERROR          → 打印可读错误信息（不崩溃，回到循环顶部等下一次输入）
        DONE           → 这一轮渲染结束，回到循环顶部
```

**agent.run_turn 内部（新增的核心编排逻辑）：**
```
session.add_user_message(user_text)
tool_calls = []
for event in provider.stream_chat(session.get_messages(), tools=tool_registry.list_tools()):
    THINKING_DELTA / TEXT_DELTA → yield 对应 TurnEvent，同时累积文字到 buffer
    TOOL_CALL → 累积到 tool_calls 列表
    ERROR     → yield TurnEvent(ERROR)，session 不记录本轮，直接 return
    DONE      → 跳出

若 tool_calls 为空:
    session.add_assistant_message(buffer)
    yield TurnEvent(DONE); return

session.add_assistant_tool_call_message(buffer, tool_calls)
for tc in tool_calls:
    yield TurnEvent(TOOL_STARTED, tool_name=tc.name, tool_arguments=tc.arguments)
    result = tool_registry.execute(tc.name, tc.arguments)
    yield TurnEvent(TOOL_FINISHED, tool_name=tc.name, tool_result=result)
    session.add_tool_result_message(tc.id, result.content)

final_buffer = ""
for event in provider.stream_chat(session.get_messages(), tools=None):  # 不带 tools，硬性禁止再次调用工具
    THINKING_DELTA / TEXT_DELTA → yield 对应 TurnEvent，累积到 final_buffer
    ERROR → yield TurnEvent(ERROR); return
    DONE  → 跳出
session.add_assistant_message(final_buffer)
yield TurnEvent(DONE)
```

**异常兜底（扩展）：** `create_provider`/`load_provider_configs`/`select_provider_config` 抛出的 `ConfigError` 仍在 `main()` 最外层捕获（不变）；单轮请求内的网络/鉴权错误仍在 `run_turn` 内部就地处理成 `ERROR` 事件（不变）；工具执行内部的未预期异常在 `ToolRegistry.execute` 里兜底捕获成 `ToolResult(ok=False, ...)`，不会向上抛出到 `run_turn` 或 `tui`（对应 N2/AC13 新增的一层）。

## 文件组织

```
sirius-agent/
├── docs/
│   └── 02-tools/
│       ├── spec.md / plan.md / task.md / checklist.md   — 本期文档
├── src/
│   └── sirius-agent/
│       ├── __main__.py            — 扩展：额外组装 ToolRegistry 并注册六个工具
│       ├── config.py               — 不变
│       ├── session.py               — 扩展：新增 tool 相关的两个写入方法
│       ├── agent.py                 — 新增：TurnEvent/TurnEventType + run_turn()
│       ├── tui.py                    — 简化：改为消费 TurnEvent 并渲染
│       └── providers/
│           ├── base.py               — 扩展：StreamEvent 加 TOOL_CALL；stream_chat 签名加 tools 参数
│           ├── factory.py             — 不变
│           ├── anthropic_provider.py  — 扩展：工具 schema 组装 + tool_use 流式块拼接解析
│           └── openai_provider.py     — 扩展：工具 schema 组装 + tool_calls 分片拼接解析
│       └── tools/
│           ├── __init__.py
│           ├── base.py                — 新增：Tool Protocol、ToolCall、ToolResult
│           ├── registry.py             — 新增：ToolRegistry
│           ├── schema.py               — 新增：两个协议的 schema 转换函数
│           ├── paths.py                — 新增：resolve_safe_path、PathOutsideWorkspaceError
│           ├── read_file.py            — 新增：ReadFileTool
│           ├── write_file.py           — 新增：WriteFileTool
│           ├── edit_file.py            — 新增：EditFileTool
│           ├── execute_command.py      — 新增：ExecuteCommandTool
│           ├── glob_files.py           — 新增：GlobFilesTool
│           └── grep_content.py         — 新增：GrepContentTool
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 工具调用编排位置 | 新增 `agent.py`，产出 `TurnEvent` 生成器流，`tui.py` 只负责渲染 | 一轮对话现在可能触发两次 `stream_chat` 调用 + N 次工具执行，逻辑明显超出"读输入/渲染"的范畴；独立出来后 `tui.py` 保持第一期的薄职责，`agent.py` 可以脱离终端单独做单元测试 |
| 工具参数 Schema 格式 | 直接用 JSON Schema（`dict`） | Anthropic/OpenAI 的 `tools` 参数本身就要求 JSON Schema，直接复用，不必再发明一套自定义类型系统再做转换 |
| 强制"这一轮不循环"的实现方式 | 追加的最终请求不传 `tools` 参数 | 从协议层面让模型在这次请求里根本拿不到工具列表，比"应用层看到 TOOL_CALL 就忽略"更彻底可靠，避免模型仍尝试调用却被静默丢弃造成的语义割裂 |
| 路径安全边界实现 | 五个文件类工具统一调用 `tools/paths.py` 的 `resolve_safe_path`（`Path.resolve()` 后判断是否在 `workspace_root` 之内） | 集中一处实现和测试，避免每个工具各自重复写容易出错的路径穿越判断逻辑 |
| `execute_command` 超时实现 | `subprocess.run(..., timeout=30, capture_output=True, text=True)`，捕获 `TimeoutExpired` | 标准库自带，跨平台（含 Windows），不需要额外依赖或手写进程管理/信号处理 |
| `glob_files`/`grep_content` 默认忽略目录 | 硬编码跳过 `.git`/`__pycache__`/`.venv`/`node_modules` | 避免每次搜索都被虚拟环境和缓存目录的海量文件淹没；等以后有真实需要再做成可配置项，符合 YAGNI |
| Message 承载工具相关信息的方式 | 单一 `Message` 类型加 `tool_calls`/`tool_call_id` 可选字段，而非拆分多个子类型 | 与第一期 `Message` 保持同一形态，`session`/`tui` 层不需要 `isinstance` 分支；具体怎么翻译成两家协议各自的请求体格式，收敛在各 Provider 内部 |
