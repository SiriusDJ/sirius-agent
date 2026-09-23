# Sirius-Agent（五层防御权限系统）Plan

## 架构概览

新增 `sirius-agent/permissions/` 包，包含七个协作模块：

- **blacklist**：硬编码正则黑名单，只对 execute_command 的 command 参数生效，命中即返回 DENY，不接受任何配置
- **rules**：规则的内存表示与匹配/合并逻辑（RuleSet），支持精确+glob 匹配，多条命中时按 deny>ask>allow 合并
- **rule_store**：三级 YAML 规则文件的加载与追加写入，定位用户级/项目级/本地级文件路径，格式校验
- **mode**：default/accept_edits/plan/bypass 四档权限模式的兜底默认值表，按 read/write/command 三类工具分别查表
- **safelist**：硬编码的安全命令白名单，只对 execute_command 生效，优先级低于规则引擎、高于模式兜底；命中即返回 ALLOW，命令里出现 shell 元字符（管道/分号/重定向/命令替换）时一律不算命中
- **engine**（PermissionEngine）：把黑名单 → 沙箱越界判断 → 规则匹配 → 安全命令白名单 → 模式兜底组织成一条可注入的 `PermissionChecker` 检查链（构造时可传自定义 `checkers` 列表，缺省即这五层默认顺序），产出一个只读的判定结果（不涉及 I/O、不涉及用户交互）
- **gate**（PermissionGate）：包裹 engine，在判定为 ask 时通过注入的回调发起人在回路询问，并根据用户选择（本次/本会话/永久）决定是否要更新内存规则集合或落盘到本地规则文件

Agent Loop（agent.py）在每轮迭代对已知工具调用做完"是否已知工具"分流后，新增一步：对所有已知调用逐个（顺序，不并发）跑 `PermissionGate.check()`；被拒绝的调用直接产出失败的工具结果反馈给模型；被允许的调用才进入原有的 safe/unsafe 并发/串行批处理逻辑。人在回路的实际终端渲染（展示工具信息、读取用户选择）由 tui.py 实现为一个回调函数，通过依赖注入的方式交给 PermissionGate，agent.py 本身仍不直接碰终端，保持可脱离 TUI 单独测试的特性。人在回路的接入方式是 run_agent_loop 新增参数 + await 回调，而不是新增一种 TurnEvent 让外层轮询/用 Future 桥接——前者天然复用现有的 async/await 控制流，不需要引入额外的同步原语。

PermissionMode.PLAN 上线后接管了原本 tui.py 里 `/plan`、`/do` 那套独立的 `tools_enabled` 开关：agent.py 不再单独接收 `tools_enabled` 参数，而是每轮直接读 `permission_gate.mode == PermissionMode.PLAN` 来决定这一轮要不要只把只读工具暴露给模型、要不要注入计划模式提醒。tui.py 相应地把 `/plan`、`/do` 两个命令合并进 `/permission default|accept_edits|plan|bypass`，切到 `plan` 档时仍会调用 `session.enter_plan_mode()` 重置提醒轮次计数器，行为跟原来的 `/plan` 命令一致。

路径沙箱判断直接复用 tools/paths.py 中已有的 `is_within_workspace`，不重复实现一套路径解析逻辑，避免两处判断行为不一致。

## 核心数据结构

### Decision（枚举）
判定结果三态：`ALLOW`、`ASK`、`DENY`

### PermissionMode（枚举）
四档权限模式：`DEFAULT`、`ACCEPT_EDITS`、`PLAN`、`BYPASS`（与 Claude Code 自己的模式命名对齐）

### RuleSource（枚举）
规则来源，只用于展示判定依据，不参与优先级计算：`USER`、`PROJECT`、`LOCAL`、`SESSION`（人在回路"本会话允许"产生、只存在内存里的规则）

### Rule
单条规则。字段：`tool`（工具名，精确匹配）、`pattern`（匹配字符串，支持 glob）、`action`（Decision）、`source`（RuleSource）

### RuleSet
内存中的规则集合，由三级 YAML 文件 + 会话内临时规则合并而成。
- `evaluate(tool_name, match_text) -> tuple[Decision, Rule] | None`：返回命中的最终判定和"导致该判定的那条规则"（用于展示依据）；无命中返回 None
- `add_session_rule(rule)`：追加一条只存在内存里的规则（人在回路"本会话允许"用）

### PermissionVerdict
`PermissionEngine.evaluate()` 的返回值，纯数据、不含副作用。字段：`decision`（Decision）、`reason`（人类可读的判定依据说明）、`match_text`（本次用于匹配规则的字符串，供后续泛化生成规则用）

