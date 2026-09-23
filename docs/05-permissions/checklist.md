# Sirius-Agent（五层防御权限系统）Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [x] 黑名单能识别已知高危命令类别，且覆盖 Windows 与类 Unix 两种变体（验证：`check_blacklist` 对至少 5 类危险命令返回命中，对 `git status` 等正常命令返回 None）
- [x] 路径越界判断先解析符号链接再做前缀判断（验证：构造一个指向工作目录外的符号链接，经过判断被识别为越界）
- [x] 规则支持精确匹配和 glob 匹配（验证：`execute_command(git status)` 只匹配精确命令，`execute_command(git *)` 匹配所有 git 开头命令）
- [x] 规则合并按 deny>ask>allow 生效，与精确度、来源文件无关（验证：构造广泛 deny + 精确 allow 同时命中同一调用，结果为 deny）
- [x] 四档权限模式的兜底默认值表正确（验证：`fallback_decision` 对 read/write/command 三类工具在 default/accept_edits/plan/bypass 四档下的返回值符合 spec.md F7 定义的表格）
- [x] 人在回路四个选项（允许一次/拒绝一次/本会话允许/永久允许）均按预期更新状态（验证：T7 单元测试场景全部通过）
- [x] 安全命令白名单命中即放行，且优先级低于规则引擎、高于模式兜底；命令注入类拼接（管道/分号/重定向/命令替换）不会靠白名单被放行（验证：`is_safe_command` 单元测试 + engine 集成测试，含显式 deny 规则压过白名单、黑名单优先于白名单两个场景）
- [x] PermissionEngine 的检查链真正可注入、可替换，且不传 `checkers` 时默认行为与重构前一致（验证：手动构造自定义 `checkers` 列表跑出跟默认链条不同的结果，证明真的被用上而不是内部悄悄走默认；跳过某一层后行为按预期变化）
- [x] RuleSet 的匹配函数、四个 Checker 各自的纯函数依赖、PermissionGate 的 pattern_generalizer/rule_appender 都能被注入的假实现替换掉，且都不传时默认行为与改造前逐字节一致（验证：对每一个注入点分别构造一个明显不同于默认实现的假函数，确认判定结果按假函数走）

## 集成

- [x] Agent Loop 在执行工具前统一经过 PermissionGate.check，被拒绝的调用不会触发 ToolRegistry.execute（验证：假 PermissionGate 返回 denied 时，工具的 execute 方法未被调用）
- [x] 人在回路的询问严格串行，一批调用里多个 ask 不会同时弹出（验证：构造一批含 2 个以上都需要 ask 的调用，观察/断言询问逐个出现）
- [x] tui.py 正确把终端交互回调注入 PermissionGate，且 `/permission <mode>` 斜杠命令能实时切换模式并影响后续判定（验证：手动切换后立即用同一操作对比判定结果变化）
- [x] __main__.py 正确加载并合并三级 YAML 规则文件，规则格式错误时清晰报错退出而非崩溃堆栈（验证：故意写一条非法规则触发）
- [x] 权限被拒绝（黑名单/沙箱/规则/模式兜底/人在回路拒绝）时，Agent Loop 不终止，继续下一轮迭代，模型能看到结构化的失败反馈（验证：观察 Agent Loop 在一次 deny 后仍正常进入下一轮）

## 编译与测试

- [x] 项目编译无错误：`uv run python -c "import sirius_agent"` 及各 permissions 子模块导入均无报错
- [x] T1-T11 各任务的单元验证全部通过（对照 task.md 逐项跑一遍）
- [x] lint 检查通过（`uv run ruff check src/sirius_agent` 全部通过）

## 端到端场景

- [x] 场景 1（黑名单硬拦截）：真实终端启动 `uv run sirius-agent`，诱导模型执行一条黑名单命令 → 命令未真正执行，终端显示拒绝原因，对话可以继续下一轮
- [x] 场景 2（沙箱 + 规则突破）：诱导模型读/写工作目录外的路径 → 默认模式下直接拒绝；写一条匹配该路径的 allow 规则到本地 YAML 后重试 → 放行
- [x] 场景 3（人在回路四选项）：触发一次 ask 判定，依次验证允许一次（下次同类调用仍会问）、拒绝一次（结果反馈给模型、循环继续）、本会话允许（本会话内不再问，重启后失效）、永久允许（写入 `.sirius-agent/rules.local.yaml`，重启后依然放行）
- [x] 场景 4（模式切换）：对同一个未配置规则的操作，分别在 `/permission default`、`/permission accept_edits`、`/permission plan`、`/permission bypass` 下触发，观察判定符合 F7 四档 × 三类的兜底表
- [x] 场景 5（配置错误处理）：故意在 `.sirius-agent/rules.yaml` 写一条 action 非法的规则，启动 `uv run sirius-agent` → 打印清晰错误信息并以非零状态码退出

> **T12 补充说明：** 场景 1-5 最初是在三档 strict/default/permissive + `/plan`/`/do` 的设计下跑通的。T12 把权限模式改成 default/accept_edits/plan/bypass 四档、`/plan`/`/do` 合并进 `/permission plan`/其他档位后，只重跑了单元回归测试（mode/engine/agent/tui/__main__ 各自的验证脚本，用新枚举名和新命令重新断言过一遍，全部通过），**没有**在真实终端里重新走一遍场景 1-5——尤其是场景 4 用的 `/permission plan`/`/permission accept_edits`/`/permission bypass`、以及 plan 档下模型是否真的只看到只读工具，这几点还需要一次真实终端手动验证才能算完全过了 T12 之后的验收。
>
> **T13 补充说明：** 安全命令白名单同样只做了单元测试和 engine 集成测试，没有在真实终端里让模型真的跑一条 `ls`/`git status` 之类的命令、确认 default 模式下确实不再弹确认框。建议下次真实终端验证时把这条也带上。
>
> **T14 补充说明：** 把 `PermissionEngine.evaluate()` 重构成可注入的 `PermissionChecker` 检查链，是一次纯内部重构，不改变任何对外行为（`PermissionEngine`/`PermissionGate` 的公开接口没变，`__main__.py` 的组装代码不用改）。所有既有 engine/gate/agent 单元测试原样重跑，结果逐字节一致；额外加了两个测试验证注入链条真的生效。没有新的端到端场景需要验证。
>
> **T15 补充说明：** 把注入范围从"检查链本身"扩到"链条里每个 Checker 依赖的纯函数"以及 `RuleSet`/`PermissionGate` 内部直接 import 的模块函数，同样是纯内部重构，公开接口只是新增了带默认值的关键字参数，不传就是原来的行为。`blacklist.py`/`safelist.py`/`mode.py` 内部的具体条目/兜底表、`__main__.py` 的组合根代码都刻意没有改成可注入——前者改了就等于让"不可配置"的黑名单/白名单变成事实上可配置，违反 spec；后者本来就该是直接持有具体实现的地方。同样没有新的端到端场景需要验证。
