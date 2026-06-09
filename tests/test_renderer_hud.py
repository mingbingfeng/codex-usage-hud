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
    ConfirmedTokens,
    GapTiming,
    ParsedSession,
    RequestRound,
    RequestTokens,
    SlowSummary,
    ToolCallTiming,
)
from codex_usage_hud.support_assets import support_qr_asset_paths, support_qr_payload
from codex_usage_hud.ui import renderer_hud
from codex_usage_hud.ui.renderer_hud import RendererHudClient, payload_from_snapshot


class RendererHudPayloadTests(unittest.TestCase):
    def test_payload_from_snapshot_formats_compact_hud_lines(self) -> None:
        snapshot = ParsedSession(
            session_id="session-abcdef123456",
            session_title="Live Renderer Thread",
            status="parsed",
            selection_source="cdp:Live Renderer Thread",
            refreshed_at=datetime(2026, 6, 5, 13, 10, 11).astimezone(),
            last_event_time=datetime(2026, 6, 5, 13, 10, 1).astimezone(),
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
            update_state={
                "visible": True,
                "phase": "downloading",
                "icon": "download",
                "title": "正在下载更新：50%",
            },
        ).to_json()

        top_line = str(payload["topLine"])
        self.assertNotIn("Live Renderer Thread", str(payload["topLine"]))
        self.assertTrue(top_line.startswith("本会话 14k/$0.132/◎~61% | 今日"))
        self.assertIn("今日 50k/$0.500", top_line)
        self.assertNotIn("命中", top_line)
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
        self.assertIn("updateState", payload)
        self.assertEqual(payload["appVersion"], "1.0.0")
        self.assertIn("实时请求", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-update-button", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="update-action"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-settings-button", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('data-action="settings-open"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-resize-edge-left", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-resize-corner-bottom-right", renderer_hud.RENDERER_HUD_SCRIPT)
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
        self.assertIn("HUD 更新暂停", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-install-update", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-restart", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settings-apply-display-mode", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-settings-loading-track", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("openSettingsLoading", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settingsCommandKey", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("localStorage.setItem", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("请作者喝咖啡链接", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-setting-key="support_url"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("拉取价格", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("window.confirm", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("fetch(`${bridge}", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-support-qr-grid", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-support-qr-title", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("previousPayload.supportImages?.length", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("settingsBridgeUrl", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("updateState", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("renderUpdateButtons", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("navigator.clipboard", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("requestRowDetails", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("header.app-header-tint", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("app-shell-header-context-menu-surface", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("topTitlebarSlot", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("headerLayoutSignature", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("characterData: true", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("topSlotCache = null", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn('window[scrollHandlerName] = () => scheduleForPanels(["request"])', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("mutations.some(mutationTouchesHeaderScope)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function scheduleRequestAfterComposerSettles()", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("mutations.some(mutationTouchesComposerScope)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("if (!mutations.some(mutationTouchesComposerScope)) return;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("mutations.some(mutationTouchesTextInput)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("pointer-events: none;", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function headerLeftControlEdge(headerNode, header", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("function headerRightControlStart(headerNode, header", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("chat actions", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("open in", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-line-inner", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codex-usage-hud-marquee", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("interpolateNumericText", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("canAnimateNumericText", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("FileEditViewWindowHelp", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("-webkit-app-region: no-drag", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("headerTitleTextEdge", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("headerRightControlStart", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("manualTopRect", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("footerGapSlot", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("codexUsageHudPanelState:v5", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertIn("grid-template-rows: auto auto minmax(0, 1fr)", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("Σ", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('data-action="reset"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn("↯", renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertNotIn('"main header"', renderer_hud.RENDERER_HUD_SCRIPT)
        self.assertTrue(payload["updateState"]["visible"])
        self.assertEqual(payload["updateState"]["phase"], "downloading")
        top_details = payload["topDetails"]
        self.assertIsInstance(top_details, dict)
        self.assertEqual(top_details["title"], "Live Renderer Thread")
        self.assertIn("本次请求", str(top_details["confirmed"]))
        self.assertNotIn("命中", str(top_details))
        self.assertTrue(payload["requestRows"])
        request_row = str(payload["requestRows"][0])
        self.assertLess(request_row.index("↑1,200"), request_row.index("◎~67%"))
        self.assertLess(request_row.index("↻800"), request_row.index("∑1,290"))
        request_row_details = payload["requestRowDetails"]
        self.assertIsInstance(request_row_details, list)
        self.assertEqual(request_row, request_row_details[0]["text"])

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
        details = payload["requestRowDetails"]
        self.assertEqual(details[0]["prefix"].strip(), "#2 ~$0.020")
        self.assertTrue(details[0]["running"])
        self.assertTrue(str(details[0]["time"]).strip().endswith("s"))
        self.assertTrue(str(details[0]["startedAt"]))
        self.assertFalse(details[1]["running"])
        self.assertEqual(details[1]["time"], "13:00:05")

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


if __name__ == "__main__":
    unittest.main()