### PermissionRequest
需要人在回路确认时传给回调的请求。字段：`tool_name`、`arguments`、`reason`（为什么落到了 ask）、`suggested_pattern`（系统泛化建议的 glob 规则，供用户确认/修改）

### HumanChoice（枚举）
人在回路的四个选项：`ALLOW_ONCE`、`DENY_ONCE`、`ALLOW_SESSION`、`ALLOW_PERMANENT`

### HumanDecision
回调的返回值。字段：`choice`（HumanChoice）、`pattern`（用户确认/修改后的 glob 规则字符串，仅 ALLOW_SESSION/ALLOW_PERMANENT 时有意义）

### PermissionOutcome
`PermissionGate.check()` 的最终返回值，供 agent.py 直接使用。字段：`allowed`（bool）、`reason`（str，被拒绝时作为工具失败结果的内容）

### AskPermissionCallback（类型别名）
`Callable[[PermissionRequest], Awaitable[HumanDecision]]`——由 tui.py 实现具体的终端交互，PermissionGate 只依赖这个签名

## 模块设计

### permissions.blacklist
**职责：** 硬编码正则黑名单，覆盖删除系统关键目录、磁盘格式化/写裸设备、远程脚本直接执行、fork 炸弹、关机重启系统等类别，同时覆盖 Windows（PowerShell/cmd）与类 Unix 命令变体
**对外接口：** `check_blacklist(command: str) -> BlacklistRule | None`
**依赖：** 无

### permissions.rules
**职责：** Rule/RuleSet 的匹配与合并逻辑；pattern 匹配用 `fnmatch.fnmatchcase`（精确匹配是无通配符 pattern 的自然特例，不需要单独实现）；命中多条规则时按 deny>ask>allow 合并，不看模式精确度；提供把一次具体调用泛化成 glob pattern 的函数
**对外接口：** `RuleSet`（构造/`merge` 都可传 `matcher: RuleMatcher = _matches` 覆盖默认匹配逻辑；`evaluate`、`add_session_rule`）、`generalize_pattern(tool_name, match_text) -> str`
**依赖：** 无

### permissions.rule_store
**职责：** 三级 YAML 规则文件的路径定位、加载、格式校验、追加写入（人在回路"永久允许"用）
**对外接口：** `user_rules_path()`、`project_rules_path(workspace_root)`、`local_rules_path(workspace_root)`、`load_rule_file(path) -> list[Rule]`（文件不存在返回空列表；格式错误抛 `RuleFileError`）、`append_rule(path, rule)`
**依赖：** 无

### permissions.mode
**职责：** 权限模式的兜底默认值表，按 read（read_file/glob_files/grep_content）、write（write_file/edit_file）、command（execute_command）三类工具分别查表；read 类四档都兜底 allow
**对外接口：** `fallback_decision(tool_name: str, mode: PermissionMode) -> Decision`
**依赖：** 无

### permissions.safelist
**职责：** 硬编码的安全命令白名单，区分"允许接任意参数"（如 `ls`/`git status`，不存在破坏性 flag 变体）和"仅允许精确匹配、不允许任何后缀"（如 `npm -v`，避免尾随参数被夹带进真正有副作用的子命令）两类；命中前先做 shell 元字符检查（`| ; & > < $( `` \n`），出现即不算命中，防止拼接命令绕过
**对外接口：** `is_safe_command(command: str) -> bool`
**依赖：** 无

### permissions.engine（PermissionEngine + PermissionChecker 检查链）
**职责：** 只读判定，不涉及 I/O 与用户交互；把黑名单/沙箱/规则/白名单/模式兜底组织成一条可注入的 `PermissionChecker` 检查链，依次跑到有一层给出结论为止
**对外接口：**
- `PermissionChecker`（Protocol）：`check(tool_name, arguments, match_text, mode) -> PermissionVerdict | None`，None 表示这层没有意见、交给下一层
- 五个默认实现：`BlacklistChecker(blacklist_check=check_blacklist)`、`SandboxChecker(workspace_root, rule_set, workspace_check=is_within_workspace)`、`RuleChecker(rule_set)`、`SafelistChecker(safelist_check=is_safe_command)`、`ModeFallbackChecker(fallback=fallback_decision)`（必须是链上最后一层，从不返回 None）——每个 checker 依赖的纯函数都以关键字参数注入，默认值就是原本硬编码 import 的那个真实实现
- `PermissionEngine`：构造 `(workspace_root, rule_set, mode, checkers=None)`——`checkers` 缺省时用 `_default_checkers()` 组装出上面五层，顺序即 spec.md F9 定义的判定顺序；`evaluate(tool_name, arguments) -> PermissionVerdict`；`mode`（只读属性）；`set_mode(mode)`
**依赖：** blacklist、rules、mode、safelist、tools.paths

