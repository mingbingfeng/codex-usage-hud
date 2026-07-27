"""Event-driven CDP/session-channel health for the renderer HUD.

Inspired by high-star connection patterns:
- Playwright/Puppeteer: transport close and command failure drive state (no
  always-on application heartbeat).
- reconnecting-websocket: jittered exponential backoff with a stable-uptime gate.
- Engine.IO-style half-open detection: probe only when traffic has gone quiet
  or the follow state is stuck.

The product surface is a three-state light: ok / recovering / failed.
Transport success alone is not enough — sticky session follow also degrades the
light so users can tell "CDP up but follow stuck" from a healthy session.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field


HEALTH_OK = "ok"
HEALTH_RECOVERING = "recovering"
HEALTH_FAILED = "failed"

# Idle probe cadence (user preference ~5s). Active successful updates skip probes.
PROBE_IDLE_SECONDS = 5.0
PROBE_FAIL_SECONDS = 1.5
PROBE_TIMEOUT_SECONDS = 0.35

# Sticky new-session / pending channel should heal after a short grace window.
STUCK_FOLLOW_SECONDS = 3.5

# Heal backoff (reconnecting-websocket style, capped).
HEAL_MIN_COOLDOWN_SECONDS = 2.0
HEAL_MAX_COOLDOWN_SECONDS = 15.0
HEAL_GROW_FACTOR = 1.7
HEAL_STABLE_UPTIME_SECONDS = 8.0

# Consecutive probe/update failures before the light turns red.
FAIL_THRESHOLD = 2

_DETAIL_ZH = {
    "ok": "CDP 连接正常",
    "update-ok": "CDP 连接正常",
    "probe-ok": "CDP 连接正常",
    "channel-restored": "会话通道已恢复",
    "update-failed": "HUD 更新失败，正在重试",
    "probe-failed": "CDP 探活失败，正在重试",
    "channel-unavailable": "会话事件通道不可用",
    "stuck-new-session": "会话跟随可能卡住，正在自愈",
    "stuck-pending": "会话映射等待超时，正在自愈",
    "follow-degraded": "会话跟随异常，连接仍可用",
    "healing": "正在修复会话跟随",
    "heal-failed": "会话自愈未成功",
    "heal-no-progress": "会话自愈未推进，仍在重试",
    "disabled": "Renderer HUD 未启用",
}


def _detail_for(reason: str, state: str) -> str:
    text = str(reason or "").strip()
    if text in _DETAIL_ZH:
        return _DETAIL_ZH[text]
    if state == HEALTH_OK:
        return _DETAIL_ZH["ok"]
    if state == HEALTH_RECOVERING:
        return "连接异常，正在恢复"
    return "CDP 连接异常"


@dataclass
class ConnectionHealth:
    """Mutable health tracker owned by the renderer loop."""

    state: str = HEALTH_OK
    reason: str = "ok"
    detail: str = field(default_factory=lambda: _DETAIL_ZH["ok"])
    last_ok_at: float = 0.0
    last_fail_at: float = 0.0
    consecutive_failures: int = 0
    next_probe_at: float = 0.0
    next_heal_at: float = 0.0
    heal_attempts: int = 0
    heal_cooldown_seconds: float = HEAL_MIN_COOLDOWN_SECONDS
    channel_available: bool = True
    last_heal_at: float = 0.0
    last_heal_reason: str = ""
    # Transport-only state before follow synthesis.
    transport_state: str = HEALTH_OK
    transport_reason: str = "ok"
    follow_state: str = ""
    follow_reason: str = ""
    follow_stuck_elapsed_ms: int = 0

    def note_success(self, reason: str = "update-ok", *, now: float | None = None) -> None:
        """Record a successful update, probe, or session observation."""
        clock = time.monotonic() if now is None else float(now)
        self.last_ok_at = clock
        self.consecutive_failures = 0
        self.channel_available = True
        self.transport_state = HEALTH_OK
        self.transport_reason = str(reason or "ok")
        self.next_probe_at = clock + PROBE_IDLE_SECONDS
        # After a healthy stretch, relax heal backoff.
        if (
            self.last_heal_at > 0
            and clock - self.last_heal_at >= HEAL_STABLE_UPTIME_SECONDS
        ):
            self.heal_attempts = 0
            self.heal_cooldown_seconds = HEAL_MIN_COOLDOWN_SECONDS
        self._synthesize()

    def note_failure(self, reason: str = "update-failed", *, now: float | None = None) -> None:
        """Record a transport/update/probe failure."""
        clock = time.monotonic() if now is None else float(now)
        self.last_fail_at = clock
        self.consecutive_failures = max(0, int(self.consecutive_failures)) + 1
        if self.consecutive_failures >= FAIL_THRESHOLD:
            self.transport_state = HEALTH_FAILED
        else:
            self.transport_state = HEALTH_RECOVERING
        self.transport_reason = str(reason or "update-failed")
        # Accelerate probes while unhealthy (still far cheaper than always-on ping).
        self.next_probe_at = clock + PROBE_FAIL_SECONDS
        self._synthesize()

    def note_channel_unavailable(
        self,
        reason: str = "channel-unavailable",
        *,
        now: float | None = None,
    ) -> None:
        """Active-session binding disconnected; keep selection but mark channel down."""
        clock = time.monotonic() if now is None else float(now)
        self.channel_available = False
        self.last_fail_at = clock
        self.consecutive_failures = max(self.consecutive_failures, FAIL_THRESHOLD)
        self.transport_state = HEALTH_FAILED
        self.transport_reason = str(reason or "channel-unavailable")
        self.next_probe_at = clock + PROBE_FAIL_SECONDS
        # Allow heal soon; do not wait a full idle period.
        self.next_heal_at = min(self.next_heal_at or clock, clock)
        self._synthesize()

    def note_channel_restored(self, *, now: float | None = None) -> None:
        """Binding/session channel is usable again."""
        self.note_success("channel-restored", now=now)

    def note_healing(self, reason: str, *, now: float | None = None) -> None:
        """Mark that a self-heal attempt is in flight."""
        clock = time.monotonic() if now is None else float(now)
        self.transport_reason = "healing"
        if self.transport_state == HEALTH_OK:
            # Follow heal should show yellow even when CDP transport is fine.
            self.transport_state = HEALTH_OK
        self.reason = "healing"
        self.last_heal_at = clock
        self.last_heal_reason = str(reason or "")
        self.heal_attempts = max(0, int(self.heal_attempts)) + 1
        # Jittered exponential backoff so concurrent clients do not thundering-herd.
        base = max(HEAL_MIN_COOLDOWN_SECONDS, float(self.heal_cooldown_seconds))
        grown = min(HEAL_MAX_COOLDOWN_SECONDS, base * HEAL_GROW_FACTOR)
        jitter = random.uniform(0.0, min(1.5, grown * 0.15))
        self.heal_cooldown_seconds = grown
        self.next_heal_at = clock + grown + jitter
        self._synthesize(force_recovering_reason=str(reason or "healing"))

    def note_heal_success(self, *, now: float | None = None) -> None:
        """Self-heal restored a healthy follow/channel state."""
        clock = time.monotonic() if now is None else float(now)
        self.heal_attempts = 0
        self.heal_cooldown_seconds = HEAL_MIN_COOLDOWN_SECONDS
        self.follow_state = "confirmed"
        self.follow_reason = "confirmed"
        self.follow_stuck_elapsed_ms = 0
        self.note_success("channel-restored", now=clock)

    def note_heal_no_progress(
        self,
        reason: str = "heal-no-progress",
        *,
        now: float | None = None,
    ) -> None:
        """Report ran but follow identity did not advance."""
        clock = time.monotonic() if now is None else float(now)
        self.last_fail_at = clock
        self.last_heal_reason = str(reason or "heal-no-progress")
        self.transport_reason = str(reason or "heal-no-progress")
        # Keep transport "ok" if CDP still works; synthesis turns light yellow.
        if self.transport_state == HEALTH_FAILED and self.channel_available:
            self.transport_state = HEALTH_OK
            self.consecutive_failures = 0
        self._synthesize(force_recovering_reason=str(reason or "heal-no-progress"))

    def observe_follow(
        self,
        *,
        follow_state: str = "",
        follow_reason: str = "",
        follow_stuck_elapsed_ms: int = 0,
    ) -> None:
        """Update follow diagnostics used for light synthesis and heal gating."""
        self.follow_state = str(follow_state or "").strip()
        self.follow_reason = str(follow_reason or "").strip()
        self.follow_stuck_elapsed_ms = max(0, int(follow_stuck_elapsed_ms or 0))
        self._synthesize()

    def should_probe(
        self,
        *,
        now: float | None = None,
        follow_state: str = "",
        follow_elapsed_ms: int = 0,
        update_failures: int = 0,
    ) -> bool:
        """Whether a cheap CDP liveness probe is warranted.

        Successful traffic resets next_probe_at, so healthy busy sessions almost
        never probe. Probes fire when idle, failing, channel-down, or stuck.
        """
        clock = time.monotonic() if now is None else float(now)
        if clock < float(self.next_probe_at or 0.0):
            return False
        if not self.channel_available:
            return True
        if int(update_failures or 0) > 0 or self.consecutive_failures > 0:
            return True
        if self.transport_state in {HEALTH_RECOVERING, HEALTH_FAILED}:
            return True
        follow = str(follow_state or self.follow_state or "").strip()
        elapsed = int(follow_elapsed_ms or self.follow_stuck_elapsed_ms or 0)
        if follow in {"new-session", "pending"} and elapsed >= int(
            STUCK_FOLLOW_SECONDS * 1000
        ):
            return True
        # Quiet healthy path: only after idle window since last success.
        if self.last_ok_at <= 0:
            return True
        return (clock - self.last_ok_at) >= PROBE_IDLE_SECONDS

    def should_heal(
        self,
        *,
        now: float | None = None,
        follow_state: str = "",
        follow_reason: str = "",
        follow_elapsed_ms: int = 0,
    ) -> tuple[bool, str]:
        """Return whether session-follow self-heal should run, and why."""
        clock = time.monotonic() if now is None else float(now)
        if clock < float(self.next_heal_at or 0.0):
            return False, ""
        reason = str(follow_reason or self.follow_reason or "").strip()
        state = str(follow_state or self.follow_state or "").strip()
        elapsed_ms = int(follow_elapsed_ms or self.follow_stuck_elapsed_ms or 0)
        if not self.channel_available or reason == "renderer-channel-unavailable":
            return True, "channel-unavailable"
        if state == "new-session" and elapsed_ms >= int(STUCK_FOLLOW_SECONDS * 1000):
            return True, "stuck-new-session"
        if state == "pending" and elapsed_ms >= int(STUCK_FOLLOW_SECONDS * 1000):
            # Exact mapping wait is normal briefly; only heal long-stuck pending
            # with a transport-ish reason or channel issues already covered above.
            if reason in {
                "awaiting-canonical-id",
                "awaiting-persistence",
                "awaiting-exact-mapping",
                "renderer-channel-unavailable",
            }:
                # Give mapping a bit longer than blank new-session before heal.
                if elapsed_ms >= int((STUCK_FOLLOW_SECONDS + 2.0) * 1000):
                    return True, "stuck-pending"
        return False, ""

    def seconds_until_probe(self, *, now: float | None = None) -> float | None:
        clock = time.monotonic() if now is None else float(now)
        target = float(self.next_probe_at or 0.0)
        if target <= 0:
            return None
        return max(0.0, target - clock)

    def seconds_until_heal(self, *, now: float | None = None) -> float | None:
        clock = time.monotonic() if now is None else float(now)
        target = float(self.next_heal_at or 0.0)
        if target <= 0:
            return None
        return max(0.0, target - clock)

    def _synthesize(self, *, force_recovering_reason: str = "") -> None:
        """Combine transport health with sticky follow degradation for the light."""
        transport = str(self.transport_state or HEALTH_OK)
        follow = str(self.follow_state or "").strip()
        follow_reason = str(self.follow_reason or "").strip()
        stuck_ms = int(self.follow_stuck_elapsed_ms or 0)
        stuck_follow = follow in {"new-session", "pending"} and stuck_ms >= int(
            STUCK_FOLLOW_SECONDS * 1000
        )
        healing = force_recovering_reason in {
            "healing",
            "stuck-new-session",
            "stuck-pending",
            "channel-unavailable",
            "heal-no-progress",
        } or str(self.transport_reason or "") in {
            "healing",
            "heal-no-progress",
        }

        if transport == HEALTH_FAILED or not self.channel_available:
            self.state = HEALTH_FAILED
            self.reason = (
                self.transport_reason
                if self.transport_reason
                else ("channel-unavailable" if not self.channel_available else "update-failed")
            )
        elif transport == HEALTH_RECOVERING:
            self.state = HEALTH_RECOVERING
            self.reason = self.transport_reason or "update-failed"
        elif healing or stuck_follow:
            # CDP still works, but session follow is degraded — show yellow.
            self.state = HEALTH_RECOVERING
            if force_recovering_reason:
                self.reason = force_recovering_reason
            elif healing:
                self.reason = self.transport_reason or "healing"
            elif follow == "new-session":
                self.reason = "stuck-new-session"
            elif follow_reason:
                self.reason = "stuck-pending"
            else:
                self.reason = "follow-degraded"
        else:
            self.state = HEALTH_OK
            self.reason = self.transport_reason or "ok"
        self.detail = _detail_for(self.reason, self.state)

    def to_payload(self) -> dict[str, object]:
        """JSON fragment for the renderer connection light."""
        return {
            "state": self.state,
            "reason": self.reason,
            "detail": self.detail,
            "channelAvailable": bool(self.channel_available),
            "consecutiveFailures": int(self.consecutive_failures),
            "healAttempts": int(self.heal_attempts),
            "lastHealReason": self.last_heal_reason,
            "transportState": self.transport_state,
            "followState": self.follow_state,
            "followReason": self.follow_reason,
            "followStuckElapsedMs": int(self.follow_stuck_elapsed_ms),
        }
