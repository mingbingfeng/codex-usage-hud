from __future__ import annotations

from codex_usage_hud.renderer_client import RendererHudClient


def _client() -> RendererHudClient:
    client = object.__new__(RendererHudClient)
    client._payload_domain_digests = {}
    client._payload_extras_digest = None
    client._payload_digest_target_id = ""
    return client


def test_payload_domain_digest_skips_identical_successful_update() -> None:
    client = _client()
    payload = {
        "global": "kept",
        "topLine": "A",
        "settingsValue": 1,
        "payloadDomains": {
            "currentSession": {"topLine": "A"},
            "settings": {"settingsValue": 1},
        },
    }

    first, digests, skipped, extras = client._prepare_payload_domain_delta(payload)
    assert first == payload
    assert set(digests) == {"currentSession", "settings"}
    assert skipped == 0
    client._payload_domain_digests.update(digests)
    client._payload_extras_digest = extras

    second, digests, skipped, _ = client._prepare_payload_domain_delta(payload)
    assert second is None
    assert digests == {}
    assert skipped == 2


def test_payload_domain_digest_keeps_only_changed_domain_aliases() -> None:
    client = _client()
    initial = {
        "global": "kept",
        "topLine": "A",
        "settingsValue": 1,
        "payloadDomains": {
            "currentSession": {"topLine": "A"},
            "settings": {"settingsValue": 1},
        },
    }
    _, digests, _, extras = client._prepare_payload_domain_delta(initial)
    client._payload_domain_digests.update(digests)
    client._payload_extras_digest = extras

    changed = {
        **initial,
        "topLine": "B",
        "payloadDomains": {
            "currentSession": {"topLine": "B"},
            "settings": {"settingsValue": 1},
        },
    }
    reduced, digests, skipped, _ = client._prepare_payload_domain_delta(changed)

    assert reduced is not None
    assert reduced["payloadDomains"] == {
        "currentSession": {"topLine": "B"},
    }
    assert reduced["topLine"] == "B"
    assert "settingsValue" not in reduced
    assert reduced["global"] == "kept"
    assert set(digests) == {"currentSession"}
    assert skipped == 1


def test_payload_domain_digest_does_not_skip_changed_non_domain_state() -> None:
    client = _client()
    payload = {
        "debug": False,
        "payloadDomains": {"settings": {"value": 1}},
    }
    _, digests, _, extras = client._prepare_payload_domain_delta(payload)
    client._payload_domain_digests.update(digests)
    client._payload_extras_digest = extras

    changed = {"debug": True, "payloadDomains": {"settings": {"value": 1}}}
    reduced, digests, skipped, _ = client._prepare_payload_domain_delta(changed)
    assert reduced == changed
    assert set(digests) == {"settings"}
    assert skipped == 0
