"""Lightweight rest / eye-care reminder scheduler.

Defaults and product choices mirror mature break tools such as Stretchly and
BreakTimer: optional, timed breaks, one postpone, idle reset, and automatic
progression to the next focus round.
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
from pathlib import Path
from typing import Any, Callable, Mapping

# Keep defaults local to avoid circular import with config -> core package init.
DEFAULT_REST_REMINDER_ENABLED = False
DEFAULT_REST_REMINDER_INTERVAL_MINUTES = 45
DEFAULT_REST_REMINDER_BREAK_MINUTES = 2
DEFAULT_REST_REMINDER_POSTPONE_MINUTES = 10
DEFAULT_REST_REMINDER_IDLE_RESET_MINUTES = 5
DEFAULT_REST_REMINDER_WORK_START_TIME = "09:00"
DEFAULT_REST_REMINDER_WORK_END_TIME = "18:00"
DEFAULT_REST_REMINDER_LUNCH_ENABLED = True
DEFAULT_REST_REMINDER_LUNCH_START_TIME = "12:00"
DEFAULT_REST_REMINDER_LUNCH_END_TIME = "13:30"
REST_REMINDER_INTERVAL_MIN = 15
REST_REMINDER_INTERVAL_MAX = 180
REST_REMINDER_BREAK_MIN = 1
REST_REMINDER_BREAK_MAX = 10
REST_REMINDER_POSTPONE_MIN = 5
REST_REMINDER_POSTPONE_MAX = 30
REST_REMINDER_IDLE_RESET_MIN = 0
REST_REMINDER_IDLE_RESET_MAX = 60
REST_REMINDER_IDLE_RETURN_POLL_SECONDS = 30.0

_LOGGER = logging.getLogger("codex_usage_hud.rest_reminder")
_LOGGER.addHandler(logging.NullHandler())
WINDOWS_NOTIFICATION_DISPLAY_MILLISECONDS = 30_000

REST_REMINDER_MESSAGES: tuple[str, ...] = (
    "写了挺久了，起来走走，给眼睛放个假。",
    "把视线移到远处，放松眼睛，再起身活动一下。",
    "喝口水、伸个懒腰，让眼睛和肩颈放松一下。",
    "给自己几分钟喘口气，回来时会更专注。",
    "站起来活动一下，再开始下一段专注时间。",
)


@dataclass(frozen=True)
class RestReminderConfig:
    enabled: bool = DEFAULT_REST_REMINDER_ENABLED
    interval_minutes: int = DEFAULT_REST_REMINDER_INTERVAL_MINUTES
    break_minutes: int = DEFAULT_REST_REMINDER_BREAK_MINUTES
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
            break_minutes=_clamp_int(
                getattr(
                    user_config,
                    "rest_reminder_break_minutes",
                    DEFAULT_REST_REMINDER_BREAK_MINUTES,
                ),
                DEFAULT_REST_REMINDER_BREAK_MINUTES,
                REST_REMINDER_BREAK_MIN,
                REST_REMINDER_BREAK_MAX,
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
    break_minutes: int
    postpone_minutes: int
    fired_at: float
    ends_at: float


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
        self._showing_until = 0.0
        self._showing_reschedules = False
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
    def showing_until(self) -> float:
        return self._showing_until

    @property
    def active_event(self) -> RestReminderEvent | None:
        return self._last_event

    @property
    def postpone_used(self) -> bool:
        return self._postpone_used

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
        schedule_state, _ = self._schedule_state(float(self._wall_clock()))
        if schedule_state != "work":
            return schedule_state
        if self._showing:
            return "break"
        if self._idle_break_active:
            return "away"
        return "work"

    def seconds_until_next(self, now: float | None = None) -> float | None:
        if not self._config.enabled:
            return None
        current = self._clock() if now is None else float(now)
        return max(0.0, self._next_fire_at - current)

    def seconds_until_break_end(self, now: float | None = None) -> float | None:
        if not self._showing:
            return None
        current = self._clock() if now is None else float(now)
        return max(0.0, self._showing_until - current)

    def seconds_until_wake(self, now: float | None = None) -> float | None:
        """Return the next timer or work-schedule boundary that needs a tick."""
        current = self._clock() if now is None else float(now)
        if not self._config.enabled:
            if self._showing:
                return max(0.0, self._showing_until - current)
            return None
        wall_now = float(self._wall_clock())
        state, boundary = self._schedule_state(wall_now)
        if state != "work" and boundary is not None:
            return max(0.0, boundary - wall_now)
        if self._showing:
            delay = max(0.0, self._showing_until - current)
            if boundary is not None:
                delay = min(delay, max(0.0, boundary - wall_now))
            return delay
        delay = max(0.0, self._next_fire_at - current)
        if boundary is not None:
            delay = min(delay, max(0.0, boundary - wall_now))
        idle_reset = float(self._config.idle_reset_minutes) * 60.0
        if idle_reset > 0:
            if self._idle_break_active:
                delay = min(delay, REST_REMINDER_IDLE_RETURN_POLL_SECONDS)
            else:
                idle_seconds = self._idle_seconds()
                if idle_seconds is not None:
                    delay = min(delay, max(0.0, idle_reset - idle_seconds))
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
        self._clear_showing()
        self._postpone_used = False
        self._idle_break_active = False
        self._schedule_waiting = False

    def export_wall_state(self) -> dict[str, Any] | None:
        """Return wall-clock timing that can be restored after a process restart."""
        if not self._config.enabled or self._next_fire_at <= 0:
            return None
        remaining = float(self.seconds_until_next() or 0.0)
        duration = max(0.0, self._next_fire_at - self._cycle_started_at)
        elapsed = max(0.0, min(duration, duration - remaining))
        now_wall = float(self._wall_clock())
        snapshot: dict[str, Any] = {
            "enabled": True,
            "phase": "break" if self._showing else "focus",
            "cycleStartedAtMs": int(round((now_wall - elapsed) * 1000.0)),
            "nextFireAtMs": int(round((now_wall + remaining) * 1000.0)),
            "postponeUsed": bool(self._postpone_used),
            "intervalMinutes": int(self._config.interval_minutes),
            "breakMinutes": int(self._config.break_minutes),
            "postponeMinutes": int(self._config.postpone_minutes),
            "idleResetMinutes": int(self._config.idle_reset_minutes),
            "workStartTime": self._config.work_start_time,
            "workEndTime": self._config.work_end_time,
            "lunchEnabled": bool(self._config.lunch_enabled),
            "lunchStartTime": self._config.lunch_start_time,
            "lunchEndTime": self._config.lunch_end_time,
        }
        if self._showing:
            current = self._clock()
            event = self._last_event
            snapshot.update(
                {
                    "reminderEndsAtMs": int(
                        round(
                            (
                                now_wall
                                + max(0.0, self._showing_until - current)
                            )
                            * 1000.0
                        )
                    ),
                    "reminderMessage": event.message if event is not None else "",
                    "reminderCanPostpone": bool(
                        event.can_postpone if event is not None else not self._postpone_used
                    ),
                    "reminderAutoReschedule": bool(self._showing_reschedules),
                }
            )
        return snapshot

    def restore_wall_state(self, state: Mapping[str, Any] | None) -> bool:
        """Restore timing from a wall-clock snapshot. Returns True on success."""
        if not self._config.enabled or not isinstance(state, Mapping):
            return False
        try:
            started_ms = float(state.get("cycleStartedAtMs"))
            next_ms = float(state.get("nextFireAtMs"))
        except (TypeError, ValueError):
            return False
        if started_ms <= 0 or next_ms <= 0:
            return False
        # Ignore snapshots that no longer match the active focus schedule.
        try:
            saved_interval = int(state.get("intervalMinutes"))
            saved_break = int(
                state.get("breakMinutes", self._config.break_minutes)
            )
            saved_postpone = int(state.get("postponeMinutes"))
            saved_idle_reset = int(state.get("idleResetMinutes"))
        except (TypeError, ValueError):
            return False
        if saved_interval != int(self._config.interval_minutes):
            return False
        if saved_break != int(self._config.break_minutes):
            return False
        if saved_postpone != int(self._config.postpone_minutes):
            return False
        if saved_idle_reset != int(self._config.idle_reset_minutes):
            return False
        for key, expected in (
            ("workStartTime", self._config.work_start_time),
            ("workEndTime", self._config.work_end_time),
            ("lunchStartTime", self._config.lunch_start_time),
            ("lunchEndTime", self._config.lunch_end_time),
        ):
            if key in state and str(state.get(key) or "") != str(expected):
                return False
        if "lunchEnabled" in state and bool(state.get("lunchEnabled")) != bool(
            self._config.lunch_enabled
        ):
            return False

        now_wall = float(self._wall_clock())
        current = self._clock()
        started_wall = started_ms / 1000.0
        next_wall = next_ms / 1000.0
        if next_wall < started_wall:
            return False
        phase = str(state.get("phase") or "focus").strip().lower()
        if phase == "break" or next_wall <= now_wall:
            # A process restart, sleep, or renderer loss counts as a natural
            # break. Never replay a stale overlay or catch up an expired alert.
            self._clear_showing()
            self._postpone_used = False
            self._idle_break_active = False
            self._schedule_waiting = False
            self._arm_from(current)
            return True
        elapsed = max(0.0, now_wall - started_wall)
        remaining = max(0.0, next_wall - now_wall)
        self._cycle_started_at = current - elapsed
        self._next_fire_at = current + remaining
        self._postpone_used = bool(state.get("postponeUsed"))
        self._clear_showing()
        self._idle_break_active = False
        self._schedule_waiting = False
        return True

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
            self._clear_showing()
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
            or previous.break_minutes != next_config.break_minutes
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
        current = self._clock() if now is None else float(now)
        if not self._config.enabled:
            if self._showing and current >= self._showing_until:
                self._clear_showing()
            return None
        wall_now = float(self._wall_clock())
        schedule_state, boundary = self._schedule_state(wall_now)
        if schedule_state != "work":
            self._clear_showing()
            self._schedule_waiting = True
            self._idle_break_active = False
            self._next_fire_at = current + max(0.0, (boundary or wall_now) - wall_now)
            self._cycle_started_at = current
            self._postpone_used = False
            return None
        if self._schedule_waiting:
            self._schedule_waiting = False
            self._arm_from(current)
            return None
        if self._showing:
            if current < self._showing_until:
                return None
            should_reschedule = self._showing_reschedules
            self._clear_showing()
            if should_reschedule:
                self._postpone_used = False
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
        break_seconds = float(self._config.break_minutes) * 60.0
        event = RestReminderEvent(
            message=str(self._pick_message() or REST_REMINDER_MESSAGES[0]),
            can_postpone=not self._postpone_used,
            interval_minutes=self._config.interval_minutes,
            break_minutes=self._config.break_minutes,
            postpone_minutes=self._config.postpone_minutes,
            fired_at=current,
            ends_at=current + break_seconds,
        )
        self._showing = True
        self._showing_until = event.ends_at
        self._showing_reschedules = True
        self._last_event = event
        return event

    def acknowledge(self, now: float | None = None) -> None:
        current = self._clock() if now is None else float(now)
        self._clear_showing()
        self._postpone_used = False
        if self._config.enabled:
            self._arm_from(current)
        else:
            self._next_fire_at = 0.0
            self._cycle_started_at = 0.0

    def postpone(self, now: float | None = None) -> bool:
        if not self._showing or self._postpone_used:
            return False
        current = self._clock() if now is None else float(now)
        self._clear_showing()
        self._postpone_used = True
        self._next_fire_at = current + float(self._config.postpone_minutes) * 60.0
        self._cycle_started_at = current
        return True

    def dismiss_without_reschedule(self) -> None:
        """Clear showing state without changing the next fire time."""
        self._clear_showing()

    def begin_preview(
        self,
        message: str | None = None,
        *,
        now: float | None = None,
    ) -> RestReminderEvent:
        """Show a rest event without moving the next fire time (test/preview)."""
        current = self._clock() if now is None else float(now)
        text = str(message or "").strip() or str(
            self._pick_message() or REST_REMINDER_MESSAGES[0]
        )
        break_seconds = float(self._config.break_minutes) * 60.0
        event = RestReminderEvent(
            message=text,
            can_postpone=not self._postpone_used,
            interval_minutes=int(self._config.interval_minutes),
            break_minutes=int(self._config.break_minutes),
            postpone_minutes=int(self._config.postpone_minutes),
            fired_at=current,
            ends_at=current + break_seconds,
        )
        self._showing = True
        self._showing_until = event.ends_at
        self._showing_reschedules = False
        self._last_event = event
        return event

    def _clear_showing(self) -> None:
        self._showing = False
        self._showing_until = 0.0
        self._showing_reschedules = False
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
        *,
        state_path: Path | str | None = None,
        persist_enabled: bool = True,
    ) -> None:
        self.scheduler = scheduler or RestReminderScheduler()
        self._wall_clock = wall_clock or time.time
        self._state_path = Path(state_path) if state_path is not None else None
        self._persist_enabled = bool(persist_enabled)
        self._last_persisted_snapshot: dict[str, Any] | None = None
        self._last_persist_mono: tuple[float, float, float, bool, bool] | None = None
        self._qt_available: bool | None = None
        self._pending_renderer_event: dict[str, object] | None = None
        self._dialog: object | None = None
        self._last_surface: str = ""  # "qt" | "renderer" | ""
        self._preview_mode: bool = False
        self._notification: dict[str, object] = {
            "status": "unknown",
            "channel": "",
            "error": "",
            "lastSentAtMs": 0,
        }

    def configure(
        self,
        user_config: object,
        *,
        force_reset: bool = False,
        restore_persisted: bool = False,
    ) -> None:
        was_enabled = self.scheduler.config.enabled
        self.scheduler.configure(user_config, force_reset=force_reset)
        if was_enabled and not self.scheduler.config.enabled:
            self.close()
            self._persist_state(clear=True)
            return
        if restore_persisted and self.scheduler.config.enabled:
            restored = self._restore_persisted_state()
            if restored:
                return
        self._persist_state()

    def tick(self) -> dict[str, object] | None:
        """Drive the scheduler and return a renderer toast payload when due."""
        was_showing = self.scheduler.showing
        was_preview = self._preview_mode
        timing_before = self._scheduler_timing_signature()
        event = self.scheduler.tick()
        if event is None:
            if was_showing and not self.scheduler.showing:
                self._pending_renderer_event = None
                self._last_surface = ""
                self._preview_mode = False
                self._close_dialog()
                self._persist_state()
                return {
                    "visible": False,
                    "autoCompleted": True,
                    "preview": bool(was_preview),
                }
            if self.scheduler.showing:
                return None
            self._pending_renderer_event = None
            self._last_surface = ""
            # Persist while running so idle/schedule shifts survive restarts.
            self._persist_state()
            if self._scheduler_timing_signature() != timing_before:
                payload = self.renderer_payload()
                payload["stateChanged"] = True
                return payload
            return None
        notification = _show_system_notification(event.message)
        self._notification = notification
        payload = self._renderer_event_payload(event, preview=False)
        self._pending_renderer_event = payload
        self._last_surface = "renderer"
        self._persist_state()
        return payload

    def _scheduler_timing_signature(self) -> tuple[str, float, float, float]:
        """Track deadline/state transitions that require a renderer refresh."""
        return (
            str(self.scheduler.state),
            float(self.scheduler.cycle_started_at),
            float(self.scheduler.next_fire_at),
            float(self.scheduler.showing_until),
        )

    def acknowledge(self) -> None:
        if self._preview_mode:
            self.scheduler.dismiss_without_reschedule()
            self._preview_mode = False
            self._pending_renderer_event = None
            self._last_surface = ""
            self._close_dialog()
            return
        self.scheduler.acknowledge()
        self._preview_mode = False
        self._pending_renderer_event = None
        self._last_surface = ""
        self._close_dialog()
        self._persist_state()

    def postpone(self) -> bool:
        if self._preview_mode:
            # Preview postpone only dismisses UI; real cycle timing is unchanged.
            self.scheduler.dismiss_without_reschedule()
            self._preview_mode = False
            self._pending_renderer_event = None
            self._last_surface = ""
            self._close_dialog()
            return True
        ok = self.scheduler.postpone()
        if ok:
            self._preview_mode = False
            self._pending_renderer_event = None
            self._last_surface = ""
            self._close_dialog()
            self._persist_state()
        return ok

    def renderer_payload(self) -> dict[str, object]:
        config = self.scheduler.config
        state = self.scheduler.state
        now_wall_ms = float(self._wall_clock()) * 1000.0
        if config.enabled and self.scheduler.next_fire_at > 0:
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
        break_remaining = self.scheduler.seconds_until_break_end()
        break_timing = {
            "breakDurationSeconds": int(config.break_minutes * 60),
            "breakRemainingSeconds": int(round(break_remaining or 0.0)),
            "breakEndsAtMs": int(
                round(now_wall_ms + float(break_remaining or 0.0) * 1000.0)
            )
            if break_remaining is not None
            else 0,
        }
        if self._pending_renderer_event:
            payload = dict(self._pending_renderer_event)
            payload.update(timing)
            payload.update(break_timing)
            payload["notification"] = dict(self._notification)
            payload["state"] = state
            payload["preview"] = bool(self._preview_mode)
            return payload
        return {
            "visible": False,
            "preview": False,
            "notification": dict(self._notification),
            "state": state,
            **timing,
            **break_timing,
        }

    def adjust_cycle_started_at_ms(self, started_at_ms: object) -> bool:
        try:
            started = float(started_at_ms) / 1000.0
        except (TypeError, ValueError):
            return False
        self.scheduler.set_cycle_started_at_wall(started)
        self._persist_state()
        return True

    def test_notification(self) -> dict[str, object]:
        """Send a system notification and open the full rest overlay as a preview."""
        message = "系统通知测试成功 · 这是专注休息提醒的实际弹窗预览"
        event = self.scheduler.begin_preview(message)
        notification = _show_system_notification(event.message)
        self._notification = notification
        payload = self._renderer_event_payload(event, preview=True)
        self._pending_renderer_event = payload
        self._last_surface = "renderer"
        self._preview_mode = True
        result = dict(notification)
        result["preview"] = True
        result["message"] = event.message
        return result

    def _renderer_event_payload(
        self,
        event: RestReminderEvent,
        *,
        preview: bool,
    ) -> dict[str, object]:
        remaining = self.scheduler.seconds_until_break_end()
        if remaining is None:
            remaining = float(event.break_minutes) * 60.0
        return {
            "visible": True,
            "preview": bool(preview),
            "message": event.message,
            "canPostpone": event.can_postpone,
            "intervalMinutes": event.interval_minutes,
            "breakMinutes": event.break_minutes,
            "postponeMinutes": event.postpone_minutes,
            "firedAt": event.fired_at,
            "breakDurationSeconds": int(event.break_minutes * 60),
            "breakRemainingSeconds": int(round(remaining)),
            "breakEndsAtMs": int(
                round((float(self._wall_clock()) + remaining) * 1000.0)
            ),
            "notification": dict(self._notification),
        }

    def close(self) -> None:
        self._close_dialog()
        self.scheduler.dismiss_without_reschedule()
        self._pending_renderer_event = None
        self._last_surface = ""
        self._preview_mode = False

    def _persist_state(self, *, clear: bool = False) -> None:
        if not self._persist_enabled:
            return
        try:
            from ..config import save_rest_reminder_state

            if clear or not self.scheduler.config.enabled:
                if self._last_persisted_snapshot is None and self._last_persist_mono is None:
                    return
                save_rest_reminder_state(None, self._state_path)
                self._last_persisted_snapshot = None
                self._last_persist_mono = None
                return
            mono_key = (
                float(self.scheduler.cycle_started_at),
                float(self.scheduler.next_fire_at),
                float(self.scheduler.showing_until),
                bool(self.scheduler.postpone_used),
                bool(self.scheduler.config.enabled),
            )
            if self._last_persist_mono == mono_key:
                return
            snapshot = self.scheduler.export_wall_state()
            if snapshot is None:
                if self._last_persisted_snapshot is None:
                    return
                save_rest_reminder_state(None, self._state_path)
                self._last_persisted_snapshot = None
                self._last_persist_mono = None
                return
            save_rest_reminder_state(snapshot, self._state_path)
            self._last_persisted_snapshot = dict(snapshot)
            self._last_persist_mono = mono_key
        except Exception:
            _LOGGER.debug("rest_reminder_persist_failed", exc_info=True)

    def _restore_persisted_state(self) -> bool:
        if not self._persist_enabled:
            return False
        try:
            from ..config import load_rest_reminder_state, save_rest_reminder_state

            state = load_rest_reminder_state(self._state_path)
            if not state:
                return False
            restored = bool(self.scheduler.restore_wall_state(state))
            if restored:
                normalized = self.scheduler.export_wall_state()
                restored_state = dict(normalized) if normalized is not None else {}
                if restored_state != dict(state):
                    save_rest_reminder_state(restored_state or None, self._state_path)
                self._last_persisted_snapshot = restored_state or None
                self._last_persist_mono = (
                    float(self.scheduler.cycle_started_at),
                    float(self.scheduler.next_fire_at),
                    float(self.scheduler.showing_until),
                    bool(self.scheduler.postpone_used),
                    bool(self.scheduler.config.enabled),
                )
            return restored
        except Exception:
            _LOGGER.debug("rest_reminder_restore_failed", exc_info=True)
            return False

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
                "[System.Media.SystemSounds]::Asterisk.Play(); "
                f"$n.ShowBalloonTip({WINDOWS_NOTIFICATION_DISPLAY_MILLISECONDS}, "
                f"'{title}', '{safe}', "
                "[System.Windows.Forms.ToolTipIcon]::Info); "
                "Start-Sleep -Seconds 30; $n.Dispose()"
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
