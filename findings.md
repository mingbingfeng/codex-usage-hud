# 发现与决策

## 需求
- 完成路径必须拆为：原位确认 -> 原位收缩成圆 -> 完整圆形停顿 -> 贴屏幕右侧垂直上浮到右上顶位。
- 圆形不能从源卡片中心斜飞到右上；成圆后应先贴到右侧轨道，再只改变 y。
- 圆形起飞前，上方矩形气泡按顺序左移让路；圆形经过后，矩形气泡弹性回位。
- 恢复聊天必须走完全反向路径：顶部唤醒 -> 原路返回 -> 回到记忆栈位 -> 展开回矩形 -> 内容渐入。

## 研究发现
- 现有 `_transition_rect_for_progress` 在完成路径中从 `source_circle` 直接插值到 `target_rect`，因此 x 和 y 同时变化，表现为斜向飞行。
- 现有 `_transition_rect_for_progress` 在恢复路径中从顶部圆直接插值到目标栈位圆，仍是 x/y 同时变化，不是同一右侧垂直轨道反向。
- 现有 `_prepare_completed_badge_moves` 只移动已完成圆形，不处理矩形卡片让路/回位。
- 现有 `_update_completed_badge_moves` 从过渡开始就把已完成圆移动到目标，没有区分“起飞前让路”和“经过后回位”。
- 现有恢复目标矩形位置来自 `_find_item_rect(new_items, ...)`，没有保存完成前记忆栈位；排序变化时不能保证回到原位。

## 技术决策
| 决策 | 理由 |
|------|------|
| 抽出垂直轨道几何函数 | 让正向和逆向共用同一路径，测试更直接 |
| 把路径拆成原位圆、右侧轨道圆、顶部槽位圆 | 满足“不斜飞”和“贴右侧垂直运动” |
| 增加过渡期让路偏移函数 | 先用纯函数表达左移/回位时序，再接入 QWidget move |
| 记录卡片历史矩形 | 恢复路径需要记忆栈位，而不是仅按当前列表重算 |
| 保留信息型完成徽章 | 用户要求完成态恢复上一版信息密度：标题、对勾、tokens、金额、缓存命中率、工作目录和点击能力 |
| 恢复过渡使用蓝色和 `↻` | 区分“完成”与“恢复运行”的视觉语义 |

## 遇到的问题
| 问题 | 解决方案 |
|------|---------|
| Puppeteer 本地缺少绑定 Chrome，无法直接渲染设计稿 | 使用设计 HTML 文本规范、录屏抽帧和源码证据完成验收 |
| 之前 aide 报告对慢速录屏有误判 | 主线程以抽帧和源码为准，只采纳可复核结论 |
| 当前 Python 3.14 Tk 缺少 `init.tcl` | 全量 Tk 窗口用例受环境限制；本轮用目标测试、compileall、demo 和排除 Tk 环境依赖的 UI 测试验证 |

## 资源
- 设计稿：`docs/designs/completed-session-bubble-concept.html`
- 录屏：`C:/Users/zjxqm/AppData/Local/Packages/Microsoft.ScreenSketch_8wekyb3d8bbwe/TempState/Recordings/20260618-0813-21.4548067.mp4`
- 核心代码：`src/codex_usage_hud/ui/work_overlay_qt.py`
- 目标测试：`tests/test_ui.py::WorkOverlayTransitionTests`

## 视觉/浏览器发现
- 录屏里第二个完成态上升时，已有完成圆和新圆有接触/重叠感。
- 录屏里恢复阶段仍是绿色对勾/绿色胶囊，缺少明显“恢复运行”语义。
- 录屏里到顶后从过渡圆切换为复杂完成徽章，有二次视觉替换感。

## 启动态完成圆发现
- 用户反馈刚启动出现两个绿色完成圆；完成态气泡不应从历史完成会话直接出现。
- 复现“两个历史完成 JSONL + 全新 context”时，当前 CLI 返回空列表，说明基础过滤链路没有直接放出历史完成项。
- 本机当前真实 work-overlay state 中两个条目是 `active`，不是 `recent`；`%TEMP%\codex-work-overlay-transition-demo.json` 留有 demo 状态，且 demo 默认循环时会出现完成圆，容易和真实启动 overlay 混淆。
- 仍然收紧 `_select_runtime_work_overlay_items`：`recent` 只认本次选择开始前已有的 seen task key，避免同一次启动扫描内自我登记后显示完成态。

## 完成态信息徽章发现
- 只保留对勾会丢失上一版完成态的信息价值和操作入口的视觉指示。
- 完成态点击锚点没有丢：`check_anchor` 仍负责关闭，`workdir_anchor` 仍负责跳转 Codex 会话；缺陷主要在绘制层。
- 已恢复 `CompletedBadgeWidget.paintEvent` 中的标题弧形文字、工作目录弧形文字、中心对勾、耗时、Tokens/Cost/Cache 三个指标。

## 方变圆裁剪发现
- 不建议增加稳定态卡片高度：会让运行态气泡长期变厚，影响信息密度。
- 不建议缩小最终完成圆：会让成圆后到稳定完成态之间出现尺寸跳变。
- 修复点应在过渡层：中间圆的 top 不允许为负，且过渡期间临时把 shell 最小高度撑到整条路径所需高度，结束后恢复。

---
*每执行2次查看/浏览器/搜索操作后更新此文件*
*防止视觉信息丢失*
