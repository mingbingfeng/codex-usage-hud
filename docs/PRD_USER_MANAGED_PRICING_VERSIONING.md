# PRD：用户维护单价与按生效时间计费

- **状态**：已实现，验收通过
- **提出日期**：2026-08-05
- **适用模块**：Renderer HUD 设置、价格配置、会话用量统计、后台用量统计
- **目标版本**：下一次价格配置功能迭代

## 1. 背景与目标

HUD 的单价必须由用户自己维护。用户通常在官方价格已经调整后才获知并修改 HUD；如果修改后整段历史都使用新价格，历史费用会被错误重算。因此需要把一次改价定义为一个新的价格版本，并允许用户指定该版本的生效时间。

本需求解决两件事：

1. 改价时选择“新价格从何时开始生效”，按每条用量发生时间选择旧价或新价。
2. 让用户可以直接导出当前价格 JSON，并获得带字段说明的空模板，能够在外部编辑后导入。

## 2. 用户故事

- 作为用户，我修改某个模型单价后，希望系统明确询问“新价格从什么时候开始生效”。
- 作为用户，我只在今天才知道官方前天降价，希望把新价格从前天 09:00 起应用，而不是只能从现在开始。
- 作为用户，我导出价格表后，希望导出的 JSON 能再次导入，并保留 provider、模型匹配规则和缓存价格。
- 作为用户，我没有现成 JSON 时，希望下载一个空模板，照字段填写即可。
- 作为用户，我填错价格或时间时，希望导入失败且现有价格不受影响。

## 3. 范围

### 包含

- Renderer 设置页的价格编辑、批量改价、导入、导出、空模板下载。
- 全局价格表和 provider 价格表的生效时间管理。
- 普通会话、CLI 会话和后台请求的按时间计价。
- 价格版本和价格快照的持久化、查询和展示。
- 迁移现有配置并保证旧数据可读。

### 不包含

- 自动抓取或自动判断官方价格。
- HUD 替用户决定官方调价日期。
- 外币换算、订阅额度折算、税费和服务层级（Standard/Fast）新规则。
- 静默重算并改写已经展示给用户的历史费用。
- Qt/Tk 新产品功能；实现必须位于 Renderer/CDP 路径。

## 4. 术语与核心规则

### 4.1 生效时间

界面文案建议使用 **“新价格生效时间”**，辅助说明为：**“发生在此时间之前的用量继续按旧价格计算；此时间及之后的用量按新价格计算。”**

- 默认值：打开改价确认弹窗时的当前本地时间，精确到分钟。
- 用户可选择过去时间或当前时间；不得选择未来时间。
- 时间必须带时区并存储为 UTC 时间戳；展示使用系统本地时区。
- 边界采用左闭右开：`occurred_at < effective_at` 使用旧价格，`occurred_at >= effective_at` 使用新价格。
- 按请求/事件发生时间判断，不按文件修改时间、HUD 启动时间或导入时间判断。

### 4.2 价格版本

每次确认改价生成一个不可变版本：

- `version_id`：唯一 ID。
- `provider`、模型匹配字段（`model`/`model_pattern`、`base_url`）。
- `input`、`cached_input`、`cache_write`、`output`、`reasoning`，单位均为 USD/1M tokens。
- `effective_at`、`created_at`、`created_by`（`user_import`、`user_edit`、`builtin_migration`）。
- `source` 可选为 `manual`、`import`、`builtin`；不代表官方认证。

同一 provider + 模型匹配范围不得存在相同 `effective_at` 的两个版本。若冲突，后保存的版本覆盖前一个版本，但必须保留版本 ID 和审计记录，不能产生不确定匹配。

### 4.3 计价选择

对每条用量：

1. 先按 provider、base URL、模型匹配优先级解析价格范围。
2. 在该范围内选择 `effective_at <= occurred_at` 的最新版本。
3. 找不到历史版本时，使用现有内置价格作为离线兜底，并把费用标记为 `fallback`；不得把当前新价格倒灌到更早用量。
4. 找到价格后保存完整 `price_snapshot`，以后价格表变化不影响已确认记录。
5. 未知模型或缺少必要价格字段时，费用为 `unavailable`，不显示为 `$0`。

