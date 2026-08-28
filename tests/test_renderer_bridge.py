import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from codex_usage_hud.renderer_bridge import (
    BINDING_ORDER,
    RendererBridgeCallbacks,
    install_renderer_bindings,
)


class _Signals:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []
        self.events: list[tuple[str, str, dict[str, object], bool]] = []

    def enqueue_command(self, command: dict[str, object]) -> None:
        self.commands.append(dict(command))

    def publish_or_wake(
        self,
        publish: object,
        event_type: str,
        *,
        source: str,
        context: dict[str, object],
        active_session: bool = False,
    ) -> None:
        self.events.append((event_type, source, dict(context), active_session))


def _callbacks(**overrides: object) -> RendererBridgeCallbacks:
    values: dict[str, object] = {
        "signals": _Signals(),
        "active_session_tracker": None,
        "attachment_estimator": None,
        "connection_health": SimpleNamespace(channel_available=True),
        "request_connection_health_light": MagicMock(),
        "request_active_session_refresh": MagicMock(),
    }
    values.update(overrides)
    return RendererBridgeCallbacks(**values)  # type: ignore[arg-type]


def test_renderer_bindings_preserve_the_renderer_abi_order() -> None:
    installed: list[str] = []
    client = SimpleNamespace(
        **{
            name: (lambda callback, name=name: installed.append(name))
            for name in BINDING_ORDER
        }
    )
    callbacks = {name: (lambda _payload: None) for name in BINDING_ORDER}

    install_renderer_bindings(client, callbacks)

    assert tuple(installed) == BINDING_ORDER


def test_renderer_bridge_command_is_copied_and_published() -> None:
    signals = _Signals()
    callbacks = _callbacks(signals=signals)
    command = {"id": "request-1", "action": "saveSettings"}

    callbacks.enqueue_command(command)
    command["action"] = "mutated"

    assert signals.commands == [{"id": "request-1", "action": "saveSettings"}]
    assert signals.events == [
        (
            "settings_command_received",
            "settings_bridge",
            {
                "action": "saveSettings",
                "id": "request-1",
                "command": {"id": "request-1", "action": "saveSettings"},
            },
            False,
        )
    ]


def test_renderer_bridge_normalizes_active_session_aliases_and_sequence() -> None:
    tracker = SimpleNamespace(
        selection_seq=8,
        observe_conversation_ref=MagicMock(return_value=False),
    )
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)

    callbacks.observe_active_session(
        {
            "session_id": "session-1",
            "renderer_session_id": "renderer-1",
            "title": "Session",
            "selection_seq": 8,
            "observed_at_ms": 42,
            "pending_session": True,
        }
    )

    tracker.observe_conversation_ref.assert_called_once_with(
        session_id="session-1",
        title="Session",
        source="renderer",
        renderer_session_id="renderer-1",
        selection_seq=8,
        observed_at_ms=42,
        pending_session=True,
    )
    assert signals.events == [
        (
            "active_session_changed",
            "active_session",
            {"reason": "renderer_bridge"},
            True,
        )
    ]
    callbacks.request_active_session_refresh.assert_called_once_with()


def test_renderer_bridge_wakes_visible_session_before_a_slow_event_publish() -> None:
    tracker = SimpleNamespace(
        selection_seq=1,
        observe_conversation_ref=MagicMock(return_value=True),
    )
    entered_publish = threading.Event()
    release_publish = threading.Event()
    visible_wake = MagicMock()

    class _BlockingSignals(_Signals):
        def publish_or_wake(
            self,
            publish: object,
            event_type: str,
            *,
            source: str,
            context: dict[str, object],
            active_session: bool = False,
        ) -> None:
            del publish, event_type, source, context, active_session
            entered_publish.set()
            assert release_publish.wait(timeout=2)

    callbacks = _callbacks(
        signals=_BlockingSignals(),
        active_session_tracker=tracker,
        request_active_session_refresh=visible_wake,
    )
    worker = threading.Thread(
        target=callbacks.observe_active_session,
        args=({"sessionId": "session-1", "selectionSeq": 1},),
    )

    worker.start()

    assert entered_publish.wait(timeout=2)
    visible_wake.assert_called_once_with()
    release_publish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()


