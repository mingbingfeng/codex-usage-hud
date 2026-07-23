# Cleanup categories, path details, and reveal action - Implementation Plan

## 1. Core payload and reveal authority

- [ ] Extend `CleanupItem` in `src/codex_usage_hud/core/safe_cleanup.py` with local display metadata (`path`, `pathKind`, `modifiedAt`) while retaining private canonical-path authority.
- [ ] Add revision-bound `SafeCleanupManager.resolve_reveal_path()` with approved-root, existence, absolute-path, and reparse checks; do not require mutating-action eligibility.
- [ ] Add an injectable Windows/macOS reveal launcher using argument vectors and `shell=False`.
- [ ] Add focused tests in `tests/test_safe_cleanup.py` for payload fields, stale/unknown/escaped/reparse rejection, and file/directory argv on both platforms.

Rollback point: core payload remains additive; reveal functions can be removed without changing cleanup plans or stored files.

## 2. CLI command bridge

- [ ] Add `safeCleanupReveal` to the Renderer safe-cleanup command allowlist.
- [ ] Resolve `itemId` through the current manager, launch the verified path, and return request-correlated settings status.
- [ ] Reject commands that include only a path, omit revision/item ID, or target an unavailable manager.
- [ ] Cover the command route and error behavior in `tests/test_ui.py` without launching Explorer/Finder.

Rollback point: remove the single command branch; scan/preview/execute remain untouched.

## 3. Renderer grouping and exact selection

- [ ] Add revision-bound selected/expanded state and raw-item lookup helpers in `src/codex_usage_hud/ui/renderer_hud.py`.
- [ ] Aggregate presentation rows by the full safety tuple; sum bytes/files/targets/time without crossing tier or blocked reasons.
- [ ] Remove silent top-eight truncation for executable selected content.
- [ ] Make category checkboxes real controls that expand to raw item IDs and regenerate preview.
- [ ] Keep the two-step scan -> confirm primary flow and existing deep-clean consent gates.
- [ ] Update preview/running/result rows to aggregate by category while retaining per-item detail status.

Rollback point: grouping helpers are renderer-local and can fall back to raw `data.groups` without backend mutation.

## 4. Path details, copy, and reveal UI

- [ ] Make chevrons real icon buttons with accessible expanded state.
- [ ] Render inline detail rows with full path, kind, size, files, modified time, retention/protection state, and per-item result.
- [ ] Reuse `copyHudText()` for path copy and add explicit icon tooltips/feedback.
- [ ] Submit `safeCleanupReveal` with revision + opaque item ID only.
- [ ] Add constrained responsive CSS for long Windows/macOS paths and stable 54px category rows.
- [ ] Add structural tests in `tests/test_renderer_hud.py` for aggregation, selection, details, command fields, and no path field in reveal commands.

## 5. Contracts and privacy

- [ ] Update `.trellis/spec/backend/safe-cleanup-contracts.md` to allow exact paths in the local renderer while forbidding remote/log leakage and renderer-authorized arbitrary paths.
- [ ] Update `docs/PRIVACY.md` with the same local transparency boundary.
- [ ] Preserve all existing revision/token/fingerprint/backup/process gates.

## 6. Verification (implementation gate — automated only)

Live Renderer interaction and screenshots are **not** required to mark this task complete. Quality profile for this task: High for security boundaries, but acceptance is evidenced by tests + static checks + the optional user-owned checklist below (remind only).

- [ ] Run focused tests:

  ```powershell
  rtk python -m pytest tests/test_safe_cleanup.py tests/test_ui.py tests/test_renderer_hud.py -q
  ```

- [ ] Run static checks on touched files:

  ```powershell
  rtk ruff check src/codex_usage_hud/core/safe_cleanup.py src/codex_usage_hud/cli.py src/codex_usage_hud/ui/renderer_hud.py tests/test_safe_cleanup.py tests/test_ui.py tests/test_renderer_hud.py
  rtk python -m compileall -q src tests
  rtk git diff --check
  ```

- [ ] Run the full authoritative suite:

  ```powershell
  rtk python -m pytest -q
  ```

## 用户推荐实测链路（可选 · 非完成门槛）

收尾向用户提醒即可，**会话内不必代跑、不必截图、不必据此阻塞归档**。建议用户按下列顺序自测（勿对真实用户数据执行清理）：

1. **重启** workspace Renderer helper，打开 **设置 → 垃圾清理**，执行一次扫描。
2. **聚合**：同类 `%TEMP%`（或同规则）过期目标首层只见一条分类；字节/目标数/文件数与扫描摘要一致。
3. **展开详情**：chevron 展开后见完整绝对路径、类型、大小、修改时间、保护/可执行状态。
4. **复制路径**：详情行复制得到精确本地路径。
5. **打开位置**：对扫描得到的 disposable 目标点「打开位置」；Windows 应 Explorer 定位/打开，macOS 应 Finder 定位/打开。勿手工构造路径命令。
6. **选择一致性**：勾选/取消分类后，预览中的目标集合与合计字节随之变化；不得出现确认范围内看不到的隐藏目标。
7. **窄窗**：宽屏、约 760px、约 520px 下设置弹窗无横向溢出；无 console/window error。
8. **禁止项**：不要对真实用户数据点「确认清理」完成实测；只需验证扫描、展开、复制、定位与预览。

## Final Review Gate

- [ ] Re-read the final diff against `prd.md` and `design.md`.
- [ ] Confirm unrelated dirty-tree changes were preserved.
- [ ] Run `trellis-check` before reporting completion (automated gates only).
- [ ] On wrap-up, remind the user of the「用户推荐实测链路」above.
