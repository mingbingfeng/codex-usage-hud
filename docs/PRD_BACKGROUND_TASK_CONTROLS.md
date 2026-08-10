# PRD：用量总览中的后台任务控制

- **状态**：待开发，已完成官方/GitHub 可行性调研
- **提出日期**：2026-08-10
- **适用模块**：Renderer HUD 设置、用量总览、Codex App 设置适配、后台用量审计
- **目标入口**：设置 → 用量总览 → 点击后台任务 → 右侧详情区域
- **目标平台**：Windows、macOS；实现只走 Renderer/CDP 路径

## 1. 决策摘要

本需求可以实现，但不能把五类后台任务都承诺为同一种“硬关闭”。公开资料只验证了两类控制能力：

1. **记忆整理**：可以通过 Codex 的公开总开关 `[features] memories = false` 停止记忆写入启动管线。
2. **上下文建议**：可以通过 Codex Desktop 原生的 Suggested prompts / context-aware suggestions 设置关闭；目前没有可稳定依赖的公开 TOML 键。
3. **建议安全检查**：没有独立公开开关，按上下文建议联动关闭，并必须用真实请求日志验证第二阶段安全检查也停止。
4. **任务标题与描述**、**刷新任务描述**：当前公开资料没有可验证的关闭接口，只能提供审计、告警和“暂不可禁止”的明确状态，不能伪造配置键。
5. **未知后台任务**：只保留识别和审计，不默认阻断。

按钮仍统一使用用户指定的初始文案 **“禁止此后台任务”**，但按钮能否执行、执行后显示什么状态，由所选功能的控制能力决定。不能因为本地策略记录写入成功，就声称 Codex 已停止发请求。

## 2. 背景与目标

用量总览已经按 feature key 识别 Codex App 的后台请求，并在左侧历史列表、右侧详情面板展示模型、请求次数、token、时间段、运行目录和请求明细。当前详情面板只有查询和审计能力，没有面向后台功能的控制入口。

用户需要在看到某个后台功能产生用量后，立即知道：

- 该功能是否有官方、可验证的关闭方式；
- 点击“禁止此后台任务”后，是否只影响后续请求；
- 是否会连带关闭其他后台功能；
- 当前版本是否真正停止了对应请求，而不是只隐藏 HUD 记录。

本 PRD 将调研结论转化为一个可落地的控制协议和 UI 合同，供新的 Codex 开发线程直接实现。

### 2.1 产品目标

- 在选中后台任务的右侧详情标题行最右侧提供控制按钮。
- 对有公开、可验证控制方式的功能执行真实控制。
- 对联动控制明确告知影响范围，不能伪装成独立开关。
- 对没有公开控制方式的功能保留审计和告警，但不执行未经验证的阻断。
- 所有控制结果都区分“已配置”“已验证”“需要用户在 Codex 设置中操作”“不可用”。
- 历史后台用量、请求明细和成本结果保留，不因为禁止操作被删除或重算。
- 不影响前台会话、普通 API 请求或其他 provider 的请求。

### 2.2 成功标准

用户从一条后台用量详情开始，最多经过一次确认，就能得到以下其中一种可解释结果：

- `已禁止并验证`：后续同类请求停止；
- `已提交，等待验证`：设置已读回，但尚未获得足够的真实日志证据；
- `需要在 Codex 设置中关闭`：HUD 已打开或定位到官方设置，但不能代替用户点击；
- `当前版本未发现可验证的官方关闭接口`：按钮不可执行，只提供审计；
- `禁止失败`：原配置和已验证状态未被破坏，错误原因可见。

## 3. 范围

### 3.1 包含

- Renderer 设置页“用量总览”后台任务详情标题行的控制按钮。
- 后台功能控制能力查询、确认、执行、读回和验证状态。
- 记忆整理的配置适配器。
- Suggested prompts 的 Desktop 原生设置适配器或设置引导。
- 建议安全检查与上下文建议的联动规则。
- 不可控任务的禁用态、原因提示和用量告警。
- 控制动作的本地审计记录。
- 后台任务历史用量保持不变，后续请求按控制生效时间区分。
- Renderer/CDP、localhost bridge、运行时命令、单元测试和契约测试。

