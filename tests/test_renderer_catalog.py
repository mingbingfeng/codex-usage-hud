from __future__ import annotations

import json
from pathlib import Path

from codex_usage_hud import renderer_catalog
from codex_usage_hud.ui import renderer_domains


def test_catalog_model_normalizes_snake_case_contract() -> None:
    model = renderer_catalog.normalize_catalog_model(
        {
            "slug": "gpt-test",
            "display_name": "GPT Test",
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {"effort": "low", "description": "Fast"},
                {"effort": "high", "description": "Deep"},
            ],
            "input_modalities": ["text", "image"],
            "priority": 5,
        }
    )

    assert model == {
        "model": "gpt-test",
        "displayName": "GPT Test",
        "description": "",
        "defaultReasoningEffort": "high",
        "supportedReasoningEfforts": [
            {"reasoningEffort": "low", "description": "Fast"},
            {"reasoningEffort": "high", "description": "Deep"},
        ],
        "inputModalities": ["text", "image"],
        "priority": 5,
    }


def test_catalog_payload_uses_first_source_and_priority_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "model-b", "priority": 20},
                    {"slug": "model-a", "display_name": "First", "priority": 10},
                ]
            }
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "model-a", "display_name": "Second", "priority": 1},
                    {"slug": "hidden", "visibility": "hide"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        renderer_catalog,
        "model_catalog_candidate_paths",
        lambda: [first, second],
    )

    payload = renderer_catalog.model_catalog_payload()

    assert [model["model"] for model in payload] == ["model-a", "model-b"]
    assert payload[0]["displayName"] == "First"
    assert all("priority" not in model for model in payload)


def test_catalog_boot_script_and_compatibility_wrapper_match() -> None:
    catalog = [{"model": "gpt-test", "displayName": "GPT Test"}]

    owner_script = renderer_catalog.renderer_hud_script_with_model_catalog(catalog)
    compatibility_script = renderer_domains._renderer_hud_script_with_model_catalog(
        catalog
    )

    assert owner_script == compatibility_script
    assert "__CODEX_MODEL_PICKER_CATALOG__" not in owner_script
    assert '"model":"gpt-test"' in owner_script
    assert renderer_domains.RENDERER_HUD_SCRIPT == renderer_catalog.RENDERER_HUD_SCRIPT
