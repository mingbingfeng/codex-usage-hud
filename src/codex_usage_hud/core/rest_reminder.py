"""Lightweight rest / eye-care reminder scheduler.

Defaults and product choices mirror Stretchly / Time Out style soft breaks:
optional, gentle, one postpone, idle reset. No hard lock in MVP.
"""

from __future__ import annotations

import ctypes
import logging
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

# Keep defaults local to avoid circular import with config -> core package init.
DEFAULT_REST_REMINDER_ENABLED = False
DEFAULT_REST_REMINDER_INTERVAL_MINUTES = 45
DEFAULT_REST_REMINDER_POSTPONE_MINUTES = 10
DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES = 5
DEFAULT_REST_REMINDER_WORK_START_TIME = "09:00"
DEFAULT_REST_REMINDER_WORK_END_TIME = "18:00"
DEFAULT_REST_REMINDER_LUNCH_ENABLED = True
DEFAULT_REST_REMINDER_LUNCH_START_TIME = "12:00"
DEFAULT_REST_REMINDER_LUNCH_END_TIME = "13:30"
REST_REMINDER_INTERVAL_MIN = 15
REST_REMINDER_INTERVAL_MAX = 180
REST_REMINDER_POSTPONE_MIN = 5
REST_REMINDER_POSTPONE_MAX = 30
REST_REMINDER_IDLE_RESET_MIN = 0
REST_REMINDER_IDLE_RESET_MAX = 60

_LOGGER = logging.getLogger("codex_usage_hud.rest_reminder")
_LOGGER.addHandler(logging.NullHandler())

REST_REMINDER_MESSAGES: tuple[str, ...] = (
    "写了挺久了，起来走走，给眼睛放个假。",
    "20-20-20：看 6 米外约 20 秒，再回来继续。",
    "喝口水、伸个懒腰，让眼睛和肩颈放松一下。",
    "给自己几分钟喘口气，回来时会更专注。",
    "站起来活动一下，再开始下一段专注时间。",
)


@dataclass(frozen=True)
class RestReminderConfig:
    enabled: bool = DEFAULT_REST_REMINDER_ENABLED
    interval_minutes: int = DEFAULT_REST_REMINDER_INTERVAL_MINUTES
    postpone_minutes: int = DEFAULT_REST_REMINDER_POSTPONE_MINUTES
    idle_reset_minutes: int = DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES
    work_start_time: str = DEFAULT_REST_REMINDER_WORK_START_TIME
    work_end_time: str = DEFAULT_REST_REMINDER_WORK_END_TIME
    lunch_enabled: bool = DEFAULT_REST_REMINDER_LUNCH_ENABLED
    lunch_start_time: str = DEFAULT_REST_REMINDER_LUNCH_START_TIME
    lunch_end_time: str = DEFAULT_REST_REMINDER_LUNCH_END_TIME

    @classmethod
    def from_user_config(cls, user_config: object) -> "RestReminderConfig":
        return cls(
            enabled=bool(getattr(user_config, "rest_reminder_enabled", False)),
            interval_minutes=_clamp_int(
                getattr(
                    user_config,
                    "rest_reminder_interval_minutes",
                    DEFAULT_REST_REMINDER_INTERVAL_MINUTES,
                ),
                DEFAULT_REST_REMINDER_INTERVAL_MINUTES,
                REST_REMINDER_INTERVAL_MIN,
                REST_REMINDER_INTERVAL_MAX,
            ),
            postpone_minutes=_clamp_int(
                getattr(
                    user_config,
                    "rest_reminder_postpone_minutes",
                    DEFAULT_REST_REMINDER_POSTPONE_MINUTES,
                ),
                DEFAULT_REST_REMINDER_POSTPONE_MINUTES,
                REST_REMINDER_POSTPONE_MIN,
                REST_REMINDER_POSTPONE_MAX,
            ),
            idle_reset_minutes=_clamp_int(
                getattr(
                    user_config,
                    "rest_reminder_idle_reset_minutes",
                    DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES,
                ),
                DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES,
                REST_REMINDER_IDLE_RESET_MIN,
                REST_REMINDER_IDLE_RESET_MAX,
            ),
            work_start_time=_normalize_time_text(
                getattr(user_config, "rest_reminder_work_start_time", None),
                DEFAULT_REST_REMINDER_WORK_START_TIME,
            ),
            work_end_time=_normalize_time_text(
                getattr(user_config, "rest_reminder_work_end_time", None),
                DEFAULT_REST_REMINDER_WORK_END_TIME,
            ),
            lunch_enabled=bool(
                getattr(
                    user_config,
                    "rest_reminder_lunch_enabled",
                    DEFAULT_REST_REMINDER_LUNCH_ENABLED,
                )
            ),
            lunch_start_time=_normalize_time_text(
                getattr(user_config, "rest_reminder_lunch_start_time", None),
                DEFAULT_REST_REMINDER_LUNCH_START_TIME,
            ),
            lunch_end_time=_normalize_time_text(
                getattr(user_config, "rest_reminder_lunch_end_time", None),
                DEFAULT_REST_REMINDER_LUNCH_END_TIME,
            ),
        )