### 3.2 不包含

- 全局网络防火墙、hosts、代理或 endpoint 拦截。
- 通过隐藏 HUD 记录、过滤日志或改写历史数据来制造“已禁止”。
- 未被官方或当前 Codex 版本验证的 TOML 键。
- 对前台请求、普通会话、provider fallback 的全局拦截。
- 将 Qt/Tk HUD 作为实现或兜底路径。
- 修改历史 token、成本、事件时间或请求明细。
- 为不可控功能设计私有“强制阻断”协议。
- 把本地 `desktop.ambient-suggestions-enabled` 这类内部状态键定义为稳定公开配置协议。

## 4. 后台功能控制能力矩阵

后台事件的稳定身份是 `featureKey`，不是单次 `eventId`。在某一条事件详情中点击按钮，控制的是该 feature 后续产生的所有后台任务；该事件本身只用于展示、审计和关联验证。

| featureKey | 展示名称 | 控制级别 | 执行方式 | 联动/限制 | 初始实现状态 |
| --- | --- | --- | --- | --- | --- |
| `memory_consolidation` | 记忆整理 | `hard_supported` | 写入 `[features] memories = false`，原子保存，重新读取配置 | `memories.generate_memories` 和 `memories.use_memories` 不是总开关；关闭后历史记录保留 | 可执行 |
| `context_suggestions` | 上下文建议 | `native_supported` 或 `requires_user_action` | 使用 Desktop 原生 Suggested prompts 设置；无公开 TOML 时打开 `codex://settings` 并定位设置 | 关闭后要验证建议请求停止；不能写入猜测的 `ambient_suggestions` 键 | 可执行或引导 |
| `suggestion_safety` | 建议安全检查 | `linked_supported` | 复用上下文建议适配器 | 不提供独立底层开关；必须明确“会同时禁止上下文建议” | 联动 |
| `title_description` | 任务标题与描述 | `unsupported` | 不写 Codex 配置，不做网络拦截 | 显示无公开关闭接口，继续记录和告警 | 只审计 |
| `description_refresh` | 刷新任务描述 | `unsupported` | 不写 Codex 配置，不做网络拦截 | 显示无公开关闭接口，继续记录和告警 | 只审计 |
| `unknown` | 未知后台任务 | `unknown` | 不执行控制 | 只告警；禁止按钮不可用 | 只审计 |

### 4.1 语义规则

- `hard_supported`：外部设置有公开定义，代码有明确早期返回或等价行为，且可以读回。
- `native_supported`：官方 Desktop 有设置，但配置文件格式没有公开稳定键；只允许通过原生设置适配器。
- `requires_user_action`：HUD 能打开或定位官方设置，但当前版本没有安全的程序化写入接口。
- `linked_supported`：自身没有独立开关，必须通过另一个已验证功能的开关联动。
- `unsupported`：公开资料未证明存在关闭接口；不得将“没有观测到请求”当作关闭成功。
- `unknown`：分类不确定；先保留事件和证据，等待版本适配。

## 5. UI 交互与布局

### 5.1 精确位置

按钮放在：

设置 → 用量总览 → 左侧点击一条真实后台任务 → 右侧详情面板 → 详情标题行最右侧。

当前红框位置是标题行右侧的时间标签。实现时：

- 该位置替换为控制按钮；
- 时间信息保留在下方详情网格的“时段”字段；
- 不能覆盖功能标题、副标题、模型信息或请求明细；
- 不放到设置页底部全局操作栏；
- Top10 会话排行详情、空状态和加载状态不显示该按钮。

现有 DOM 合同：

- 右侧容器：`.codex-usage-hud-background-detail`
- 标题行：`.codex-usage-hud-background-detail-head`
- 标题左侧：`minmax(0, 1fr)`
- 操作右侧：`auto`
- 复用样式：`.codex-usage-hud-settings-action`

### 5.2 按钮文案和状态

初始可执行状态必须显示：

**禁止此后台任务**

