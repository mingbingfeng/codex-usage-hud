# Implementation plan

设计稿已通过评审，按以下顺序实施。

1. 在 renderer 设置表单中引入弹窗级 Provider draft 状态，切换 tab 前收集当前 Provider 表单。
2. 用单行 tabs 替换 Provider 下拉框，以当前 Provider 的单个 checkbox 替换全量 scope checkbox 列表。
3. 将 Provider context、价格 URL、补充额度和模型价格表组合为紧凑价格编辑器。
4. 保存时合并全部 Provider drafts，并从完整 Provider checkbox 状态推导 `provider_scope_mode` 与 `selected_providers`。
5. 增加未保存 tab 标记、关闭确认、tab 键盘交互和 overflow 行为。
6. 更新 renderer 字符串/DOM 测试，覆盖 App 必选、历史 Provider、跨 tab 暂存、一次保存和窄窗口结构。
7. 运行聚焦 renderer/settings 测试，并在真实 Codex App renderer 中完成桌面与窄窗口截图验证。

## Validation targets

- `tests/test_renderer_hud.py`
- `tests/test_settings_bridge.py`
- `tests/test_config.py`
- `tests/test_ui.py`

## Rollback point

所有产品改动应限制在 renderer 设置面板及对应测试；不修改 Provider 注册表或配置格式。回滚可恢复旧表单渲染与收集逻辑，不影响现有配置数据。
