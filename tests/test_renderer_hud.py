"""Unit tests for the renderer-injected HUD payload and CDP client."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
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
        self.assertEqual(payload["appVersion"], "1.0.2")
        self.assertIn("本会话用量", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("实时请求", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("符号说明", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("只看剩余", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("applyTheme(root, nextPayload)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("--codex-usage-hud-surface", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-update-button", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="update-action"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-settings-button", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="settings-open"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-resize-edge-left", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-resize-corner-bottom-right", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-overflow", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-progress-badge", renderer_hud.RENDERER_HUD_SCRIPT)

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
        self.assertIn("settingsCommandKey", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("localStorage.setItem", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("请作者喝咖啡链接", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-setting-key="support_url"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-setting-key="work_overlay_max_items"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("PySide6 桌面气泡数量", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("0 为关闭", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("气泡依赖 PySide6", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("需要安装环境", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("立即安装", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("已安装，立即启用", renderer_hud.RENDERER_HUD_SCRIPT)
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
        self.assertIn("codex-usage-hud-support-qr-grid", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-support-qr-title", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("previousPayload.supportImages?.length", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settingsBridgeUrl", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("updateState", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("renderUpdateButtons", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("const submitted = submitSettingsCommand", renderer_hud.RENDERER_HUD_SCRIPT)
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

        self.assertIn('const version = "17";', script)
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

        self.assertIn('beginGesture(event, name, action.dataset.action, action.dataset.edge || "")', script)
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

    def test_client_consumes_renderer_settings_command_over_cdp(self) -> None:
        expressions: list[str] = []
        originals = (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
        )

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

        def fake_send(
            websocket_url: str,
            method: str,
            params: dict[str, object],
            timeout_seconds: float,
        ) -> dict[str, object]:
            del websocket_url, timeout_seconds
            self.assertEqual(method, "Runtime.evaluate")
            expression = str(params["expression"])
            expressions.append(expression)
            self.assertIn(renderer_hud.SETTINGS_COMMAND_STORAGE_KEY, expression)
            self.assertIn("localStorage.removeItem", expression)
            self.assertIn("expiresAt", expression)
            return {
                "result": {
                    "result": {
                        "value": {
                            "action": "save",
                            "settings": {"daily_reset_time": "09:30"},
                        }
                    }
                }
            }

        (
            renderer_hud.list_targets,
            renderer_hud.send_cdp_command,
        ) = (fake_list_targets, fake_send)
        try:
            client = RendererHudClient(port=9229, timeout_seconds=0.05, enabled=True)
            command = client.take_settings_command()
            second = client.take_settings_command()
        finally:
            (
                renderer_hud.list_targets,
                renderer_hud.send_cdp_command,
            ) = originals

        self.assertEqual(command["action"], "save")
        self.assertEqual(command["settings"]["daily_reset_time"], "09:30")
        self.assertIsNone(second)
        self.assertEqual(len(expressions), 1)

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
