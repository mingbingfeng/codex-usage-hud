# PRD: Safe Cleanup Scan Progressive Feedback

## Problem

Settings → 空间清理：点击「扫描」后只能干等。按钮最多变成「正在扫描...」，主区无进度、无阶段、无渐进结果。

## Goal

落地已批准的方案 C（渐进填充），设计稿：

- `docs/designs/space-cleanup-scan-progress-v1.html`

## Scope

### In scope

1. 垃圾清理扫描中：启动壳、阶段进度条、摘要累计、结果行渐进填充、重扫遮罩、失败/取消
2. 会话扫描中：扫描条/启动壳 + busy 锁定（可不分批推 session 行）
3. 后端 scanning 阶段 progress 字段与 partial groups 推送
4. 测试与 contracts 更新

### Out of scope

- 改变清理安全策略 / backup / offline helper
- 扫描中签发 confirmationToken 或可执行清理
- Qt/Tk 设置面
- 假全盘文件百分比

## Acceptance criteria

1. 点击扫描后，在首个 partial 前 UI 已显示扫描中态（非仅按钮文案）
2. 扫描中 operation 更新 phase/progress/discovered；UI 显示阶段文案与进度条
3. 阶段边界发布 partial groups；列表区分已发现/扫描中/排队
4. 扫描中「确认清理」始终 disabled；完成后现有 preview+token 流程不变
5. 有旧 revision 时重扫不闪回「尚未扫描」大空态
6. 会话扫描中锁定永久删除并显示扫描反馈
7. Payload 无绝对路径
8. 相关单测通过；改动文件 ruff 干净

## Constraints

- Renderer only；遵守 safe-cleanup-contracts
- 固定阶段权重 progress
- Partial 不得签发 confirmationToken
- 无新增 idle 轮询
