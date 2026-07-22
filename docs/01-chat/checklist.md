# Sirius-Agent（终端流式对话）Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [x] config 模块能加载 YAML 并正确解析出 ProviderConfig 列表（验证：T3 的单元测试通过）
- [x] Provider 统一接口对两种协议均有效（验证：AnthropicProvider、OpenAIProvider 都能正确实现 stream_chat 并产出 StreamEvent 序列）
- [x] session 模块正确维护消息历史顺序与 role（验证：T8 的单元测试通过）
- [x] tui 主循环能正确路由四种 StreamEvent 类型到对应渲染逻辑（验证：用假 Provider 跑 T9 的验证步骤）

## 集成
- [x] `__main__.main()` 能把 config → provider → session → tui 完整串起来跑起来（验证：`uv run sirius-agent` 能进入交互提示符）
- [x] 新增协议后端时无需改动 tui.py（验证：审查 tui.py 代码，确认其中不出现 "anthropic"/"openai" 等协议专属字符串或 import）

## 编译与运行
- [x] `uv sync` 成功安装全部依赖，无报错
- [x] `uv run python -c "import sirius_agent"` 无 ImportError
- [x] `uv run sirius-agent --config not_exist.yaml` 打印清晰错误信息并以非零状态码退出

## 端到端场景（对应 spec.md AC1-AC11）
- [x] 场景 1（AC1/AC2）：在含 anthropic 配置的目录下运行 `uv run sirius-agent`（不带任何参数），输入一句话，终端逐字/逐块流式打印回复（而非一次性整段出现）
- [x] 场景 2（AC3）：连续对话至少 3 轮，AI 的回复能正确引用更早轮次提到的信息
- [x] 场景 3（AC4）：分别把 sirius-agent.yaml 的 protocol 设为 anthropic 和 openai（各自配对正确的 base_url/api_key/model），两种情况下均能完成一次完整流式对话，不改代码
- [x] 场景 4（AC5）：`sirius-agent --provider <存在的name>` 确实使用了该供应商而非配置文件中的第一个
- [x] 场景 5（AC6）：`sirius-agent --config <非默认路径>` 能从该路径正确加载配置
- [x] 场景 6（AC7）：protocol 为 anthropic 且 thinking: true 时，能看到思考过程以不同样式流式展示，且先于正式回答出现
- [x] 场景 7（AC8）：api_key 写成 `${SOME_ENV_VAR}`，设置好环境变量后能正确鉴权发出请求；设置错误值时能正确触发鉴权失败
- [x] 场景 8（AC9）：故意填错 api_key/触发 API 错误，程序打印可读错误信息、不崩溃，并能继续下一轮输入或正常退出
- [x] 场景 9（AC10）：故意提供缺字段的配置文件，程序打印清晰错误提示后退出，没有原始 Python 异常堆栈
- [x] 场景 10（AC11）：输入 `/exit` 和用 Ctrl+D（Windows 上为 Ctrl+Z + 回车）两种方式都能让程序正常退出、返回 shell
