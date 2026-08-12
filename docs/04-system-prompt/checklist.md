# 系统提示工程化 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [x] AC1 模块化装配——系统提示由 身份→系统约束→任务模式→动作执行→工具使用→语气风格→文本输出 七个固定模块按 `Section.priority` 排序后拼成（不是靠代码里书写顺序），模块间空行分隔；新增模块只需要挂一个 `Section` 进列表，不用管它写在哪一行（验证：`tests/test_prompt_builder.py::test_build_system_prompt_returns_two_blocks`、`test_assembly_order_is_driven_by_priority_not_list_position`——后者故意把优先级数字更大的 Section 写在列表更靠前的位置，验证真正生效的是 priority 而非书写位置）
- [x] AC2 可选空槽——自定义指令/已激活 Skill/长期记忆这三个名字不出现在实际产出或代码常量里，`optional_sections` 为空时不产生多余空行（验证：`tests/test_prompt_builder.py::test_build_system_prompt_appends_optional_sections`；`grep -r "已激活 Skill\|长期记忆" src/` 无命中）
- [x] AC3 环境信息呈现——环境信息段含工作目录、平台、当前日期、git 状态、应用版本、当前模型，与稳定模块分属不同 `SystemPromptBlock`（验证：`tests/test_prompt_sections.py::test_environment_section_includes_all_fields`）
- [x] AC7 双重强化——`ExecuteCommandTool.description`/`EditFileTool.description` 与系统提示的 `tool_usage_section()` 都能读到"优先专用工具""编辑前必读"（验证：`tests/test_prompt_sections.py::test_tool_usage_section_reinforces_key_rules`；人工查看两个工具的 `description`）

## 集成

- [x] AC4a 缓存标记机制（AC4 的必要条件，非充分条件）——`system` 字段里稳定块带 `cache_control`、环境信息块不带；`tools` 数组最后一项带 `cache_control`（验证：`tests/test_anthropic_provider.py::test_system_prompt_cache_control_only_on_cacheable_block`、`test_tools_cache_control_on_last_tool_only`）。**注意：这一项只证明"标记打对了"，不证明"缓存真的命中"——AC4 字面要求的 `cache_creation_input_tokens > 0` / 次轮 `cache_read_input_tokens > 0` 是运行时事实，mock 测不出来，真正的验证在下面「端到端场景」的场景 1，两项都打勾才算 AC4 完整通过**
- [x] AC5 缓存确定性——同一份 `EnvironmentContext` 输入下，`build_system_prompt()` 两次调用产出的稳定块文本逐字节相等；改变环境信息不影响稳定块内容（验证：`build_system_prompt` 的稳定块只由固定模块函数拼成，不接收 `env` 参与拼接）
- [x] AC6 缓存字段解析——`TokenUsage.cache_creation_input_tokens`/`cache_read_input_tokens` 两个协议都会填充；Anthropic 取自 `usage.cache_*_input_tokens`，OpenAI 取自 `usage.prompt_tokens_details`；字段缺失时为 `0`、不报错（验证：`tests/test_anthropic_provider.py::test_usage_event_defaults_cache_fields_to_zero_when_absent`、`tests/test_openai_provider.py::test_usage_event_parses_cached_tokens_when_present`、`test_usage_event_defaults_cache_fields_to_zero_when_details_absent`）
- [x] AC8 补充消息注入——`role="system"` 的消息在 Anthropic 下降级为 `<system-reminder>` 包裹的 `user` 消息，在 OpenAI 下原生透传；这类消息只出现在发给 provider 的请求里，不出现在 `session.get_messages()`（验证：`tests/test_anthropic_provider.py::test_to_anthropic_messages_downgrades_system_role_to_tagged_user_message`、`tests/test_openai_provider.py::test_to_openai_messages_passes_through_system_role_natively`、`tests/test_agent.py::test_plan_mode_reminder_injected_but_not_persisted`）
- [x] AC9 规划模式按轮次注入——第 1、4、7...轮注入完整版，其余轮次注入精简版，每一轮都会注入（验证：`tests/test_prompt_reminders.py`、`tests/test_agent.py::test_plan_mode_round_counter_advances_across_turns_and_alternates_full_brief`）；Plan Mode 下 `tool_registry.list_tools(only_safe=True)` 只暴露只读工具（验证：`tests/test_agent.py::test_plan_mode_only_exposes_safe_tools`）；`/do` 后不再注入（验证：`test_plan_mode_reminder_not_injected_when_tools_enabled`）
- [x] AC10 跨协议一致——`run_agent_loop` 对两个协议都调用同一个 `build_system_prompt`/`gather_environment`/`plan_mode_reminder`，协议差异只体现在各自 Provider 内部的翻译逻辑（验证：`tests/test_agent.py` 全部用例不区分协议、走同一套 `run_agent_loop`；`tests/test_anthropic_provider.py` 与 `tests/test_openai_provider.py` 分别验证各自协议下的翻译结果）