费用公式沿用现有语义：

```text
billable_input = input_tokens - cached_input_tokens - cache_write_tokens
cost = billable_input * input_price
      + cached_input_tokens * cached_input_price
      + cache_write_tokens * cache_write_price
      + output_tokens * output_price
```

均除以 1,000,000；`output_tokens` 已包含 reasoning output 时不得重复计费。

## 5. 交互设计

### 5.1 改价确认弹窗

触发：用户在设置页修改任意模型价格并点击“保存价格”、导入新价格或批量覆盖。

弹窗内容：

- 标题：**“设置新价格的生效时间”**。
- 明确提示：**“早于该时间的历史用量保留原价格；从该时间开始的新用量使用这次价格。”**
- 生效时间输入：本地日期时间控件，默认当前时间。
- 变更摘要：模型/provider、旧价格、新价格。
- 影响预览：按当前已知数据估算“生效前记录数/费用”和“生效后记录数/费用”；无数据时显示“暂无可预览历史”。
- 操作：`取消`、`确认并保存`。

确认前校验：时间不能为空、格式有效、不得晚于当前时间；价格必须是非负有限数字，至少包含 input 和 output。

### 5.2 历史显示与重新计算

- 保存新版本后，默认不修改已有记录的 `cost_usd` 和快照。
- 对尚未固化费用的实时/后台记录，按其发生时间立即选择正确版本。
- 设置页显示当前生效版本和生效时间。
- 提供显式操作 **“按价格版本重算历史费用”**，仅在用户主动确认后执行；默认范围为当前 provider/模型，可选择日期范围。
- 重算前展示差异预览；执行后记录重算时间和范围。重算不删除原始 token 数据。

### 5.3 导出

在价格设置区域提供：

- **“导出当前价格”**：导出包含当前所有模型版本、provider、匹配字段、生效时间和字段版本的完整 JSON；导出不包含 token、会话标题、路径等隐私数据。
- 文件名建议：`codex-usage-hud-pricing-YYYYMMDD-HHmm.json`。
- 导出内容必须可直接再次导入，保留未知字段时给出兼容提示或安全忽略。

### 5.4 空模板

提供 **“下载空价格模板”**，模板包含：

- 顶层 `schema_version`、`unit`、`prices`。
- 一条完整注释/示例结构（如 JSON 不支持注释，则使用 `__说明` 字段或随模板提供说明文本）。
- 所有支持字段：`model`、`provider`、`base_url`、`input`、`cached_input`、`cache_write`、`output`、`reasoning`、`effective_at`。
- `prices` 默认空数组，不带任何实际价格，避免用户误把示例当真实价格。
- 同时提供“复制示例”按钮，复制最小合法条目。

### 5.5 导入

- 支持文件选择和粘贴 JSON 两种方式。
- 导入先解析、校验、去重并展示预览，用户确认后才写入。
- 默认导入策略：按条目 `effective_at` 新增版本；相同匹配范围和时间的条目显示冲突并要求选择覆盖或取消。
- 导入失败必须原子回滚，现有价格、版本和生效时间不变。
- 导入成功提示新增/更新/跳过数量，并显示最近一次导入时间。

## 6. 数据与兼容迁移

- 为现有单价配置生成一个 `builtin_migration` 版本，`effective_at` 使用迁移时刻；现有历史记录若没有快照，按“当前可证明的旧版本”处理，不得凭空声称知道官方历史价格。
- 对已有普通会话记录：若记录已有明确费用，保留原值；若只有 token 没有费用，按最早可用价格版本计算并标记来源。
- 现有 `pricing_url` 保留，但更名/辅助说明为 **“价格 JSON 获取地址（可选）”**；它只是用户自定义导入来源，不是官方认证地址。
- `ModelPrice.to_dict()` 的旧字段继续可读；新字段缺省时按当前迁移规则补齐。

## 7. 错误、回退与安全

