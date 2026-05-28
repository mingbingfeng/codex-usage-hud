"""Unit tests for cross-platform Codex path helpers."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms import get_current_platform
from codex_usage_hud.platforms.base import BasePlatform
from codex_usage_hud.platforms.linux import LinuxPlatform
from codex_usage_hud.platforms.macos import MacOSPlatform
from codex_usage_hud.platforms.windows import WindowsPlatform


class ActiveSessionDetectionTests(unittest.TestCase):
    def test_detect_active_session_returns_newest_jsonl_file(self) -> None:
        platform = get_current_platform()

        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_root = Path(temp_dir)
            nested_dir = sessions_root / "nested"
            nested_dir.mkdir()

            older_session = sessions_root / "older-session.jsonl"
            newer_session = nested_dir / "newer-session.jsonl"

            older_session.write_text('{"id": "older"}\n', encoding="utf-8")
            newer_session.write_text('{"id": "newer"}\n', encoding="utf-8")

            os.utime(older_session, (1_000, 1_000))
            os.utime(newer_session, (2_000, 2_000))

            active_session = platform.detect_active_session(sessions_root)

            self.assertEqual(active_session, newer_session)


class PlatformFactoryTests(unittest.TestCase):
    def test_get_current_platform_returns_platform_instance(self) -> None:
        platform = get_current_platform()

        self.assertIsNotNone(platform)
        self.assertIsInstance(platform, BasePlatform)

        if sys.platform.startswith("win"):
            self.assertIsInstance(platform, WindowsPlatform)
        elif sys.platform == "darwin":
            self.assertIsInstance(platform, MacOSPlatform)
        elif sys.platform.startswith("linux"):
            self.assertIsInstance(platform, LinuxPlatform)
        else:
            self.fail(f"Unexpected platform under test: {sys.platform}")


if __name__ == "__main__":
    unittest.main()