def test_renderer_bridge_invalidates_mapping_on_composer_send_click() -> None:
    tracker = SimpleNamespace(
        selection_seq=0,
        invalidate_mapping_cache=MagicMock(),
        observe_conversation_ref=MagicMock(return_value=True),
    )
    callbacks = _callbacks(active_session_tracker=tracker)

    callbacks.observe_active_session(
        {
            "reason": "composer-send-click",
            "newSession": True,
            "selectionSeq": 1,
        }
    )

    tracker.invalidate_mapping_cache.assert_called_once_with()
    tracker.observe_conversation_ref.assert_called_once_with(
        session_id="",
        title="",
        source="renderer",
        selection_seq=1,
        new_session=True,
    )


def test_renderer_bridge_forwards_composer_draft_and_send_state() -> None:
    tracker = SimpleNamespace(
        selection_seq=1,
        observe_conversation_ref=MagicMock(return_value=True),
    )
    callbacks = _callbacks(active_session_tracker=tracker)

    callbacks.observe_active_session(
        {
            "newSession": True,
            "selectionSeq": 1,
            "draftText": "Draft before send",
            "sendRequested": True,
        }
    )

    tracker.observe_conversation_ref.assert_called_once_with(
        session_id="",
        title="",
        source="renderer",
        selection_seq=1,
        new_session=True,
        draft_text="Draft before send",
        send_requested=True,
    )


def test_renderer_bridge_suppresses_identical_observation_after_successful_ack() -> None:
    tracker = SimpleNamespace(
        selection_seq=7,
        observe_conversation_ref=MagicMock(return_value=False),
    )
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)
    payload = {
        "sessionId": "session-1",
        "rendererSessionId": "renderer-1",
        "title": "Session",
        "selectionSeq": 7,
    }

    callbacks.observe_active_session(payload)
    observation_key = callbacks.capture_active_session_observation()
    callbacks.complete_active_session_update(
        7,
        succeeded=True,
        observation_key=observation_key,
    )
    callbacks.observe_active_session(payload)

    assert len(signals.events) == 1
    assert callbacks.active_session_observation_key() == observation_key


def test_renderer_bridge_does_not_duplicate_identical_observation_before_ack() -> None:
    tracker = SimpleNamespace(
        selection_seq=7,
        observe_conversation_ref=MagicMock(return_value=False),
    )
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)
    payload = {
        "sessionId": "session-1",
        "rendererSessionId": "renderer-1",
        "title": "Session",
        "selectionSeq": 7,
    }

    callbacks.observe_active_session(payload)
    callbacks.observe_active_session(payload)

    assert len(signals.events) == 1


def test_renderer_bridge_drops_slower_older_observation() -> None:
    old_started = threading.Event()
    release_old = threading.Event()

    class _Tracker:
        selection_seq = 7

        def observe_conversation_ref(self, **kwargs: object) -> bool:
            if kwargs.get("session_id") == "session-1":
                old_started.set()
                assert release_old.wait(timeout=2)
                return False
            self.selection_seq = int(kwargs.get("selection_seq") or 0)
            return False

    tracker = _Tracker()
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)
    old_payload = {
        "sessionId": "session-1",
        "rendererSessionId": "renderer-1",
        "title": "Old",
        "selectionSeq": 7,
    }
    new_payload = {
        "sessionId": "session-2",
        "rendererSessionId": "renderer-2",
        "title": "New",
        "selectionSeq": 8,
    }
    old_thread = threading.Thread(
        target=callbacks.observe_active_session,
        args=(old_payload,),
    )
    old_thread.start()
    assert old_started.wait(timeout=2)
    callbacks.observe_active_session(new_payload)
    release_old.set()
    old_thread.join(timeout=2)

    assert not old_thread.is_alive()
    assert callbacks.active_session_observation_key() == (
        8,
        "session-2",
        "renderer-2",
        "New",
        False,
        False,
    )
    assert len(signals.events) == 1


