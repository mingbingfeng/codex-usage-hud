from __future__ import annotations

import pytest

from codex_usage_hud.core.parser import ParsedSession
from codex_usage_hud import renderer_client, renderer_payload_builder
from codex_usage_hud.ui import renderer_domains


def test_renderer_domains_exports_the_client_owner() -> None:
    assert renderer_domains.RendererHudClient is renderer_client.RendererHudClient


def test_renderer_domains_exports_the_payload_builder_owner() -> None:
    assert (
        renderer_domains.payload_from_snapshot
        is renderer_payload_builder.payload_from_snapshot
    )


def test_renderer_domains_rejects_unknown_dynamic_owner_name() -> None:
    with pytest.raises(AttributeError):
        getattr(renderer_domains, "_unsupported_payload_builder_symbol")


def test_client_resolves_transport_from_its_owner(monkeypatch) -> None:
    client = renderer_client.RendererHudClient(
        port=9229,
        timeout_seconds=0.05,
        enabled=True,
    )
    client._active_session_binding = None
    calls: list[tuple[str, str]] = []

    def fake_send(
        websocket_url: str,
        method: str,
        params: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        del params, timeout_seconds
        calls.append((websocket_url, method))
        return {"result": {"result": {"value": {"ok": True, "applyMs": 1.0}}}}

    monkeypatch.setattr(renderer_client, "send_cdp_command", fake_send)

    assert client._send_update(  # noqa: SLF001
        "ws://127.0.0.1:9229/devtools/page/1",
        {"topLine": "A"},
    )
    assert calls == [
        ("ws://127.0.0.1:9229/devtools/page/1", "Runtime.evaluate")
    ]


def test_client_uses_its_payload_and_support_owners(monkeypatch) -> None:
    client = renderer_client.RendererHudClient(enabled=True)
    client._theme_snapshot = object()  # noqa: SLF001
    client.update_payload = lambda payload: payload == {"requestLine": "owner"}  # type: ignore[method-assign]
    captured: dict[str, object] = {}

    class FakePayload:
        def to_json(self) -> dict[str, object]:
            return {"requestLine": "owner"}

    def fake_builder(snapshot: ParsedSession, **kwargs: object) -> FakePayload:
        captured["snapshot"] = snapshot
        captured.update(kwargs)
        return FakePayload()

    monkeypatch.setattr(renderer_client, "support_qr_payload", lambda: [])
    monkeypatch.setattr(renderer_client, "_renderer_theme_payload", lambda _: {})
    monkeypatch.setattr(renderer_client, "payload_from_snapshot", fake_builder)

    snapshot = ParsedSession(status="waiting")
    assert client.update(snapshot)
    assert captured["snapshot"] is snapshot
    assert captured["support_images"] == []
    assert captured["settings_path"] is None


def test_session_lock_blocks_startup_retry_before_any_cdp_work(monkeypatch) -> None:
    client = renderer_client.RendererHudClient(enabled=True)
    client.suspend_for_session_lock()
    attempted: list[str] = []
    monkeypatch.setattr(
        client,
        "_update_payload_once",
        lambda *_args, **_kwargs: attempted.append("cdp") or True,
    )

    assert not client.update_payload({"payloadDomains": {}}, startup_retry=True)
    assert attempted == []
    assert client.last_status == "session-locked"


def test_session_unlock_clears_stale_cdp_state_once(monkeypatch) -> None:
    client = renderer_client.RendererHudClient(enabled=True)
    calls: list[bool] = []
    monkeypatch.setattr(
        client,
        "_clear_target_cache",
        lambda *, clear_script: calls.append(clear_script),
    )

    client.suspend_for_session_lock()
    client.resume_after_session_unlock()
    client.resume_after_session_unlock()

    assert calls == [True]
