import json
from pathlib import Path
import subprocess
import sys

from tools.check_p0_baseline import (
    compare,
    fingerprint_sha256,
    normalize_failure,
    validate_capture_summary,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (ROOT / "tests/contracts/p0_confirmed_failures.json").read_text(encoding="utf-8")
)
INDEX_FAILURE = {
    "nodeid": "tests/test_example.py::test_index_failure",
    "owner": "overlay",
    "expectedOutcome": "failed",
    "fingerprint": {
        "exception": "IndexError",
        "expression": "written_items[0]",
        "message": "list index out of range",
    },
    "expiresAtPhase": "P2",
    "removalCondition": "synthetic gate fixture",
}
INDEX_FAILURE["fingerprintSha256"] = fingerprint_sha256(
    INDEX_FAILURE["fingerprint"]
)
GATE_MANIFEST = {
    "schemaVersion": 1,
    "baselineHead": MANIFEST["baselineHead"],
    "entries": [
        INDEX_FAILURE,
        {
            **INDEX_FAILURE,
            "nodeid": "tests/test_example.py::test_second_failure",
        },
    ],
}
GATE_FLAKY = {
    "schemaVersion": 1,
    "entries": [
        {
            "nodeid": "tests/test_example.py::test_timing_candidate",
            "owner": "qt-helper",
            "observedOutcomes": ["failed", "passed"],
            "failureFingerprint": {"exception": "AssertionError"},
            "fingerprintSha256": fingerprint_sha256(
                {"exception": "AssertionError"}
            ),
            "releaseAllowlisted": False,
            "isolation": "fresh pytest subprocess",
            "removalCondition": "synthetic gate fixture",
        }
    ],
}


def _matching_results() -> dict[str, object]:
    return {
        "results": [
            {
                "nodeid": entry["nodeid"],
                "outcome": "failed",
                "fingerprintSha256": entry["fingerprintSha256"],
            }
            for entry in MANIFEST["entries"]
        ]
    }


def test_manifest_hashes_are_canonical_and_not_expired_in_p0() -> None:
    assert not validate_manifest(MANIFEST, phase="P0")
    for entry in MANIFEST["entries"]:
        assert fingerprint_sha256(entry["fingerprint"]) == entry["fingerprintSha256"]


def test_gate_accepts_only_exact_expected_failures() -> None:
    assert not compare(MANIFEST, _matching_results(), phase="P0")


def test_gate_blocks_xpass_new_failure_fingerprint_drift_and_expiry() -> None:
    results = {
        "results": [
            {
                "nodeid": entry["nodeid"],
                "outcome": "failed",
                "fingerprintSha256": entry["fingerprintSha256"],
            }
            for entry in GATE_MANIFEST["entries"]
        ]
    }
    results["results"][0]["outcome"] = "passed"
    results["results"][1]["fingerprintSha256"] = "changed"
    results["results"].append(
        {"nodeid": "tests/test_new.py::test_failure", "outcome": "failed"}
    )
    errors = compare(GATE_MANIFEST, results, phase="P2")

    assert any("unexpected pass" in error for error in errors)
    assert any("fingerprint mismatch" in error for error in errors)
    assert any("unexpected failure" in error for error in errors)
    assert any("expired" in error for error in errors)


def test_capture_summary_records_can_each_be_compared() -> None:
    results = _matching_results()
    summary = {"records": [{"sourceKind": "clean-head", "runIndex": 1, **results}]}
    assert not compare(MANIFEST, summary["records"][0], phase="P0")


def test_capture_summary_requires_both_sources_and_three_runs() -> None:
    flaky = json.loads(
        (ROOT / "tests/contracts/p0_flaky_candidates.json").read_text(encoding="utf-8")
    )
    errors = validate_capture_summary(
        GATE_MANIFEST,
        flaky,
        {
            "schemaVersion": 1,
            "requestedRuns": 3,
            "head": MANIFEST["baselineHead"],
            "dirty": True,
            "gitStatusSha256": "status",
            "trackedPatchSha256": "patch",
            "worktreeContentSha256": "tree",
            "worktreeContentManifest": "worktree-content.json",
            "gitStatusArtifact": "git-status.porcelain",
            "trackedPatchArtifact": "tracked.patch",
            "records": [],
        },
    )
    assert "clean-head: requires exactly 3 recorded runs" in errors
    assert "worktree: requires exactly 3 recorded runs" in errors


