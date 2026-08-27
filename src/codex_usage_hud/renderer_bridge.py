"""Renderer callback normalization and stable CDP binding installation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import threading
from typing import Protocol

from . import runtime_policies


BINDING_ORDER = (
    "set_active_session_callback",
    "set_settings_command_callback",
    "set_attachments_callback",
    "set_layout_callback",
    "set_theme_callback",
)


def _active_session_observation_key(payload: Mapping[str, object]) -> tuple[object, ...]:
    """Return the renderer observation identity, excluding delivery metadata."""
    raw_sequence = payload.get("selectionSeq") or payload.get("selection_seq")
    try:
        sequence = max(0, int(raw_sequence or 0))
    except (TypeError, ValueError):
        sequence = 0
    return (
        sequence,
        str(payload.get("sessionId") or payload.get("session_id") or "").strip(),
        str(
            payload.get("rendererSessionId")
            or payload.get("renderer_session_id")
            or ""
        ).strip(),
        str(payload.get("title") or "").strip(),
        bool(payload.get("newSession") or payload.get("new_session")),
        bool(payload.get("pendingSession") or payload.get("pending_session")),
    )


def _observation_key_sequence(observation_key: object | None) -> int:
    if not isinstance(observation_key, tuple) or not observation_key:
        return 0
    try:
        return max(0, int(observation_key[0] or 0))
    except (TypeError, ValueError):
        return 0


_COMPOSER_SEND_REASONS = frozenset({
    "composer-send",
    "composer-enter",
    "composer-submit",
})

_SESSION_ID_PREFIXES = frozenset({"local", "remote", "thread", "session", "conversation"})


def _normalize_session_id(value: object) -> str:
    """Strip renderer transport prefix (local:/remote:/thread:/etc.) for comparison."""
    text = str(value or "").strip()
    if ":" in text:
        prefix, suffix = text.split(":", 1)
        if prefix.lower() in _SESSION_ID_PREFIXES:
            return suffix.strip()
    return text


def _invalidate_cache_on_composer_send(
    tracker: object,
    payload: Mapping[str, object],
) -> None:
    """Invalidate the thread-id→path cache on a composer send, but only when
    the session identity may actually have changed.

    The in-page script reports composer-send / composer-enter / composer-submit
    within ~32ms of the user action. For a genuine new session, Codex's state
    DB may not yet contain the rollout_path, so the first lookup caches a
    negative result for 2s. Invalidating the cache lets the subsequent
    state-db write (watched as a session-map file event) be re-queried
    immediately.

    However, sending a message in an *existing* session keeps the same
    session_id. Unconditionally invalidating the cache there causes a transient
    None lookup that briefly overwrites the already-resolved path with pending
    state — the bubble flashes out then back in, or stays gone for the full
    follow-up backoff. Guard against that by comparing the incoming session
    identity against the tracker's current identity, normalizing both to the
    canonical (prefix-stripped) form because payload.sessionId is canonical
    while tracker.renderer_session_id retains the raw transport prefix.
    """
    reason = str(payload.get("reason") or "").strip()
    if reason not in _COMPOSER_SEND_REASONS:
        return
    is_new = bool(payload.get("newSession") or payload.get("new_session"))
    is_pending = bool(payload.get("pendingSession") or payload.get("pending_session"))
    if is_new or is_pending:
        _do_invalidate_mapping_cache(tracker)
        return
    payload_session_id = _normalize_session_id(
        payload.get("sessionId")
        or payload.get("session_id")
        or payload.get("rendererSessionId")
        or payload.get("renderer_session_id")
        or ""
    )
    # latest_session_id is already canonical; renderer_session_id is raw-prefixed.
    # Normalize both to be safe.
    current_session_id = _normalize_session_id(
        getattr(tracker, "latest_session_id", "")
        or getattr(tracker, "renderer_session_id", "")
        or ""
    )
    # Same session_id as the already-resolved current session → this is a
    # message send in an existing conversation, not a session switch. Keep the
    # cache intact to avoid the flash-out / long-disappear regression.
    if current_session_id and payload_session_id == current_session_id:
        return
    _do_invalidate_mapping_cache(tracker)


def _do_invalidate_mapping_cache(tracker: object) -> None:
    invalidate = getattr(tracker, "invalidate_mapping_cache", None)
    if callable(invalidate):
        try:
            invalidate()
        except Exception:
            pass


class RuntimeSignalsPort(Protocol):
    def enqueue_command(self, command: dict[str, object]) -> None: ...

    def publish_or_wake(
        self,
        publish: Callable[..., object] | None,
        event_type: str,
        *,
        source: str,
        context: dict[str, object],
        active_session: bool = False,
    ) -> None: ...


@dataclass(slots=True)
class RendererBridgeCallbacks:
    """Normalize Renderer binding payloads into runtime-owned operations."""

    signals: RuntimeSignalsPort
    active_session_tracker: object | None
    attachment_estimator: object | None
    connection_health: object
    request_connection_health_light: Callable[[], None]
    request_active_session_refresh: Callable[[], None]
    publish_event: Callable[..., object] | None = None
    _active_session_state_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _active_session_observation_key: object | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _active_session_applied_seq: int = field(
        default=0,
        init=False,
        repr=False,
    )
    _active_session_applied_observation_key: object | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _active_session_refresh_pending_key: object | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _active_session_refresh_in_flight_key: object | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _active_session_observation_generation: int = field(
        default=0,
        init=False,
        repr=False,
    )
    _active_session_highest_observation_seq: int = field(
        default=0,
        init=False,
        repr=False,
    )

    def complete_active_session_update(
        self,
        selection_seq: object,
        *,
        succeeded: bool,
        observation_key: object | None = None,
    ) -> None:
        """Record the result of the runtime's session payload attempt."""
        try:
            sequence = max(0, int(selection_seq or 0))
        except (TypeError, ValueError):
            sequence = 0
        requeue_pending = False
        with self._active_session_state_lock:
            pending = self._active_session_refresh_pending_key
            in_flight = self._active_session_refresh_in_flight_key
            current = self._active_session_observation_key
            completed_key = observation_key
            if completed_key is None:
                completed_key = in_flight
            if completed_key is None and pending is not None:
                pending_sequence = _observation_key_sequence(pending)
                if sequence > 0 and pending_sequence == sequence:
                    completed_key = pending
            if completed_key is None and current is not None:
                current_sequence = _observation_key_sequence(current)
                if sequence > 0 and current_sequence == sequence:
                    completed_key = current
            completed_sequence = max(
                sequence,
                _observation_key_sequence(completed_key),
            )
            if succeeded and completed_sequence > 0:
                self._active_session_applied_seq = max(
                    self._active_session_applied_seq,
                    completed_sequence,
                )
            matches_pending = (
                pending is not None
                and completed_key is not None
                and pending == completed_key
            )
            if succeeded and completed_key is not None and (
                pending is not None
                or current == completed_key
                or self._active_session_applied_observation_key is None
            ):
                self._active_session_applied_observation_key = completed_key
            if succeeded and matches_pending:
                self._active_session_refresh_pending_key = None
            elif not succeeded and pending is not None and matches_pending:
                # Let a later identical callback enqueue another attempt after
                # a transport/render acknowledgement failure.
                self._active_session_refresh_pending_key = None
            in_flight_completed = in_flight is not None and in_flight == completed_key
            if in_flight_completed:
                self._active_session_refresh_in_flight_key = None
            pending_after = self._active_session_refresh_pending_key
            requeue_pending = bool(
                in_flight_completed
                and pending_after is not None
                and pending_after != completed_key
            )
        if requeue_pending:
            self.publish_active_session_changed("renderer_bridge_ack_pending")

    def retry_active_session_update(self) -> None:
        """Keep the latest observation queued after a stale snapshot discard."""
        should_publish = False
        with self._active_session_state_lock:
            observation_key = self._active_session_observation_key
            if observation_key is None:
                return
            if self._active_session_refresh_pending_key != observation_key:
                should_publish = True
            self._active_session_refresh_pending_key = observation_key
        if should_publish:
            self.publish_active_session_changed("renderer_bridge_stale_retry")

    def capture_active_session_observation(self) -> object | None:
        """Capture the observation identity associated with a refresh attempt."""
        with self._active_session_state_lock:
            observation_key = self._active_session_observation_key
            self._active_session_refresh_in_flight_key = observation_key
            return observation_key

    def active_session_observation_key(self) -> object | None:
        with self._active_session_state_lock:
            return self._active_session_observation_key

    def acknowledge_active_session_update(
        self,
        selection_seq: object,
        observation_key: object | None = None,
    ) -> None:
        """Backward-compatible successful-ack adapter."""
        self.complete_active_session_update(
            selection_seq,
            succeeded=True,
            observation_key=observation_key,
        )

    def connect_tracker(self) -> None:
        setter = getattr(self.active_session_tracker, "set_change_callback", None)
        if callable(setter):
            setter(lambda: self.publish_active_session_changed("tracker_callback"))

    def disconnect_tracker(self) -> None:
        setter = getattr(self.active_session_tracker, "set_change_callback", None)
        if callable(setter):
            setter(None)

    def publish_active_session_changed(self, reason: str) -> None:
        self.signals.publish_or_wake(
            self.publish_event,
            "active_session_changed",
            source="active_session",
            context={"reason": reason},
            active_session=True,
        )

    def enqueue_command(self, command: dict[str, object]) -> None:
        normalized = dict(command)
        self.signals.enqueue_command(normalized)
        self.signals.publish_or_wake(
            self.publish_event,
            "settings_command_received",
            source="settings_bridge",
            context={
                "action": str(normalized.get("action") or ""),
                "id": str(normalized.get("id") or ""),
                "command": normalized,
            },
        )

    def observe_active_session(self, payload: dict[str, object]) -> None:
        tracker = self.active_session_tracker
        if bool(
            payload.get("channelUnavailable")
            or payload.get("channel_unavailable")
        ):
            marker = getattr(tracker, "mark_renderer_channel_unavailable", None)
            if callable(marker):
                marker(str(payload.get("reason") or ""))
            note_unavailable = getattr(
                self.connection_health,
                "note_channel_unavailable",
                None,
            )
            if callable(note_unavailable):
                note_unavailable(str(payload.get("reason") or "channel-unavailable"))
            self.request_connection_health_light()
            return

        observer = getattr(tracker, "observe_conversation_ref", None)
        if not callable(observer):
            return
        observer_kwargs: dict[str, object] = {
            "session_id": str(
                payload.get("sessionId") or payload.get("session_id") or ""
            ),
            "title": str(payload.get("title") or ""),
            "source": "renderer",
        }
        renderer_session_id = str(
            payload.get("rendererSessionId")
            or payload.get("renderer_session_id")
            or ""
        )
        if renderer_session_id:
            observer_kwargs["renderer_session_id"] = renderer_session_id
        selection_seq = payload.get("selectionSeq") or payload.get("selection_seq")
        if selection_seq:
            observer_kwargs["selection_seq"] = selection_seq
        observed_at_ms = payload.get("observedAt") or payload.get("observed_at_ms")
        if observed_at_ms:
            observer_kwargs["observed_at_ms"] = observed_at_ms
        if bool(payload.get("newSession") or payload.get("new_session")):
            observer_kwargs["new_session"] = True
        if bool(payload.get("pendingSession") or payload.get("pending_session")):
            observer_kwargs["pending_session"] = True

        observation_key = _active_session_observation_key(payload)
        try:
            incoming_sequence = max(0, int(selection_seq or 0))
        except (TypeError, ValueError):
            incoming_sequence = 0
        try:
            tracker_sequence = int(getattr(tracker, "selection_seq", 0) or 0)
        except (TypeError, ValueError):
            tracker_sequence = 0
        if (
            incoming_sequence > 0
            and tracker_sequence > 0
            and incoming_sequence < tracker_sequence
        ):
            return
        with self._active_session_state_lock:
            if (
                incoming_sequence > 0
                and self._active_session_highest_observation_seq > 0
                and incoming_sequence < self._active_session_highest_observation_seq
            ):
                return
            if incoming_sequence > self._active_session_highest_observation_seq:
                self._active_session_highest_observation_seq = incoming_sequence
            self._active_session_observation_generation += 1
            observation_generation = self._active_session_observation_generation
        # A composer send (button click / Enter / form submit) means Codex is
        # starting a new task or session. Invalidate the thread-id→path cache
        # immediately so the first state-db write after send is re-queried
        # instead of being blocked by the 2s negative-cache from the pre-send
        # probe. This is event-driven (triggered by the in-page send listener),
        # not polling.
        _invalidate_cache_on_composer_send(tracker, payload)
        changed = observer(**observer_kwargs)
        if not bool(payload.get("newSession") or payload.get("new_session")):
            channel_available = bool(
                getattr(self.connection_health, "channel_available", True)
            )
            if not channel_available:
                note_restored = getattr(
                    self.connection_health,
                    "note_channel_restored",
                    None,
                )
                if callable(note_restored):
                    note_restored()
                self.request_connection_health_light()
        try:
            tracker_sequence = int(getattr(tracker, "selection_seq", 0) or 0)
        except (TypeError, ValueError):
            tracker_sequence = 0
        stale_observation = bool(
            incoming_sequence > 0
            and tracker_sequence > 0
            and incoming_sequence < tracker_sequence
        )
        should_publish = False
        with self._active_session_state_lock:
            # A slower callback must not commit an older observation after a
            # newer callback has already reached the tracker.
            if observation_generation != self._active_session_observation_generation:
                return
            previous_observation_key = self._active_session_observation_key
            applied_seq = self._active_session_applied_seq
            applied_observation_key = self._active_session_applied_observation_key
            refresh_pending = self._active_session_refresh_pending_key is not None
            if not stale_observation:
                self._active_session_observation_key = observation_key
            should_refresh = runtime_policies.active_session_observation_should_refresh(
                changed=bool(changed),
                selection_seq=selection_seq,
                current_seq=tracker_sequence,
                observation_key=observation_key,
                previous_observation_key=previous_observation_key,
                applied_seq=applied_seq,
                applied_observation_key=applied_observation_key,
                refresh_pending=refresh_pending,
            )
            if should_refresh and not stale_observation:
                self._active_session_refresh_pending_key = observation_key
                should_publish = True
        if should_publish:
            self.publish_active_session_changed("renderer_bridge")

    def observe_attachments(self, payload: dict[str, object]) -> None:
        setter = getattr(self.attachment_estimator, "set_attachments", None)
        if not callable(setter):
            return
        setter(payload)
        self.request_active_session_refresh()

    def observe_layout(self, payload: dict[str, object]) -> None:
        self.signals.publish_or_wake(
            self.publish_event,
            "renderer_layout_changed",
            source="renderer_layout",
            context={
                "reason": str(payload.get("reason") or ""),
                "panel": str(payload.get("panel") or ""),
                "layout": payload.get("layout"),
                "observedAt": payload.get("observedAt"),
            },
        )

    def observe_theme(self, payload: dict[str, object]) -> None:
        self.signals.publish_or_wake(
            self.publish_event,
            "renderer_theme_changed",
            source="renderer_theme",
            context={"theme": dict(payload)},
        )

    def install(self, client: object) -> None:
        install_renderer_bindings(
            client,
            {
                "set_active_session_callback": self.observe_active_session,
                "set_settings_command_callback": self.enqueue_command,
                "set_attachments_callback": self.observe_attachments,
                "set_layout_callback": self.observe_layout,
                "set_theme_callback": self.observe_theme,
            },
        )


def install_renderer_bindings(
    client: object,
    callbacks: Mapping[str, Callable[[dict[str, object]], None]],
) -> None:
    """Install supported CDP callbacks in the renderer ABI's fixed order."""
    for setter_name in BINDING_ORDER:
        callback = callbacks.get(setter_name)
        setter = getattr(client, setter_name, None)
        if callback is not None and callable(setter):
            setter(callback)


__all__ = [
    "BINDING_ORDER",
    "RendererBridgeCallbacks",
    "RuntimeSignalsPort",
    "install_renderer_bindings",
]