def test_renderer_bridge_drops_older_sequence_while_newer_observation_is_in_flight() -> None:
    newer_started = threading.Event()
    release_newer = threading.Event()

    class _Tracker:
        selection_seq = 7

        def observe_conversation_ref(self, **kwargs: object) -> bool:
            if kwargs.get("session_id") == "session-2":
                newer_started.set()
                assert release_newer.wait(timeout=2)
                self.selection_seq = int(kwargs.get("selection_seq") or 0)
            return False

    tracker = _Tracker()
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)
    newer_payload = {
        "sessionId": "session-2",
        "rendererSessionId": "renderer-2",
        "title": "New",
        "selectionSeq": 8,
    }
    older_payload = {
        "sessionId": "session-1",
        "rendererSessionId": "renderer-1",
        "title": "Old",
        "selectionSeq": 7,
    }
    newer_thread = threading.Thread(
        target=callbacks.observe_active_session,
        args=(newer_payload,),
    )
    newer_thread.start()
    assert newer_started.wait(timeout=2)
    callbacks.observe_active_session(older_payload)
    release_newer.set()
    newer_thread.join(timeout=2)

    assert not newer_thread.is_alive()
    assert callbacks.active_session_observation_key() == (
        8,
        "session-2",
        "renderer-2",
        "New",
        False,
        False,
    )
    assert len(signals.events) == 1


def test_renderer_bridge_retries_identical_observation_after_failed_update() -> None:
    tracker = SimpleNamespace(
        selection_seq=7,
        observe_conversation_ref=MagicMock(return_value=False),
    )
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)
    payload = {
        "sessionId": "session-1",
        "rendererSessionId": "renderer-1",
        "title": "Session",
        "selectionSeq": 7,
    }

    callbacks.observe_active_session(payload)
    observation_key = callbacks.capture_active_session_observation()
    callbacks.complete_active_session_update(
        7,
        succeeded=False,
        observation_key=observation_key,
    )
    callbacks.observe_active_session(payload)

    assert len(signals.events) == 2


def test_renderer_bridge_refreshes_same_sequence_when_identity_changes() -> None:
    tracker = SimpleNamespace(
        selection_seq=7,
        observe_conversation_ref=MagicMock(return_value=False),
    )
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)

    first = {
        "sessionId": "session-1",
        "rendererSessionId": "renderer-1",
        "title": "Session",
        "selectionSeq": 7,
    }
    second = {
        **first,
        "rendererSessionId": "renderer-1-prefixed",
        "title": "Renamed Session",
    }
    callbacks.observe_active_session(first)
    observation_key = callbacks.capture_active_session_observation()
    callbacks.complete_active_session_update(
        7,
        succeeded=True,
        observation_key=observation_key,
    )
    callbacks.observe_active_session(second)

    assert len(signals.events) == 2


def test_renderer_bridge_requeues_newer_observation_after_older_ack() -> None:
    tracker = SimpleNamespace(
        selection_seq=7,
        observe_conversation_ref=MagicMock(return_value=False),
    )
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)
    first = {
        "sessionId": "session-1",
        "rendererSessionId": "renderer-1",
        "title": "Session",
        "selectionSeq": 7,
    }
    second = {**first, "selectionSeq": 8, "sessionId": "session-2"}

    callbacks.observe_active_session(first)
    first_key = callbacks.capture_active_session_observation()
    callbacks.observe_active_session(second)
    second_key = callbacks.active_session_observation_key()
    callbacks.complete_active_session_update(
        7,
        succeeded=True,
        observation_key=first_key,
    )

    assert second_key != first_key
    assert len(signals.events) == 3
    second_key = callbacks.capture_active_session_observation()
    callbacks.complete_active_session_update(
        8,
        succeeded=True,
        observation_key=second_key,
    )
    callbacks.observe_active_session(second)

    assert len(signals.events) == 3


