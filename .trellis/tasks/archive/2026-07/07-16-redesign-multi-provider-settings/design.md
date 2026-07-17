# Multi-provider settings design

## Design intent

保留现有设置单页和整体字段顺序，只把 Provider 相关控件收拢为模型单价区中的一个紧凑编辑器。用户先通过 tabs 确认“正在编辑谁”，再在同一上下文中决定该 Provider 是否参与统计并编辑价格配置。

## Layout

```text
模型单价  [ custom · App ] [ muyuan ] [ archive · 历史 ]  USD / 1M
----------------------------------------------------------------
[x] 纳入统计   Codex App · 必选 / Profile: default      context
[价格 URL................................] [拉取] [补充额度 0.00]
模型              输入      缓存      输出      推理
gpt-5.5           5.00      0.50      30.00     30.00
gpt-5.4-mini      1.10      0.11       4.40      4.40
[+] 添加模型
```

- tabs 与“模型单价”标题保持同一行，是当前价格编辑对象的唯一切换入口，不再显示 Provider 下拉框。
- 每个 tab 下只显示该 Provider 自己的“纳入统计”复选框。
- App Provider 复选框禁用并保持选中；辅助文本显示 `Codex App · 必选`。
- profile、历史通道等来源信息只显示在当前 Provider context 行，不新增 Provider 清单。
- 价格 URL、拉取命令和补充额度保持同一行；窄窗口下换成两行。
- 模型价格表保持现有紧凑密度；窄窗口允许表格区域横向滚动，不压缩字段到不可读。

## Interaction states

- tab 切换：先把当前表单写入弹窗内 draft，再渲染目标 Provider，不写真实配置。
- 未保存：修改后的 tab 在名称右侧显示圆点；底部状态显示未保存 Provider 数量。
- 保存：一次提交全局设置与所有 Provider drafts，成功后清除全部圆点。
- 取消/关闭：沿用设置弹窗现有关闭语义；产品实现阶段需要保留现有未保存关闭确认。
- tab overflow：tabs 保持单行横向滚动；键盘左右方向键移动当前 tab，活动 tab 自动滚入可见区域。
- 空状态：没有 Provider 时显示“尚未发现 Provider”，保留价格区标题但隐藏编辑器。

## Responsive behavior

- Desktop: 对话框维持 760px 级宽度，全局设置双列，Provider 工具行单行。
- Narrow: 全局设置改为单列，Provider tabs 横向滚动，context 和工具行换行，价格表在自身区域横向滚动。
- tabs、按钮和表格列使用固定最小尺寸，交互状态不得导致页面跳动。

## Compatibility boundaries

- 沿用现有 `provider_settings`、`provider_scope_mode`、`selected_providers`、`provider_registry` 和 `app_provider` 数据合同。
- UI 从每个 Provider 的 checkbox 集合推导 `all/custom`；不增加新的持久化字段。
- 原型仅使用本地示例数据，不连接 settings bridge，不读取或写入用户配置。

## Review artifact

- Interactive prototype: `provider-settings-prototype.html`
- The prototype is standalone and uses local sample data only.