| 状态 | 文案 | 可点击 | 说明 |
| --- | --- | --- | --- |
| `enabled` | 禁止此后台任务 | 是 | 进入确认流程 |
| `pending` | 正在禁止… | 否 | 等待外部设置写入/读回/日志验证 |
| `verified_disabled` | 已禁止此后台任务 | 否或显示后续恢复动作 | 只在证据满足后展示 |
| `requires_user_action` | 禁止此后台任务 | 是 | 打开官方设置或显示操作引导，不能直接宣称成功 |
| `unsupported` | 禁止此后台任务 | 否 | `title`/辅助状态说明当前版本未发现可验证接口 |
| `unknown` | 禁止此后台任务 | 否 | 显示未知功能不能默认阻断 |
| `error` | 禁止此后台任务 | 是 | 可以重试；不得保留未验证的成功状态 |

如果某个适配器支持恢复，已禁止状态下显示次级动作 **允许此后台任务**。恢复也必须走读回和日志验证。对于 `suggestion_safety`，恢复动作只能恢复上下文建议联动链路，不能伪造独立安全检查开关。

尺寸和防溢出要求：

- `min-width` 112px，建议 120px；
- `min-height` 28px；
- `white-space: nowrap`；
- 标题左侧 `min-width: 0`，按钮 `flex: 0 0 auto`；
- 小窗口/移动宽度下标题允许省略，按钮不能被挤出或覆盖；
- 复用现有主题色和焦点样式，不新增 Qt/Tk 样式。

### 5.3 确认文案

#### 记忆整理

标题：**禁止“记忆整理”**

> 将关闭 Codex 的 Memories 总开关。以后新的记忆整理后台任务不会启动；已有后台用量和请求明细保留，不会被删除或重算。确认后会重新读取配置，并等待日志验证。

操作：`取消`、`确认禁止`

#### 上下文建议

标题：**禁止“上下文建议”**

> 将关闭 Codex Desktop 的 Suggested prompts / context-aware suggestions。当前版本如果不能安全地由 HUD 直接写入原生设置，会打开 Codex 设置让你完成关闭；在读回设置和日志验证前，不会显示“已禁止”。

操作：`取消`、`打开设置并禁止`

#### 建议安全检查

标题：**禁止“建议安全检查”**

> “建议安全检查”没有独立公开开关。继续操作会同时关闭上下文建议，因此以后不会生成这条建议链路及其后续安全检查请求；前台会话不受影响。

操作：`取消`、`同时禁止上下文建议`

#### 不可控/未知任务

按钮不可点击，不弹确认框。辅助状态必须明确：

> 当前 Codex 版本未发现可验证的官方关闭接口。HUD 仍会记录用量并在产生新请求时告警。

### 5.4 成功后的详情刷新

- 保持设置页、用量总览页签、选中事件和右侧滚动位置不变。
- 只刷新控制状态、按钮状态和提示，不重建历史用量。
- 后台用量查询仍可正常读取；控制响应不得被普通查询响应覆盖。
- 控制状态按 `featureKey` 展示，即使切换到同一功能的另一条历史事件，也应保持一致。

## 6. 用户流程与状态机

### 6.1 主流程

1. 用户进入“用量总览”，点击后台任务。
2. Renderer 读取该事件详情和该 `featureKey` 的控制策略。
3. 标题行显示 capability 对应的按钮状态。
4. 用户点击“禁止此后台任务”。
5. Renderer 根据 capability 展示确认框、打开官方设置或拒绝执行。
6. 确认后发送带 `requestId`、`featureKey`、`expectedPolicyRevision` 的控制命令。
7. Runtime 调用对应 adapter；适配器原子写入或触发原生设置流程。
8. Runtime 读回外部状态；必要时等待一次日志/进程事件验证，不使用高频轮询。
9. Renderer 接收相关响应，更新按钮和状态提示。
10. 如果验证失败，恢复到 `error`/`requires_user_action`，不能显示为已禁止。

### 6.2 状态转移

    enabled
      └─ 点击并确认 → pending
                       ├─ 配置读回 + 真实日志验证 → verified_disabled
                       ├─ 需要用户在 Codex 设置中点击 → requires_user_action
                       └─ 写入/读回/验证失败 → error → 可重试

