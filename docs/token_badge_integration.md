# 发送前预消耗估算 + 运行中隐性行为警告灯

本功能采用**动静结合、低耦合**方案，通过 `ParsedSession` 与 `RendererHudPayload`
两个既有数据总线接入 CDP 渲染管线。已随代码落地，本文档描述最终实现。

## 新增文件

| 文件 | 职责 |
|------|------|
| `core/pre_send_estimator.py` | 静态底价估算（C+D+F），后台防抖线程，tiktoken/启发式双模 |
| `core/activity_monitor.py` | 运行中读取行为感知，默认零新增线程（复用快照） |
| `tests/test_pre_send_estimator.py` | 两个模块的单元测试 |

## 变量分工（关键设计）

底价 = **A + B + C + D + F**，但按“谁算最便宜”拆分到两侧：

| 变量 | 含义 | 计算方 | 原因 |
|------|------|--------|------|
| **A** | 当前输入框文本 | **浏览器 JS** | 只有 DOM 能实时拿到 composer 文本；打字即时刷新 |
| **B** | 会话历史 | **Python（快照）** | 直接取 `snapshot.confirmed.cumulative_input`，API 已确认，精确且免分词 |
| **C** | 静态约束文件 | **Python（后台）** | AGENTS.md / context.md / CLAUDE.md，变动缓慢，带缓存 |
| **D** | MCP 工具 Schema | **Python（后台）** | 通过 `mcp_schema_getter` 注入（当前留空，预留接口） |
| **F** | 协议底噪 | **Python** | 固定 +50 padding |
| **E** | 运行中偷读文件 | **Python（快照）** | 无法预测，改为行为感知警告灯 |

Python 侧把 **B+C+D+F** 合成 `preSendBaseTokens` 推给前端，JS 侧叠加实时 **A**
后显示总额。

## 三态状态机（同一 snapshot 驱动，无竞态）

```
未发送(静态)  ──检测到读取──▶  运行中(动态)  ──task_complete/abort──▶  结算(既有面板)
   │                          │                                       │
预估 ~150k Ts             ⚡AI正在深度读取:X.cs…                  轮次流水账单
(蓝色常态)                (黄灯脉冲滚动)                          (交棒给现有 UI)
```

- **静态 → 动态**：`detect_reading_activity()` 在快照里发现运行中的读取类
  `function_call`（`read_file`/`view`/`cat`/`grep`/MCP `filesystem.*` 等），`active=True`。
- **动态 → 结算**：`task_completed_at` / `task_aborted_at` 非空 ⇒ `active=False`，
  黄灯熄灭，既有轮次面板接管。

## 接入点（已落地）

1. **`core/parser.py`** — `ParsedSession` 新增 `estimate_base: BaseEstimate` 与
   `reading_activity: ReadingActivity` 两个字段。
2. **`cli.py`** —
   - `RuntimeContext` 新增 `pre_send_estimator` 字段，构造时 `start()`、`close()` 时停止；
   - `_apply_pre_send_and_activity()` 在 `snapshot_or_error` / `build_snapshot` 末尾注入
     底价（含 B）与读取行为，随会话 cwd 更新扫描根目录。
3. **`ui/renderer_hud.py`** —
   - `RendererHudPayload` 新增 `pre_send_estimate` / `pre_send_base_tokens` /
     `activity_warning` / `activity_reading_file`，并进入 `to_json()`；
   - 复用既有 `codex-usage-hud-token-badge`（请求面板右侧）扩展为三态：
     JS `updateComposerBadgeText()` 合成 base+live，`refreshComposerBadgeState()`
     切换 `data-badge-state="warning"` 黄灯脉冲。

## 依赖

`tiktoken` 为**可选依赖**：缺失时自动回退到 `calculator.estimate_tokens` 启发式，
功能不降级，仅精度略降。可在 `pyproject.toml` 中加 optional extra：

```toml
[project.optional-dependencies]
precise-estimate = ["tiktoken>=0.7"]
```

## 后续可扩展

- **D（MCP Schema）**：现为占位（`mcp_schema_getter=None`）。接入时从 Codex 的
  MCP 配置读取激活服务器的 tools schema 文本，传入 getter 即可，无需改其它层。
- **A 的服务端兜底**：目前 A 仅在浏览器侧计。若将来能从 runtime 拿到输入框文本，
  可填 `input_text_getter` 让 Python 侧也纳入 A。