@dataclass(frozen=True)
class RestReminderEvent:
    """A due rest reminder ready for UI presentation."""

    message: str
    can_postpone: bool
    interval_minutes: int
    postpone_minutes: int
    fired_at: float


def _clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        amount = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        amount = int(default)
    return max(int(minimum), min(int(maximum), amount))


def _normalize_time_text(value: object, default: str) -> str:
    text = str(value or "").strip()
    try:
        hour, minute = (int(part) for part in text.split(":", 1))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        pass
    return default


def _minutes_from_time_text(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour * 60 + minute


def system_idle_seconds(now: float | None = None) -> float | None:
    """Return OS idle seconds when available; None if unsupported/unavailable."""
    del now
    if sys.platform != "win32":
        return None
    try:

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("dwTime", ctypes.c_uint),
            ]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = int(ctypes.windll.kernel32.GetTickCount())  # type: ignore[attr-defined]
        last = int(info.dwTime)
        idle_ms = max(0, tick - last)
        return idle_ms / 1000.0
    except Exception:
        return None


class RestReminderScheduler:
    """Process-local rest reminder timer with one postpone and idle reset."""

    def __init__(
        self,
        *,
        idle_seconds_provider: Callable[[], float | None] | None = None,
        message_picker: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self._idle_seconds = idle_seconds_provider or system_idle_seconds
        self._pick_message = message_picker or (
            lambda: random.choice(REST_REMINDER_MESSAGES)
        )
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._config = RestReminderConfig()
        self._next_fire_at = 0.0
        self._postpone_used = False
        self._showing = False
        self._last_event: RestReminderEvent | None = None
        self._cycle_started_at = 0.0
        self._schedule_waiting = False
        self._idle_break_active = False
        self.configure(self._config, force_reset=True)

    @property
    def config(self) -> RestReminderConfig:
        return self._config

    @property
    def showing(self) -> bool:
        return self._showing

    @property
    def next_fire_at(self) -> float:
        return self._next_fire_at

    @property
    def cycle_started_at(self) -> float:
        return self._cycle_started_at

    @property
    def state(self) -> str:
        if not self._config.enabled:
            return "disabled"
        if self._idle_break_active:
            return "away"
        schedule_state, _ = self._schedule_state(float(self._wall_clock()))
        return schedule_state

    def seconds_until_next(self, now: float | None = None) -> float | None:
        if not self._config.enabled:
            return None
        current = self._clock() if now is None else float(now)
        return max(0.0, self._next_fire_at - current)

    def seconds_until_wake(self, now: float | None = None) -> float | None:
        """Return the next timer or work-schedule boundary that needs a tick."""
        if not self._config.enabled:
            return None
        current = self._clock() if now is None else float(now)
        wall_now = float(self._wall_clock())
        state, boundary = self._schedule_state(wall_now)
        if state != "work" and boundary is not None:
            return max(0.0, boundary - wall_now)
        delay = max(0.0, self._next_fire_at - current)
        if boundary is not None:
            delay = min(delay, max(0.0, boundary - wall_now))
        return delay

    def set_cycle_started_at_wall(self, started_at_wall: float) -> None:
        """Adjust the current round start from a wall-clock timestamp."""
        if not self._config.enabled:
            return
        now_wall = float(self._wall_clock())
        elapsed = max(0.0, now_wall - float(started_at_wall))
        current = self._clock()
        self._cycle_started_at = current - elapsed
        self._next_fire_at = self._cycle_started_at + float(self._config.interval_minutes) * 60.0
        self._showing = False
        self._last_event = None
        self._postpone_used = False

    def configure(
        self,
        config: RestReminderConfig | object,
        *,
        force_reset: bool = False,
    ) -> None:
        if isinstance(config, RestReminderConfig):
            next_config = config
        else:
            next_config = RestReminderConfig.from_user_config(config)
        previous = self._config
        self._config = next_config
        if not next_config.enabled:
            self._showing = False
            self._last_event = None
            self._next_fire_at = 0.0
            self._cycle_started_at = 0.0
            self._postpone_used = False
            self._schedule_waiting = False
            self._idle_break_active = False
            return
        timing_changed = (
            force_reset
            or not previous.enabled
            or previous.interval_minutes != next_config.interval_minutes
            or previous.postpone_minutes != next_config.postpone_minutes
            or previous.idle_reset_minutes != next_config.idle_reset_minutes
            or previous.work_start_time != next_config.work_start_time
            or previous.work_end_time != next_config.work_end_time
            or previous.lunch_enabled != next_config.lunch_enabled
            or previous.lunch_start_time != next_config.lunch_start_time
            or previous.lunch_end_time != next_config.lunch_end_time
        )
        if timing_changed and not self._showing:
            self._arm_from_now()
            self._postpone_used = False

    def tick(self, now: float | None = None) -> RestReminderEvent | None:
        if not self._config.enabled or self._showing:
            return None
        current = self._clock() if now is None else float(now)
        schedule_state, boundary = self._schedule_state(float(self._wall_clock()))
        if schedule_state != "work":
            self._schedule_waiting = True
            self._idle_break_active = False
            wall_now = float(self._wall_clock())
            self._next_fire_at = current + max(0.0, (boundary or wall_now) - wall_now)
            self._cycle_started_at = current
            self._postpone_used = False
            return None
        if self._schedule_waiting:
            self._schedule_waiting = False
            self._arm_from(current)
            return None
        idle_seconds = self._idle_seconds()
        idle_reset = float(self._config.idle_reset_minutes) * 60.0
        if idle_reset > 0 and idle_seconds is not None and idle_seconds >= idle_reset:
            if not self._idle_break_active:
                self._next_fire_at = current + float(self._config.interval_minutes) * 60.0
                self._cycle_started_at = current
                self._postpone_used = False
            self._idle_break_active = True
            return None
        if self._idle_break_active:
            self._idle_break_active = False
            self._arm_from(current)
            return None
        if current < self._next_fire_at:
            return None
        event = RestReminderEvent(
            message=str(self._pick_message() or REST_REMINDER_MESSAGES[0]),
            can_postpone=not self._postpone_used,
            interval_minutes=self._config.interval_minutes,
            postpone_minutes=self._config.postpone_minutes,
            fired_at=current,
        )
        self._showing = True
        self._last_event = event
        return event

    def acknowledge(self, now: float | None = None) -> None:
        current = self._clock() if now is None else float(now)
        self._showing = False
        self._last_event = None
        self._postpone_used = False
        self._next_fire_at = current + float(self._config.interval_minutes) * 60.0
        self._cycle_started_at = current

    def postpone(self, now: float | None = None) -> bool:
        if not self._showing or self._postpone_used:
            return False
        current = self._clock() if now is None else float(now)
        self._showing = False
        self._last_event = None
        self._postpone_used = True
        self._next_fire_at = current + float(self._config.postpone_minutes) * 60.0
        self._cycle_started_at = current
        return True

    def dismiss_without_reschedule(self) -> None:
        """Clear showing state without changing the next fire time."""
        self._showing = False
        self._last_event = None

    def _arm_from_now(self) -> None:
        self._arm_from(self._clock())

    def _arm_from(self, current: float) -> None:
        self._cycle_started_at = current
        self._next_fire_at = current + float(self._config.interval_minutes) * 60.0

    def _schedule_state(self, wall_now: float) -> tuple[str, float | None]:
        local = datetime.fromtimestamp(wall_now)
        minute = local.hour * 60 + local.minute
        work_start = _minutes_from_time_text(self._config.work_start_time)
        work_end = _minutes_from_time_text(self._config.work_end_time)
        lunch_start = _minutes_from_time_text(self._config.lunch_start_time)
        lunch_end = _minutes_from_time_text(self._config.lunch_end_time)
        if work_start >= work_end:
            work_start, work_end = 0, 24 * 60
        in_work = work_start <= minute < work_end
        in_lunch = (
            in_work
            and self._config.lunch_enabled
            and lunch_start < lunch_end
            and lunch_start <= minute < lunch_end
        )
        state = "lunch" if in_lunch else "work" if in_work else "off"
        boundaries = [work_start, work_end]
        if self._config.lunch_enabled and lunch_start < lunch_end:
            boundaries.extend([lunch_start, lunch_end])
        next_minute = next((candidate for candidate in sorted(set(boundaries)) if candidate > minute), None)
        if next_minute is None:
            next_minute = min(boundaries) + 24 * 60
        midnight = datetime(local.year, local.month, local.day)
        boundary = midnight.timestamp() + next_minute * 60
        if boundary <= wall_now:
            boundary += 24 * 60 * 60
        return state, boundary


class RestReminderPresenter:
    """Present rest reminders in renderer and send an independent system notification."""

    def __init__(
        self,
        scheduler: RestReminderScheduler | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self.scheduler = scheduler or RestReminderScheduler()
        self._wall_clock = wall_clock or time.time
        self._qt_available: bool | None = None
        self._pending_renderer_event: dict[str, object] | None = None
        self._dialog: object | None = None
        self._last_surface: str = ""  # "qt" | "renderer" | ""
        self._notification: dict[str, object] = {
            "status": "unknown",
            "channel": "",
            "error": "",
            "lastSentAtMs": 0,
        }

    def configure(self, user_config: object, *, force_reset: bool = False) -> None:
        was_enabled = self.scheduler.config.enabled
        self.scheduler.configure(user_config, force_reset=force_reset)
        if was_enabled and not self.scheduler.config.enabled:
            self.close()

    def tick(self) -> dict[str, object] | None:
        """Drive the scheduler and return a renderer toast payload when due."""
        if self.scheduler.showing:
            if self._last_surface == "renderer" and self._pending_renderer_event:
                return dict(self._pending_renderer_event)
            return None
        event = self.scheduler.tick()
        if event is None:
            self._pending_renderer_event = None
            self._last_surface = ""
            return None
        notification = _show_system_notification(event.message)
        self._notification = notification
        payload = {
            "visible": True,
            "message": event.message,
            "canPostpone": event.can_postpone,
            "intervalMinutes": event.interval_minutes,
            "postponeMinutes": event.postpone_minutes,
            "firedAt": event.fired_at,
            "notification": dict(notification),
        }
        self._pending_renderer_event = payload
        self._last_surface = "renderer"
        return payload

    def acknowledge(self) -> None:
        self.scheduler.acknowledge()
        self._pending_renderer_event = None
        self._last_surface = ""
        self._close_dialog()

    def postpone(self) -> bool:
        ok = self.scheduler.postpone()
        if ok:
            self._pending_renderer_event = None
            self._last_surface = ""
            self._close_dialog()
        return ok

    def renderer_payload(self) -> dict[str, object]:
        config = self.scheduler.config
        state = self.scheduler.state
        if config.enabled and self.scheduler.next_fire_at > 0:
            now_wall_ms = float(self._wall_clock()) * 1000.0
            remaining = float(self.scheduler.seconds_until_next() or 0.0)
            duration = max(0.0, self.scheduler.next_fire_at - self.scheduler.cycle_started_at)
            elapsed = max(0.0, min(duration, duration - remaining))
            timing = {
                "enabled": True,
                "running": state == "work",
                "timerStartedAtMs": int(now_wall_ms - (elapsed * 1000.0)),
                "nextReminderAtMs": int(now_wall_ms + (remaining * 1000.0)),
                "remainingSeconds": int(round(remaining)),
                "durationSeconds": int(round(duration)),
            }
        else:
            timing = {
                "enabled": False,
                "running": False,
                "timerStartedAtMs": 0,
                "nextReminderAtMs": 0,
                "remainingSeconds": 0,
                "durationSeconds": int(config.interval_minutes * 60),
            }
        if self._pending_renderer_event:
            payload = dict(self._pending_renderer_event)
            payload.update(timing)
            payload["notification"] = dict(self._notification)
            payload["state"] = state
            return payload
        return {
            "visible": False,
            "notification": dict(self._notification),
            "state": state,
            **timing,
        }

    def adjust_cycle_started_at_ms(self, started_at_ms: object) -> bool:
        try:
            started = float(started_at_ms) / 1000.0
        except (TypeError, ValueError):
            return False
        self.scheduler.set_cycle_started_at_wall(started)
        return True

    def test_notification(self) -> dict[str, object]:
        self._notification = _show_system_notification("系统通知测试成功")
        return dict(self._notification)

    def close(self) -> None:
        self._close_dialog()
        self.scheduler.dismiss_without_reschedule()
        self._pending_renderer_event = None
        self._last_surface = ""

    def _try_show_qt_dialog(self, event: RestReminderEvent) -> bool:
        if self._qt_available is False:
            return False
        try:
            from ..ui.rest_reminder_qt import show_rest_reminder_dialog

            dialog = show_rest_reminder_dialog(
                message=event.message,
                can_postpone=event.can_postpone,
                postpone_minutes=event.postpone_minutes,
                on_ack=self.acknowledge,
                on_postpone=lambda: self.postpone(),
            )
            self._dialog = dialog
            self._qt_available = True
            self._pump_qt_events()
            return True
        except Exception:
            self._qt_available = False
            self._dialog = None
            _LOGGER.info("rest_reminder_qt_unavailable; falling back to renderer toast")
            return False

    def _pump_qt_events(self) -> None:
        if self._last_surface != "qt" and self._dialog is None:
            return
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is None:
                return
            app.processEvents()
            dialog = self._dialog
            if dialog is not None and hasattr(dialog, "isVisible"):
                if not bool(dialog.isVisible()) and self.scheduler.showing:
                    # Dialog closed without callback (e.g. OS force-close).
                    self.acknowledge()
        except Exception:
            return

    def _close_dialog(self) -> None:
        dialog = self._dialog
        self._dialog = None
        if dialog is None:
            return
        try:
            close = getattr(dialog, "close", None)
            if callable(close):
                close()
        except Exception:
            return


def _show_system_notification(message: str) -> dict[str, object]:
    """Send a desktop notification and expose the selected channel/result."""
    title = "休息提醒"
    body = str(message or "").strip() or "该休息一下了。"
    sent_at = int(time.time() * 1000)
    if sys.platform == "win32":
        try:
            safe = body.replace("'", "''")[:180]
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "Add-Type -AssemblyName System.Drawing; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                "$n.Visible = $true; "
                f"$n.ShowBalloonTip(8000, '{title}', '{safe}', "
                "[System.Windows.Forms.ToolTipIcon]::Info); "
                "Start-Sleep -Seconds 8; $n.Dispose()"
            )
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            return {"status": "sent", "channel": "windows-notifyicon", "error": "", "lastSentAtMs": sent_at}
        except Exception as exc:
            return {"status": "failed", "channel": "windows-notifyicon", "error": str(exc), "lastSentAtMs": sent_at}
    if sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", f'display notification "{body.replace(chr(34), chr(39))}" with title "{title}"'], check=True, timeout=5)
            return {"status": "sent", "channel": "osascript", "error": "", "lastSentAtMs": sent_at}
        except Exception as exc:
            return {"status": "failed", "channel": "osascript", "error": str(exc), "lastSentAtMs": sent_at}
    try:
        subprocess.run(["notify-send", title, body], check=True, timeout=5)
        return {"status": "sent", "channel": "notify-send", "error": "", "lastSentAtMs": sent_at}
    except Exception as exc:
        return {"status": "failed", "channel": "notify-send", "error": str(exc), "lastSentAtMs": sent_at}
