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
from codex_usage_hud.platforms.windows import (
    _UIA_LIST_ITEM_CONTROL_TYPE_ID,
    _UIA_TEXT_CONTROL_TYPE_ID,
    _UiaTitleNode,
    _UiaTitleProbe,
    WindowsPlatform,
)


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


class WindowsActiveTitleTests(unittest.TestCase):
    def test_uia_title_scoring_ignores_sidebar_rows_for_full_window_poll(self) -> None:
        window_rect = (570, 167, 1806, 905)
        main_title = _UiaTitleNode(
            name="修复 HUD 会话标题错乱",
            control_type=_UIA_TEXT_CONTROL_TYPE_ID,
            selected=False,
            offscreen=False,
            rect=(826, 216, 976, 236),
        )
        pinned_sidebar_row = _UiaTitleNode(
            name="所有业务分支维护1w",
            control_type=_UIA_LIST_ITEM_CONTROL_TYPE_ID,
            selected=False,
            offscreen=False,
            rect=(578, 275, 787, 307),
        )

        main_score = _UiaTitleProbe._score_title_node(
            main_title,
            16,
            window_rect,
            main_title_only=True,
        )
        sidebar_poll_score = _UiaTitleProbe._score_title_node(
            pinned_sidebar_row,
            17,
            window_rect,
            main_title_only=True,
        )
        sidebar_click_score = _UiaTitleProbe._score_title_node(
            pinned_sidebar_row,
            17,
            window_rect,
        )

        self.assertGreater(main_score, 10_000)
        self.assertEqual(sidebar_poll_score, 0)
        self.assertGreater(sidebar_click_score, 0)

    def test_windows_platform_poll_refreshes_before_using_cached_event_title(self) -> None:
        class _FakeProbe:
            def __init__(self, title: str | None) -> None:
                self.title = title

            def conversation_title(self, hwnd: int) -> str | None:
                del hwnd
                return self.title

        platform = object.__new__(WindowsPlatform)
        platform._last_observed_title = "置顶旧标题"
        platform._uia_title_probe = _FakeProbe("当前窗口标题")
        platform._title_probe = None
        platform._find_codex_window = lambda: 123  # type: ignore[method-assign]

        self.assertEqual(platform.get_active_conversation_title(), "当前窗口标题")
        self.assertEqual(platform._last_observed_title, "当前窗口标题")


if __name__ == "__main__":
    unittest.main()
