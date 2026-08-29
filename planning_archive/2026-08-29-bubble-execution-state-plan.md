# 会话气泡「执行状态」实施计划（最终版，已采纳方案 A 骨架）

日期：2026-08-29 ｜ 状态：待实施 ｜ 设计来源：本会话调研 + design-hub 四方案吸收
可视化演示：`tmp/bubble_step_ui_demo.html`（方案 A 左列即目标形态）

## 0. 已验证的事实基础（实施者无需重测）

数据源：codex rollout JSONL（`~/.codex/sessions/...`），解析在 `src/codex_usage_hud/core/parser.py`。

事件时序（Desktop 0.150.1 与 CLI 一致，实测会话 01a04b41）：
`task_started` → 思考期（rollout 静默 10-20s）→ `item_completed Reasoning`（折叠标题）→
`custom_tool_call`（命令开始，时间戳可计时）→ **执行期零事件（24s 长命令同样静默，硬约束）** →
`item_completed CommandExecution`（command/cwd/stdout/stderr/exit_code/duration 全量）→
`custom_tool_call_output` → `token_count` → … 循环 … → `item_completed FileChange`（apply_patch，
changes={文件: {type, unified_diff}}）→ `item_completed AgentMessage`（输出）→ `task_complete`。
等确认 = `request_user_input` 工具调用。

折叠标题字段（两种记录等价，均带 `**加粗**` 需去 markdown）：
- `response_item reasoning.summary[]`：`[{type:"summary_text", text:"**…**"}]`
- `event_msg item_completed Reasoning.summary_text`：`["**…**", …]`

parser 现状（渲染层已把最多 32 条 activitySteps 送到 UI，footer 只显示最后一条）：
- `ActivityStep`（parser.py:543）含 timestamp/title/detail/status/call_id/line/tool_name/output。
- 命令开始（function_call/custom_tool_call）开 running step，item_completed/function_call_output 关闭。

## 1. 必修的 parser 缺陷（已逐行核实）

1. **shell 包装剥离永不生效**：`parser.py:169-173` `_shell_launcher()` 去掉 `.exe` 后拿
   完整路径（`d:\program files\powershell\7\pwsh`）与裸名集合比对，恒 False →
   `_normalise_command_parts` 剥离分支被跳过 → footer 显示
   `D:\Program Files\PowerShell\7\pwsh.exe -Command <真实命令>`。
2. **输出无行结构且取头部**：`parser.py:52-59` `compact_text` 把换行压成空格；
   step.output 取输出开头 220 字符（真实数据：输出中位数 4283 字符、95% 多行）。
3. **干净命令字段未用**：`CommandExecution.parsed_cmd`（100% 覆盖、已剥离包装）未读取；
   `command_execution_text`（parser.py:206-212）只读 `command` 数组。

## 2. 工作包

### WP1 parser 修复与增强（core/parser.py + active_work.py + overlay_projection.py）

- WP1.1 修 `_shell_launcher`：basename 比对（`ntpath.basename` / 兼容 `\` 与 `/`），回归：
  完整路径 pwsh 包装可剥离。
- WP1.2 `command_execution_text` 优先 `parsed_cmd[].cmd`（取最后一个非空 cmd 段拼接），
  失败回退现有解析；长度上限 140 可放宽到 200。
- WP1.3 折叠标题接线：reasoning 活动（`_activity_from_record` :271-282 现在只给"正在思考"）
  把 summary_text 首条去 markdown（去 `**`、`#`、反引号）作为 detail/title；优先
  `item_completed Reasoning`，`response_item reasoning` 经现有 `reasoning_text()`（:84）兜底。
- WP1.4 `FileChange` → 编辑步骤：现 `item_completed` 分支对非 CommandExecution 直接
  continue（:2438 一带），补 FileChange：title「编辑文件」，detail=changes 键的 basename
  （多文件用 ` · ` 连接），output=各文件 `+行/-行` 摘要或 unified_diff 首行，status 沿用。
- WP1.5 CommandExecution 增强：step 新增 `exit_code`；output 改优先
  `formatted_output`→`stdout`（失败时 `stderr`），弃用 custom_tool_call_output 原始串；
  新增 `active_tail` 字段：output 按 `\r?\n` 切分取尾 3 行、每行 compact 120、**保留换行**。
- WP1.6 payload 直通：`active_work.py:353 _activity_step_payloads()` 增加
  `activeTail`/`exitCode`；`overlay_projection.py` 缓存合并按直传处理，不裁剪新字段。

### WP2 Qt 气泡渲染（ui/work_overlay/，用户实际观看的气泡）

