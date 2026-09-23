# Sirius-Agent（五层防御权限系统）Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/sirius_agent/permissions/__init__.py` | 包标识 |
| 新建 | `src/sirius_agent/permissions/types.py` | 全部枚举与 dataclass 类型 |
| 新建 | `src/sirius_agent/permissions/blacklist.py` | 硬编码黑名单、check_blacklist |
| 新建 | `src/sirius_agent/permissions/rules.py` | RuleSet 匹配/合并、generalize_pattern |
| 新建 | `src/sirius_agent/permissions/rule_store.py` | 三级 YAML 规则文件加载/追加写入 |
| 新建 | `src/sirius_agent/permissions/mode.py` | 权限模式兜底默认值表 |
| 新建 | `src/sirius_agent/permissions/engine.py` | PermissionEngine |
| 新建 | `src/sirius_agent/permissions/gate.py` | PermissionGate |
| 修改 | `src/sirius_agent/agent.py` | 接入权限检查阶段 |
| 修改 | `src/sirius_agent/tui.py` | 人在回路终端交互、/permission 命令 |
| 修改 | `src/sirius_agent/__main__.py` | --permission-mode 参数、组装 gate |
| 修改（T12） | `src/sirius_agent/permissions/types.py` | PermissionMode 改四档 |
| 修改（T12） | `src/sirius_agent/permissions/mode.py` | 兜底表改 read/write/command 三分类 |
| 修改（T12） | `src/sirius_agent/permissions/engine.py`、`gate.py` | PERMISSIVE→BYPASS，新增 mode 只读属性 |
| 修改（T12） | `src/sirius_agent/agent.py` | 删 tools_enabled，改读 permission_gate.mode |
| 修改（T12） | `src/sirius_agent/tui.py` | 删 /plan、/do，合并进 /permission 四档 |
| 修改（T12） | `src/sirius_agent/__main__.py` | --permission-mode 可选值改四档 |
| 修改（T12） | `src/sirius_agent/prompt/reminders.py` | 提醒文案不再点名已废弃的 /do 命令 |
| 新建（T13） | `src/sirius_agent/permissions/safelist.py` | 安全命令白名单、is_safe_command |
| 修改（T13） | `src/sirius_agent/permissions/engine.py` | 接入白名单，插在规则引擎和模式兜底之间 |

## T1: 定义权限类型（types.py）

**文件：** `src/sirius_agent/permissions/__init__.py`, `src/sirius_agent/permissions/types.py`
**依赖：** 无
**步骤：**
1. 创建 `src/sirius_agent/permissions/` 目录和空的 `__init__.py`
2. 定义枚举 `Decision(ALLOW, ASK, DENY)`、`PermissionMode(STRICT, DEFAULT, PERMISSIVE)`、`RuleSource(USER, PROJECT, LOCAL, SESSION)`、`HumanChoice(ALLOW_ONCE, DENY_ONCE, ALLOW_SESSION, ALLOW_PERMANENT)`
3. 定义 dataclass `Rule(tool: str, pattern: str, action: Decision, source: RuleSource)`
4. 定义 dataclass `PermissionVerdict(decision: Decision, reason: str, match_text: str)`
5. 定义 dataclass `PermissionRequest(tool_name: str, arguments: dict, reason: str, suggested_pattern: str)`
6. 定义 dataclass `HumanDecision(choice: HumanChoice, pattern: str | None = None)`
7. 定义 dataclass `PermissionOutcome(allowed: bool, reason: str)`
8. 定义类型别名 `AskPermissionCallback = Callable[[PermissionRequest], Awaitable[HumanDecision]]`
9. 所有类型加中文注释说明用途

**验证：** `uv run python -c "from sirius_agent.permissions.types import Decision, PermissionMode, RuleSource, Rule, PermissionVerdict, PermissionRequest, HumanChoice, HumanDecision, PermissionOutcome, AskPermissionCallback"` 无报错

## T2: 实现黑名单（blacklist.py）

