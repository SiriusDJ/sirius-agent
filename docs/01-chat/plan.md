# Sirius-Agent（终端流式对话）Plan

## 架构概览

整体分五个模块：

- **config 模块**：加载 YAML 配置文件，展开 `${ENV_VAR}` 占位符，按命令行指定的 `name`（或默认第一个）选出要用的供应商配置，并校验必需字段是否齐全
- **providers 模块**：定义统一的 Provider 抽象和归一化的流式事件类型；`AnthropicProvider`、`OpenAIProvider` 两个具体实现，各自负责认证、组装请求、解析各自的 SSE 格式，并把结果翻译成统一事件
- **session 模块**：维护本次运行期间的对话历史（纯内存列表），供每次请求把历史一并带上
- **tui 模块**：基于 prompt_toolkit 读取用户输入、基于 rich 渲染流式输出（用不同样式区分 thinking 和正式回答），驱动"读输入 → 调用 provider → 渲染 → 存历史"的主循环
- **cli 入口**：解析 `--config`、`--provider` 命令行参数，把以上模块组装起来跑起来，并在最外层兜底捕获异常转成用户可读的错误提示

## 核心数据结构

```python
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional, Protocol


class StreamEventType(Enum):
    TEXT_DELTA = "text_delta"          # 正式回答的文本增量
    THINKING_DELTA = "thinking_delta"  # extended thinking 的文本增量
    DONE = "done"                      # 本轮回复结束
    ERROR = "error"                    # 请求过程中出错


@dataclass
class StreamEvent:
    type: StreamEventType
    text: Optional[str] = None          # TEXT_DELTA / THINKING_DELTA 时携带增量内容
    error_message: Optional[str] = None  # ERROR 时携带可读错误信息


@dataclass
class Message:
    role: str      # "user" 或 "assistant"
    content: str


@dataclass
class ProviderConfig:
    name: str
    protocol: str        # "anthropic" 或 "openai"
    model: str
    base_url: str
    api_key: str
    thinking: bool = False


class Provider(Protocol):
    """所有供应商协议实现都要满足这个接口"""

    def stream_chat(self, messages: list[Message]) -> Iterator[StreamEvent]:
        """发送带完整历史的对话请求，逐个产出归一化的流式事件"""
        ...
```

要点说明：
- `Provider` 用 `typing.Protocol`（结构化子类型），不强制继承，符合 Python 惯用法
- `stream_chat` 是一个生成器方法：调用方用 `for event in provider.stream_chat(messages)` 自然地消费流式数据
- `StreamEvent` 是 Anthropic/OpenAI 两家原始 SSE 格式的"公约数"，`THINKING_DELTA` 只有 Anthropic 在 `thinking: true` 时会产出，OpenAI 的实现永远不会发出这个类型

## 模块设计

### config 模块（`config.py`）
**职责：** 加载并解析 YAML 配置文件，展开环境变量占位符，选出目标供应商配置，做基础校验
**对外接口：**
```python
def load_provider_configs(path: str) -> list[ProviderConfig]: ...

def select_provider_config(
    configs: list[ProviderConfig], name: Optional[str]
) -> ProviderConfig:
    """name 为 None 时返回 configs[0]；否则按 name 精确匹配，找不到抛 ConfigError"""

class ConfigError(Exception):
    """配置缺字段、协议不支持、文件不存在、name 匹配不到等场景统一抛这个"""
```
**依赖：** PyYAML、标准库 `os.environ`（做 `${VAR}` 展开）

### providers 模块（`providers/`）
**职责：** 提供统一 Provider 接口定义 + 两个具体协议实现 + 一个按 protocol 字符串创建实例的工厂
**对外接口：**
```python
# providers/base.py: StreamEvent, StreamEventType, Message, Provider（见上一段）

# providers/factory.py
def create_provider(config: ProviderConfig) -> Provider:
    """按 config.protocol 返回 AnthropicProvider 或 OpenAIProvider 实例；
    不支持的 protocol 抛 ConfigError"""

# providers/anthropic_provider.py
class AnthropicProvider:
    def __init__(self, config: ProviderConfig): ...
    def stream_chat(self, messages: list[Message]) -> Iterator[StreamEvent]: ...

# providers/openai_provider.py
class OpenAIProvider:
    def __init__(self, config: ProviderConfig): ...
    def stream_chat(self, messages: list[Message]) -> Iterator[StreamEvent]: ...
```
**依赖：** 官方 `anthropic` SDK、官方 `openai` SDK（两者都内置流式/SSE 支持，不用手写 SSE 解析）

### session 模块（`session.py`）
**职责：** 维护本次进程运行期间的对话历史（内存，不持久化）
**对外接口：**
```python
class ConversationSession:
    def add_user_message(self, content: str) -> None: ...
    def add_assistant_message(self, content: str) -> None: ...
    def get_messages(self) -> list[Message]: ...
```
**依赖：** 无（纯内存列表）