### permissions.gate（PermissionGate）
**职责：** 包裹 engine；判定为 ask 时通过注入的回调发起人在回路；根据用户选择更新会话内规则或落盘本地规则文件
**对外接口：** 构造 `(engine, rule_set, local_rules_path, ask_callback=None, pattern_generalizer=generalize_pattern, rule_appender=append_rule)`——后两个默认指向 rules.py/rule_store.py 的真实实现，可注入替换；`async check(tool_name, arguments) -> PermissionOutcome`；`mode`（只读属性，转发给 engine，供 agent.py 判断是否处于 plan 档）；`set_mode(mode)`（转发给 engine）；`set_ask_callback(callback)`
**依赖：** engine、rules、rule_store

## 文件组织

```
src/sirius_agent/permissions/
├── __init__.py
├── types.py       — Decision、PermissionMode、RuleSource、Rule、PermissionVerdict、
│                     PermissionRequest、HumanChoice、HumanDecision、PermissionOutcome、AskPermissionCallback
├── blacklist.py    — BlacklistRule、硬编码黑名单列表、check_blacklist()
├── rules.py         — RuleSet、pattern 匹配、generalize_pattern()
├── rule_store.py     — YAML 加载/追加写入、三级文件路径定位、RuleFileError
├── mode.py            — fallback_decision()
├── safelist.py         — is_safe_command()
├── engine.py            — PermissionEngine
└── gate.py               — PermissionGate

# 改动的既有文件
src/sirius_agent/agent.py     — 新增 _run_permission_checks，run_agent_loop 新增 permission_gate 参数、
                           删掉 tools_enabled（改读 permission_gate.mode 判断是否 plan 档）
src/sirius_agent/tui.py        — 新增 create_ask_callback()（终端渲染人在回路询问）、
                           /permission default|accept_edits|plan|bypass 斜杠命令（取代原来的 /plan、/do）
src/sirius_agent/__main__.py    — 新增 --permission-mode 参数（四档可选），组装规则文件加载 + PermissionEngine + PermissionGate
src/sirius_agent/prompt/reminders.py — 计划模式提醒文案里不再点名具体斜杠命令
```

## 模块交互