`verified_disabled` 只能由运行时证据产生，不能由 Renderer 本地点击直接产生。没有足够新请求样本时，允许显示 `configured_unverified`，但不能降级成“已禁止”。

## 7. 控制策略数据与持久化

新增本地控制策略存储，不修改历史后台请求表。每条策略至少包含：

- `feature_key`
- `desired_state`：`enabled` 或 `disabled`
- `capability`
- `effective_at`：用户确认时刻，UTC
- `policy_revision`
- `last_attempt_at`
- `last_verified_at`
- `verification_state`
- `adapter_id`、`adapter_version`
- `external_state_fingerprint`（只保存状态摘要，不保存 prompt）
- `last_error_code`、`last_error_message`
- `source`：`usage_detail`、`native_settings`、`migration`

规则：

- 通过 `featureKey` 全局生效，不按单次 `eventId` 生成假的局部开关。
- 保存策略不删除、不更新、不重算 `background-usage.sqlite3` 中的历史事件和请求。
- 策略写入失败必须原子回滚；现有可用策略和已验证状态保持不变。
- 版本升级发现适配器失效时，降级为 `requires_user_action` 或 `unsupported`，不能保留过期的“已禁止”承诺。
- `unknown` 不写入阻断策略，只写审计和告警。

建议增加本地审计表或等价 JSONL，字段至少包括：

- `action_id`、`feature_key`、`event_id`、`requested_state`
- `capability`、`started_at`、`completed_at`
- `verification_state`、`evidence_kind`、`error_code`

审计不记录完整请求 prompt、token 内容或账号隐私信息。

## 8. 命令与响应契约

现有 `codexUsageHudSettings` CDP binding 和 settings bridge 的查询/详情响应合同继续保留。新增控制命令必须使用同样的 request correlation，不得依赖全局最后一次响应。

### 8.1 查询能力

动作名：`backgroundUsagePolicyQuery`

请求字段：

- `requestId`
- `featureKey`
- `eventId`（可选，仅用于来源和详情关联）

响应字段：

- `kind: policyQuery`
- `requestId`、`featureKey`、`capability`
- `desiredState`、`effectiveState`、`verificationState`
- `canDisable`、`canEnable`、`requiresUserAction`
- `message`、`policyRevision`

### 8.2 设置策略

动作名：`backgroundUsagePolicySet`

请求字段：

- `requestId`
- `featureKey`
- `desiredState: disabled | enabled`
- `eventId`（可选）
- `expectedPolicyRevision`
- `source: usage_detail`

响应字段：

- `kind: policyApply`
- `requestId`、`featureKey`
- `desiredState`、`effectiveState`、`verificationState`
- `policyRevision`、`adapterId`、`externalState`
- `evidence`、`error`

`verificationState` 允许值：

`pending`、`verified`、`configured_unverified`、`requires_user_action`、`unsupported`、`failed`。

### 8.3 HTTP fallback

如果 Renderer binding 不可用，localhost bridge 提供等价合同：

- `GET /background-usage/policy?feature=...`
- `POST /background-usage/policy`

继续使用现有 `access_token` 校验。HTTP 和 CDP 的响应字段必须一致，不能出现一套路径声称已禁止、另一套路径仍显示启用。

### 8.4 刷新域和超时

- `backgroundUsagePolicyQuery`、`backgroundUsagePolicySet` 只刷新 `backgroundUsage` 域及其控制状态。
- 设置响应必须包含 requestId；旧响应不得覆盖新选中事件的按钮状态。
- 写入/读回/验证使用一次性超时和事件唤醒，禁止新增固定间隔高频轮询。
- Renderer 的 pending 状态在超时后变为 `failed` 或 `requires_user_action`，并允许重试。

## 9. 外部 Codex 设置适配器

### 9.1 Memories adapter

适配器读取和写入用户实际使用的 Codex 配置存储：

- 目标键：`[features] memories`
- 禁止值：`false`
- 恢复值：`true`

写入要求：

