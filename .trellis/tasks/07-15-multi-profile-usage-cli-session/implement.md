# 实施计划：多 Provider 用量统计与 CLI 会话交互

## Ordered implementation

1. 在 `config.py` 增加 provider 注册/规范化、provider 设置和范围持久化模型；实现旧全局价格、URL、补充额度的无损读取迁移及原子写入。
2. 在现有 Codex 配置读取路径旁扩展基础配置与 profile 的 provider 发现；合并近 30 天 JSONL provider、保存设置和历史通道标记，避免让 profile 成为计费键。
3. 扩展 `ParsedSession` 与 JSONL 解析器，保留 `model_provider`，并用共享分类器输出 App/CLI/未知客户端类型；缺失 provider 映射为 `unknown`。
4. 将 provider/base URL 上下文传至 `CostEstimator`/`UsageCalculator`；为每条会话应用正确 provider 的价格表。
5. 在 `UsageSummaryCache` 贡献汇总边界接入统一有效范围谓词，覆盖会话列表、token、费用、预算、告警和补充额度；保持增量缓存和事件驱动刷新。
6. 更新 renderer payload、设置桥和设置页：provider 切换价格编辑、provider 范围、App 必选锁定、历史通道标记，以及 provider 独立价格拉取。
7. 更新 renderer 气泡数据与点击绑定：CLI 标签、普通工作目录文本、禁止 CLI `activateSession`；保留 App 现有跳转。
8. 补齐迁移、解析、聚合、设置桥和 renderer 单测；运行全套聚焦验证并进行 Windows/macOS 配置发现的夹具验证。

## Validation

```powershell
python -m pytest tests/test_config.py tests/test_codex_theme.py tests/test_parser.py tests/test_settings_bridge.py tests/test_renderer_hud.py tests/test_ui.py tests/test_active_session.py -q
python -m compileall -q src tests tools
git diff --check
```

手工验证：使用默认 App provider 与 `muyuan` CLI provider 的 JSONL 夹具，检查范围筛选、共享 provider 去重、旧配置迁移、未知通道、App provider 校正、CLI 标签及其无跳转行为。Windows 与 macOS 各使用对应配置路径夹具验证 provider 发现；renderer 运行时按 `docs/HUD_RUNTIME_ACCEPTANCE_CHECKLIST.md` 检查无轮询回归和 DEBUG 错误。

## Risky files and rollback points

- 高风险：`src/codex_usage_hud/config.py`（用户设置迁移）、`src/codex_usage_hud/core/parser.py`（会话合同）、`src/codex_usage_hud/cli.py`（汇总与运行时接线）、`src/codex_usage_hud/ui/renderer_hud.py`（注入式 renderer UI）。
- 兼容性：保留旧全局字段读取，先用固定 JSON fixture 锁定迁移输出，再改 renderer 保存载荷。
- 回滚：若 renderer 设置/范围出现问题，回滚其新字段消费即可；旧字段仍可读，且不应删除任何原始用户配置字段。

## Pre-start review gate

- 复核 `prd.md` 的 R1–R20 与 AC1–AC20 都有对应实施切片。
- 确认 renderer-only 边界、CLI 无跳转和 App provider 强制规则没有被实现计划扩大。
- 用户审阅这些规划产物后，才运行 `task.py start`。