def test_renderer_bridge_ignores_stale_sequence_after_newer_observation() -> None:
    tracker = SimpleNamespace(
        selection_seq=7,
        observe_conversation_ref=MagicMock(return_value=False),
    )
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)

    current = {
        "sessionId": "session-1",
        "rendererSessionId": "renderer-1",
        "title": "Session",
        "selectionSeq": 7,
    }
    newer = {**current, "selectionSeq": 8, "sessionId": "session-2"}
    stale = {**current, "selectionSeq": 6, "sessionId": "old-session"}
    callbacks.observe_active_session(current)
    observation_key = callbacks.capture_active_session_observation()
    callbacks.complete_active_session_update(
        7,
        succeeded=True,
        observation_key=observation_key,
    )
    callbacks.observe_active_session(newer)
    callbacks.observe_active_session(stale)

    assert len(signals.events) == 2
    assert callbacks.active_session_observation_key() == (
        8,
        "session-2",
        "renderer-1",
        "Session",
        False,
        False,
    )


def test_renderer_bridge_requeues_latest_observation_after_stale_snapshot() -> None:
    tracker = SimpleNamespace(
        selection_seq=7,
        observe_conversation_ref=MagicMock(return_value=False),
    )
    signals = _Signals()
    callbacks = _callbacks(signals=signals, active_session_tracker=tracker)
    payload = {
        "sessionId": "session-1",
        "rendererSessionId": "renderer-1",
        "title": "Session",
        "selectionSeq": 7,
    }

    callbacks.observe_active_session(payload)
    observation_key = callbacks.capture_active_session_observation()
    callbacks.complete_active_session_update(
        7,
        succeeded=False,
        observation_key=observation_key,
    )
    callbacks.retry_active_session_update()

    assert len(signals.events) == 2
    assert callbacks._active_session_refresh_pending_key == observation_key


def test_renderer_bridge_tracks_channel_loss_and_restore() -> None:
    tracker = SimpleNamespace(
        selection_seq=1,
        mark_renderer_channel_unavailable=MagicMock(),
        observe_conversation_ref=MagicMock(return_value=False),
    )
    health = SimpleNamespace(
        channel_available=True,
        note_channel_unavailable=MagicMock(
            side_effect=lambda reason: setattr(health, "channel_available", False)
        ),
        note_channel_restored=MagicMock(
            side_effect=lambda: setattr(health, "channel_available", True)
        ),
    )
    request_light = MagicMock()
    callbacks = _callbacks(
        active_session_tracker=tracker,
        connection_health=health,
        request_connection_health_light=request_light,
    )

    callbacks.observe_active_session(
        {"channelUnavailable": True, "reason": "binding-lost"}
    )
    callbacks.observe_active_session({"sessionId": "session-1"})

    tracker.mark_renderer_channel_unavailable.assert_called_once_with("binding-lost")
    health.note_channel_unavailable.assert_called_once_with("binding-lost")
    health.note_channel_restored.assert_called_once_with()
    assert request_light.call_count == 2


def test_renderer_bridge_routes_attachment_layout_theme_and_tracker_wake() -> None:
    tracker = SimpleNamespace(set_change_callback=MagicMock())
    estimator = SimpleNamespace(set_attachments=MagicMock())
    signals = _Signals()
    request_active = MagicMock()
    callbacks = _callbacks(
        signals=signals,
        active_session_tracker=tracker,
        attachment_estimator=estimator,
        request_active_session_refresh=request_active,
    )
    callbacks.connect_tracker()
    tracker_callback = tracker.set_change_callback.call_args.args[0]

    callbacks.observe_attachments({"count": 2})
    callbacks.observe_layout(
        {"reason": "drag", "panel": "budget", "layout": {"x": 1}}
    )
    callbacks.observe_theme({"mode": "dark"})
    tracker_callback()
    callbacks.disconnect_tracker()

    estimator.set_attachments.assert_called_once_with({"count": 2})
    assert request_active.call_count == 2
    assert tracker.set_change_callback.call_args_list[-1].args == (None,)
    assert signals.events == [
        (
            "renderer_layout_changed",
            "renderer_layout",
            {
                "reason": "drag",
                "panel": "budget",
                "layout": {"x": 1},
                "observedAt": None,
            },
            False,
        ),
        (
            "renderer_theme_changed",
            "renderer_theme",
            {"theme": {"mode": "dark"}},
            False,
        ),
        (
            "active_session_changed",
            "active_session",
            {"reason": "tracker_callback"},
            True,
        ),
    ]
