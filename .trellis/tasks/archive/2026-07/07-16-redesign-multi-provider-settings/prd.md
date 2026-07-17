# Redesign multi-provider settings

## Goal

按已批准的交互设计重构 renderer 设置界面中的多 Provider 选择与计费配置区域，降低统计范围、当前编辑对象和 Provider 级配置混在一起造成的理解与操作负担，同时保持现有配置合同与单页设置路径不变。

## Background

- 当前设置页把全局预算、气泡设置、Provider 统计范围、当前 Provider 切换、Provider 级补充额度、价格地址和模型价格表放在同一张两列表单中。
- “当前编辑的 Provider”和“纳入统计的 Provider”共享一个区域但使用不同控件，用户难以判断切换下拉框是否会改变统计范围。
- 切换当前 Provider 会重绘整个设置面板，并明确提示未保存输入不会保留，增加误操作风险。
- 设置弹窗当前宽度为 760px，并沿用 renderer HUD 的深色主题、8px 圆角和紧凑密度。
- 用户已评审并批准 `provider-settings-prototype.html`，授权按该设计进入产品实现。

## Confirmed Facts

- Provider 的稳定主键是规范化后的 `model_provider`；profile 名称只用于辅助展示。
- Provider 列表来自 Codex 配置、profile、已保存设置和近 30 天历史记录的并集。
- 当前 Codex App Provider 必须纳入统计范围，界面中保持选中且不可取消。
- 仅由历史发现的 Provider 需要显示“历史通道”状态；未知归因显示为可见的 `unknown` 通道。
- 每个 Provider 独立维护 `model_prices`、`pricing_url` 和 `weekly_adjustment_usd`。
- `all/custom` 是统计范围的持久化语义；全选会自动包含以后发现的新 Provider，自定义范围不会自动扩大。

## Requirements

- R1. 清楚区分“统计范围选择”和“当前 Provider 配置编辑”，不得继续把两者表现为同一个选择动作。
- R2. 保留现有单页设置布局，不增加二级页面或侧栏导航。
- R3. 在模型单价区域顶部使用紧凑 tabs 切换 Provider；tab 同时承担当前价格编辑对象的指示，不再保留 Provider 下拉框。
- R4. 当前 tab 下只显示该 Provider 的一个“纳入统计”复选框，不再额外铺开全量 Provider 勾选列表；App Provider 的复选框保持选中且不可取消。
- R5. 当前 Provider 的统计开关、价格地址、补充额度和模型单价形成一个紧凑且明确的编辑上下文。
- R6. 切换 Provider tab 时，在当前设置弹窗内暂存各 Provider 的未保存修改；发生修改的 tab 显示未保存圆点，底部“保存”一次提交全部修改。
- R7. 历史通道和关联 profile 作为当前 tab 的紧凑辅助状态展示，不能重新形成一列 Provider 清单。
- R8. 保留现有 renderer 主题语言和紧凑工具型界面，不改变 HUD 气泡或其他产品表面。
- R9. 设计必须适配桌面窗口及窄窗口，不允许字段、标签、操作按钮重叠。
- R10. 产品实现必须接入现有设置保存与价格拉取路径，不增加 Provider 注册表、设置桥或持久化格式的新合同。

## Acceptance Criteria

- [ ] AC1. 用户无需阅读说明即可区分当前 Provider tab、该 Provider 是否参与统计以及 App 必选状态。
- [ ] AC2. 用户单击 tab 即可切换任一 Provider 并看到其独立的价格 URL、补充额度和模型价格；不再显示 Provider 下拉框或全量 checkbox 列表。
- [ ] AC3. 切换 tab 会暂存当前输入；修改后的 tab 显示未保存圆点，底部保存会一次提交全部 Provider 修改。
- [ ] AC4. App Provider 始终勾选且不可取消；普通、历史和 unknown Provider 可通过当前 tab 下的单个 checkbox 控制统计范围。
- [ ] AC5. 保存后 `provider_settings`、`provider_scope_mode` 和 `selected_providers` 与原有配置合同兼容，价格拉取只更新当前 Provider。
- [ ] AC6. 历史通道、关联 profile、是否纳入统计和未保存状态均有稳定且不只依赖颜色的视觉表达。
- [ ] AC7. 桌面和窄窗口下无溢出、遮挡或关键操作丢失；tabs 可横向滚动并支持左右方向键切换。
- [ ] AC8. renderer/settings 相关测试通过，Provider 注册表、聚合逻辑、HUD 气泡及 Qt/Tk 行为不发生回归。

## Out of Scope

- 修改 Provider 注册表、配置格式、聚合筛选/会话计费语义或设置桥协议。
- 修改 HUD 气泡、支持页、版本更新页或 Qt/Tk 旧界面。
