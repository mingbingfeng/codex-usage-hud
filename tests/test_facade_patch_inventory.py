from pathlib import Path

from tools.check_facade_patch_inventory import compare, extract


def test_facade_patch_extractor_tracks_patch_object_and_rejects_dynamic_targets(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_example.py").write_text(
        """
import codex_usage_hud.cli as cli_module
from unittest.mock import patch

patch.object(cli_module, "build_snapshot")
patch("codex_usage_hud.cli." + "build_runtime_context")
""",
        encoding="utf-8",
    )

    entries, errors = extract(tmp_path)

    assert [(entry["path"], entry["referenceCount"]) for entry in entries] == [
        ("codex_usage_hud.cli.build_snapshot", 1)
    ]
    assert errors == ["dynamic facade patch target: tests/test_example.py:6"]


def test_facade_inventory_rejects_owner_metadata_drift() -> None:
    inventory = {
        "schemaVersion": 1,
        "totalReferences": 1,
        "entries": [
            {
                "path": "codex_usage_hud.cli.build_snapshot",
                "referenceCount": 1,
                "terminalOwner": "snapshot_builder",
                "targetPhase": "P3",
                "classification": "migrate-to-owner",
                "removalCondition": (
                    "Tests import or patch the snapshot_builder owner directly"
                ),
            }
        ],
    }
    actual = [
        {
            "path": "codex_usage_hud.cli.build_snapshot",
            "referenceCount": 1,
            "terminalOwner": "runtime_context",
            "targetPhase": "P3",
            "classification": "migrate-to-owner",
            "removalCondition": (
                "Tests import or patch the runtime_context owner directly"
            ),
        }
    ]

    errors = compare(inventory, actual)

    assert errors == [
        "facade patch metadata changed: codex_usage_hud.cli.build_snapshot "
        "terminalOwner 'snapshot_builder' -> 'runtime_context'",
        "facade patch metadata changed: codex_usage_hud.cli.build_snapshot "
        "removalCondition 'Tests import or patch the snapshot_builder owner directly' "
        "-> 'Tests import or patch the runtime_context owner directly'",
    ]