1. 读取当前配置和版本信息；
2. 保存前校验 TOML/配置模型；
3. 原子写入，保留原有 `memories.generate_memories`、`memories.use_memories`；
4. 写入后重新读取并确认 `features.memories == false`；
5. 通过新进程/实时日志验证记忆写入启动管线不再创建任务；
6. 如果当前 Codex 进程必须重启才能生效，返回 `requires_restart`，在重启和验证完成前不显示已禁止。

注意：`memories.generate_memories = false` 只控制是否生成记忆输入，`memories.use_memories = false` 只控制记忆注入，都不能替代总开关。

### 9.2 Suggested prompts adapter

官方 Desktop 设置名称是 Suggested prompts / context-aware suggestions。当前官方 Config Reference 没有对应公开 TOML 键。

适配器优先级：

1. 如果当前 Desktop 版本提供受支持的原生设置调用，使用该调用并读回；
2. 否则打开 `codex://settings` 或等价设置入口，并定位到 Suggested prompts；
3. 在用户完成操作后重新读取原生设置；
4. 结合日志确认建议请求停止；
5. 无法读取或验证时显示 `requires_user_action`/`configured_unverified`，不伪造成功。

本机发现的 `[desktop] ambient-suggestions-enabled` 只能作为当前版本内部状态的观察结果，不能作为跨版本、跨平台或 CLI 的公开协议。新线程不得直接把它写成产品契约。

### 9.3 Suggestion safety adapter

建议安全检查的已知行为是上下文建议生成后的后续 safety/compliance pass。由于没有独立公开开关：

- 禁止请求必须转发到 Suggested prompts adapter；
- 确认文案必须说明会同时禁止上下文建议；
- 只有上下文建议关闭且安全检查请求也通过日志验证停止时，才显示 `verified_disabled`；
- 恢复时同样只能恢复整条联动链路。

### 9.4 不可控任务 adapter

`title_description`、`description_refresh` 和 `unknown` 不实现写配置、发防火墙规则或阻断网络的 adapter。它们只提供：

- capability 查询；
- 继续审计；
- 新请求告警；
- 版本/官方接口变化后重新评估的扩展点。

## 10. 真实验证与审计

“关闭成功”至少需要两类证据中的一类强证据和一类辅助证据。

### 10.1 配置/原生设置证据

- 配置文件重新读取后的实际值；
- Desktop 原生设置 API 或可见设置状态；
- 适配器版本、读回时间和外部状态摘要。

### 10.2 请求行为证据

- 控制生效时间之后，后台日志中不再出现对应 feature signature；
- 若有安全测试触发器，确认 Suggested prompts 的生成请求和后续 safety 请求均停止；
- 当前进程未重启时，不能只用旧配置值推断新行为；
- 远程/SSH 或 app-server 场景单独标记“客户端开关可能不生效”，不能复用本地结论。

### 10.3 观测窗口

- 记录 `effective_at` 和验证窗口结束时间；
- 只检查新产生的日志，不扫描或重算历史用量；
- 没有新事件时使用 `configured_unverified`，不要把“没有数据”当成“没有请求”；
- 一旦发现控制后仍有对应请求，立即标记 `failed` 并在用量总览显示告警。

用户可见提示：

- 成功：**已禁止此后台任务，后续请求已通过日志验证停止。**
- 已写入但未验证：**设置已写入，尚未获得足够的后台日志证据；不会把它标记为已禁止。**
- 需要用户操作：**请在 Codex 设置中关闭 Suggested prompts，完成后返回此处验证。**
- 失败：**禁止失败，现有配置未改变。** 后跟简短错误原因和重试入口。

## 11. 错误、回滚与安全

- 配置解析失败、写文件失败或读回不一致：不提交策略，不更新按钮为成功。
- 外部设置版本不匹配：降级为 `requires_user_action`，不写猜测键。
- 控制请求超时：保留历史记录，策略进入 `failed`/`configured_unverified`，可重试。
- 并发操作发现 `expectedPolicyRevision` 过期：拒绝旧写入，重新读取当前策略。
- 用户取消确认：不写配置、不写“已禁止”审计，仅记录取消（可选）。
- 恢复失败：保持 `verified_disabled` 或 `failed` 的真实状态，不能为了 UI 好看回写 enabled。
- 不使用防火墙、代理、hosts、全局 endpoint 拦截，避免误伤前台请求。
- 不把 prompt、token、账号路径写入控制审计；沿用现有详情展示的脱敏规则。
- 配置备份和恢复必须是明确的本地可恢复操作，不自动删除用户文件。