- 导出失败：不改变配置，提示本地文件写入错误。
- 导入 JSON 非对象、价格非数字、负数、NaN/Infinity、缺少模型或 input/output：拒绝整个导入。
- 导入地址仅允许 HTTP(S)；限制响应大小和超时时间；不执行返回内容中的代码。
- 任何网络获取失败都不覆盖最近成功的本地价格版本。
- 价格金额使用 Decimal 或等价精确表示，最终展示按现有 HUD 精度格式化。

## 8. 验收标准

### 生效时间

- 修改价格并确认，默认生效时间等于确认弹窗打开时的当前时间。
- 一条发生在生效时间前 1 秒的记录按旧价；恰好等于生效时间的记录按新价。
- 修改价格后刷新 HUD，已固化历史费用和快照不变化。
- 导入过去生效时间的价格后，历史统计按时间分段显示正确；无可用旧版本时明确显示 fallback/unavailable。
- provider、base URL、模型别名同时存在时，匹配优先级和版本选择可由测试固定。

### 导入导出模板

- 导出 JSON 可以无损导入，模型匹配、缓存价格和生效时间保持一致。
- 空模板无需用户猜字段即可填写并通过校验。
- 非法导入不会改变任何原配置。
- 导入冲突有预览和明确处理结果。

### 回归与兼容

- 现有 `hud_settings.json` 可加载；旧格式价格仍可读。
- Renderer 设置页完成全部流程；不新增 Qt/Tk 行为。
- 普通会话、CLI、后台用量的 token 总数不变，只改变价格版本选择。
- 单元测试覆盖边界时间、时区、迁移、导入导出、冲突、回退和历史快照。
- `python -m pytest -q`、`python -m compileall -q src tests tools`、`git diff --check` 通过。

## 9. 实现与验证映射

1. `src/codex_usage_hud/pricing.py` 和 `config.py`：`pricing_versions`、审计记录、旧配置迁移、版本化保存、导入预览/原子提交、导出和模板。
2. `core/calculator.py`：按 provider、base URL、模型匹配优先级选择 `occurred_at` 对应版本，并输出 `fallback`/`unavailable` 与价格快照。
3. `core/parser.py`、`usage_cache.py`、`snapshot_builder.py`：普通会话、CLI 会话和汇总缓存复用同一 CostEstimator，并持久化请求级快照。
4. `core/background_usage.py`、`background_usage_runtime.py`：后台请求按请求时间计价，支持只读影响预览、时间分桶和显式历史重算审计。
5. `runtime_commands.py`、`settings_bridge.py`：设置命令、导入导出、模板、影响预览和重算命令；普通 `/settings` 与旧 `/prices/fetch` 路径不能绕过生效时间流程。
6. `renderer_assets/settings_shell.py`、`router.py`、`layout_style.py`：改价生效时间弹窗、加载态、影响预览、导入冲突取消/覆盖、文件导入、下载和显式历史重算。
7. `tests/test_calculator.py`、`test_config.py`、`test_parser.py`、`test_background_usage.py`、`test_pricing_snapshots.py`、`test_pricing_runtime_commands.py`、Renderer 契约测试：覆盖边界时间、时区、迁移、快照、回退、冲突、原子性和 Renderer bundle 指纹。

## 10. 已决策产品规则

- 不允许未来预约调价；`effective_at` 必须带时区且不得晚于确认时刻。
- provider 是计费边界；同一模型在不同 provider 或 base URL 下不共用价格版本。
- 历史重算只允许显式预览后执行；原始费用和原始价格快照保存在账本中，重算后的值写入当前字段并记录审计，不在保存新价格时自动执行。
- 重算界面展示预览中的原值/新值差异；账本保留原始值，避免用户无法追溯。

## 11. 当前验收证据

- Renderer bundle：运行版本 `672693` bytes，SHA-256 `b9a29f2e0a5e978b42fcd0bb01f21e1e11ee93e6ae5a9783122cb14d9a3bc842`；模板版本同步更新。
- 真实 Windows Codex App CDP `53236` 已验证改价确认、过去/未来生效时间校验、取消不写入、影响预览加载态、导入冲突预览、覆盖提交、导出回导和重启持久化。
- 完整验收门槛已通过：`python -m pytest -q`、`python -m compileall -q src tests tools`、`git diff --check`；Renderer/架构聚焦测试也已通过。