```
启动阶段（__main__.py）：
  解析 --permission-mode（默认 DEFAULT）
  → rule_store.load_rule_file() × 3（用户级/项目级/本地级，缺失视为空列表，格式错误抛 RuleFileError → 打印错误退出）
  → RuleSet.merge(用户规则, 项目规则, 本地规则)
  → PermissionEngine(workspace_root, rule_set, mode)
  → PermissionGate(engine, rule_set, local_rules_path)
  → 传给 run_repl(provider, tool_registry, session, workspace_root, permission_gate)

run_repl（tui.py）：
  → gate.set_ask_callback(create_ask_callback(console, prompt_session, watcher))
  → 处理 /permission <mode> 斜杠命令时调用 gate.set_mode(mode)，mode==PLAN 时额外调用 session.enter_plan_mode()
  → 调用 run_agent_loop(..., permission_gate=gate)

每轮迭代（agent.py run_agent_loop）：
  plan_mode = permission_gate.mode == PermissionMode.PLAN
  → active_tools = tool_registry.list_tools(only_safe=plan_mode)
  → plan_mode 为真时把 plan_mode_reminder(...) 拼进这一轮请求消息
  已知工具调用列表 known_calls
  → _run_permission_checks(gate, known_calls, session, iteration, allowed_out)
      对每个 tc 顺序执行（保证 ask 严格串行）：
        outcome = await gate.check(tc.name, tc.arguments)
          → engine.evaluate(tc.name, tc.arguments)
              execute_command → blacklist.check_blacklist(command)：命中→DENY，结束
              路径类工具 → is_within_workspace 判断越界
                越界 → rule_set.evaluate 找显式 allow，或 mode==BYPASS → ALLOW；否则 DENY，结束
              rule_set.evaluate(tool_name, match_text)：命中 → 按结果返回，结束
              execute_command 且 safelist.is_safe_command(command) → ALLOW，结束
              都未命中 → mode.fallback_decision(tool_name, mode)（按 read/write/command 三类查表）
          → 若 verdict.decision == ASK：
              request = PermissionRequest(..., suggested_pattern=generalize_pattern(...))
              human = await ask_callback(request)     ← tui.py 渲染询问、读取用户输入
              ALLOW_SESSION → rule_set.add_session_rule(...)（人在回路里可修改 pattern）
              ALLOW_PERMANENT → rule_set.add_session_rule(...) + rule_store.append_rule(local_rules_path, ...)
              DENY_ONCE → 不放行
        outcome.allowed == True → 加入 allowed_out
        outcome.allowed == False → yield TOOL_STARTED/TOOL_FINISHED（失败结果），写入 session
  → allowed_out 按 safe/unsafe 分流，交给原有 _run_safe_batch（并发）/ _run_unsafe_batch（串行）执行
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| pattern 匹配实现 | `fnmatch.fnmatchcase` | 精确匹配是无通配符 pattern 的自然特例，一套逻辑覆盖精确+glob 两种需求，不引入额外依赖 |
| 人在回路接入方式 | run_agent_loop 新增 permission_gate 参数 + 回调注入 | 与 provider/tool_registry 的依赖注入风格一致，agent.py 保持不直接碰终端、可脱离 TUI 测试 |
| 权限检查与工具执行分离 | 新增 `_run_permission_checks` 跑在 safe/unsafe 分流之前，被拒绝的调用直接产出失败结果，允许的才进入原有批处理 | 改动面最小，复用现有并发/串行逻辑；检查阶段本身顺序 await，天然保证多个 ask 严格串行 |
| 规则文件路径 | 用户级 `~/.sirius-agent/rules.yaml`；项目级 `<root>/.sirius-agent/rules.yaml`；本地级 `<root>/.sirius-agent/rules.local.yaml` | 与 Claude Code settings.json / settings.local.json 的心智模型一致；本地级需提示用户加入 .gitignore |
| 沙箱判断复用 | 直接复用 `tools/paths.py` 的 `is_within_workspace`，不重复实现 | 避免权限层和工具层各自维护一套路径解析逻辑、行为不一致 |
| 规则 YAML 格式 | 顶层 `rules:` 列表，每项 `tool`/`pattern`/`action` 三字段 | 结构简单、人工可读可编辑，校验逻辑单一 |
| engine 与 gate 拆分 | engine 只做纯判定（无 I/O），gate 包一层处理人在回路与落盘 | engine 可以脱离终端单独做单元测试；gate 是唯一有副作用、需要异步等待的入口 |
| 权限模式档位 | 四档 default/accept_edits/plan/bypass，按 read/write/command 三类工具查表，取代最初的三档 strict/default/permissive + 只读/有副作用二分 | 跟 Claude Code 自己的模式命名和语义对齐；plan 档顺带接管原本跟权限系统平行的 tools_enabled 开关，只用一套"模式"概念，不用维护两套互相独立又要保持同步的状态 |
| 安全命令白名单的两种匹配模式 | "允许任意后缀参数"和"只允许精确匹配"分两个集合，而不是像最初参考的实现那样统一用一套前缀匹配逻辑 | 有些命令的某些 flag 组合有副作用（如 `git branch -d`、`date --set`），统一前缀匹配会让这些危险变体也跟着被放行；版本查询类命令（如 `npm -v`）也不能保证 CLI 一定在该 flag 处停止解析后续参数，所以只精确匹配，不给拼接空间 |
| 白名单在五层里的位置 | 黑名单 > 规则引擎 > 白名单 > 模式兜底 | 用户显式配置的规则（包括 deny）必须能覆盖硬编码的默认白名单，白名单只是"规则集合完全没命中时，比直接问用户更友好"的一层，不应该有更高的优先级 |
| PermissionEngine 内部结构 | 五层判定拆成实现同一个 `PermissionChecker` Protocol 的类，`evaluate()` 依次跑链条到第一个非 None 结果；构造时可传自定义 `checkers` 覆盖默认链条 | 这次会话里短时间内连续加了两层（四档模式改造、安全命令白名单），且 spec 的"不做的事"里明确写了网络请求限制/资源配额/审计日志留给后续章节——预期还会继续加层；链条化之后新增一层只需要写一个新 Checker 类插进默认列表，不用碰 `evaluate()` 内部的 if/elif；黑名单/白名单具体条目仍然写死在各自模块里，没有因此变成可配置，不违背 spec 的"不可配置"要求 |
| 注入范围的边界 | `RuleSet` 的匹配函数、四个 Checker 各自依赖的纯函数（`check_blacklist`/`is_within_workspace`/`is_safe_command`/`fallback_decision`）、`PermissionGate` 的 `generalize_pattern`/`append_rule`，全部改成构造时可传参数覆盖，默认值就是原来硬编码 import 的真实实现；`__main__.py` 的 `_build_permission_gate`（组合根）、`blacklist.py`/`safelist.py` 内部的具体条目列表、`mode.py` 的兜底表本身，都没有改成可注入 | 前者是"代码层面可替换的依赖"，跟用户能不能从 YAML/CLI 配置无关，不会改变任何默认运行时行为；后者要么本来就该是组合根直接持有具体实现的地方（`__main__.py`），要么一旦注入就等于让黑名单/白名单/模式表变成可配置，直接违反 spec 明确写的"不可被配置放开" |