def test_formal_cli_rejects_single_record_results(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    path.write_text(json.dumps(_matching_results()), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "tools/check_p0_baseline.py", "--results", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "requires a capture summary" in result.stdout


def test_summary_phase_is_applied_to_manifest_expiry() -> None:
    flaky = json.loads(
        (ROOT / "tests/contracts/p0_flaky_candidates.json").read_text(encoding="utf-8")
    )
    errors = validate_capture_summary(
        GATE_MANIFEST,
        flaky,
        {
            "schemaVersion": 1,
            "requestedRuns": 3,
            "head": MANIFEST["baselineHead"],
            "dirty": True,
            "gitStatusSha256": "status",
            "trackedPatchSha256": "patch",
            "worktreeContentSha256": "tree",
            "worktreeContentManifest": "worktree-content.json",
            "gitStatusArtifact": "git-status.porcelain",
            "trackedPatchArtifact": "tracked.patch",
            "records": [],
        },
        phase="P2",
    )
    assert any("expired at P2" in error for error in errors)


def test_summary_rejects_noncanonical_run_indices_and_incomplete_metadata() -> None:
    records = [
        {
            "schemaVersion": 999,
            "sourceKind": source,
            "runIndex": index,
            "head": MANIFEST["baselineHead"],
            "archiveSha256": "archive" if source == "clean-head" else None,
            "sourceRoot": "C:/expected",
            "moduleOrigins": {"package": "C:/outside/pkg.py", "cli": "C:/outside/cli.py"},
            "commands": [],
            "environment": {},
            "artifacts": {},
            "exitCodes": [],
            "results": [],
        }
        for source in ("clean-head", "worktree")
        for index in (4, 8, 99)
    ]
    errors = validate_capture_summary(
        GATE_MANIFEST,
        GATE_FLAKY,
        {
            "schemaVersion": 1,
            "requestedRuns": 3,
            "head": MANIFEST["baselineHead"],
            "dirty": True,
            "gitStatusSha256": "status",
            "trackedPatchSha256": "patch",
            "worktreeContentSha256": "tree",
            "worktreeContentManifest": "worktree-content.json",
            "gitStatusArtifact": "git-status.porcelain",
            "trackedPatchArtifact": "tracked.patch",
            "records": records,
        },
    )
    assert any("runIndex set must be 1..3" in error for error in errors)
    assert any("record schemaVersion must be 1" in error for error in errors)
    assert any("origin is outside sourceRoot" in error for error in errors)
    assert any("exact pytest commands are required" in error for error in errors)


def test_flaky_candidate_is_never_release_allowlisted() -> None:
    flaky = json.loads(
        (ROOT / "tests/contracts/p0_flaky_candidates.json").read_text(encoding="utf-8")
    )
    assert flaky["entries"] == []
    assert all(
        entry["releaseAllowlisted"] is False for entry in GATE_FLAKY["entries"]
    )


def test_failure_normalization_ignores_volatile_mock_payload() -> None:
    output = """E AssertionError: Expected 'write_json_object' to have been called once. Called 2 times.
Calls: [call('C:/Temp/work-overlay-123-999.json', {'updatedAt': 9999999999999})]
"""
    assert normalize_failure(output) == {
        "actual_calls": 2,
        "exception": "AssertionError",
        "expected_calls": 1,
        "mock": "write_json_object",
    }


def test_failure_normalization_reads_index_error_from_actual_traceback() -> None:
    output = "written_items[0]\nE IndexError: list index out of range"
    assert fingerprint_sha256(normalize_failure(output)) == INDEX_FAILURE[
        "fingerprintSha256"
    ]