## 12. 实现映射

新线程应按以下边界实现，避免将控制逻辑塞入 Renderer 字符串：

1. `src/codex_usage_hud/core/background_usage.py`
   - 复用现有 `BACKGROUND_FEATURE_LABELS` 和 feature key；
   - 为未知 key 保持只读审计语义；
   - 不修改历史请求分类和 token 统计。
2. 新建 `src/codex_usage_hud/background_control.py` 或等价领域模块
   - capability、policy record、adapter 接口、状态机、审计和验证状态；
   - 让 memories/native/linked/unsupported 适配器可测试。
3. `src/codex_usage_hud/runtime_commands.py`
   - 扩展后台命令白名单；
   - 校验 feature key、desired state、policy revision；
   - 统一返回 policy response，不吞异常。
4. `src/codex_usage_hud/runtime_settings.py`
   - 把 policy query/set 映射到 `backgroundUsage` 域；
   - 增加 response correlation 和 pending 判断。
5. `src/codex_usage_hud/settings_bridge.py`
   - 增加带 access token 的 policy GET/POST fallback；
   - HTTP 与 CDP 使用同一 runtime service。
6. `src/codex_usage_hud/renderer_runtime_assembly.py`
   - 注入控制 runtime 和 adapter；
   - 保持现有 background usage runtime 的生命周期。
7. `src/codex_usage_hud/renderer_assets/background_usage.py`
   - 在 `backgroundUsageDetailHtml` 标题行右侧渲染按钮；
   - 按 feature capability 渲染状态；
   - 只在真实后台 event detail 渲染，保留时间在详情网格；
   - 提交 policy query/set，并保持滚动、选择和 prompt 状态。
8. `src/codex_usage_hud/renderer_assets/router.py`
   - 增加禁止/恢复动作；
   - 复用现有 settings confirm/status 流程；
   - 不在 router 内直接改 TOML 或判断“已验证”。
9. `src/codex_usage_hud/renderer_assets/layout_style.py`
   - 复用 `.codex-usage-hud-settings-action`；
   - 保持标题左侧可收缩、按钮固定宽度、响应式不重叠；
   - 不增加 Qt/Tk 样式。
10. `tests/`
   - 增加 adapter、策略状态机、原子保存、读回、日志验证和回滚测试；
   - 增加 Renderer DOM/action/响应关联合同；
   - 增加 bridge 鉴权、HTTP/CDP 一致性和 revision 冲突测试；
   - 保留现有后台用量查询、详情、历史不变测试。

## 13. 验收标准

### 13.1 UI 合同

- 进入设置 → 用量总览并选中真实后台事件后，详情标题行最右侧出现按钮。
- 初始可执行按钮文案严格为“禁止此后台任务”。
- 时间仍在“时段”详情中可见，标题、副标题和按钮无重叠。
- 空状态、加载状态、Top10 会话排行详情和未知任务不会显示可执行控制。
- 详情滚动、左侧选择、设置页签在控制响应后保持不变。
- 按钮具备键盘焦点、禁用态、辅助标签和可读错误提示。

### 13.2 功能控制

- `memory_consolidation` 关闭后，配置读回为 `features.memories = false`，并通过真实日志验证新记忆写入任务停止。
- `context_suggestions` 不写入未公开 TOML；能自动适配时读回原生设置，不能自动适配时明确引导用户。
- `suggestion_safety` 禁止时明确联动上下文建议，并验证两类请求都停止。
- `title_description`、`description_refresh`、`unknown` 按钮禁用并显示无验证接口，不产生网络拦截。
- 远程/SSH 场景显示风险或未验证状态，不把本地设置结果复制为远程成功。

### 13.3 历史与隔离

- 禁止操作不删除、不修改、不重算历史后台事件、请求、token、成本和时间。
- 控制只针对相应 feature 后续请求，不影响前台会话和其他 provider。
- HUD 仍记录新请求和控制后异常请求，不能通过隐藏记录制造成功。
- 相同 feature 的新历史事件显示同一策略状态。

