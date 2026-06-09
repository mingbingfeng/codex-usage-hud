"""Unit tests for GitHub Release updater helpers."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.updater import (
    UpdateInfo,
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


if __name__ == "__main__":
    unittest.main()
