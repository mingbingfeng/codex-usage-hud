"""Unit tests for the read-only CDP DOM probe helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms.cdp_probe import (
    CodexCdpProbe,
    CdpRect,
    DOM_PROBE_SCRIPT,
    install_new_document_script,
    pick_page_target,
    snapshot_from_evaluate_result,
)
from codex_usage_hud.platforms import cdp_probe


class CdpProbeTests(unittest.TestCase):
    def test_pick_page_target_prefers_codex_page(self) -> None:
        targets = [
            {
                "type": "page",
                "title": "Other",
                "url": "https://example.test",
                "webSocketDebuggerUrl": "ws://127.0.0.1/other",
            },
            {
                "type": "page",
                "title": "Codex",
                "url": "app://-/index.html",
                "webSocketDebuggerUrl": "ws://127.0.0.1/codex",
            },
        ]

        self.assertEqual(pick_page_target(targets)["webSocketDebuggerUrl"], "ws://127.0.0.1/codex")

    def test_pick_page_target_prefers_main_index_over_hotkey_window(self) -> None:
        targets = [
            {
                "type": "page",
                "title": "Codex",
                "url": "app://-/index.html?initialRoute=%2Fhotkey-window",
                "webSocketDebuggerUrl": "ws://127.0.0.1/hotkey",
            },
            {
                "type": "page",
                "title": "Codex",
                "url": "app://-/index.html",
                "webSocketDebuggerUrl": "ws://127.0.0.1/main",
            },
        ]

        self.assertEqual(
            pick_page_target(targets)["webSocketDebuggerUrl"],
            "ws://127.0.0.1/main",
        )

    def test_pick_page_target_rejects_hotkey_only_surface(self) -> None:
        targets = [
            {
                "type": "page",
                "title": "Codex Hotkey",
                "url": "app://-/index.html?initialRoute=%2Fhotkey-window",
                "webSocketDebuggerUrl": "ws://127.0.0.1/hotkey",
            },
        ]

        with self.assertRaisesRegex(RuntimeError, "No main Codex CDP page target found"):
            pick_page_target(targets)

    def test_pick_page_target_rejects_non_codex_pages(self) -> None:
        targets = [
            {
                "type": "page",
                "title": "Other",
                "url": "https://example.test",
                "webSocketDebuggerUrl": "ws://127.0.0.1/other",
            },
        ]

        with self.assertRaises(RuntimeError):
            pick_page_target(targets)

    def test_probe_returns_none_and_cools_down_when_debug_port_missing(self) -> None:
        calls = 0
        original = cdp_probe.list_targets

        def failing_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            nonlocal calls
            del port, timeout_seconds
            calls += 1
            raise OSError("debug port unavailable")

        cdp_probe.list_targets = failing_list_targets
        try:
            probe = CodexCdpProbe(
                port=9229,
                timeout_seconds=0.05,
                cache_seconds=0.0,
                failure_cooldown_seconds=60.0,
                enabled=True,
            )

            self.assertIsNone(probe.snapshot(force=True))
            self.assertIsNone(probe.snapshot())
            self.assertEqual(calls, 1)
        finally:
            cdp_probe.list_targets = original

    def test_snapshot_from_evaluate_result_reads_rects_and_ref(self) -> None:
        result = {
            "result": {
                "result": {
                    "value": {
                        "sessionId": "thread-123",
                        "title": "Selected Thread",
                        "devicePixelRatio": 1.25,
                        "headerRect": {"left": 10, "top": 20, "width": 900, "height": 44},
                        "composerRect": {"left": 260, "top": 700, "right": 1000, "bottom": 760},
                        "appError": "exceeded retry limit, last status: 429 Too Many Requests",
                    }
                }
            }
        }

        snapshot = snapshot_from_evaluate_result(result)

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.session_id, "thread-123")
        self.assertEqual(snapshot.title, "Selected Thread")
        self.assertEqual(
            snapshot.app_error,
            "exceeded retry limit, last status: 429 Too Many Requests",
        )
        self.assertEqual(snapshot.device_pixel_ratio, 1.25)
        self.assertEqual(snapshot.header_rect, CdpRect(10.0, 20.0, 910.0, 64.0))
        self.assertEqual(snapshot.composer_rect, CdpRect(260.0, 700.0, 1000.0, 760.0))

    def test_dom_probe_scores_conversation_header_before_global_menu(self) -> None:
        self.assertIn("header.app-header-tint", DOM_PROBE_SCRIPT)
        self.assertIn("app-shell-header-context-menu-surface", DOM_PROBE_SCRIPT)
        self.assertIn("FileEditViewWindowHelp", DOM_PROBE_SCRIPT)
        self.assertIn("rect.top > 20", DOM_PROBE_SCRIPT)
        self.assertIn("appError: appErrorText()", DOM_PROBE_SCRIPT)
        self.assertIn("exceeded retry limit", DOM_PROBE_SCRIPT)
        self.assertIn("[class*='text-token-error-foreground']", DOM_PROBE_SCRIPT)
        self.assertIn('node.closest("aside")', DOM_PROBE_SCRIPT)
        self.assertIn(".wrap-anywhere", DOM_PROBE_SCRIPT)
        self.assertIn(".text-pretty", DOM_PROBE_SCRIPT)
        self.assertNotIn('"main header"', DOM_PROBE_SCRIPT)

    def test_install_new_document_script_registers_and_evaluates_script(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        original = cdp_probe.send_cdp_commands

        def fake_send_commands(
            websocket_url: str,
            commands: list[tuple[str, dict[str, object]]],
            timeout_seconds: float,
        ) -> dict[int, dict[str, object]]:
            del websocket_url, timeout_seconds
            calls.extend(commands)
            return {2: {"result": {"identifier": "script-1"}}, 3: {"result": {}}}

        cdp_probe.send_cdp_commands = fake_send_commands
        try:
            identifier = install_new_document_script("ws://127.0.0.1/page", "window.x=1", 0.1)
        finally:
            cdp_probe.send_cdp_commands = original

        self.assertEqual(identifier, "script-1")
        self.assertEqual(calls[0][0], "Page.enable")
        self.assertEqual(calls[1], ("Page.addScriptToEvaluateOnNewDocument", {"source": "window.x=1"}))
        self.assertEqual(calls[2][0], "Runtime.evaluate")
        self.assertEqual(calls[2][1]["expression"], "window.x=1")



if __name__ == "__main__":
    unittest.main()