### tui 模块（`tui.py`）
**职责：** 驱动主循环——读输入、调用 Provider、流式渲染、维护历史，直到用户退出
**对外接口：**
```python
def run_repl(provider: Provider, session: ConversationSession) -> None:
    """进入交互循环；内部处理 /exit 退出、Ctrl+D、以及单轮请求出错时的
    降级展示（打印错误、不崩溃、继续下一轮输入）"""
```
**依赖：** `prompt_toolkit`（读输入、历史）、`rich`（流式渲染、区分 thinking/正式回答样式）、providers 模块、session 模块

### cli 入口（`__main__.py`）
**职责：** 解析命令行参数，组装 config → provider → session → tui，最外层兜底异常处理
**对外接口：**
```python
def main() -> None:
    """argparse 解析 --config/--provider；
    捕获 ConfigError 打印清晰提示后以非零状态码退出（对应 N2）"""
```
**依赖：** argparse（标准库）、以上四个模块

## 模块交互

**启动阶段：**
```
__main__.main()
  → argparse 解析 --config / --provider
  → config.load_provider_configs(path)           读取 YAML，展开 ${ENV_VAR}
  → config.select_provider_config(configs, name)  选出目标供应商配置
  → providers.factory.create_provider(config)     实例化 AnthropicProvider 或 OpenAIProvider
  → session.ConversationSession()                 新建空的内存历史
  → tui.run_repl(provider, session)                进入交互循环
```

**每一轮对话（run_repl 内部循环）：**
```
prompt_toolkit 读取一行用户输入
  → 若是退出指令（/exit 或 Ctrl+D）→ 结束循环，正常退出
  → session.add_user_message(输入内容)
  → provider.stream_chat(session.get_messages())   把完整历史一并发过去
      for event in 上面这个生成器:
          THINKING_DELTA → rich 用"思考样式"流式打印 event.text
          TEXT_DELTA     → rich 用"正式回答样式"流式打印 event.text，同时累积到 buffer
          ERROR          → rich 打印可读错误信息，跳出本轮（不加入历史，不崩溃）→ 回到循环顶部等下一次输入
          DONE           → 跳出 for 循环
  → 若本轮没出错 → session.add_assistant_message(buffer 累积的完整正式回答)
  → 回到循环顶部，等待下一次输入
```

**异常兜底：** `create_provider` / `load_provider_configs` / `select_provider_config` 抛出的 `ConfigError` 在 `main()` 最外层被捕获，打印清晰错误信息后以非零状态码退出（对应 N2）；单轮请求内的错误（网络、鉴权失败等）在 `run_repl` 循环内部就地处理，不向上抛（对应 N3）。

## 文件组织

```
sirius-agent/
├── pyproject.toml              — 项目元数据、依赖声明（uv 管理）
├── sirius-agent.yaml.example        — 配置文件示例（含 anthropic/openai 两种样例）
├── spec.md / plan.md / task.md / checklist.md
└── src/
    └── sirius-agent/
        ├── __init__.py
        ├── __main__.py          — CLI 入口：argparse + 组装启动 + 顶层异常兜底
        ├── config.py            — YAML 加载、${ENV_VAR} 展开、供应商选择、ConfigError
        ├── session.py           — ConversationSession，内存对话历史
        ├── tui.py                — run_repl 主循环，prompt_toolkit 输入 + rich 流式渲染
        └── providers/
            ├── __init__.py
            ├── base.py           — StreamEvent、StreamEventType、Message、Provider Protocol
            ├── factory.py        — create_provider(config) 按 protocol 分发
            ├── anthropic_provider.py  — AnthropicProvider，封装官方 anthropic SDK 流式调用
            └── openai_provider.py     — OpenAIProvider，封装官方 openai SDK 流式调用
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 调用 API 的方式 | 官方 `anthropic` / `openai` Python SDK 的流式接口 | 官方 SDK 已封装好 SSE 解析、认证头、重试等细节，比手写 httpx + SSE 解析更省心，且随官方 API 变化自动同步 |
| Provider 抽象方式 | `typing.Protocol`（结构化子类型） | 比 ABC 继承更轻量，符合 Python 惯用法，不强制层级关系 |
| 流式数据传递方式 | 生成器（`yield StreamEvent`） | 调用方用 `for event in ...` 自然消费，代码线性易读，无需回调/事件总线 |
| 配置解析与校验 | PyYAML + 手写 dataclass 校验（不引入 pydantic） | 项目规模小、字段少，标准库+PyYAML 足够，遵循 YAGNI |
| 命令行参数解析 | `argparse`（标准库） | 只有 `--config`、`--provider` 两个参数，不需要 click/typer 这类重量级框架 |
| TUI 技术栈 | prompt_toolkit（输入）+ rich（渲染） | 前面已确认的组合，是 Python CLI AI 工具的主流做法 |
