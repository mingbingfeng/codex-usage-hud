"""Focused tests for the active-session provider payload plumbing."""

from __future__ import annotations

import unittest

from codex_usage_hud.core.parser import ParsedSession
from codex_usage_hud.renderer_payload_builder import (
    payload_from_snapshot,
    session_switch_payload_from_snapshot,
)


class ActiveSessionProviderPayloadTests(unittest.TestCase):
    def test_payload_from_snapshot_propagates_session_provider(self) -> None:
        payload = payload_from_snapshot(
            ParsedSession(status="waiting", model_provider="168661"),
        ).to_json()

        self.assertEqual(payload["activeSessionProvider"], "168661")
        domains = payload["payloadDomains"]
        self.assertEqual(domains["settings"]["activeSessionProvider"], "168661")
        self.assertEqual(domains["sessionSwitch"]["activeSessionProvider"], "168661")
        self.assertEqual(domains["currentSession"]["activeSessionProvider"], "168661")

    def test_unknown_provider_falls_back_to_empty_for_renderer_default(self) -> None:
        payload = payload_from_snapshot(ParsedSession(status="waiting")).to_json()

        self.assertEqual(payload["activeSessionProvider"], "")

    def test_session_switch_partial_carries_provider(self) -> None:
        payload = session_switch_payload_from_snapshot(
            ParsedSession(status="waiting", model_provider="168661"),
        )

        self.assertEqual(payload["activeSessionProvider"], "168661")
        self.assertEqual(
            payload["payloadDomains"]["sessionSwitch"]["activeSessionProvider"],
            "168661",
        )


if __name__ == "__main__":
    unittest.main()