## 编译与测试

- [x] AC11 不破坏 ch04——`run_agent_loop` 的多轮编排、流式双路收集、安全/不安全批次分批并发、用户取消、未知工具连续计数停止等既有场景全部通过（验证：`uv run pytest tests/test_agent.py`）
- [x] AC14 代码规范——`ruff format --check .` 与 `ruff check .` 全仓库无告警；对话区/日志输出不出现 `api_key` 明文（验证：`uv run ruff check .`、`uv run ruff format --check .`；`grep -rn "api_key" src/sirius_agent/prompt/ src/sirius_agent/agent.py src/sirius_agent/tui.py` 无命中）
- [x] 全部单元测试通过（验证：`uv run pytest`，66 passed）

## 端到端场景

- [x] AC13 环境采集降级——在非 git 目录下调用 `gather_environment`，`git_branch`/`git_dirty` 为 `None`，不抛异常、不阻塞（验证：`tests/test_prompt_environment.py::test_gather_environment_outside_git_repo`）
- [x] N4 快速且有界——git 子进程卡住时不会无限期挂住请求，超时后被 kill、降级返回 `None`（验证：`tests/test_prompt_environment.py::test_run_git_times_out_and_kills_hanging_process`，用假进程模拟卡住，确认 `_GIT_TIMEOUT` 到点后触发 `kill()`）
- [x] AC12 历史合法——Plan Mode 下多轮对话 + 中途用户取消（Esc/Ctrl+C）后再发一条新消息，`ConversationSession` 历史里的 `assistant`/`tool` 消息配对完整、不含任何 `role="system"` 条目，provider 请求不会因消息序列不合法而返回 400（验证：`tests/test_agent.py::test_user_cancel_stops_before_next_iteration`、`test_plan_mode_reminder_injected_but_not_persisted` 的组合行为；真实场景已由用户在真实终端人工验证通过）
- [x] 场景 1（AC4 完整验证，人工在真实终端跑）：用 Anthropic 协议连续发起两轮内容不变的对话，第一轮用量行"缓存写入"大于 0，第二轮"缓存命中"大于 0——用户已确认通过，AC4 完整成立
- [x] 场景 2（Plan Mode 全程可感知，人工在真实终端跑）：`/plan` 后要求模型写文件，模型只给计划不动手；连续对话到第 4 轮左右仍记得自己在计划模式；`/do` 后同样请求能真正执行——用户已确认通过
- [x] 场景 3（终端观感不受影响，人工在真实终端跑）：全程终端只显示用户自己输入的原始文本，看不到 `<system-reminder>` 或提醒原文，模型不会把提醒当成一句话单独回应——用户已确认通过
- [x] 场景 4（协议互换，人工在真实终端跑）：把 `--provider` 从 anthropic 换成 openai，重复场景 2，行为一致；用量行里能看到 OpenAI 的缓存命中信息（若端点支持）——用户已确认通过