- WP2.1 footer 折叠标题（qt_rendering.py:147-165 `_activity_step_display_text`）：
  选择优先级 = running 步骤 > 等确认 > 失败 > 最新 reasoning 标题 > 末条（现状）。
  命令文本中缀省略（保头 24 字符+尾 12，QFontMetrics 宽度制）；tooltip 仍为全量步骤（现状）。
- WP2.2 主区阶段切换（qt_rendering.py:630 一带）：存在未关闭步骤（或 task_started 后无新
  agent_message）→ body 渲染最近 3 条活动行，行型：
  `✓/✗ done(标题·耗时)`、`▸ 折叠标题`、`$ 命令`、`└ active_tail 尾行`、`⠙ live(本地计时)`；
  最新在底，超出取末 3 条。无活跃步骤 → 回退现有 lastText 逻辑（像素级不变）。
- WP2.3 本地计时与 spinner：复用 `_refresh_live_elapsed_text` 的 1s 节拍刷新进行行与
  footer 秒数；spinner glyph 轮换 QTimer 仅活跃期运行，常量开关
  `WORK_OVERLAY_FEED_SPINNER_ENABLED`（constants.py 新增，默认 True）。
  这是唯一新增动画；不新增任何轮询。
- WP2.4 行内「回输出」：live 行尾小字按钮，经 qt_hotspots 现有 anchor 机制注册点击 →
  body 临时切回输出视图（虚线/角标提示回看态），再点返回；点击主区兜底同效应保留。
- WP2.5 命令行交互：tooltip=完整命令（未 compact 原文）；点击复制到剪贴板
  （QApplication.clipboard()，同样走 hotspots）。
- WP2.6 完成归还：`agent_message`/`task_complete`/`turn_aborted` 到达（快照无未关闭步骤
  且 last_output 更新）→ body 回输出视图；失败滞留：末步 failed 且无新输出 → feed 保留、
  footer 红色（现有 error palette）；等确认黄色（waiting_user palette）。
- WP2.7 约束守护：不改 `constants.py` 布局常量（430/402/3 行/10/8/7/18px）、不动
  `qt_transitions.py`、不动 `ShimmerTextLabel` 参数；活跃态 feed 恒 3 行高，空闲态维持
  自适应高度（现状）。renderer 为产品正典（AGENTS.md），Qt 侧本次属用户指定气泡表面。

### WP3 renderer 数据/文案镜像（canonical 表面最小集）

- payload 已携带 activitySteps → 确认 `activeTail`/`exitCode` 直达 renderer；
- `renderer_activity_projection.py` 的 `executing_text`/`activity_main` 采用同一
  "活跃步优先 + 剥壳命令 + 折叠标题"策略，使 renderer 的"正在执行"文案与气泡一致。
- renderer 侧 3 行 feed UI 另开任务，本次不做。

### WP4 测试（最小集，遵守 AGENTS.md 测试范围约束）

- parser 回归（tests/test_parser.py 风格，fixture 参照 :90-249 时序）：
  shell 剥离、parsed_cmd 优先、reasoning 标题去 markdown、FileChange step、
  active_tail 尾 3 行保留换行、exit_code、失败 status、等确认分类不回归。
- 渲染最小断言（tests/test_ui.py 风格）：footer 活跃步优先、body 阶段切换与回退。
- 真实数据验证：本次调研已留存真实 rollout——
  `~/.codex/sessions/2026/08/29/rollout-2026-08-29T10-03-13-01a04b41-*.jsonl`
  （Desktop：24s 长命令/读/apply_patch/失败/等确认全路径）与同日 09-56/09-59 两个 CLI 会话；
  解析这三个文件应得到预期步骤序列与标题。
- 不跑全仓库/长耗时套件。

## 3. 验收标准

1. 命令执行期：footer 显示剥壳命令 + 本地秒数（无 pwsh.exe -Command 前缀）；主区显示
   最近 3 行活动；思考期 footer/主区显示折叠标题。
2. 命令完成瞬间：feed 出现保留换行的输出尾行；失败时含 stderr 尾行 + 退出码，footer 红色。
3. `agent_message`/`task_complete` 到达：主区立即恢复输出 3 行；空闲态与改动前视觉一致。
4. 等确认：feed 明示命令全文，footer 黄色「等确认 · 命令」。
5. 零新增轮询；spinner 仅活跃期；全部既有动画与布局常量不动。
6. WP4 最小测试集全绿；三个真实 rollout 解析结果符合预期。

## 4. 建议实施顺序

WP1.1+1.2（修命令显示，立竿见影）→ WP1.5+1.6（output/active_tail/exit_code）→
WP1.3+1.4（折叠标题/FileChange）→ WP2.1（footer 优先级）→ WP2.2+2.6（body 阶段切换与
归还）→ WP2.3+2.4+2.5（计时/回输出/复制）→ WP3 → WP4 全程穿插。
