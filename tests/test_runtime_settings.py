from codex_usage_hud.config import UserConfig
from codex_usage_hud.runtime_settings import (
    background_usage_response_status,
    changed_config_keys,
    partial_domains_for_command,
)


def test_background_usage_response_correlates_request() -> None:
    status = background_usage_response_status(
        "detail", "request-1", payload={"id": "event-1"}, event_id="event-1"
    )

    assert status["backgroundUsageResponse"] == {
        "kind": "detail",
        "requestId": "request-1",
        "payload": {"id": "event-1"},
        "error": "",
        "eventId": "event-1",
    }


def test_partial_domains_follow_changed_settings() -> None:
    previous = UserConfig.defaults()
    current = UserConfig.from_dict(
        {**previous.to_dict(), "daily_budget_usd": previous.daily_budget_usd + 1}
    )

    assert "daily_budget_usd" in changed_config_keys(previous, current)
    assert partial_domains_for_command(
        {"action": "save"},
        previous_config=previous,
        current_config=current,
    ) == {"settings", "currentSession", "budget"}
