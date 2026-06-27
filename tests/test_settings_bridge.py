"""Unit tests for the renderer settings bridge."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.config import UserConfig, UserConfigStore
from codex_usage_hud.settings_bridge import SettingsBridgeServer


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class SettingsBridgeServerTests(unittest.TestCase):
    def test_settings_endpoint_loads_and_saves_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            existing = UserConfig.defaults()
            existing.daily_budget_usd = 12.34
            store.save(existing)
            bridge = SettingsBridgeServer(store, port=0)
            try:
                url = bridge.start()
                with urlopen(f"{url}/settings", timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    private_network_header = response.headers.get(
                        "Access-Control-Allow-Private-Network"
                    )

                self.assertEqual(payload["status"], "ok")
                self.assertIn("settingsPath", payload)
                self.assertEqual(private_network_header, "true")

                request = Request(
                    f"{url}/settings",
                    data=json.dumps(
                        {"settings": {"weekly_adjustment_usd": 9.25}}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                persisted = store.load()
                persisted_adjustment = persisted.weekly_adjustment_usd
                persisted_daily_budget = persisted.daily_budget_usd
            finally:
                bridge.close()

        self.assertEqual(saved["status"], "ok")
        self.assertEqual(saved["settings"]["weekly_adjustment_usd"], 9.25)
        self.assertEqual(persisted_adjustment, 9.25)
        self.assertEqual(persisted_daily_budget, 12.34)

    def test_configured_bridge_port_is_stable_when_available(self) -> None:
        port = _available_port()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            bridge = SettingsBridgeServer(store, port=port)
            try:
                url = bridge.start()
            finally:
                bridge.close()

        self.assertTrue(
            url.endswith(f":{port}"),
            msg=url,
        )

    def test_restart_endpoint_invokes_restart_callback(self) -> None:
        restart_requested = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")
            bridge = SettingsBridgeServer(
                store,
                port=0,
                restart_callback=restart_requested.set,
            )
            try:
                url = bridge.start()
                request = Request(
                    f"{url}/restart",
                    data=json.dumps({"reason": "settings"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                bridge.close()

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(restart_requested.is_set())

    def test_command_endpoint_invokes_command_callback(self) -> None:
        received: list[dict[str, object]] = []
        command_received = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")

            def on_command(command: dict[str, object]) -> None:
                received.append(command)
                command_received.set()

            bridge = SettingsBridgeServer(
                store,
                port=0,
                command_callback=on_command,
            )
            try:
                url = bridge.start()
                request = Request(
                    f"{url}/command",
                    data=json.dumps({"action": "exit", "id": "cmd-1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                bridge.close()

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(command_received.is_set())
        self.assertEqual(received, [{"action": "exit", "id": "cmd-1"}])

    def test_active_session_endpoint_invokes_callback(self) -> None:
        received: list[dict[str, object]] = []
        session_received = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = UserConfigStore(Path(temp_dir) / "hud_settings.json")

            def on_active_session(payload: dict[str, object]) -> None:
                received.append(payload)
                session_received.set()

            bridge = SettingsBridgeServer(
                store,
                port=0,
                active_session_callback=on_active_session,
            )
            try:
                url = bridge.start()
                request = Request(
                    f"{url}/active-session",
                    data=json.dumps(
                        {"sessionId": "thread-1", "title": "Selected Thread"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                bridge.close()

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(session_received.is_set())
        self.assertEqual(
            received,
            [{"sessionId": "thread-1", "title": "Selected Thread"}],
        )


if __name__ == "__main__":
    unittest.main()
