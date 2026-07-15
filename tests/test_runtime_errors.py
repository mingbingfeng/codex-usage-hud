"""Tests for renderer runtime error diagnostics."""

from __future__ import annotations

import unittest

from codex_usage_hud.core.runtime_events import RuntimeEventBus
from codex_usage_hud.core.runtime_errors import RuntimeErrorRegistry


class RuntimeEventBusTests(unittest.TestCase):
    def test_event_payload_includes_source_timestamp_session_and_error_context(self) -> None:
        bus = RuntimeEventBus(clock=lambda: 42.0)

        event = bus.publish(
            "runtime_error",
            source="cdp",
            session="C:/sessions/current.jsonl",
            context={"action": "recorded"},
            error={"code": "cdp.update_failed", "message": "CDP update failed"},
        )

        self.assertEqual(
            event.to_payload(),
            {
                "type": "runtime_error",
                "source": "cdp",
                "timestamp": 42.0,
                "session": "C:/sessions/current.jsonl",
                "context": {"action": "recorded"},
                "error": {
                    "code": "cdp.update_failed",
                    "message": "CDP update failed",
                },
            },
        )


class RuntimeErrorRegistryTests(unittest.TestCase):
    def test_record_merges_same_source_and_code_with_last_context(self) -> None:
        registry = RuntimeErrorRegistry(clock=lambda: 10.0)

        first = registry.record(
            source="active_session",
            code="unmatched_thread",
            message="Renderer thread could not be mapped",
            context={"threadId": "thread-a"},
        )
        registry.clock = lambda: 12.5
        second = registry.record(
            source="active_session",
            code="unmatched_thread",
            message="Renderer thread could not be mapped",
            severity="error",
            context={"threadId": "thread-b"},
        )

        self.assertIs(first, second)
        payload = registry.to_payload()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["source"], "active_session")
        self.assertEqual(payload[0]["code"], "active_session.unmatched_thread")
        self.assertEqual(payload[0]["severity"], "error")
        self.assertEqual(payload[0]["message"], "Renderer thread could not be mapped")
        self.assertEqual(payload[0]["context"], {"threadId": "thread-b"})
        self.assertEqual(payload[0]["count"], 2)
        self.assertEqual(payload[0]["firstSeenAt"], 10.0)
        self.assertEqual(payload[0]["lastSeenAt"], 12.5)

    def test_resolve_removes_matching_error(self) -> None:
        registry = RuntimeErrorRegistry(clock=lambda: 1.0)
        registry.record(source="cdp", code="target_lost", message="Target lost")
        registry.resolve(source="cdp", code="target_lost")

        self.assertEqual(registry.to_payload(), [])

    def test_record_publishes_runtime_error_event(self) -> None:
        bus = RuntimeEventBus(clock=lambda: 20.0)
        events = []
        bus.subscribe(events.append)
        registry = RuntimeErrorRegistry(clock=lambda: 10.0, event_bus=bus)

        registry.record(
            source="active_session",
            code="unmatched_thread",
            message="Renderer thread could not be mapped",
            context={"sessionPath": "C:/sessions/one.jsonl", "threadId": "thread-a"},
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.type, "runtime_error")
        self.assertEqual(event.source, "active_session")
        self.assertEqual(event.timestamp, 10.0)
        self.assertEqual(event.session, "C:/sessions/one.jsonl")
        self.assertEqual(event.context["action"], "recorded")
        self.assertEqual(event.context["error"]["code"], "active_session.unmatched_thread")
        self.assertEqual(event.context["error"]["context"]["threadId"], "thread-a")

    def test_repeated_same_error_aggregates_without_second_wakeup(self) -> None:
        bus = RuntimeEventBus(clock=lambda: 20.0)
        events = []
        bus.subscribe(events.append)
        registry = RuntimeErrorRegistry(clock=lambda: 10.0, event_bus=bus)

        registry.record(
            source="cdp",
            code="update_failed",
            message="Update failed",
            context={"failures": 1},
        )
        registry.clock = lambda: 11.0
        registry.record(
            source="cdp",
            code="update_failed",
            message="Update failed",
            context={"failures": 2},
        )

        self.assertEqual(len(events), 1)
        payload = registry.to_payload()
        self.assertEqual(payload[0]["count"], 2)
        self.assertEqual(payload[0]["context"], {"failures": 2})

    def test_resolve_publishes_runtime_error_resolved_event(self) -> None:
        bus = RuntimeEventBus(clock=lambda: 20.0)
        events = []
        bus.subscribe(events.append)
        registry = RuntimeErrorRegistry(clock=lambda: 10.0, event_bus=bus)
        registry.record(
            source="cdp",
            code="update_failed",
            message="Update failed",
            context={"sessionId": "session-a"},
        )
        events.clear()
        registry.clock = lambda: 15.0

        registry.resolve(source="cdp", code="update_failed")

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.type, "runtime_error")
        self.assertEqual(event.source, "cdp")
        self.assertEqual(event.timestamp, 15.0)
        self.assertEqual(event.session, "session-a")
        self.assertEqual(event.context["action"], "resolved")
        self.assertEqual(event.context["code"], "cdp.update_failed")

    def test_record_and_resolve_call_diagnostic_callback(self) -> None:
        diagnostics = []
        registry = RuntimeErrorRegistry(
            clock=lambda: 10.0,
            diagnostic_callback=lambda action, event: diagnostics.append(
                (action, event.to_payload())
            ),
        )

        registry.record(
            source="cdp",
            code="update_failed",
            message="Update failed",
            context={"sessionId": "session-a"},
        )
        registry.resolve(source="cdp", code="update_failed")

        self.assertEqual([entry[0] for entry in diagnostics], ["recorded", "resolved"])
        self.assertEqual(diagnostics[0][1]["code"], "cdp.update_failed")
        self.assertEqual(diagnostics[0][1]["context"], {"sessionId": "session-a"})
        self.assertEqual(diagnostics[1][1]["code"], "cdp.update_failed")


if __name__ == "__main__":
    unittest.main()
