"""Unit tests for rest reminder scheduler and config fields."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.config import UserConfig, UserConfigStore
from codex_usage_hud.core.rest_reminder import (
    RestReminderConfig,
    RestReminderPresenter,
    RestReminderScheduler,
)


class RestReminderConfigTests(unittest.TestCase):
    def test_defaults_disabled(self) -> None:
        config = UserConfig.defaults()
        self.assertFalse(config.rest_reminder_enabled)
        self.assertEqual(config.rest_reminder_interval_minutes, 45)
        self.assertEqual(config.rest_reminder_postpone_minutes, 10)
        self.assertEqual(config.rest_reminder_idle_reset_minutes, 5)

    def test_round_trip_and_bounds(self) -> None:
        config = UserConfig.from_dict(
            {
                "rest_reminder_enabled": True,
                "rest_reminder_interval_minutes": 5,
                "rest_reminder_postpone_minutes": 100,
                "rest_reminder_idle_reset_minutes": -3,
            }
        )
        self.assertTrue(config.rest_reminder_enabled)
        self.assertEqual(config.rest_reminder_interval_minutes, 15)
        self.assertEqual(config.rest_reminder_postpone_minutes, 30)
        self.assertEqual(config.rest_reminder_idle_reset_minutes, 0)
        payload = config.to_dict()
        self.assertTrue(payload["rest_reminder_enabled"])
        self.assertEqual(payload["rest_reminder_interval_minutes"], 15)

    def test_store_preserves_rest_reminder_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            store = UserConfigStore(path)
            config = UserConfig.from_dict(
                {
                    "rest_reminder_enabled": True,
                    "rest_reminder_interval_minutes": 60,
                    "rest_reminder_postpone_minutes": 15,
                    "rest_reminder_idle_reset_minutes": 8,
                }
            )
            store.save(config)
            loaded = store.load()
            self.assertTrue(loaded.rest_reminder_enabled)
            self.assertEqual(loaded.rest_reminder_interval_minutes, 60)
            self.assertEqual(loaded.rest_reminder_postpone_minutes, 15)
            self.assertEqual(loaded.rest_reminder_idle_reset_minutes, 8)


class RestReminderSchedulerTests(unittest.TestCase):
    def test_presenter_payload_exposes_timer_start_and_remaining(self) -> None:
        clock = {"now": 1000.0}
        wall = {"now": 1_700_000_000.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
        )
        presenter = RestReminderPresenter(scheduler, wall_clock=lambda: wall["now"])
        presenter.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=5,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )

        payload = presenter.renderer_payload()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["timerStartedAtMs"], 1_700_000_000_000)
        self.assertEqual(payload["remainingSeconds"], 60)
        self.assertEqual(payload["durationSeconds"], 60)

        clock["now"] = 1012.0
        wall["now"] += 12.0
        payload = presenter.renderer_payload()
        self.assertEqual(payload["remainingSeconds"], 48)
        self.assertEqual(payload["timerStartedAtMs"], 1_700_000_000_000)

    def test_fires_after_interval_and_ack_reschedules(self) -> None:
        clock = {"now": 1000.0}
        idle = {"seconds": 0.0}
        messages = iter(["msg-a", "msg-b"])
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: idle["seconds"],
            message_picker=lambda: next(messages),
            clock=lambda: clock["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=1,
                idle_reset_minutes=5,
            ),
            force_reset=True,
        )
        self.assertIsNone(scheduler.tick())
        clock["now"] = 1000.0 + 61.0
        event = scheduler.tick()
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.message, "msg-a")
        self.assertTrue(event.can_postpone)
        # While showing, no second fire.
        self.assertIsNone(scheduler.tick())
        scheduler.acknowledge()
        self.assertFalse(scheduler.showing)
        self.assertIsNone(scheduler.tick())
        clock["now"] = scheduler.next_fire_at + 0.1
        event2 = scheduler.tick()
        self.assertIsNotNone(event2)
        assert event2 is not None
        self.assertEqual(event2.message, "msg-b")

    def test_postpone_once_only(self) -> None:
        clock = {"now": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: 0.0,
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=2,
                idle_reset_minutes=0,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        event = scheduler.tick()
        self.assertIsNotNone(event)
        self.assertTrue(scheduler.postpone())
        self.assertFalse(scheduler.showing)
        clock["now"] = scheduler.next_fire_at + 0.1
        event2 = scheduler.tick()
        self.assertIsNotNone(event2)
        assert event2 is not None
        self.assertFalse(event2.can_postpone)
        self.assertFalse(scheduler.postpone())
        scheduler.acknowledge()

    def test_idle_reset_skips_fire(self) -> None:
        clock = {"now": 0.0}
        idle = {"seconds": 0.0}
        scheduler = RestReminderScheduler(
            idle_seconds_provider=lambda: idle["seconds"],
            message_picker=lambda: "rest",
            clock=lambda: clock["now"],
        )
        scheduler.configure(
            RestReminderConfig(
                enabled=True,
                interval_minutes=1,
                postpone_minutes=1,
                idle_reset_minutes=1,
            ),
            force_reset=True,
        )
        clock["now"] = 61.0
        idle["seconds"] = 120.0
        self.assertIsNone(scheduler.tick())
        self.assertGreater(scheduler.next_fire_at, 61.0)


if __name__ == "__main__":
    unittest.main()
