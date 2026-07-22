# Sirius-Agent（终端流式对话）Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | 项目元数据、依赖声明 |
| 新建 | `sirius-agent.yaml.example` | 配置文件示例 |
| 新建 | `src/sirius_agent/__init__.py` | 包标识 |
| 新建 | `src/sirius_agent/providers/__init__.py` | 包标识 |
| 新建 | `src/sirius_agent/providers/base.py` | StreamEvent、StreamEventType、Message、Provider Protocol |
| 新建 | `src/sirius_agent/config.py` | ProviderConfig、ConfigError、YAML 加载、环境变量展开、供应商选择 |
| 新建 | `src/sirius_agent/providers/anthropic_provider.py` | AnthropicProvider（含 thinking 支持） |
| 新建 | `src/sirius_agent/providers/openai_provider.py` | OpenAIProvider |
| 新建 | `src/sirius_agent/providers/factory.py` | create_provider 按 protocol 分发 |
| 新建 | `src/sirius_agent/session.py` | ConversationSession |
| 新建 | `src/sirius_agent/tui.py` | run_repl 主循环 |
| 新建 | `src/sirius_agent/__main__.py` | CLI 入口 |

## T1: 初始化项目骨架

**文件：** `pyproject.toml`, `src/sirius_agent/__init__.py`, `src/sirius_agent/providers/__init__.py`
**依赖：** 无
**步骤：**
1. 用 `uv init` 或手写 `pyproject.toml`，声明项目名 `sirius-agent`、Python 版本下限，依赖列表：`anthropic`、`openai`、`pyyaml`、`prompt_toolkit`、`rich`
2. 配置 `[project.scripts]`，把 `sirius-agent` 命令指向 `sirius_agent.__main__:main`
3. 创建空的 `src/sirius_agent/__init__.py` 和 `src/sirius_agent/providers/__init__.py`
4. 创建 `src/sirius_agent/providers/` 目录

**验证：** `uv sync` 成功装完依赖无报错；`uv run python -c "import sirius_agent"` 不报 ImportError

## T2: 定义 Provider 统一接口与基础类型

**文件：** `src/sirius_agent/providers/base.py`
**依赖：** T1
**步骤：**
1. 定义 `StreamEventType` 枚举：`TEXT_DELTA`、`THINKING_DELTA`、`DONE`、`ERROR`
2. 定义 `StreamEvent` dataclass：`type`、`text: Optional[str]`、`error_message: Optional[str]`
3. 定义 `Message` dataclass：`role: str`、`content: str`
4. 定义 `Provider` Protocol：`stream_chat(self, messages: list[Message]) -> Iterator[StreamEvent]`
5. 所有类型和字段加中文注释说明用途

**验证：** `uv run python -c "from sirius_agent.providers.base import StreamEvent, StreamEventType, Message, Provider"` 无报错

## T3: 实现配置加载（config 模块）

**文件：** `src/sirius_agent/config.py`
**依赖：** T1
**步骤：**
1. 定义 `ProviderConfig` dataclass：`name`、`protocol`、`model`、`base_url`、`api_key`、`thinking: bool = False`
2. 定义 `ConfigError(Exception)`
3. 实现内部函数 `_expand_env_placeholders(value: str) -> str`：识别 `${VAR}` 形式并用 `os.environ` 展开；环境变量不存在时抛 `ConfigError`，报错信息里写清楚是哪个变量缺失
4. 实现 `load_provider_configs(path: str) -> list[ProviderConfig]`：
   - 文件不存在时抛 `ConfigError`（信息含路径）
   - 用 PyYAML 解析；每个供应商条目校验六个字段是否齐全（`thinking` 允许缺省为 `False`），缺失字段时抛 `ConfigError`（信息含供应商 name 和缺失字段名）
   - 对 `api_key` 字段调用 `_expand_env_placeholders`
   - `protocol` 只接受 `"anthropic"` 或 `"openai"`，否则抛 `ConfigError`
5. 实现 `select_provider_config(configs, name: Optional[str]) -> ProviderConfig`：`name` 为 `None` 返回 `configs[0]`；否则精确匹配，找不到抛 `ConfigError`（信息含可用的 name 列表）

