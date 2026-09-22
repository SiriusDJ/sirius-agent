# Sirius-Agent（五层防御权限系统）Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [x] 黑名单能识别已知高危命令类别，且覆盖 Windows 与类 Unix 两种变体（验证：`check_blacklist` 对至少 5 类危险命令返回命中，对 `git status` 等正常命令返回 None）
- [x] 路径越界判断先解析符号链接再做前缀判断（验证：构造一个指向工作目录外的符号链接，经过判断被识别为越界）
- [x] 规则支持精确匹配和 glob 匹配（验证：`execute_command(git status)` 只匹配精确命令，`execute_command(git *)` 匹配所有 git 开头命令）
- [x] 规则合并按 deny>ask>allow 生效，与精确度、来源文件无关（验证：构造广泛 deny + 精确 allow 同时命中同一调用，结果为 deny）
- [x] 三档权限模式的兜底默认值表正确（验证：`fallback_decision` 对只读/有副作用工具在三档下的返回值符合 spec.md F7 定义的表格）
- [x] 人在回路四个选项（允许一次/拒绝一次/本会话允许/永久允许）均按预期更新状态（验证：T7 单元测试场景全部通过）

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
- [x] 场景 4（模式切换）：对同一个未配置规则的有副作用操作，分别在 `/permission strict`、`/permission default`、`/permission permissive` 下触发，观察判定依次为拒绝/询问/直接放行
- [x] 场景 5（配置错误处理）：故意在 `.sirius-agent/rules.yaml` 写一条 action 非法的规则，启动 `uv run sirius-agent` → 打印清晰错误信息并以非零状态码退出
