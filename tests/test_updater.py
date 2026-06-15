"""Unit tests for GitHub Release updater helpers."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.updater import (
    AutoUpdateManager,
    UpdateInfo,
    UpdateAsset,
    format_update_info,
    installer_asset_name,
    is_newer_version,
    select_windows_installer_asset,
    version_key,
)


class VersionComparisonTests(unittest.TestCase):
    def test_version_key_accepts_plain_and_tagged_versions(self) -> None:
        self.assertEqual(version_key("v1.0.0"), (1, 0, 0))
        self.assertEqual(version_key("1.2"), (1, 2, 0))

    def test_is_newer_version_uses_semver_order(self) -> None:
        self.assertTrue(is_newer_version("v1.0.1", "1.0.0"))
        self.assertFalse(is_newer_version("v1.0.0", "1.0.0"))
        self.assertFalse(is_newer_version("v0.3.0", "1.0.0"))


class UpdateAssetSelectionTests(unittest.TestCase):
    def test_installer_asset_name_matches_release_convention(self) -> None:
        self.assertEqual(
            installer_asset_name("1.0.0"),
            "codex-usage-hud-v1.0.0-windows-x64-setup.exe",
        )

    def test_select_windows_installer_prefers_exact_versioned_asset(self) -> None:
        asset = select_windows_installer_asset(
            [
                {
                    "name": "codex-usage-hud-v1.0.0-windows-x64.zip",
                    "browser_download_url": "https://example.test/archive.zip",
                },
                {
                    "name": "codex-usage-hud-v1.0.0-windows-x64-setup.exe",
                    "browser_download_url": "https://example.test/setup.exe",
                    "size": 123,
                },
            ],
            latest_version="v1.0.0",
        )

        self.assertIsNotNone(asset)
        self.assertEqual(asset.name, "codex-usage-hud-v1.0.0-windows-x64-setup.exe")
        self.assertEqual(asset.size, 123)

    def test_format_update_info_mentions_installer_when_available(self) -> None:
        asset = select_windows_installer_asset(
            [
                {
                    "name": "codex-usage-hud-v1.0.1-windows-x64-setup.exe",
                    "browser_download_url": "https://example.test/setup.exe",
                }
            ],
            latest_version="v1.0.1",
        )

        text = format_update_info(
            UpdateInfo(
                current_version="1.0.0",
                latest_version="v1.0.1",
                available=True,
                asset=asset,
            )
        )

        self.assertIn("1.0.0 -> v1.0.1", text)
        self.assertIn("codex-usage-hud-v1.0.1-windows-x64-setup.exe", text)


class _FakeResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self._chunks = list(chunks)
        self.status = status
        self.headers = headers or {}
        self.delay_seconds = max(0.0, float(delay_seconds))

    def read(self, _size: int = -1) -> bytes:
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


class AutoUpdateManagerTests(unittest.TestCase):
    def _wait_for_phase(
        self,
        manager: AutoUpdateManager,
        phase: str,
        *,
        timeout_seconds: float = 2.0,
    ) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if manager.tick().phase == phase:
                return
            time.sleep(0.02)
        self.fail(f"timed out waiting for phase {phase}, last={manager.status().phase}")

    def _wait_for_progress(
        self,
        manager: AutoUpdateManager,
        *,
        timeout_seconds: float = 2.0,
    ) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            state = manager.tick()
            if state.phase == "downloading" and state.downloaded_bytes > 0:
                return
            time.sleep(0.02)
        self.fail("timed out waiting for download progress")

    def test_auto_update_manager_can_pause_resume_and_finish_download(self) -> None:
        requests: list[dict[str, str]] = []
        launched: list[Path] = []
        payload = b"1234567890"

        def fake_check(**_kwargs: object) -> UpdateInfo:
            return UpdateInfo(
                current_version="1.0.0",
                latest_version="v1.0.1",
                available=True,
                asset=UpdateAsset(
                    name="codex-usage-hud-v1.0.1-windows-x64-setup.exe",
                    url="https://example.test/setup.exe",
                    size=10,
                ),
                release_url="https://example.test/release",
            )

        def fake_open(request, timeout: float):
            del timeout
            requests.append({str(key).lower(): value for key, value in request.header_items()})
            if len(requests) == 1:
                return _FakeResponse(
                    [bytes([value]) for value in payload],
                    headers={"Content-Length": "10"},
                    delay_seconds=0.02,
                )
            range_header = str(requests[-1].get("range") or "")
            offset = int(range_header.split("=", 1)[1].split("-", 1)[0])
            return _FakeResponse(
                [payload[offset:]],
                status=206,
                headers={
                    "Content-Length": str(len(payload) - offset),
                    "Content-Range": f"bytes {offset}-{len(payload) - 1}/{len(payload)}",
                },
            )

        def fake_launch(path: Path) -> None:
            launched.append(path)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = AutoUpdateManager(
                current_version="1.0.0",
                check_interval_seconds=3600,
                retry_interval_seconds=60,
                download_dir=Path(tmp_dir),
                check_func=fake_check,
                download_opener=fake_open,
                launch_func=fake_launch,
            )
            try:
                self._wait_for_phase(manager, "downloading")
                self._wait_for_progress(manager)
                paused = manager.handle_click()
                self.assertEqual(paused.phase, "paused")
                self._wait_for_phase(manager, "paused")
                paused_bytes = manager.status().downloaded_bytes
                self.assertGreater(paused_bytes, 0)
                self.assertLess(paused_bytes, len(payload))
                self.assertEqual(requests[0].get("user-agent"), "codex-usage-hud-updater")

                deadline = time.time() + 1.0
                resumed = manager.status()
                while time.time() < deadline:
                    resumed = manager.handle_click()
                    if resumed.phase in {"downloading", "ready"}:
                        break
                    time.sleep(0.02)
                self.assertIn(resumed.phase, {"downloading", "ready"})
                self._wait_for_phase(manager, "ready")
                ready = manager.status()
                self.assertEqual(ready.asset_name, "codex-usage-hud-v1.0.1-windows-x64-setup.exe")
                self.assertEqual(ready.downloaded_bytes, 10)
                self.assertEqual(Path(ready.installer_path).read_bytes(), payload)
                resumed_offset = int(str(requests[1].get("range") or "bytes=0-").split("=", 1)[1].split("-", 1)[0])
                self.assertGreaterEqual(resumed_offset, paused_bytes)
                self.assertLess(resumed_offset, len(payload))

                launched_state = manager.handle_click()
                self.assertEqual(launched_state.phase, "ready")
                self.assertTrue(launched, launched_state)
                self.assertEqual(launched[0], Path(ready.installer_path))
            finally:
                manager.close()

    def test_manual_check_reports_available_without_starting_download(self) -> None:
        requests: list[dict[str, str]] = []

        def fake_check(**_kwargs: object) -> UpdateInfo:
            return UpdateInfo(
                current_version="1.0.0",
                latest_version="v1.0.1",
                available=True,
                asset=UpdateAsset(
                    name="codex-usage-hud-v1.0.1-windows-x64-setup.exe",
                    url="https://example.test/setup.exe",
                    size=10,
                ),
                release_url="https://example.test/release",
            )

        def fake_open(request, timeout: float):
            del timeout
            requests.append({str(key).lower(): value for key, value in request.header_items()})
            return _FakeResponse([b"123"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = AutoUpdateManager(
                current_version="1.0.0",
                check_interval_seconds=3600,
                retry_interval_seconds=60,
                download_dir=Path(tmp_dir),
                check_func=fake_check,
                download_opener=fake_open,
            )
            try:
                state = manager.request_check(auto_download=False)
                self.assertEqual(state.phase, "checking")
                self._wait_for_phase(manager, "available")
                available = manager.status()
                self.assertEqual(available.asset_name, "codex-usage-hud-v1.0.1-windows-x64-setup.exe")
                self.assertTrue(available.visible)
                self.assertEqual(requests, [])
            finally:
                manager.close()

    def test_request_install_downloads_and_launches_installer_async(self) -> None:
        launched: list[Path] = []
        payload = b"installer-payload"

        def fake_check(**_kwargs: object) -> UpdateInfo:
            return UpdateInfo(
                current_version="1.0.0",
                latest_version="v1.0.1",
                available=True,
                asset=UpdateAsset(
                    name="codex-usage-hud-v1.0.1-windows-x64-setup.exe",
                    url="https://example.test/setup.exe",
                    size=len(payload),
                ),
                release_url="https://example.test/release",
            )

        def fake_open(request, timeout: float):
            del request, timeout
            return _FakeResponse(
                [payload],
                headers={"Content-Length": str(len(payload))},
            )

        def fake_launch(path: Path) -> None:
            launched.append(path)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = AutoUpdateManager(
                current_version="1.0.0",
                check_interval_seconds=3600,
                retry_interval_seconds=60,
                download_dir=Path(tmp_dir),
                check_func=fake_check,
                download_opener=fake_open,
                launch_func=fake_launch,
            )
            try:
                state = manager.request_install()
                self.assertEqual(state.phase, "checking")
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    manager.tick()
                    if launched:
                        break
                    time.sleep(0.02)
                self.assertTrue(launched)
                ready = manager.status()
                self.assertEqual(ready.phase, "ready")
                self.assertIn("已启动", ready.message)
                self.assertEqual(Path(ready.installer_path).read_bytes(), payload)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