**验证：** 写一个临时测试 YAML（含一条 anthropic 配置、`api_key: "${TEST_SIRIUS_AGENT_KEY}"`），设置环境变量后调用 `load_provider_configs` 断言返回值字段正确；再测试缺字段、错误 protocol、name 匹配不到三种场景确实抛出 `ConfigError`

## T4: 编写配置文件示例

**文件：** `sirius-agent.yaml.example`
**依赖：** T3
**步骤：**
1. 写两条供应商配置样例：一条 `protocol: anthropic`（含 `thinking: true` 示例），一条 `protocol: openai`
2. `api_key` 字段用 `${ANTHROPIC_API_KEY}` / `${OPENAI_API_KEY}` 占位符演示用法
3. 补充顶部注释说明每个字段的含义（中文）

**验证：** 用 `load_provider_configs` 加载这个示例文件（对应环境变量随便设个测试值），能成功解析出两条 `ProviderConfig`，无异常

## T5: 实现 Anthropic Provider

**文件：** `src/sirius_agent/providers/anthropic_provider.py`
**依赖：** T2
**步骤：**
1. 定义 `AnthropicProvider`，构造函数接收 `ProviderConfig`，内部用 `config.api_key`、`config.base_url` 初始化官方 `anthropic.Anthropic` 客户端
2. 实现 `stream_chat(messages)`：把 `Message` 列表转换成 Anthropic Messages API 所需的格式，调用其流式接口（`client.messages.stream(...)`），若 `config.thinking` 为真则在请求里开启 extended thinking 参数
3. 遍历 SDK 返回的流式事件，把 thinking 增量翻译成 `StreamEvent(type=THINKING_DELTA, text=...)`，正文增量翻译成 `StreamEvent(type=TEXT_DELTA, text=...)`，流结束时 yield `StreamEvent(type=DONE)`
4. 用 `try/except` 包住网络调用，捕获异常后 yield `StreamEvent(type=ERROR, error_message=...)` 并结束生成器（不再往外抛）

**验证：** 用真实（或临时测试用）Anthropic API key 手动调用 `stream_chat([Message("user","你好")])`，遍历打印每个 `StreamEvent`，能看到 `TEXT_DELTA` 序列并以 `DONE` 结束；把 `thinking` 设为真时能看到 `THINKING_DELTA` 事件

## T6: 实现 OpenAI Provider

**文件：** `src/sirius_agent/providers/openai_provider.py`
**依赖：** T2
**步骤：**
1. 定义 `OpenAIProvider`，构造函数接收 `ProviderConfig`，内部用 `config.api_key`、`config.base_url` 初始化官方 `openai.OpenAI` 客户端
2. 实现 `stream_chat(messages)`：把 `Message` 列表转换成 Chat Completions 所需的 `messages` 格式，调用 `client.chat.completions.create(..., stream=True)`
3. 遍历返回的 chunk，把 `delta.content` 非空的部分翻译成 `StreamEvent(type=TEXT_DELTA, text=...)`；流结束时 yield `StreamEvent(type=DONE)`（OpenAI 分支不产出 `THINKING_DELTA`）
4. 同 T5，网络/鉴权异常一律转换成 `StreamEvent(type=ERROR, ...)`，不向外抛

**验证：** 用真实（或临时测试用）OpenAI API key 手动调用 `stream_chat([Message("user","你好")])`，能看到 `TEXT_DELTA` 序列并以 `DONE` 结束

## T7: 实现 Provider 工厂

**文件：** `src/sirius_agent/providers/factory.py`
**依赖：** T5, T6
**步骤：**
1. 实现 `create_provider(config: ProviderConfig) -> Provider`：`protocol == "anthropic"` 返回 `AnthropicProvider(config)`，`protocol == "openai"` 返回 `OpenAIProvider(config)`，其余情况抛 `ConfigError`（理论上 T3 已校验过，这里是防御性兜底）

**验证：** 分别构造两种 `ProviderConfig`，`create_provider` 返回对应类型的实例；传入非法 protocol 抛 `ConfigError`

## T8: 实现对话历史管理（session 模块）

