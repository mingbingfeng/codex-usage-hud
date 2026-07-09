"""Unit tests for the renderer-injected HUD payload and CDP client."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.parser import (
    Activity,
    ConfirmedTokens,
    GapTiming,
    ParsedSession,
    RequestRound,
    RequestTokens,
    SlowSummary,
    ToolCallTiming,
)
from codex_usage_hud.core.runtime_errors import RuntimeErrorEvent
from codex_usage_hud.platforms.codex_theme import CodexThemeSnapshot
from codex_usage_hud.support_assets import support_qr_asset_paths, support_qr_payload
from codex_usage_hud.ui import renderer_hud
from codex_usage_hud.ui.renderer_hud import (
    RendererHudClient,
    _renderer_theme_payload,
    payload_from_snapshot,
)


class RendererHudPayloadTests(unittest.TestCase):
    def test_renderer_theme_payload_accepts_persisted_source(self) -> None:
        snapshot = CodexThemeSnapshot.from_probe_result(
            {
                "mode": "dark",
                "effectiveVariant": "dark",
                "darkCodeThemeId": "linear",
                "darkTheme": {
                    "accent": "#5e6ad2",
                    "contrast": 60,
                    "fonts": {"code": None, "ui": "Inter"},
                    "ink": "#e3e4e6",
                    "opaqueWindows": True,
                    "semanticColors": {
                        "diffAdded": "#69c967",
                        "diffRemoved": "#ff7e78",
                        "skill": "#c2a1ff",
                    },
                    "surface": "#0f0f11",
                },
            },
            source="persisted",
        )

        assert snapshot is not None
        payload = _renderer_theme_payload(snapshot)

        self.assertEqual(payload["source"], "persisted")
        self.assertEqual(payload["variant"], "dark")
        self.assertEqual(payload["tokens"]["accent"], "#5e6ad2")
        self.assertEqual(payload["effectiveTheme"]["codeThemeId"], "linear")

    def test_payload_from_snapshot_formats_compact_hud_lines(self) -> None:
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            selection_source="cdp:Live Renderer Thread",
            refreshed_at=datetime(2026, 6, 5, 13, 10, 11).astimezone(),
            last_event_time=datetime(2026, 6, 5, 13, 10, 1).astimezone(),
            task_index=3,
            task_count=3,
            confirmed=ConfirmedTokens(
                cumulative_total=12345,
                cumulative_input=10000,
                cumulative_cached=6000,
                cumulative_output=2345,
                cumulative_cost_usd=0.1234,
            ),
            request=RequestTokens(
                status="running",
                model="gpt-5.5",
                input_tokens=1200,
                cached_tokens=800,
                output_tokens=90,
                reasoning_tokens=10,
                total_tokens=1300,
                estimated=True,
                cost_usd=0.0123,
            ),
        )
        snapshot.today_tokens = 50000
        snapshot.today_cost_usd = 0.5
        snapshot.week_tokens = 200000
        snapshot.week_cost_usd = 1.5

        payload = payload_from_snapshot(
            snapshot,
            theme={
                "variant": "dark",
                "source": "cdp",
                "tokens": {
                    "surface": "#181818",
                    "panelSurface": "#202020",
                    "panelBorder": "#3a485a",
                    "headerSurface": "#202833",
                    "divider": "#273241",
                    "text": "#e8eef7",
                    "muted": "#8492a6",
                    "accent": "#339cff",
                    "info": "#ad7bf9",
                    "warning": "#ffb86b",
                    "error": "#fa423e",
                    "success": "#40c977",
                    "requestSurface": "#151515",
                    "requestHeaderSurface": "#202020",
                    "requestPanelSurface": "#171717",
                    "requestText": "#f0f0f0",
                    "requestMuted": "#808080",
                    "progressTrack": "#111822",
                    "progressTrackBorder": "#314052",
                    "progressTrackText": "#657589",
                    "progressCache": "#5d8bff",
                    "progressCacheEnd": "#7d6dff",
                    "progressCacheText": "#07131f",
                    "progressDay": "#339cff",
                    "progressDayEnd": "#5d8bff",
                    "progressDayText": "#07131f",
                    "progressWeek": "#6ea8ff",
                    "progressWeekEnd": "#8db7ff",
                    "progressWeekText": "#07131f",
                    "progressOverflow": "#fa423e",
                    "progressOverflowHighlight": "#ffb5b2",
                    "progressOverflowAnchor": "#ff7a77",
                    "progressOverflowAnchorEdge": "#ffc3a4",
                    "progressOverflowBadge": "#5e2424",
                    "progressOverflowBadgeEdge": "#fa423e",
                    "progressOverflowBadgeText": "#ffe0de",
                },
            },
            update_state={
                "visible": True,
                "phase": "downloading",
                "icon": "download",
                "title": "正在下载更新：50%",
            },
        ).to_json()

        top_line = str(payload["topLine"])
        self.assertNotIn("Live Renderer Thread", str(payload["topLine"]))
        self.assertTrue(top_line.startswith("本会话 14k/"))
        self.assertIn("/~61% | 今日", top_line)
        self.assertIn("今日 50k/$0.500", top_line)
        self.assertIn("本周 200k/$1.50", top_line)
        top_progress = payload["topProgress"]
        self.assertIsInstance(top_progress, dict)
        collapsed_progress = top_progress["collapsed"]
        self.assertEqual([item["tone"] for item in collapsed_progress], ["session", "day", "week"])
        self.assertTrue(collapsed_progress[0]["label"].startswith("本会话 14k/"))
        self.assertIn("/~61%", collapsed_progress[0]["label"])
        self.assertNotIn("rightText", collapsed_progress[0])
        self.assertEqual(collapsed_progress[1]["rightText"], "总 $100.00")
        self.assertEqual(collapsed_progress[2]["rightText"], "总 $400.00")
        self.assertEqual(top_progress["cache"]["label"], "缓存命中 ~61%")
        self.assertEqual(top_progress["budget"][0]["label"], "今日 50k/$0.500")
        self.assertEqual(top_progress["budget"][0]["rightText"], "总 $100.00")
        self.assertNotIn("overflowRatio", top_progress["budget"][0])
        self.assertNotIn("overflowBadge", top_progress["budget"][0])
        self.assertEqual(top_progress["budget"][1]["label"], "本周 200k/$1.50")
        self.assertEqual(top_progress["budget"][1]["rightText"], "总 $400.00")
        request_line = str(payload["requestLine"])
        self.assertIn("↑~1,200", request_line)
        self.assertIn("◎~61%", request_line)
        self.assertIn("∑1,290", request_line)
        self.assertLess(request_line.index("↑~1,200"), request_line.index("◎~61%"))
        self.assertLess(request_line.index("◎~61%"), request_line.index("↓~90"))
        self.assertLess(request_line.index("↻~800"), request_line.index("∑1,290"))
        self.assertTrue(request_line.endswith("↻~800 ∑1,290"))
        self.assertEqual(payload["model"], "gpt-5.5")
        self.assertEqual(payload["observedModels"], ["gpt-5.5"])
        self.assertFalse(payload["warning"])
        self.assertIn("topDetails", payload)
        self.assertIn("topCopies", payload)
        self.assertIn("requestRows", payload)
        self.assertIn("requestRowDetails", payload)
        self.assertIn("settings", payload)
        self.assertIn("activeDisplayMode", payload)
        self.assertIn("settingsPath", payload)
        self.assertIn("settingsBridgeUrl", payload)
        self.assertIn("settingsCommandStatus", payload)
        self.assertIn("workOverlaySelectableMax", payload)
        self.assertIn("desktopOverlayDependency", payload)
        self.assertIn("theme", payload)
        self.assertEqual(payload["theme"]["variant"], "dark")
        self.assertEqual(payload["theme"]["tokens"]["accent"], "#339cff")
        self.assertIn("updateState", payload)
        self.assertEqual(payload["appVersion"], "1.0.5")
        self.assertIn("本会话用量", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("实时请求", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("符号说明", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("只看剩余", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("applySettingsPayload(root", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("--codex-usage-hud-surface", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-update-button", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="update-action"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-settings-button", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="settings-open"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-resize-edge-left", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-resize-corner-bottom-right", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-overflow", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-badge", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function normalizePayloadDomains", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("applyPayloadDomains(root, nextPayload, domains)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('if ("currentSession" in domains)', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('if ("diagnostics" in domains)', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("Object.assign(nextPayload, domainPayload);", renderer_hud.RENDERER_HUD_SCRIPT)

        self.assertIn("codex-usage-hud-resize-corner-top-left", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-edge="left"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-edge="bottom-right"', renderer_hud.RENDERER_HUD_SCRIPT)

        self.assertIn('data-edge="top-left"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('class="codex-usage-hud-resize"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("codex-usage-hud-settings-menu", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="settings-menu"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="coffee-open"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertGreaterEqual(
            renderer_hud.RENDERER_HUD_SCRIPT.count(
                'class="codex-usage-hud-panel-header" data-action="toggle"'
            ),
            2,
        )
        self.assertIn("请作者喝咖啡", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-fetch-prices", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-price-advanced", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-price-field="provider"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-price-field="base_url"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("unknownPriceModels", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-add-detected-model", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="settings-exit"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="settings-exit-confirm"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("退出 HUD", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("版本更新", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-check-update", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("__codexUsageHudStaleTimer", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("payloadNeedsStaleGuard", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("数据可能不是最新", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("HUD 更新暂停", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("后端未继续刷新", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("正在显示旧数据", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-install-update", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-restart", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("settings-apply-display-mode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("HUD 显示方案", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-setting-key="display_mode"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('option value="renderer"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("Renderer -> Qt -> Tk", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('option value="qt"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('option value="tk"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("Qt 独立窗口", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("Tk 独立窗口", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-settings-loading-track", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("openSettingsLoading", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("settingsCommandKey", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("codexUsageHudSettingsCommand:v1", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("localStorage.setItem(settingsCommandKey", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("请作者喝咖啡链接", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-setting-key="support_url"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-setting-key="work_overlay_max_items"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("会话进度气泡数量", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("0 为关闭", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("气泡运行环境", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("方形进度气泡", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("圆形总结", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("需要安装环境", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("立即安装", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("已安装，启用气泡", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-install-desktop-overlay", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-enable-desktop-overlay", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("desktopOverlayDependency", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("workOverlaySelectableMax", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("拉取价格", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("window.confirm", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("fetch(`${bridge}/command`", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("fetch(`${bridge}/active-session`", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function readActiveSessionRef()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activeSessionHistoryPatchName", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("[data-app-action-sidebar-thread-id]", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("activeSessionRowSelector", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("a[href*='thread']", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("[role='button']", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('postActiveSession("click"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("newSession", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("reason === \"new-session\"", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("if (!ref.sessionId && !ref.title) return;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('scheduleActiveSessionSendFollowup("click")', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-support-qr-grid", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-support-qr-title", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("previousPayload.supportImages?.length", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settingsBridgeUrl", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("updateState", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("renderUpdateButtons", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const submitted = submitSettingsCommand", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codexUsageHudSettingsCommand", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const binding = window[settingsCommandBindingName];", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("binding(JSON.stringify(payload));", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('setSettingsStatus(state.message || state.title || "", state.error ? "error" : "")', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("window.__codexUsageHudRemove()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("expiresAt: Date.now() + 10000", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("navigator.clipboard", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("requestRowDetails", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("header.app-header-tint", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("app-shell-header-context-menu-surface", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("topTitlebarSlot", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("headerLayoutSignature", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("characterData: true", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("topSlotCache = null", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('window[scrollHandlerName] = () => scheduleForPanels(["request"])', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("observe(document.documentElement", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function refreshLayoutObservers()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("window[mutationObserverName].observe(headerNode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("window[mutationObserverName].observe(composerNode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("new ResizeObserver", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function startBootstrapObserver()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("window[bootstrapObserverName].observe(document.body", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("setTimeout(stopBootstrapObserver, 5000)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("cachedHeaderNode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("cachedComposerNode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function scheduleRequestAfterComposerSettles()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("handleLayoutMutations", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("layoutMutationTouchesTextInput", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("pointer-events: none;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function headerLeftControlEdge(headerNode, header", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function headerRightControlStart(headerNode, header", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("chat actions", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("open in", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-line-inner", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-marquee", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-strip", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-strip-viewport", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-strip-marquee", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-size-probe", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            "textNode.textContent = fullText;",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn(
            'rail.appendChild(progressTextLayer("codex-usage-hud-progress-track-text"));',
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertNotIn(
            "codex-usage-hud-progress-fill-text",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("codex-usage-hud-progress-text", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("codex-usage-hud-progress-total", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("codex-usage-hud-progress-label", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("mix-blend-mode: difference;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function refreshCollapsedProgressStrip(node)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const collapsedTailPeekWidth = 40;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("tailShare > collapsedTailPeekWidth", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn(
            "grid-template-columns: max-content minmax(0, 1fr) minmax(0, 1fr);",
            renderer_hud.RENDERER_HUD_SCRIPT,
        )
        self.assertIn("codex-usage-hud-budget-rails", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("container-type: inline-size;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("@container (max-width: 560px)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("@container (max-width: 440px)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-activity-timeline", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-field="topActivityLoadMore"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("list.appendChild(button)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("height: calc(34px * 4 + 7px * 3);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("grid-auto-rows: minmax(34px, auto);", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("bottom: -15px;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("list.dataset.context !== context", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const previousScrollTop = list.scrollTop || 0;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("if (!contextChanged)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("list.dataset.signature !== signature", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("grid-template-rows: repeat(3, minmax(32px, 1fr));", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("暂无会话高消耗轮次", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertLess(
            renderer_hud.RENDERER_HUD_SCRIPT.index('data-field="topTaskOrdinalSession"'),
            renderer_hud.RENDERER_HUD_SCRIPT.index("当前活动"),
        )
        self.assertGreater(
            renderer_hud.RENDERER_HUD_SCRIPT.index('data-field="topTaskOrdinalActivity"'),
            renderer_hud.RENDERER_HUD_SCRIPT.index("当前活动"),
        )
        self.assertIn("codex-usage-hud-copy-chip", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("正在执行", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("当前需求", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("interpolateNumericText", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("canAnimateNumericText", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("FileEditViewWindowHelp", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("-webkit-app-region: no-drag", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("headerTitleTextEdge", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("headerRightControlStart", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("manualTopRect", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("footerGapSlot", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codexUsageHudPanelState:v5", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertLess(
            renderer_hud.RENDERER_HUD_SCRIPT.index(
                '<div class="codex-usage-hud-request-list" data-field="requestRows"></div>'
            ),
            renderer_hud.RENDERER_HUD_SCRIPT.index(
                '<div class="codex-usage-hud-panel-header" data-action="toggle">',
                renderer_hud.RENDERER_HUD_SCRIPT.index("function requestExpandedMarkup()"),
            ),
        )
        self.assertNotIn("Σ", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="reset"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("↯", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('"main header"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertTrue(payload["updateState"]["visible"])
        self.assertEqual(payload["updateState"]["phase"], "downloading")
        top_details = payload["topDetails"]
        self.assertIsInstance(top_details, dict)
        self.assertEqual(top_details["title"], "Live Renderer Thread")
        self.assertEqual(top_details["sessionCost"], "$0.123")
        self.assertEqual(top_details["sessionTokens"], "12k")
        self.assertEqual(top_details["taskOrdinal"], "第3次需求")
        self.assertEqual(top_details["taskOrdinalSession"], "")
        self.assertEqual(top_details["taskOrdinalActivity"], "第3次需求")
        self.assertEqual(top_details["sessionMix"], "缓存命中 ~61%")
        self.assertEqual(top_details["sessionAverage"], "均值 n/a")
        self.assertIn("↑↻ $", top_details["sessionComposition"])
        self.assertNotIn("=", top_details["sessionComposition"])
        self.assertNotIn("\n", top_details["sessionComposition"])
        self.assertFalse(str(top_details["sessionComposition"]).startswith("$"))
        self.assertIn("heavyRounds", top_details)
        self.assertEqual(top_details["activityLastLabel"], "需求轮次")
        self.assertEqual(top_details["activityLast"], "1轮")
        self.assertEqual(top_details["sessionInputTokens"], "10k")
        self.assertEqual(top_details["sessionCachedTokens"], "6,000")
        self.assertEqual(top_details["sessionOutputTokens"], "2,345")
        self.assertEqual(top_details["sessionReasoningTokens"], "0")
        self.assertEqual(top_details["warnings"], "")
        self.assertEqual(top_details["currentTask"], "Live Renderer Thread")
        self.assertIsInstance(top_details["activityTrail"], list)
        self.assertTrue(top_details["activityTrail"])
        self.assertNotIn("budget", top_details)
        self.assertNotIn("legend", top_details)
        self.assertNotIn("requestTokens", top_details)
        self.assertNotIn("requestCost", top_details)
        self.assertTrue(payload["requestRows"])
        request_row = str(payload["requestRows"][0])
        self.assertLess(request_row.index("↑1,200"), request_row.index("◎~67%"))
        self.assertLess(request_row.index("↻800"), request_row.index("∑1,290"))
        request_row_details = payload["requestRowDetails"]
        self.assertIsInstance(request_row_details, list)
        self.assertEqual(request_row, request_row_details[0]["text"])

    def test_payload_from_renderer_new_session_clears_session_and_round_stats(self) -> None:
        snapshot = ParsedSession(
            status="waiting",
            selection_source="renderer-new-session",
        )
        snapshot.today_tokens = 1234567
        snapshot.today_cost_usd = 1.23
        snapshot.week_tokens = 2345678
        snapshot.week_cost_usd = 2.34

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["session"], "新会话")
        self.assertTrue(str(payload["topLine"]).startswith("新会话 0/$0.0000/- | 今日"))
        top_progress = payload["topProgress"]
        self.assertEqual(top_progress["collapsed"][0]["label"], "新会话 0/$0.0000/-")
        top_details = payload["topDetails"]
        self.assertEqual(top_details["title"], "新会话")
        self.assertEqual(top_details["session"], "新会话 | 行 0 | 确认 0")
        self.assertEqual(top_details["sessionCost"], "$0.0000")
        self.assertEqual(top_details["sessionTokens"], "0")
        self.assertEqual(top_details["sessionRounds"], "0 轮确认")
        self.assertEqual(top_details["sessionAverage"], "均值 n/a")
        self.assertEqual(top_details["currentTask"], "新会话")
        self.assertEqual(top_details["activityLast"], "0轮")
        self.assertEqual(top_details["heavyRounds"], [])
        self.assertEqual(payload["requestRows"], [])
        self.assertEqual(payload["requestRowDetails"], [])

    def test_payload_exposes_debug_runtime_errors(self) -> None:
        snapshot = ParsedSession(status="waiting")
        event = RuntimeErrorEvent(
            source="active_session",
            severity="error",
            code="active_session.unmatched_thread",
            message="Renderer thread could not be mapped",
            context={"threadId": "abc"},
            first_seen_at=1.0,
            last_seen_at=2.0,
            count=3,
        )

        payload = payload_from_snapshot(
            snapshot,
            debug=True,
            runtime_errors=[event],
        ).to_json()

        self.assertTrue(payload["debug"])
        self.assertEqual(len(payload["runtimeErrors"]), 1)
        error = payload["runtimeErrors"][0]
        self.assertEqual(error["code"], "active_session.unmatched_thread")
        self.assertEqual(error["context"], {"threadId": "abc"})
        self.assertEqual(error["count"], 3)

    def test_renderer_script_renders_debug_error_hud(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("function renderRuntimeErrors", script)
        self.assertIn("codex-usage-hud-runtime-errors", script)
        self.assertIn("runtimeErrors", script)
        self.assertIn("DEBUG HUD active", script)
        self.assertIn("runtimeErrorsPanel.hidden = !debug", script)
        self.assertIn("debugStatusItem", script)
        self.assertIn("left: 16px;", script)
        self.assertNotIn("right: 16px;", script)
        self.assertIn("user-select: text;", script)
        self.assertIn('title.dataset.action = "runtime-errors-move";', script)
        self.assertIn("[data-action='runtime-errors-move']", script)
        self.assertIn("beginRuntimeErrorsGesture", script)
        self.assertIn("setRuntimeErrorsPanelState", script)

    def test_renderer_debug_error_hud_defaults_collapsed_and_toggles_width(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('runtimeErrorsPanel.dataset.expanded = String(expanded);', script)
        self.assertIn('expanded: false', script)
        self.assertIn("runtime-errors-toggle", script)
        self.assertIn('body.hidden = !expanded;', script)
        self.assertIn('heading.textContent = "errors";', script)
        self.assertIn('setRuntimeErrorsPanelState({ expanded });', script)
        self.assertIn('.codex-usage-hud-runtime-errors[data-expanded="false"]', script)
        self.assertIn('.codex-usage-hud-runtime-errors[data-expanded="true"]', script)

    def test_payload_from_snapshot_exposes_update_domains(self) -> None:
        snapshot = ParsedSession(
            session_id="session-domain",
            session_title="Domain Split",
            status="parsed",
            selection_source="renderer",
            refreshed_at=datetime(2026, 7, 6, 10, 0, 0).astimezone(),
            last_event_time=datetime(2026, 7, 6, 9, 59, 58).astimezone(),
            confirmed=ConfirmedTokens(cumulative_total=42, cumulative_cost_usd=0.01),
            request=RequestTokens(status="idle", model="gpt-5", total_tokens=0),
        )
        snapshot.today_tokens = 100
        snapshot.week_tokens = 200
        snapshot.today_cost_usd = 0.02
        snapshot.week_cost_usd = 0.04

        payload = payload_from_snapshot(
            snapshot,
            settings_bridge_url="http://127.0.0.1:8765",
            settings_command_status={"lastCommand": "saved"},
            theme={"variant": "dark"},
            update_state={"visible": True, "phase": "ready"},
            debug=True,
            runtime_errors=[
                RuntimeErrorEvent(
                    source="renderer",
                    severity="error",
                    code="renderer.anchor_missing",
                    message="anchor missing",
                    context={"session": "session-domain"},
                    first_seen_at=1.0,
                    last_seen_at=2.0,
                )
            ],
            work_overlay_selectable_max=3,
            desktop_overlay_dependency={"available": True},
        ).to_json()

        domains = payload["payloadDomains"]
        self.assertEqual(
            set(domains),
            {
                "currentSession",
                "sessionSwitch",
                "budget",
                "settings",
                "overlay",
                "diagnostics",
            },
        )
        self.assertEqual(domains["currentSession"]["topLine"], payload["topLine"])
        self.assertEqual(domains["sessionSwitch"]["topLine"], payload["topLine"])
        self.assertNotIn("topDetails", domains["sessionSwitch"])
        self.assertEqual(domains["currentSession"]["requestRows"], payload["requestRows"])
        self.assertEqual(domains["budget"]["topProgress"], payload["topProgress"])
        self.assertEqual(domains["settings"]["settingsBridgeUrl"], "http://127.0.0.1:8765")
        self.assertEqual(domains["settings"]["updateState"]["phase"], "ready")
        self.assertEqual(domains["overlay"]["workOverlaySelectableMax"], 3)
        self.assertEqual(domains["overlay"]["desktopOverlayDependency"], {"available": True})
        self.assertTrue(domains["diagnostics"]["debug"])
        self.assertEqual(
            domains["diagnostics"]["runtimeErrors"][0]["code"],
            "renderer.anchor_missing",
        )

    def test_renderer_payload_can_emit_domain_only_update(self) -> None:
        payload = payload_from_snapshot(
            ParsedSession(status="parsed"),
            debug=True,
            runtime_errors=[
                RuntimeErrorEvent(
                    source="renderer",
                    severity="warning",
                    code="renderer.anchor_missing",
                    message="anchor missing",
                    first_seen_at=1.0,
                    last_seen_at=2.0,
                )
            ],
        )

        partial = payload.to_domain_json("diagnostics")

        self.assertEqual(set(partial), {"debug", "runtimeErrors", "payloadDomains"})
        self.assertEqual(set(partial["payloadDomains"]), {"diagnostics"})
        self.assertTrue(partial["debug"])
        self.assertEqual(partial["runtimeErrors"][0]["code"], "renderer.anchor_missing")
        self.assertNotIn("topLine", partial)
        self.assertNotIn("requestRows", partial)

    def test_renderer_payload_can_emit_session_switch_without_expanded_details(self) -> None:
        snapshot = ParsedSession(
            session_id="session-switch",
            session_title="Switch Target",
            status="parsed",
            selection_source="renderer",
            confirmed=ConfirmedTokens(cumulative_total=42, cumulative_cost_usd=0.01),
            request=RequestTokens(status="idle", model="gpt-5", total_tokens=10),
        )

        partial = payload_from_snapshot(snapshot).to_domain_json("sessionSwitch")

        self.assertEqual(set(partial["payloadDomains"]), {"sessionSwitch"})
        self.assertEqual(partial["topLine"], partial["payloadDomains"]["sessionSwitch"]["topLine"])
        self.assertIn("requestLine", partial)
        self.assertNotIn("topDetails", partial)
        self.assertNotIn("requestRows", partial)
        self.assertNotIn("requestRowDetails", partial)

    def test_update_payload_reports_failed_update_without_reinstall_retry(self) -> None:
        client = RendererHudClient(port=9229, enabled=True)
        install_force_flags = []
        send_payloads = []

        client._page_target = lambda: {  # type: ignore[method-assign]
            "id": "target-1",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9229/devtools/page/1",
        }

        def install(websocket_url: str, target_id: str, *, force: bool = False) -> None:
            install_force_flags.append(force)
            client._script_identifier = "script-1"
            client._target_id = target_id
            client._websocket_url = websocket_url

        def send_update(websocket_url: str, payload: dict[str, object]) -> bool:
            send_payloads.append((websocket_url, dict(payload)))
            return False

        client._install = install  # type: ignore[method-assign]
        client._send_update = send_update  # type: ignore[method-assign]

        result = client.update_payload({"debug": True})

        self.assertFalse(result)
        self.assertEqual(install_force_flags, [False])
        self.assertEqual(len(send_payloads), 1)
        self.assertEqual(client.last_status, "failed")
        self.assertIn("renderer update function did not acknowledge payload", client.last_error)

    def test_renderer_active_session_detects_blank_titlebar_new_session(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("function activeSessionHeaderTitleText()", script)
        self.assertIn("function activeSessionComposerVisible()", script)
        self.assertIn("function activeSessionHeaderLooksNewSession(rows)", script)
        self.assertIn("activeSessionHeaderLooksNewSession(rows)", script)
        self.assertIn('matchedBy: "header-empty"', script)

    def test_renderer_active_session_filters_out_folder_rows(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn("function activeSessionRowLooksThread(row)", script)
        self.assertIn(
            "sourceRow?.querySelector?.(activeSessionTitleSelector)", script
        )
        self.assertIn(
            ".filter((row) => activeSessionRowLooksThread(row))",
            script,
        )

    def test_renderer_active_session_container_prefers_identity_rows(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn(
            "document.querySelector(activeSessionIdentitySelector)", script
        )
        self.assertIn(
            "document.querySelector(activeSessionTitleSelector)?.closest?.(activeSessionRowSelector)",
            script,
        )

    def test_renderer_active_session_follows_composer_submit_from_new_session(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn(
            "function scheduleActiveSessionSendFollowup(reason = \"composer-send\")",
            script,
        )
        self.assertIn(
            "function activeSessionComposerSubmitButton(button)",
            script,
        )
        self.assertIn(
            'scheduleActiveSessionSendFollowup("composer-enter")',
            script,
        )
        self.assertIn(
            'scheduleActiveSessionSendFollowup("composer-send-click")',
            script,
        )
        self.assertIn(
            'input.addEventListener("keydown", handlers.keydown, true)',
            script,
        )
        self.assertIn(
            'scheduleActiveSessionSendFollowup("history")',
            script,
        )
        self.assertIn(
            'scheduleActiveSessionSendFollowup("popstate")',
            script,
        )

    def test_renderer_composer_watchers_do_not_redeclare_handlers_binding(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn(
            "const existingHandlers = window[composerInputHandlersName];",
            script,
        )

    def test_payload_hides_pre_send_estimate_and_activity_light_by_default(self) -> None:
        from codex_usage_hud.core.pre_send_estimator import BaseEstimate
        from codex_usage_hud.core.activity_monitor import ReadingActivity

        snapshot = ParsedSession(status="parsed")
        snapshot.estimate_base = BaseEstimate(total_tokens=152000)
        snapshot.reading_activity = ReadingActivity(
            active=True, file_name="ScanClient.cs", tool_name="read_file"
        )

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["preSendEstimate"], "")
        self.assertEqual(payload["preSendBaseTokens"], 0)
        self.assertEqual(payload["preSendBreakdown"], [])
        self.assertFalse(payload["preSendHasPrices"])
        self.assertIsNone(payload["preSendTotalCost"])
        self.assertFalse(payload["activityWarning"])
        self.assertEqual(payload["activityReadingFile"], "")

    def test_payload_activity_light_off_by_default(self) -> None:
        payload = payload_from_snapshot(ParsedSession(status="waiting")).to_json()
        self.assertFalse(payload["activityWarning"])
        self.assertEqual(payload["activityReadingFile"], "")

    def test_renderer_script_disables_pre_send_badge_by_default(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT
        self.assertIn("const composerBadgeEnabled = false;", script)
        self.assertIn('data-has-badge="${name === "request" && composerBadgeEnabled ? "true" : "false"}"', script)
        self.assertIn("if (!composerBadgeEnabled) return;", script)

    def test_payload_hides_pre_send_breakdown_rows_by_default(self) -> None:
        from codex_usage_hud.core.pre_send_estimator import BaseEstimate

        snapshot = ParsedSession(status="parsed")
        snapshot.estimate_base = BaseEstimate(
            total_tokens=48981,
            input_text_tokens=6,
            session_history_tokens=48000,
            context_files_tokens=920,
            mcp_schema_tokens=5,
            padding_tokens=50,
        )
        payload = payload_from_snapshot(snapshot).to_json()
        rows = payload["preSendBreakdown"]
        self.assertEqual(rows, [])
        self.assertFalse(payload["preSendHasPrices"])
        self.assertIsNone(payload["preSendTotalCost"])
        self.assertEqual(payload["preSendInputPrice"], 0.0)

    def test_payload_ignores_pre_send_cost_when_disabled(self) -> None:
        from codex_usage_hud.core.pre_send_estimator import BaseEstimate

        snapshot = ParsedSession(status="parsed")
        snapshot.estimate_base = BaseEstimate(
            session_history_tokens=10000,
            padding_tokens=50,
        ).with_pricing(
            input_price_per_token=5e-6,
            cached_price_per_token=0.5e-6,
            cache_hit_rate=0.8,
            model_name="gpt-5.5",
        )
        payload = payload_from_snapshot(snapshot).to_json()
        self.assertFalse(payload["preSendHasPrices"])
        self.assertIsNone(payload["preSendTotalCost"])
        self.assertEqual(payload["preSendInputPrice"], 0.0)
        self.assertEqual(payload["preSendBreakdown"], [])

    def test_payload_hides_attachment_rows_when_pre_send_disabled(self) -> None:
        from codex_usage_hud.core.pre_send_estimator import (
            AttachmentEstimate,
            BaseEstimate,
        )

        snapshot = ParsedSession(status="parsed")
        snapshot.estimate_base = BaseEstimate(
            input_text_tokens=6,
            session_history_tokens=1000,
            padding_tokens=50,
            attachments=AttachmentEstimate(
                image_tokens=300, image_count=2,
                file_tokens=5000, file_count=1,
                mention_tokens=10, mention_count=1,
                approximate=True,
            ),
        )
        payload = payload_from_snapshot(snapshot).to_json()
        self.assertEqual(payload["preSendBreakdown"], [])

    def test_renderer_script_collects_composer_attachments(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT
        # 采集与上报函数存在。
        self.assertIn("function collectComposerAttachments", script)
        self.assertIn("function reportComposerAttachments", script)
        self.assertIn("/composer-attachments", script)
        # 使用探针实测确认的稳定选择器。
        self.assertIn("composer-attachment-surface", script)
        self.assertIn("inline-mention-brand-aware", script)
        self.assertIn("file-attachment", script)
        self.assertIn("skills: []", script)
        self.assertIn("collectMentionOrSkillText", script)
        self.assertIn("attachments.skills", script)
        self.assertIn("looksLikeFileReferenceText", script)
        self.assertIn("defaultKind === \"mention\"", script)
        # mention chip 也要能带上 fiber 上的绝对路径 / 解析 [name](path) markdown。
        self.assertIn("readFiberFilePath(node)", script)
        self.assertIn("parseMarkdownFileRef", script)
        self.assertIn("pushMentionEntry", script)
        # ProseMirror atMention 节点的路径挂在 pmViewDesc.node.attrs 上。
        self.assertIn("pmViewDesc", script)
        self.assertIn("img[src^='blob:']", script)
        self.assertIn("img[src^='data:image']", script)
        self.assertIn("collectImageAttachment", script)
        self.assertIn("scheduleComposerBadgeUpdate", script)
        self.assertIn("requestAnimationFrame(() => updateComposerBadgeText", script)
        self.assertIn("composerAttachmentsDebounceMs", script)
        self.assertIn("}, composerAttachmentsDebounceMs)", script)

    def test_payload_from_snapshot_exposes_overflow_progress_style(self) -> None:
        snapshot = ParsedSession(status="parsed")
        snapshot.today_tokens = 6600000
        snapshot.today_cost_usd = 112.0
        snapshot.daily_limit_usd = 100.0
        snapshot.week_tokens = 124700000
        snapshot.week_cost_usd = 128.0
        snapshot.weekly_limit_usd = 100.0

        payload = payload_from_snapshot(snapshot).to_json()

        top_progress = payload["topProgress"]
        collapsed = top_progress["collapsed"]
        day = collapsed[1]
        week = collapsed[2]
        self.assertEqual(day["rightText"], "112%")
        self.assertEqual(day["ratio"], 1.0)
        self.assertAlmostEqual(day["overflowRatio"], 0.12, places=3)
        self.assertEqual(week["rightText"], "128%")
        self.assertEqual(week["ratio"], 1.0)
        self.assertAlmostEqual(week["overflowRatio"], 0.28, places=3)

        budget_day = top_progress["budget"][0]
        budget_week = top_progress["budget"][1]
        self.assertNotIn("rightText", budget_day)
        self.assertAlmostEqual(budget_day["overflowRatio"], 0.12, places=3)
        self.assertEqual(budget_day["overflowBadge"], "+12% / +$12.00")
        self.assertNotIn("rightText", budget_week)
        self.assertAlmostEqual(budget_week["overflowRatio"], 0.28, places=3)
        self.assertEqual(budget_week["overflowBadge"], "+28% / +$28.00")

    def test_renderer_top_redesign_styles_are_theme_tokenized(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('const version = "24";', script)
        self.assertIn(
            "scrollbar-color: var(--codex-usage-hud-divider) var(--codex-usage-hud-surface);",
            script,
        )
        self.assertIn(
            "#${rootId} .codex-usage-hud-activity-main,\n"
            "      #${rootId} .codex-usage-hud-activity-step,\n"
            "      #${rootId} .codex-usage-hud-activity-metric,\n"
            "      #${rootId} .codex-usage-hud-session-insight,\n"
            "      #${rootId} .codex-usage-hud-heavy-round,\n"
            "      #${rootId} .codex-usage-hud-token-chip {\n"
            "        background: var(--codex-usage-hud-request-panel-surface);",
            script,
        )
        self.assertIn(
            "#${rootId} .codex-usage-hud-chip {\n"
            "        background: var(--codex-usage-hud-header-surface);\n"
            "        color: var(--codex-usage-hud-text);",
            script,
        )
        self.assertIn(
            "#${rootId} .codex-usage-hud-stat-label,\n"
            "      #${rootId} .codex-usage-hud-section-title,\n"
            "      #${rootId} .codex-usage-hud-token-chip span:first-child,\n"
            "      #${rootId} .codex-usage-hud-heavy-round-detail,\n"
            "      #${rootId} .codex-usage-hud-activity-node-time,\n"
            "      #${rootId} .codex-usage-hud-activity-node-detail,\n"
            "      #${rootId} .codex-usage-hud-session-composition,\n"
            "      #${rootId} .codex-usage-hud-value.muted {\n"
            "        color: var(--codex-usage-hud-muted);",
            script,
        )
        self.assertIn(
            "background: color-mix(in srgb, var(--codex-usage-hud-warning) 13%, var(--codex-usage-hud-panel-surface));",
            script,
        )
        self.assertIn(
            "box-shadow: 0 0 0 3px color-mix(in srgb, var(--codex-usage-hud-accent) 16%, transparent);",
            script,
        )
        self.assertGreater(
            script.rfind("background: var(--codex-usage-hud-request-panel-surface);"),
            script.index("background: #101821;"),
        )

    def test_payload_warning_summary_uses_ratio_and_threshold_only(self) -> None:
        now = datetime(2026, 6, 5, 13, 10, 11).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            refreshed_at=now,
            last_event_time=now,
        )
        snapshot.today_cost_usd = 59.12
        snapshot.week_cost_usd = 210.0
        snapshot.daily_limit_usd = 85.0
        snapshot.weekly_limit_usd = 400.0
        snapshot.budget_warnings = [
            "日额度已用 59.12/85 USD (70%)，超过 50% 阈值",
            "周额度已用 210.00/400 USD (52%)，超过 50% 阈值",
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["topDetails"]["warnings"], "预警  日已用 70%，超过 50% 阈值；周已用 52%，超过 50% 阈值")

    def test_error_snapshot_uses_single_collapsed_top_progress_rail(self) -> None:
        snapshot = ParsedSession(status="missing", error="session file unavailable")

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertIn("未找到", payload["topLine"])
        top_progress = payload["topProgress"]
        self.assertEqual(top_progress["budget"], [])
        self.assertEqual(len(top_progress["collapsed"]), 1)
        self.assertEqual(top_progress["collapsed"][0]["tone"], "error")
        self.assertEqual(top_progress["collapsed"][0]["ratio"], 1.0)

    def test_support_qr_assets_are_available_for_renderer_payload(self) -> None:
        images = support_qr_payload()
        paths = support_qr_asset_paths()

        self.assertEqual([item["key"] for item in images], ["alipay", "wechat"])
        self.assertEqual([item["key"] for item in paths], ["alipay", "wechat"])
        self.assertTrue(images[0]["src"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(images[1]["src"].startswith("data:image/jpeg;base64,"))
        self.assertGreater(len(images[0]["src"]), 1000)
        self.assertGreater(len(images[1]["src"]), 1000)
        self.assertTrue(Path(paths[0]["path"]).exists())
        self.assertTrue(Path(paths[1]["path"]).exists())

    def test_payload_can_include_support_qr_images(self) -> None:
        snapshot = ParsedSession(status="waiting")
        images = support_qr_payload()

        payload = payload_from_snapshot(snapshot, support_images=images).to_json()

        self.assertEqual(payload["supportImages"][0]["label"], "支付宝")
        self.assertEqual(payload["supportImages"][1]["label"], "微信赞赏")
        self.assertTrue(payload["supportImages"][0]["src"].startswith("data:image/jpeg;base64,"))

    def test_settings_domain_update_does_not_emit_empty_support_images(self) -> None:
        snapshot = ParsedSession(status="waiting")

        payload = payload_from_snapshot(snapshot, support_images=[]).to_domain_json("settings")

        self.assertNotIn("supportImages", payload)
        self.assertNotIn("supportImages", payload["payloadDomains"]["settings"])

    def test_renderer_script_persists_support_images_across_page_reinject(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('supportImagesStorageKey = "codexUsageHudSupportImages:v1"', script)
        self.assertIn("function loadPersistedSupportImages()", script)
        self.assertIn("function persistSupportImages(images)", script)
        self.assertIn("const persistedSupportImages = loadPersistedSupportImages();", script)
        self.assertIn("nextPayload.supportImages = persistedSupportImages;", script)

    def test_payload_marks_request_errors_for_red_renderer_bubble(self) -> None:
        snapshot = ParsedSession(
            request=RequestTokens(
                status="error",
                error="exceeded retry limit, last status: 429 Too Many Requests",
            )
        )

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["requestStatus"], "error")
        self.assertIn("429 Too Many Requests", str(payload["requestLine"]))
        self.assertTrue(payload["warning"])
        self.assertIn("codex-usage-hud-error", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("node.classList.toggle(errorClass", renderer_hud.RENDERER_HUD_SCRIPT)

    def test_payload_exposes_top_copy_targets_and_live_request_row_details(self) -> None:
        started_at = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        completed_at = datetime(2026, 6, 5, 13, 0, 5).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            request=RequestTokens(status="running", model="gpt-5.5"),
            slow=SlowSummary(
                slowest_tool_call=ToolCallTiming(
                    call_id="call_1",
                    name="shell_command",
                    args='{"command":"git status","timeout_ms":1000}',
                    start=started_at,
                    start_line=3,
                    end=completed_at,
                    end_line=4,
                ),
                longest_gap_detail=GapTiming(
                    start=started_at,
                    end=completed_at,
                    duration_seconds=5.0,
                    category="model_or_idle",
                    from_event="user:开始",
                    to_event="assistant:结束",
                    start_line=10,
                    end_line=11,
                ),
            ),
        )
        snapshot.request_history = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=100,
                cached_tokens=10,
                output_tokens=20,
                reasoning_tokens=0,
                total_tokens=120,
                estimated=False,
                cost_usd=0.01,
                started_at=started_at,
                completed_at=completed_at,
            ),
            RequestRound(
                index=2,
                status="running",
                model="gpt-5.5",
                input_tokens=200,
                cached_tokens=50,
                output_tokens=10,
                reasoning_tokens=0,
                total_tokens=210,
                estimated=True,
                cost_usd=0.02,
                started_at=started_at,
            ),
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        self.assertEqual(payload["topCopies"]["slow"], "git status")
        self.assertIn("类型: 模型思考", payload["topCopies"]["gap"])
        self.assertIn("行号: 10 -> 11", payload["topCopies"]["gap"])
        top_details = payload["topDetails"]
        self.assertEqual(top_details["slow"], "最慢工具:5.0s")
        self.assertEqual(top_details["gap"], "最长等待:5.0s")
        trail_titles = [str(item["title"]) for item in top_details["activityTrail"]]
        self.assertTrue(any("工具调用" in title for title in trail_titles))
        self.assertTrue(any("等待结束" in title for title in trail_titles))
        self.assertIn("shell_command", str(top_details["activityTrail"]))
        details = payload["requestRowDetails"]
        self.assertEqual(details[0]["prefix"].strip(), "#2 ~$0.020")
        self.assertTrue(details[0]["running"])
        self.assertTrue(str(details[0]["time"]).strip().endswith("s"))
        self.assertTrue(str(details[0]["startedAt"]))
        self.assertFalse(details[1]["running"])
        self.assertEqual(details[1]["time"], "13:00:05")

    def test_activity_trail_uses_current_rounds_and_filters_old_tool_events(self) -> None:
        task_started_at = datetime(2026, 6, 5, 9, 9, 0).astimezone()
        old_started_at = datetime(2026, 6, 4, 22, 38, 4).astimezone()
        old_completed_at = datetime(2026, 6, 4, 22, 38, 24).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            task_started_at=task_started_at,
            request=RequestTokens(status="confirmed", model="gpt-5.5"),
            slow=SlowSummary(
                slowest_tool_call=ToolCallTiming(
                    call_id="old",
                    name="shell_command",
                    args='{"command":"python -m unittest old"}',
                    start=old_started_at,
                    start_line=3,
                    end=old_completed_at,
                    end_line=4,
                )
            ),
        )
        snapshot.request_history = [
            RequestRound(
                index=index,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=100 + index,
                cached_tokens=10,
                output_tokens=20,
                reasoning_tokens=0,
                total_tokens=120 + index,
                estimated=False,
                cost_usd=0.001,
                started_at=task_started_at,
                completed_at=datetime(2026, 6, 5, 9, 9, index).astimezone(),
                activity_summary=f"输入：当前需求第 {index} 轮",
                copy_text=f"输入：当前需求第 {index} 轮",
            )
            for index in range(1, 30)
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        trail = payload["topDetails"]["activityTrail"]
        round_nodes = [item for item in trail if str(item["title"]).startswith("轮次 #")]
        self.assertEqual(len(round_nodes), 29)
        self.assertNotIn("22:38", str(trail))
        self.assertIn("当前需求第 29 轮", str(trail[0]))

    def test_payload_formats_heavy_rounds_as_copyable_single_line_reason(self) -> None:
        started_at = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        completed_at = datetime(2026, 6, 5, 13, 0, 31).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            request=RequestTokens(status="confirmed", model="gpt-5.5"),
        )
        snapshot.session_request_history = [
            RequestRound(
                index=28,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=208000,
                cached_tokens=3840,
                output_tokens=1232,
                reasoning_tokens=0,
                total_tokens=210000,
                estimated=False,
                cost_usd=1.06,
                started_at=started_at,
                completed_at=completed_at,
                activity_summary="输入：分析一个很大的日志文件",
                copy_text="输入：\n分析一个很大的日志文件",
            ),
            RequestRound(
                index=7,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=20,
                cached_tokens=0,
                output_tokens=10,
                reasoning_tokens=0,
                total_tokens=30,
                estimated=False,
                cost_usd=0.01,
            ),
        ]
        snapshot.request_history = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=10,
                cached_tokens=0,
                output_tokens=5,
                reasoning_tokens=0,
                total_tokens=15,
                estimated=False,
                cost_usd=0.005,
            )
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        heavy = payload["topDetails"]["heavyRounds"][0]
        self.assertEqual(heavy["title"], "#28 $1.06 · ∑210k")
        self.assertEqual(heavy["detail"], "输入：分析一个很大的日志文件")
        self.assertIn("分析一个很大的日志文件", heavy["copyText"])
        self.assertNotIn("gpt-5.5", heavy["detail"])
        self.assertNotIn("已确认", heavy["detail"])

    def test_payload_switches_activity_card_to_completed_task_stats(self) -> None:
        started_at = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        completed_at = datetime(2026, 6, 5, 13, 0, 42).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            task_prompt="把右侧活动面板改成完成态统计",
            task_index=4,
            task_count=4,
            task_started_at=started_at,
            task_completed_at=completed_at,
            last_output=Activity(
                kind="agent",
                detail="已完成 HUD 布局和统计字段调整。",
                timestamp=completed_at,
            ),
            request=RequestTokens(status="confirmed", model="gpt-5.5"),
        )
        snapshot.request_history = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=100,
                cached_tokens=80,
                output_tokens=20,
                reasoning_tokens=0,
                total_tokens=120,
                estimated=False,
                cost_usd=0.001,
                started_at=started_at,
                completed_at=completed_at,
            ),
            RequestRound(
                index=2,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=50,
                cached_tokens=40,
                output_tokens=10,
                reasoning_tokens=0,
                total_tokens=60,
                estimated=False,
                cost_usd=0.002,
                started_at=started_at,
                completed_at=completed_at,
            ),
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        top_details = payload["topDetails"]
        self.assertEqual(top_details["executingLabel"], "完成任务")
        self.assertEqual(top_details["taskOrdinal"], "共4次需求")
        self.assertEqual(top_details["taskOrdinalSession"], "共4次需求")
        self.assertEqual(top_details["taskOrdinalActivity"], "")
        self.assertEqual(top_details["executing"], "已完成 HUD 布局和统计字段调整。")
        self.assertEqual(top_details["currentTaskLabel"], "当前需求")
        self.assertEqual(top_details["currentTask"], "把右侧活动面板改成完成态统计")
        self.assertEqual(top_details["activityElapsedLabel"], "已处理")
        self.assertEqual(top_details["activityElapsed"], "42.0s")
        self.assertEqual(top_details["activityGapLabel"], "处理轮次")
        self.assertEqual(top_details["activityGap"], "2轮")
        self.assertEqual(top_details["activityLastLabel"], "处理花费")
        self.assertEqual(top_details["activityLast"], "$0.003")
        self.assertEqual(top_details["activityLastTooltip"], "180Tokens/$0.003/80%")

    def test_payload_merges_same_second_activity_nodes_and_suppresses_token_details(self) -> None:
        started_at = datetime(2026, 6, 5, 13, 0, 0).astimezone()
        completed_at = datetime(2026, 6, 5, 13, 0, 42).astimezone()
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            task_prompt="完成右侧活动轨迹合并",
            task_started_at=started_at,
            task_completed_at=completed_at,
            last_event_time=completed_at,
            activity=Activity(
                kind="confirmed",
                detail="received token_count",
                timestamp=completed_at,
            ),
            request=RequestTokens(
                status="confirmed",
                model="gpt-5.5",
                completed_at=completed_at,
            ),
        )
        snapshot.request_history = [
            RequestRound(
                index=1,
                status="confirmed",
                model="gpt-5.5",
                input_tokens=100,
                cached_tokens=80,
                output_tokens=20,
                reasoning_tokens=0,
                total_tokens=120,
                estimated=False,
                cost_usd=0.001,
                started_at=started_at,
                completed_at=completed_at,
            )
        ]

        payload = payload_from_snapshot(snapshot).to_json()

        trail = payload["topDetails"]["activityTrail"]
        self.assertGreaterEqual(len(trail), 1)
        newest = trail[0]
        self.assertEqual(newest["title"], "任务完成，Token确认")
        self.assertEqual(newest["detail"], "完成右侧活动轨迹合并")
        self.assertNotIn("请求完成", str(newest))
        self.assertNotIn("received token_count", str(newest))

    def test_renderer_script_resizes_request_panel_from_fixed_bottom(self) -> None:
        script = renderer_hud.RENDERER_HUD_SCRIPT

        self.assertIn('beginGesture(event, name, "resize", action.dataset.edge || "", false)', script)
        self.assertIn('const resizeFromLeft = gesture.edge === "left" || gesture.edge.endsWith("-left")', script)
        self.assertIn('const left = resizeFromLeft ? (gesture.left + gesture.width - width) : gesture.left', script)
        self.assertIn("height = clamp(gesture.height - dy", script)
        self.assertIn("top = bottom - height", script)
        self.assertIn("patch.bottomOffset", script)
        self.assertIn("anchor.top + anchor.height + bottomOffset - height", script)
        self.assertIn(": clamp(anchor.top + Number(state.yOffset || 0)", script)


class RendererHudClientTests(unittest.TestCase):
    def test_client_installs_renderer_script_once_and_pushes_payloads(self) -> None:
        install_calls: list[tuple[str, str]] = []
        list_calls = 0
        update_expressions: list[str] = []
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            nonlocal list_calls
            del port, timeout_seconds
            list_calls += 1
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del timeout_seconds
            install_calls.append((websocket_url, script))
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, timeout_seconds
            self.assertEqual(method, "Runtime.evaluate")
            update_expressions.append(str(params["expression"]))
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_install, fake_send)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            self.assertTrue(client.update_payload({"topLine": "C", "requestLine": "D"}))
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(len(install_calls), 1)
        self.assertEqual(list_calls, 1)
        self.assertIn("__codexUsageHudUpdate", update_expressions[0])
        self.assertIn('"topLine": "C"', update_expressions[1])

    def test_send_update_records_cdp_and_renderer_apply_timings(self) -> None:
        client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
        originals = (renderer_hud.send_cdp_command,)
        expressions: list[str] = []

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, timeout_seconds
            self.assertEqual(method, "Runtime.evaluate")
            expressions.append(str(params["expression"]))
            return {
                "result": {
                    "result": {
                        "value": {
                            "ok": True,
                            "applyMs": 812.5,
                        }
                    }
                }
            }

        (renderer_hud.send_cdp_command,) = (fake_send,)
        try:
            self.assertTrue(
                client._send_update(  # pylint: disable=protected-access
                    "ws://127.0.0.1/devtools/page/1",
                    {
                        "topLine": "A",
                        "payloadDomains": {"sessionSwitch": {"topLine": "A"}},
                    },
                )
            )
        finally:
            (renderer_hud.send_cdp_command,) = originals

        self.assertIn("performance.now", expressions[0])
        self.assertGreaterEqual(client.last_update_metrics["cdpMs"], 0.0)
        self.assertEqual(client.last_update_metrics["rendererApplyMs"], 812.5)
        self.assertGreater(client.last_update_metrics["payloadBytes"], 0)
        self.assertEqual(client.last_update_metrics["payloadDomains"], ["sessionSwitch"])

    def test_client_uses_subscribed_target_state_after_cache_ttl(self) -> None:
        install_calls: list[tuple[str, str]] = []
        list_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            nonlocal list_calls
            del port, timeout_seconds
            list_calls += 1
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del timeout_seconds
            install_calls.append((websocket_url, script))
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_install, fake_send)
        try:
            client = RendererHudClient(
                port=9229,
                timeout_seconds=0.05,
                target_cache_seconds=0.0,
                enabled=True,
            )
            self.assertTrue(client.update_payload({"topLine": "A"}))
            time.sleep(0.002)
            self.assertTrue(client.update_payload({"topLine": "B"}))
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(len(install_calls), 1)
        self.assertEqual(list_calls, 1)

    def test_client_reports_target_discovery_disconnect_without_http_rescan(self) -> None:
        list_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            nonlocal list_calls
            del port, timeout_seconds
            list_calls += 1
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_install, fake_send)
        try:
            client = RendererHudClient(
                port=9229,
                timeout_seconds=0.05,
                target_cache_seconds=0.0,
                enabled=True,
            )
            self.assertTrue(client.update_payload({"topLine": "A"}))
            client._target_discovery.mark_disconnected("CDP websocket closed")
            self.assertFalse(client.update_payload({"topLine": "B"}))
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(list_calls, 1)
        self.assertIn("CDP target discovery disconnected", client.last_error)

    def test_client_starts_active_session_binding_after_update(self) -> None:
        ensure_calls: list[tuple[str, str]] = []
        close_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                nonlocal close_calls
                close_calls += 1

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_active_session_callback(lambda payload: payload)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertEqual(close_calls, 1)

    def test_client_bootstraps_active_session_binding_before_payload_update(self) -> None:
        ensure_calls: list[tuple[str, str]] = []
        update_expressions: list[str] = []
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                pass

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, timeout_seconds
            self.assertEqual(method, "Runtime.evaluate")
            update_expressions.append(str(params["expression"]))
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_active_session_callback(lambda payload: payload)
            self.assertTrue(client.bootstrap_active_session())
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertIn("__codexUsageHudReportActiveSession", update_expressions[0])
        self.assertNotIn("__codexUsageHudUpdate", update_expressions[0])

    def test_client_starts_attachments_binding_after_update(self) -> None:
        # 附件走 CDP binding（页面 CSP 拦截 fetch），验证 update 后 binding 被 ensure。
        ensure_calls: list[tuple[str, str]] = []
        close_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        captured: list[str] = []

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds
                captured.append(binding_name)

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                nonlocal close_calls
                close_calls += 1

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_attachments_callback(lambda payload: payload)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertIn(renderer_hud.COMPOSER_ATTACHMENTS_BINDING_NAME, captured)
        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertEqual(close_calls, 1)

    def test_client_starts_settings_command_binding_after_update(self) -> None:
        ensure_calls: list[tuple[str, str]] = []
        close_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        captured: list[str] = []

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds
                captured.append(binding_name)

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                nonlocal close_calls
                close_calls += 1

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_settings_command_callback(lambda payload: payload)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertIn(renderer_hud.SETTINGS_COMMAND_BINDING_NAME, captured)
        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertEqual(close_calls, 1)

    def test_client_starts_layout_binding_after_update(self) -> None:
        # 布局变更（拖拽/缩放/展开）通过 CDP binding 上报，验证 update 后 binding 被 ensure。
        ensure_calls: list[tuple[str, str]] = []
        close_calls = 0
        originals = (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        )

        captured: list[str] = []

        class FakeBinding:
            def __init__(self, binding_name, callback, *, timeout_seconds, disconnect_callback=None):
                self.binding_name = binding_name
                self.callback = callback
                self.timeout_seconds = timeout_seconds
                captured.append(binding_name)

            def ensure(self, websocket_url: str, target_id: str) -> None:
                ensure_calls.append((websocket_url, target_id))

            def close(self) -> None:
                nonlocal close_calls
                close_calls += 1

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "title": "Codex",
                    "url": "app://codex",
                    "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1",
                }
            ]

        def fake_install(websocket_url: str, script: str, timeout_seconds: float) -> str:
            del websocket_url, script, timeout_seconds
            return "script-1"

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, method, params, timeout_seconds
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.install_new_document_script,
            renderer_hud.send_cdp_command,
            renderer_hud._RendererBinding,
        ) = (fake_list_targets, fake_install, fake_send, FakeBinding)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client.set_layout_callback(lambda payload: payload)
            self.assertTrue(client.update_payload({"topLine": "A", "requestLine": "B"}))
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.install_new_document_script,
                renderer_hud.send_cdp_command,
                renderer_hud._RendererBinding,
            ) = originals

        self.assertIn(renderer_hud.LAYOUT_BINDING_NAME, captured)
        self.assertEqual(ensure_calls, [("ws://127.0.0.1/devtools/page/1", "target-1")])
        self.assertEqual(close_calls, 1)

    def test_client_does_not_expose_renderer_settings_polling_fallback(self) -> None:
        client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)

        self.assertFalse(hasattr(client, "take_settings_command"))

    def test_client_close_uses_cached_target_when_force_lookup_fails(self) -> None:
        calls: list[tuple[str, str]] = []
        removed_scripts: list[tuple[str, str]] = []
        originals = (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
            renderer_hud.remove_new_document_script,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            raise RuntimeError("target list unavailable")

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del method, timeout_seconds
            calls.append((websocket_url, str(params["expression"])))
            return {"result": {"result": {"value": True}}}

        def fake_remove(websocket_url: str, identifier: str, timeout_seconds: float) -> None:
            del timeout_seconds
            removed_scripts.append((websocket_url, identifier))

        (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
            renderer_hud.remove_new_document_script,
        ) = (fake_list_targets, fake_send, fake_remove)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            client._websocket_url = "ws://cached"
            client._script_identifier = "script-1"
            client.close()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.send_cdp_command,
                renderer_hud.remove_new_document_script,
            ) = originals

        self.assertEqual(calls[0][0], "ws://cached")
        self.assertIn("codex-usage-hud-root", calls[0][1])
        self.assertEqual(removed_scripts, [("ws://cached", "script-1")])

    def test_remove_renderer_hud_from_pages_sweeps_page_targets(self) -> None:
        calls: list[str] = []
        originals = (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
        )

        def fake_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            self.assertEqual(port, 9229)
            del timeout_seconds
            return [
                {
                    "type": "page",
                    "title": "Codex",
                    "webSocketDebuggerUrl": "ws://page-1",
                },
                {
                    "type": "worker",
                    "title": "ignored",
                    "webSocketDebuggerUrl": "ws://worker",
                },
            ]

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del method, timeout_seconds
            calls.append(f"{websocket_url}:{params['expression']}")
            return {"result": {"result": {"value": True}}}

        (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_send)
        try:
            removed = renderer_hud.remove_renderer_hud_from_pages(
                port=9229,
                timeout_seconds=0.05,
            )
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(removed, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("ws://page-1", calls[0])
        self.assertIn("__codexUsageHudRemove", calls[0])


if __name__ == "__main__":
    unittest.main()
