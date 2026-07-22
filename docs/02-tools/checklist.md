# Sirius-Agent（工具系统）Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [x] 六个工具（read_file/write_file/edit_file/execute_command/glob_files/grep_content）均正确实现 Tool 接口且可独立执行（验证：T3-T8 单元测试全部通过）
- [x] ToolRegistry 能注册、按名查找、执行工具，并对工具内部未预期异常兜底捕获（验证：T10 单元测试通过）
- [x] AnthropicProvider、OpenAIProvider 均能组装工具 schema、翻译含工具调用的 Message 历史、从流式响应中正确拼接解析出工具调用（验证：T12/T13 单元测试通过）
- [x] session 能正确记录"assistant 请求工具调用"和"tool 执行结果"两类新历史条目（验证：T14 单元测试通过）
- [x] agent.run_turn 能正确编排单轮工具往返，且第二次追加请求强制不带 tools 参数（验证：T15 单元测试通过）

## 集成
- [x] tui.py 主循环改为消费 agent.run_turn 产出的 TurnEvent，能正确渲染工具名/参数/结果与最终回复（验证：T16 单元测试；手动运行观察终端输出待端到端阶段确认）
- [x] `__main__.main()` 完整串起 config → provider → ToolRegistry（含六个工具）→ session → tui（验证：T17 单元测试确认组装正确；`uv run sirius-agent` 实际启动待端到端阶段确认）
- [x] 新增一个工具后无需改动 tui.py/agent.py（验证：审查代码，确认 tui.py/agent.py 不出现具体工具名字符串或工具专属逻辑，只依赖 Tool/ToolRegistry 抽象）

## 编译与运行
- [x] `uv sync` 成功安装依赖，无报错（本期未新增第三方依赖）
- [x] `uv run python -c "import sirius_agent"` 无 ImportError
- [x] T1-T17 对应的全部单元测试批量运行通过，无失败用例

## 端到端场景（对应 spec.md AC1-AC13）
- [x] 场景1（AC1）：要求模型读一个已存在的文件，模型调用 read_file 拿到内容并在最终回复中正确体现
- [x] 场景2（AC2）：要求模型创建一个新文件并写入内容，磁盘上该文件真实存在且内容与预期一致（`hello.txt`）
- [x] 场景3（AC3）：要求模型修改文件中唯一出现的一段文本，edit_file 正确定位替换，文件内容按预期变化（`test.txt` 的 quick→slow；模型第一轮先 read_file 确认内容，第二轮用户复述后才调用 edit_file 完成替换——两轮工具调用是本期"不做 Agent Loop"的已知边界，非缺陷）
- [x] 场景4（AC4）：对文件中出现多次的文本执行 edit_file，返回清晰的"匹配到多处"错误，文件不被改动
- [x] 场景5（AC5）：对文件中不存在的文本执行 edit_file，返回清晰的"未找到匹配"错误，文件不被改动
- [x] 场景6（AC6）：要求模型执行一个命令（如列出目录），execute_command 正确返回 stdout/退出码，模型在回复中引用了结果
- [x] 场景7（AC7）：执行一个耗时超过超时阈值的命令，能正确超时终止并返回超时错误，不卡死整个程序
- [x] 场景8（AC8）：要求模型查找匹配某模式的文件，glob_files 返回正确的文件列表
- [x] 场景9（AC9）：要求模型搜索代码里包含某关键字的位置，grep_content 返回正确的匹配文件与位置
- [x] 场景10（AC10）：尝试路径穿越（`../` 或工作目录外绝对路径）访问工作目录外文件，被拒绝并返回清晰错误，未发生实际读写
- [x] 场景11（AC11）：同一轮对话中，模型调用工具、拿到结果后程序自动追加一次请求得到最终回复；工具名/参数/结果/最终回复全过程终端依次可见，无需用户手动催促
- [x] 场景12（AC12）：把 protocol 切换为 openai 后，以上工具调用场景同样正常工作，不改代码只改配置
- [x] 场景13（AC13）：工具执行内部抛出未预期异常时，程序打印清晰错误、不崩溃，并能继续下一轮正常对话