**文件：** `src/sirius_agent/session.py`
**依赖：** T2
**步骤：**
1. 定义 `ConversationSession`，内部维护 `_messages: list[Message]`
2. 实现 `add_user_message(content)`、`add_assistant_message(content)`：各自 append 对应 role 的 `Message`
3. 实现 `get_messages() -> list[Message]`：返回历史列表的拷贝（防止外部误改内部状态）

**验证：** 新建实例，依次 add 两轮用户/助手消息，`get_messages()` 返回按顺序排列、role 正确的 4 条消息

## T9: 实现 TUI 主循环

**文件：** `src/sirius_agent/tui.py`
**依赖：** T2, T7, T8
**步骤：**
1. 用 `prompt_toolkit.PromptSession` 创建带历史记录的输入会话
2. 实现 `run_repl(provider, session)`：进入 `while True` 循环，`prompt_session.prompt("> ")` 读一行输入
3. 输入为 `/exit` 或读到 EOF（`Ctrl+D` 触发的 `EOFError`）时打印告别语并 `return`，正常结束循环
4. 非退出输入：`session.add_user_message(text)`，调用 `provider.stream_chat(session.get_messages())`
5. 用 `rich.console.Console` 遍历返回的事件：`THINKING_DELTA` 用暗色/斜体样式流式打印，`TEXT_DELTA` 用默认样式流式打印并累积到本地 buffer 字符串，`ERROR` 用红色打印 `error_message` 并 `continue` 到下一轮循环（不写入历史）
6. 循环正常遇到 `DONE` 后，若 buffer 非空则 `session.add_assistant_message(buffer)`

**验证：** 用一个假的 `Provider`（`stream_chat` 直接 yield 几个预设的 `StreamEvent`，不发真实网络请求）跑 `run_repl`，人工输入验证：文本正常流式打印、thinking 样式和正文样式有区别、输入 `/exit` 能正常退出

## T10: 实现 CLI 入口

**文件：** `src/sirius_agent/__main__.py`
**依赖：** T3, T7, T8, T9
**步骤：**
1. 用 `argparse` 定义 `--config`（默认值 `"sirius-agent.yaml"`）和 `--provider`（默认值 `None`）两个可选参数
2. 实现 `main()`：解析参数 → `try` 块内依次调用 `load_provider_configs` → `select_provider_config` → `create_provider` → 新建 `ConversationSession` → `run_repl`
3. `except ConfigError as e`：打印形如 `错误：{e}` 的可读信息到 stderr，`sys.exit(1)`
4. 加 `if __name__ == "__main__": main()`，并在 `pyproject.toml` 的 `[project.scripts]` 里确认 `sirius-agent` 命令指向这里（T1 已配置，此处复核一致）

**验证：** `uv run sirius-agent --config not_exist.yaml` 打印清晰错误信息并以非零状态码退出（`echo $?` 验证）；用一份合法配置文件加真实 API key 跑 `uv run sirius-agent`，能进入交互提示符

## T11: 端到端手动验证

**文件：** 无新建文件（用已完成的程序）
**依赖：** T10
**步骤：**
1. 准备一份含真实 Anthropic 和 OpenAI 两条配置的 `sirius-agent.yaml`（`api_key` 用环境变量占位符），设置好对应环境变量
2. 按项目 CLAUDE.md 约定，在 tmux 中启动 `uv run sirius-agent`
3. 连续输入至少 3 轮对话，观察流式打印效果和上下文记忆是否生效
4. 切换 `--provider` 到 OpenAI 那条配置，重复一遍
5. 测试 `thinking: true` 场景下能看到不同样式的思考过程
6. 故意用错误 api_key 触发一次鉴权失败，观察程序是否优雅报错而不崩溃

**验证：** 对照 checklist.md（下一阶段产出）逐项打勾

## 执行顺序

```
T1 → T2 ─┬→ T5 ─┐
         ├→ T6 ─┼→ T7 ─┐
         └→ T8 ─┘      ├→ T9 → T10 → T11
                        │
              T3 → T4   │（T3 与 T2/T5/T6/T7/T8 并行）
```
