"""Strictly compare normalized pytest outcomes with the P0 failure manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "contracts" / "p0_confirmed_failures.json"
DEFAULT_FLAKY = ROOT / "tests" / "contracts" / "p0_flaky_candidates.json"
PHASE_ORDER = {f"P{number}": number for number in range(9)}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def fingerprint_sha256(value: dict[str, object]) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_failure(output: str) -> dict[str, object]:
    """Extract stable semantics while excluding paths, PIDs, times, and payloads."""
    if "IndexError: list index out of range" in output and "written_items[0]" in output:
        return {
            "exception": "IndexError",
            "expression": "written_items[0]",
            "message": "list index out of range",
        }
    mock_match = re.search(
        r"Expected '(?P<mock>[^']+)' to (?P<expect>not have been called|have been called once)\. Called (?P<actual>\d+) times?\.",
        output,
    )
    if mock_match:
        return {
            "actual_calls": int(mock_match.group("actual")),
            "exception": "AssertionError",
            "expected_calls": 0 if mock_match.group("expect").startswith("not") else 1,
            "mock": mock_match.group("mock"),
        }
    attempts_match = re.search(
        r"(?s)self\.assertGreaterEqual\(read_attempts, 2\).*?AssertionError: (?P<actual>\d+) not greater than or equal to 2",
        output,
    )
    if attempts_match:
        return {
            "actual": int(attempts_match.group("actual")),
            "counter": "read_attempts",
            "exception": "AssertionError",
            "expected_gte": 2,
        }
    exception = re.search(r"(?m)^E\s+(?P<type>[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):\s*(?P<message>.+)$", output)
    if exception:
        message = re.sub(r"\b\d{5,}\b", "[number]", exception.group("message"))
        return {
            "exception": exception.group("type"),
            "message": " ".join(message.split())[:240],
        }
    return {"exception": "UnrecognizedFailure", "message": "no stable fingerprint"}


def validate_manifest(manifest: dict[str, Any], *, phase: str) -> list[str]:
    errors: list[str] = []
    if manifest.get("schemaVersion") != 1:
        errors.append("manifest schemaVersion must be 1")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("baselineHead") or "")):
        errors.append("manifest baselineHead must be a full commit SHA")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return [*errors, "manifest entries must be a list"]
    seen: set[str] = set()
    for entry in entries:
        nodeid = str(entry.get("nodeid") or "")
        if not nodeid or nodeid in seen:
            errors.append(f"duplicate or empty manifest nodeid: {nodeid!r}")
        seen.add(nodeid)
        if entry.get("expectedOutcome") != "failed":
            errors.append(f"{nodeid}: expectedOutcome must be failed")
        for key in ("owner", "removalCondition"):
            if not str(entry.get(key) or "").strip():
                errors.append(f"{nodeid}: {key} is required")
        fingerprint = entry.get("fingerprint")
        if not isinstance(fingerprint, dict):
            errors.append(f"{nodeid}: fingerprint must be an object")
        elif fingerprint_sha256(fingerprint) != entry.get("fingerprintSha256"):
            errors.append(f"{nodeid}: manifest fingerprint hash mismatch")
        expiry = str(entry.get("expiresAtPhase") or "")
        if expiry not in PHASE_ORDER:
            errors.append(f"{nodeid}: invalid expiry {expiry!r}")
        elif PHASE_ORDER[phase] >= PHASE_ORDER[expiry]:
            errors.append(f"{nodeid}: entry expired at {expiry}")
    return errors


def validate_flaky_manifest(flaky: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if flaky.get("schemaVersion") != 1:
        errors.append("flaky manifest schemaVersion must be 1")
    entries = flaky.get("entries")
    if not isinstance(entries, list):
        return [*errors, "flaky manifest entries must be a list"]
    seen: set[str] = set()
    for entry in entries:
        nodeid = str(entry.get("nodeid") or "")
        if not nodeid or nodeid in seen:
            errors.append(f"duplicate or empty flaky nodeid: {nodeid!r}")
        seen.add(nodeid)
        if entry.get("releaseAllowlisted") is not False:
            errors.append(f"{nodeid}: flaky candidate is release allowlisted")
        for key in ("owner", "isolation", "removalCondition"):
            if not str(entry.get(key) or "").strip():
                errors.append(f"{nodeid}: {key} is required")
        outcomes = entry.get("observedOutcomes")
        if not isinstance(outcomes, list) or not outcomes:
            errors.append(f"{nodeid}: observedOutcomes must be a non-empty list")
        fingerprint = entry.get("failureFingerprint")
        if not isinstance(fingerprint, dict):
            errors.append(f"{nodeid}: failureFingerprint must be an object")
        elif fingerprint_sha256(fingerprint) != entry.get("fingerprintSha256"):
            errors.append(f"{nodeid}: flaky fingerprint hash mismatch")
    return errors


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compare(
    manifest: dict[str, Any], results: dict[str, Any], *, phase: str
) -> list[str]:
    errors = validate_manifest(manifest, phase=phase)
    expected = {entry["nodeid"]: entry for entry in manifest.get("entries", [])}
    actual_entries = results.get("results", [])
    actual = {entry.get("nodeid"): entry for entry in actual_entries}
    if len(actual) != len(actual_entries):
        errors.append("results contain duplicate or empty nodeids")

    for nodeid in sorted(set(actual) - set(expected)):
        outcome = actual[nodeid].get("outcome")
        if outcome not in {"passed", "skipped"}:
            errors.append(f"unexpected failure: {nodeid} ({outcome})")
    for nodeid, entry in expected.items():
        result = actual.get(nodeid)
        if result is None:
            errors.append(f"missing expected result: {nodeid}")
            continue
        if result.get("outcome") != "failed":
            errors.append(f"unexpected pass/outcome: {nodeid} ({result.get('outcome')})")
            continue
        if result.get("fingerprintSha256") != entry.get("fingerprintSha256"):
            errors.append(f"fingerprint mismatch: {nodeid}")
    return errors


def validate_capture_summary(
    manifest: dict[str, Any],
    flaky: dict[str, Any],
    summary: dict[str, Any],
    *,
    phase: str = "P0",
    artifact_root: Path | None = None,
) -> list[str]:
    errors = [*validate_manifest(manifest, phase=phase), *validate_flaky_manifest(flaky)]
    if summary.get("schemaVersion") != 1:
        errors.append("capture schemaVersion must be 1")
    if summary.get("head") != manifest.get("baselineHead"):
        errors.append("capture HEAD does not match manifest baselineHead")
    if not isinstance(summary.get("dirty"), bool):
        errors.append("capture dirty must be a boolean")
    requested_runs = summary.get("requestedRuns")
    if not isinstance(requested_runs, int) or requested_runs < 3:
        errors.append("capture requestedRuns must be an integer of at least 3")
    for key in (
        "gitStatusSha256",
        "trackedPatchSha256",
        "worktreeContentSha256",
        "worktreeContentManifest",
    ):
        if not summary.get(key):
            errors.append(f"capture missing provenance field: {key}")
    for artifact_key, hash_key in (
        ("gitStatusArtifact", "gitStatusSha256"),
        ("trackedPatchArtifact", "trackedPatchSha256"),
    ):
        relative = summary.get(artifact_key)
        if not isinstance(relative, str) or not relative:
            errors.append(f"capture missing provenance field: {artifact_key}")
        elif artifact_root is not None:
            path = artifact_root / relative
            if not path.is_file():
                errors.append(f"capture provenance artifact does not exist: {artifact_key}")
            elif _sha256(path) != summary.get(hash_key):
                errors.append(f"capture provenance artifact hash mismatch: {artifact_key}")
    manifest_path = summary.get("worktreeContentManifest")
    if artifact_root is not None and isinstance(manifest_path, str):
        content_path = artifact_root / manifest_path
        if not content_path.is_file():
            errors.append("worktree content manifest does not exist")
        else:
            try:
                content = json.loads(content_path.read_text(encoding="utf-8"))
                encoded = json.dumps(
                    content, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                if hashlib.sha256(encoded).hexdigest() != summary.get("worktreeContentSha256"):
                    errors.append("worktree content manifest hash mismatch")
            except (OSError, ValueError, TypeError):
                errors.append("worktree content manifest is invalid")

    confirmed_ids = {entry["nodeid"] for entry in manifest.get("entries", [])}
    flaky_entries = {entry["nodeid"]: entry for entry in flaky.get("entries", [])}
    expected_ids = confirmed_ids | set(flaky_entries)
    records = summary.get("records")
    if not isinstance(records, list):
        return [*errors, "capture records must be a list"]
    grouped: dict[str, list[dict[str, Any]]] = {"clean-head": [], "worktree": []}
    for record in records:
        source = record.get("sourceKind")
        if source not in grouped:
            errors.append(f"unexpected sourceKind: {source}")
            continue
        grouped[source].append(record)
        if record.get("head") != summary.get("head"):
            errors.append(f"{source}: record HEAD mismatch")
        if record.get("schemaVersion") != 1:
            errors.append(f"{source}: record schemaVersion must be 1")
        if source == "clean-head" and not record.get("archiveSha256"):
            errors.append("clean-head: archive SHA is required")
        origins = record.get("moduleOrigins")
        source_root = str(record.get("sourceRoot") or "")
        if not isinstance(origins, dict) or not all(origins.get(key) for key in ("package", "cli")):
            errors.append(f"{source}: package and cli module origins are required")
        elif not source_root:
            errors.append(f"{source}: sourceRoot is required")
        else:
            resolved_root = Path(source_root).resolve()
            for module, origin in origins.items():
                try:
                    Path(origin).resolve().relative_to(resolved_root)
                except ValueError:
                    errors.append(f"{source}: {module} origin is outside sourceRoot")
        environment = record.get("environment")
        if not isinstance(environment, dict) or not all(
            environment.get(key)
            for key in ("pythonVersion", "pytestVersion", "os", "architecture", "envOverrides")
        ):
            errors.append(f"{source}: environment metadata is required")
        commands = record.get("commands")
        if not isinstance(commands, list) or len(commands) != len(expected_ids) or not all(
            isinstance(command, list) and "pytest" in command and "-vv" in command
            for command in commands
        ):
            errors.append(f"{source}: exact pytest commands are required")
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, dict) or not all(
            artifacts.get(key)
            for key in ("stdout", "stderr", "stdoutSha256", "stderrSha256")
        ):
            errors.append(f"{source}: complete artifact metadata is required")
        elif artifact_root is not None:
            for name in ("stdout", "stderr"):
                path = artifact_root / artifacts[name]
                if not path.is_file():
                    errors.append(f"{source}: {name} artifact does not exist")
                elif _sha256(path) != artifacts[f"{name}Sha256"]:
                    errors.append(f"{source}: {name} artifact hash mismatch")
        actual = record.get("results", [])
        actual_ids = {entry.get("nodeid") for entry in actual}
        if actual_ids != expected_ids or len(actual) != len(expected_ids):
            errors.append(f"{source} run-{record.get('runIndex')}: selected node set mismatch")
        errors.extend(
            f"{source} run-{record.get('runIndex')}: {error}"
            for error in compare(manifest, record, phase=phase)
        )
        by_id = {entry.get("nodeid"): entry for entry in actual}
        expected_exit_codes = [1] * len(confirmed_ids) + [0] * len(flaky_entries)
        if record.get("exitCodes") != expected_exit_codes:
            errors.append(f"{source} run-{record.get('runIndex')}: exit code matrix mismatch")
        for result in actual:
            fingerprint = result.get("fingerprint")
            if isinstance(fingerprint, dict) and fingerprint.get("exception") == "UnrecognizedFailure":
                errors.append(f"{source} run-{record.get('runIndex')}: unrecognized failure fingerprint")
        for nodeid, entry in flaky_entries.items():
            outcome = by_id.get(nodeid, {}).get("outcome")
            if outcome != "passed":
                errors.append(f"{source} run-{record.get('runIndex')}: flaky candidate {outcome}")
    for source, source_records in grouped.items():
        if isinstance(requested_runs, int) and len(source_records) != requested_runs:
            errors.append(f"{source}: requires exactly {requested_runs} recorded runs")
        indices = [record.get("runIndex") for record in source_records]
        if isinstance(requested_runs, int) and set(indices) != set(range(1, requested_runs + 1)):
            errors.append(f"{source}: runIndex set must be 1..{requested_runs}")
    clean_archives = {record.get("archiveSha256") for record in grouped["clean-head"]}
    if len(clean_archives) > 1:
        errors.append("clean-head: archive SHA changed across runs")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--flaky", type=Path, default=DEFAULT_FLAKY)
    parser.add_argument("--phase", choices=sorted(PHASE_ORDER), default="P0")
    args = parser.parse_args()
    manifest = _load(args.manifest)
    payload = _load(args.results)
    if not isinstance(payload.get("records"), list):
        print("formal P0 gate requires a capture summary with records")
        return 1
    errors = validate_capture_summary(
        manifest,
        _load(args.flaky),
        payload,
        phase=args.phase,
        artifact_root=args.results.resolve().parent,
    )
    if errors:
        for error in errors:
            print(error)
        return 1
    print("P0 baseline matches exactly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