**文件：** `src/sirius_agent/permissions/blacklist.py`
**依赖：** T1
**步骤：**
1. 定义 dataclass `BlacklistRule(category: str, regex: re.Pattern[str])`
2. 定义模块级常量列表（类别说明, 正则字符串），至少覆盖：删除系统关键目录（`rm -rf /`、`rm -rf ~`、Windows `rd /s /q C:\`、`del /f /s /q C:\`）、磁盘格式化/写裸设备（`mkfs`、`format C:`、`dd if=... of=/dev/`）、远程脚本直接执行（`curl ... | sh`、`wget ... | bash`、PowerShell `iwr ... | iex`）、fork 炸弹（`:(){ :|:& };:`）、关机重启系统（`shutdown`、`reboot`、`Restart-Computer`）
3. 加载时编译成 `re.compile(pattern, re.IGNORECASE)`，组装成 `_BLACKLIST: list[BlacklistRule]`
4. 实现 `check_blacklist(command: str) -> BlacklistRule | None`：遍历命中即返回该规则，否则 None

**验证：** 临时脚本测试至少 5 条命中黑名单的命令（每类至少一条，含一条 Windows 变体）返回非 None，以及 `git status`、`ls` 等正常命令返回 None

## T3: 实现规则匹配与合并（rules.py）

**文件：** `src/sirius_agent/permissions/rules.py`
**依赖：** T1
**步骤：**
1. 实现 `_matches(rule, tool_name, match_text) -> bool`：`rule.tool == tool_name and fnmatch.fnmatchcase(match_text, rule.pattern)`
2. 实现 `RuleSet` 类，内部维护 `_rules: list[Rule]`；`merge(*rule_lists) -> RuleSet` 构造方法拼接多个来源
3. 实现 `evaluate(tool_name, match_text) -> tuple[Decision, Rule] | None`：找出所有命中的规则，无命中返回 None；按 DENY>ASK>ALLOW 顺序取该严重度里第一条规则，与其 Decision 一并返回
4. 实现 `add_session_rule(rule) -> None`：append 到 `_rules`
5. 实现 `generalize_pattern(tool_name, match_text) -> str`：execute_command 取命令首词 + `" *"`；read_file/write_file/edit_file 取 match_text 所在目录 + `"/**"`（无目录部分原样返回）；其余工具原样返回

**验证：** 临时脚本断言：单条 allow 命中生效；deny+allow 同时命中最终为 deny（与精确度无关）；无命中返回 None；`add_session_rule` 后立即参与后续 `evaluate`；`generalize_pattern("execute_command", "git commit -m x")` 返回 `"git *"`，`write_file` 类 path `"src/sirius_agent/foo.py"` 返回 `"src/sirius_agent/**"`

## T4: 实现规则文件读写（rule_store.py）

**文件：** `src/sirius_agent/permissions/rule_store.py`
**依赖：** T1
**步骤：**
1. 定义 `RuleFileError(Exception)`
2. 实现 `user_rules_path()`（`Path.home()/".sirius-agent"/"rules.yaml"`）、`project_rules_path(workspace_root)`（`workspace_root/".sirius-agent"/"rules.yaml"`）、`local_rules_path(workspace_root)`（`workspace_root/".sirius-agent"/"rules.local.yaml"`）
3. 实现 `load_rule_file(path, source) -> list[Rule]`：文件不存在返回空列表；PyYAML 解析，顶层需有 `rules` 列表，每项校验 `tool`/`pattern`/`action` 三字段齐全且 `action` 属于 allow/ask/deny，否则抛 `RuleFileError`（含文件路径与具体缺失/非法字段）
4. 实现 `append_rule(path, rule) -> None`：读出现有 `rules` 列表（不存在视为空）追加一条写回，写前确保父目录存在

**验证：** 临时目录写合法/缺字段/action 非法/文件不存在四种场景 YAML，分别断言 `load_rule_file` 返回正确列表或抛 `RuleFileError`；连续两次 `append_rule` 后 `load_rule_file` 能看到两条规则

## T5: 实现权限模式兜底表（mode.py）

**文件：** `src/sirius_agent/permissions/mode.py`
**依赖：** T1
**步骤：**
1. 定义 `_READONLY_TOOLS = {"read_file", "glob_files", "grep_content"}`
2. 实现 `fallback_decision(tool_name, mode) -> Decision`：只读工具三档一律 ALLOW；有副作用工具 STRICT→DENY、DEFAULT→ASK、PERMISSIVE→ALLOW

**验证：** 对 `read_file`/`write_file` 各测三档，断言只读工具三档均 ALLOW，有副作用工具依次 DENY/ASK/ALLOW

## T6: 实现权限判定引擎（engine.py）

**文件：** `src/sirius_agent/permissions/engine.py`
**依赖：** T1, T2, T3, T5
**步骤：**
1. 定义 `_PATH_TOOLS = {"read_file", "write_file", "edit_file"}` 和参数字段映射 `_MATCH_ARG = {"execute_command": "command", "read_file": "path", "write_file": "path", "edit_file": "path", "glob_files": "pattern", "grep_content": "pattern"}`
2. 实现 `PermissionEngine` 类，构造函数接收 `(workspace_root: Path, rule_set: RuleSet, mode: PermissionMode)`
3. 实现 `set_mode(mode) -> None`
4. 实现 `evaluate(tool_name, arguments) -> PermissionVerdict`：
   - `match_text = arguments[_MATCH_ARG[tool_name]]`
   - execute_command 先调用 `check_blacklist(match_text)`，命中则返回 `PermissionVerdict(DENY, reason=f"命中黑名单（{类别}）", match_text)`
   - 若 tool_name 在 `_PATH_TOOLS`：解析 `(workspace_root / match_text).resolve()`，用 `is_within_workspace` 判断越界；越界时若 `rule_set.evaluate` 命中且结果是 ALLOW 则放行，否则 mode==PERMISSIVE 则放行，否则返回 DENY（reason 说明越界且无匹配放行规则）
   - 调用 `rule_set.evaluate(tool_name, match_text)`：命中则按结果返回（reason 带上命中规则内容）
   - 都未命中：调用 `fallback_decision(tool_name, self._mode)`，reason 说明按哪档模式兜底

**验证：** 构造临时工作目录和 RuleSet，覆盖场景各断言一次：execute_command 命中黑名单→DENY；write_file 越界+无规则+非放行模式→DENY；同越界场景切到放行模式→ALLOW；越界但有匹配 allow 规则、严格模式→ALLOW；未越界命中一条 ask 规则→ASK；未越界无命中、默认模式、有副作用工具→ASK；只读工具无命中、任意模式→ALLOW

## T7: 实现权限网关（gate.py）

**文件：** `src/sirius_agent/permissions/gate.py`
**依赖：** T1, T3, T4, T6
**步骤：**
1. 实现 `PermissionGate` 类，构造函数接收 `(engine, rule_set, local_rules_path, ask_callback=None)`
2. 实现 `set_mode(mode)`：转发给 `engine.set_mode`；`set_ask_callback(callback)`
3. 实现 `async def check(tool_name, arguments) -> PermissionOutcome`：
   - `verdict = engine.evaluate(...)`；ALLOW/DENY 直接映射成 `PermissionOutcome`
   - ASK 且 `ask_callback is None`：防御性兜底，视为拒绝
   - ASK 且有回调：构造 `PermissionRequest(..., suggested_pattern=generalize_pattern(...))`，`human = await ask_callback(request)`
     - `DENY_ONCE` → 拒绝；`ALLOW_ONCE` → 允许（不留痕）
     - `ALLOW_SESSION` → `rule_set.add_session_rule(Rule(tool_name, human.pattern, ALLOW, SESSION))` → 允许
     - `ALLOW_PERMANENT` → 同上 `add_session_rule`（立即生效）+ `append_rule(local_rules_path, Rule(..., LOCAL))` → 允许

**验证：** 用假 `ask_callback`（直接返回预设 `HumanDecision`，不碰真实终端）测试四种选择：`outcome.allowed` 符合预期；`ALLOW_SESSION` 后同一 gate 再次 `check` 同类调用不再触发 `ask_callback`（直接命中规则）；`ALLOW_PERMANENT` 后临时 `local_rules_path` 文件里能读到新规则

## T8: 接入 Agent Loop（agent.py）

**文件：** `src/sirius_agent/agent.py`
**依赖：** T7
**步骤：**
1. 引入 `PermissionGate`
2. 新增 `_run_permission_checks(gate, calls, session, iteration, allowed_out)` 异步生成器：对 calls 逐个顺序 `await gate.check(...)`；允许的追加进 `allowed_out`；拒绝的 yield 一对 `TOOL_STARTED`/`TOOL_FINISHED`（`ToolResult(ok=False, content=outcome.reason)`）并调用 `session.add_tool_result_message`
3. `run_agent_loop` 签名新增 `permission_gate: PermissionGate` 参数
4. 在 "known_calls / unknown tool" 分流之后、safe/unsafe 分流之前插入权限检查阶段，`safe_calls`/`unsafe_calls` 改为基于权限检查后的 `allowed_calls` 计算

**验证：** 用假 ToolRegistry + 假 PermissionGate（`check` 直接返回预设 `PermissionOutcome`）跑 `run_agent_loop`：`allowed=False` 的调用不触发 `tool_registry.execute` 且产出失败的 `TOOL_FINISHED`；`allowed=True` 的调用正常执行、行为与改动前一致

## T9: 接入 TUI（tui.py）

**文件：** `src/sirius_agent/tui.py`
**依赖：** T7, T8
**步骤：**
1. 引入 `PermissionRequest`、`HumanChoice`、`HumanDecision`、`PermissionMode`、`PermissionGate`
2. 实现 `create_ask_callback(console, prompt_session) -> AskPermissionCallback`：打印工具名/参数/reason/suggested_pattern 和四个选项（1 允许一次/2 拒绝一次/3 本会话允许/4 永久允许），读取编号；选 3/4 时额外一行确认/修改 `suggested_pattern`（回车采用建议值）；返回对应 `HumanDecision`
3. `run_repl` 签名新增 `permission_gate` 参数；循环前 `permission_gate.set_ask_callback(create_ask_callback(console, prompt_session))`
4. 新增斜杠命令 `/permission strict|default|permissive`：解析档位调用 `permission_gate.set_mode(...)`，非法档位打印提示，合法打印确认信息
5. 调用 `run_agent_loop` 时传入 `permission_gate`

**验证：** 用假 Provider + 假 ToolRegistry + 真实 PermissionGate（配一条 ask 规则）跑 `run_repl`，人工验证：能看到询问提示；四个选项各自触发预期效果（执行/拒绝/本会话内不再问/本地 YAML 已写入）；`/permission strict` 切换后提示正确且后续判定改变

## T10: 接入 CLI 入口（__main__.py）

**文件：** `src/sirius_agent/__main__.py`
**依赖：** T4, T6, T7, T9
**步骤：**
1. `_parse_args` 新增 `--permission-mode`（`choices=["strict","default","permissive"]`，默认 `"default"`）
2. 新增 `_build_permission_gate(workspace_root, mode) -> PermissionGate`：加载三级规则文件（缺失返回空列表）合并成 `RuleSet`；`RuleFileError` 按现有 `ConfigError` 风格打印错误退出；构造 `PermissionEngine`/`PermissionGate` 并返回
3. `main()` 解析 `args.permission_mode` 转 `PermissionMode`，调用 `_build_permission_gate`，传给 `run_repl`

**验证：** `uv run sirius-agent --permission-mode strict` 正常进入交互提示符；在 `.sirius-agent/rules.yaml` 写一条格式错误的规则，启动时打印清晰错误信息并以非零状态码退出

## T11: 端到端手动验证

**文件：** 无新建文件
**依赖：** T10
**步骤：**
1. tmux 中启动 `uv run sirius-agent`
2. 触发一次黑名单命中的命令，观察是否被直接拒绝且对话能继续
3. 让模型读/写工作目录外的路径，观察沙箱拒绝；写一条 allow 规则后重试，观察放行
4. 触发一次 ask 场景，依次体验允许一次/拒绝一次/本会话允许/永久允许
5. 用 `/permission strict`、`/permission permissive` 切换模式，观察判定结果变化
6. 检查 `.sirius-agent/rules.local.yaml` 确实被人在回路写入过

**验证：** 对照 checklist.md（下一阶段产出）逐项打勾

## T12: 权限模式改造为 default/accept_edits/plan/bypass 四档

**文件：** `src/sirius_agent/permissions/types.py`、`mode.py`、`engine.py`、`gate.py`、`src/sirius_agent/agent.py`、`src/sirius_agent/tui.py`、`src/sirius_agent/__main__.py`、`src/sirius_agent/prompt/reminders.py`
**依赖：** T1-T11 已完成，且已通过一轮真实终端端到端验收
**背景：** 验收后在对话中发现原来的三档 strict/default/permissive + 只读/有副作用二分兜底表，跟 Claude Code 自己的权限模式（default/acceptEdits/plan/bypassPermissions）不一致；同时 tui.py 里 `/plan`、`/do` 是一套独立于权限系统之外的开关（`tools_enabled`），容易跟权限模式的语义打架。这次把两者合并成一套。
**步骤：**
1. `types.py`：`PermissionMode` 改成 `DEFAULT`、`ACCEPT_EDITS`、`PLAN`、`BYPASS` 四个成员，去掉 `STRICT`/`PERMISSIVE`
2. `mode.py`：把"只读/有副作用"二分改成 read（read_file/glob_files/grep_content）/write（write_file/edit_file）/command（execute_command）三分类，按四档 × 三类查表；read 类四档都兜底 allow
3. `engine.py`：沙箱越界分支里 `mode == PermissionMode.PERMISSIVE` 改成 `mode == PermissionMode.BYPASS`；新增只读属性 `mode`
4. `gate.py`：新增只读属性 `mode`（转发 `engine.mode`），供 agent.py 判断是否处于 plan 档
5. `agent.py`：`run_agent_loop` 删掉 `tools_enabled` 参数；改成每轮读 `permission_gate.mode == PermissionMode.PLAN` 决定 `active_tools = tool_registry.list_tools(only_safe=plan_mode)` 和是否注入 `plan_mode_reminder`
6. `tui.py`：删掉 `_PLAN_COMMAND`/`_DO_COMMAND` 及其处理分支；`_handle_permission_command` 的可选值改成 `{"default", "accept_edits", "plan", "bypass"}`，切到 `plan` 档时调用 `session.enter_plan_mode()`；`run_repl` 不再维护本地 `plan_mode` 变量，`run_agent_loop` 调用去掉 `tools_enabled=...`
7. `__main__.py`：`--permission-mode` 的 `choices` 改成四档
8. `prompt/reminders.py`：`_PLAN_MODE_FULL` 文案里"用户会输入 /do 切回全工具模式"改成不点名具体命令的通用说法

**验证：**
- 单元回归：mode.py 新的四档 × 三分类查表全部覆盖测试；engine.py 原有 7 个场景（含沙箱越界+BYPASS 放行）改用新枚举名重跑，全部通过；agent.py 的假 PermissionGate 补上 `.mode` 属性后重跑通过；tui.py 的 `create_ask_callback`/`_handle_permission_command`（新签名多了 `session` 参数）重跑通过，含 `/permission plan` 会调用 `session.enter_plan_mode()`、`/permission bogus` 报错不崩溃；`__main__.py` 的 `--permission-mode` 参数与 `_build_permission_gate` 用新枚举重跑通过
- `uv run ruff check src/sirius_agent` 全量通过，`uv run python -c "import ..."` 全模块导入无报错
- 遗留：这次改动涉及交互命令（`/plan`/`/do` 退役），未在真实终端里重新跑一遍 checklist.md 的端到端场景，后续有需要时应至少手动验证一次 `/permission plan`（确认只读工具生效、提醒文案还在）和 `/permission bypass`（确认不再询问直接放行）

## T13: 新增安全命令白名单

**文件：** `src/sirius_agent/permissions/safelist.py`、`src/sirius_agent/permissions/engine.py`
**依赖：** T6（engine 已存在）
**背景：** 除了黑名单硬拦截已知危险命令，再加一层"已知安全命令直接放行"，减少 default/plan 模式下对 `ls`/`git status` 这类明显无害的只读命令也要每次确认的打扰；这一层严格说是原来"五层"之外新增的第六个机制（不重新编号章节标题）。
**步骤：**
1. `safelist.py`：定义两个 frozenset——`_SAFE_PREFIX_COMMANDS`（允许接任意后缀参数，只收不存在破坏性 flag 变体的命令，如 `ls`/`pwd`/`cat`/`git status`/`git log`/`git diff`/`git show`）和 `_SAFE_EXACT_COMMANDS`（只允许精确匹配、不允许任何后缀，如 `npm -v`/`java -version`/`git remote -v`，覆盖 Windows 和类 Unix 常用工具的版本查询）
2. 定义 `_SHELL_METACHARACTERS = ("|", ";", "&", ">", "<", "$(", "`", "\n")`
3. 实现 `is_safe_command(command: str) -> bool`：先 strip 判空、判元字符（命中任意一个直接 False）；再判断是否精确匹配 `_SAFE_EXACT_COMMANDS`；否则判断是否等于或以 `<prefix> ` 开头命中 `_SAFE_PREFIX_COMMANDS` 中的某一项
4. `engine.py`：在规则引擎判断之后、模式兜底之前插入一步——`tool_name == "execute_command" and is_safe_command(match_text)` 时直接返回 `PermissionVerdict(ALLOW, reason="命中安全命令白名单", match_text)`

**验证：**
- `is_safe_command` 单元测试：覆盖白名单命令本身、带合法后缀参数、精确匹配类命令不允许接后缀、以及命令注入类输入（`;`/`&&`/`|`/`>`/`` ` ``/`$(`）全部判 False，跟已知危险变体（`git branch -d`、`date --set`、`find -exec`）故意不在名单里
- engine 集成测试：命中白名单且无规则时直接 ALLOW；配置一条显式 deny/ask 规则后能压过白名单；黑名单优先于白名单生效；非白名单命令继续走模式兜底；命令拼接（如 `ls; npm run build`）不会靠白名单被放行
- `uv run ruff check src/sirius_agent` 通过

## 执行顺序

```
T1 ─┬→ T2 ─┐
    ├→ T3 ─┼→ T6 ─┐
    ├→ T4 ─┤      ├→ T7 → T8 → T9 → T10 → T11 → T12 → T13
    └→ T5 ─┘      │
          T4 ─────┘（T7 同时依赖 T4）
```