### 13.4 失败安全

- 任何写入失败、读回不一致、验证超时或 revision 冲突都不会显示 `verified_disabled`。
- 取消确认不会改变配置。
- 不支持的功能不会写入猜测键。
- CDP binding 和 HTTP fallback 的状态结果一致。

### 13.5 验证命令

实现线程完成后至少运行：

- `python -m pytest -q`
- `python -m compileall -q src tests tools`
- `rtk git diff --check`

如果需要运行真实 Codex App 验证，必须记录：

- Codex/App/CLI 版本；
- 使用的 adapter 和配置路径；
- 控制前后的关键日志时间；
- 配置/原生设置读回值；
- 新请求是否仍出现；
- Windows、macOS 各自的结果；
- SSH/远程场景是否明确标记未验证。

## 14. 公开证据与限制

以下链接是本 PRD 的调研依据，访问/基线日期为 2026-08-10：

官方文档：

- Memories：https://developers.openai.com/codex/customization/memories
- Chronicle：https://developers.openai.com/codex/customization/chronicle
- Desktop Settings / Suggested prompts：https://developers.openai.com/codex/app/settings#context-aware-suggestions

公开源码（Codex 基线 `89a335ed50258dc9dc5b3d7f410db61b431244f9`）：

- `Feature::MemoryTool`：https://github.com/openai/codex/blob/89a335ed50258dc9dc5b3d7f410db61b431244f9/codex-rs/features/src/lib.rs#L1008-L1013
- Memories 写入启动管线的 feature 早期返回：https://github.com/openai/codex/blob/89a335ed50258dc9dc5b3d7f410db61b431244f9/codex-rs/memories/write/src/start.rs#L19-L37

GitHub Issue/逆向资料：

- Ambient 可关闭但设置隐藏：https://github.com/openai/codex/issues/25302#issuecomment-4606677266
- 猜测 Ambient TOML 键不可靠：https://github.com/openai/codex/issues/29380
- Ambient 安全检查第二请求、SSH 场景开关可能无效：https://github.com/openai/codex/issues/30606
- 标题生成没有 config override：https://github.com/openai/codex/issues/28741
- 隐藏 thread description/helper session：https://github.com/openai/codex/issues/19858
- Ambient 刷新链路（逆向，非官方协议）：https://github.com/JimLiu/decode-codex/blob/main/restored/main/ambient/ambient-suggestions-background-refresh/availability.ts
- Ambient 建议与安全检查链路（逆向，非官方协议）：https://github.com/JimLiu/decode-codex/blob/main/restored/main/ambient/ambient-suggestions/refresh.ts

证据使用边界：

- 官方文档优先于 GitHub Issue 和逆向仓库。
- Issue/逆向资料只用于解释现象和风险，不构成稳定配置协议。
- “公开资料未发现关闭接口”不等同于私有实现绝对不存在；因此不可控任务只能审计，不能承诺阻断。
- Codex Desktop、CLI、远程 app-server 可能使用不同设置路径；必须按运行环境分别验证。

## 15. 新线程开发约束

1. 开始编码前先阅读 `docs/RENDERER_MODE_STRATEGY.md` 和本 PRD。
2. 先实现 capability/adapter/响应合同，再接 Renderer 按钮。
3. 不要先做“统一禁用所有后台请求”的抽象；每个 feature 必须有独立证据和能力级别。
4. 不要把本地策略状态当成 Codex 已停止请求的证明。
5. 不要修改 Qt/Tk 产品行为，不要把 Qt/Tk 作为失败兜底。
6. 不要删除历史后台用量或修改现有 token/cost 统计。
7. 不要添加 `ambient_suggestions=false`、`title_generation=false`、`description_refresh=false` 等未经官方验证的配置键。
8. 真实 Codex App 验证完成前，按钮最多显示“已配置，待验证”，不能显示“已禁止”。
9. 如果官方接口、Codex 版本或 Desktop 设置变化，先更新 capability matrix 和证据，再修改 adapter。
