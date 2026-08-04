"""Capture reproducible clean-HEAD and working-tree P0 focused baselines."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import locale
import os
import platform
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import zipfile

from check_p0_baseline import fingerprint_sha256, normalize_failure


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/contracts/p0_confirmed_failures.json"
FLAKY = ROOT / "tests/contracts/p0_flaky_candidates.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str, binary: bool = False) -> bytes | str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=not binary
    )


def _environment() -> dict[str, object]:
    try:
        pyside = importlib.metadata.version("PySide6")
    except importlib.metadata.PackageNotFoundError:
        pyside = None
    return {
        "pythonVersion": platform.python_version(),
        "implementation": platform.python_implementation(),
        "pytestVersion": importlib.metadata.version("pytest"),
        "pysideVersion": pyside,
        "os": platform.platform(),
        "architecture": platform.machine(),
        "locale": locale.getlocale(),
        "preferredEncoding": locale.getpreferredencoding(False),
        "timezone": time.tzname,
        "envOverrides": {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONUTF8": "1",
            "QT_QPA_PLATFORM": "offscreen",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONNOUSERSITE": "1",
        },
    }


def _focused_nodeids() -> tuple[list[dict[str, object]], list[str]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    flaky = json.loads(FLAKY.read_text(encoding="utf-8"))
    entries = manifest["entries"]
    nodes = [entry["nodeid"] for entry in entries]
    nodes.extend(entry["nodeid"] for entry in flaky["entries"])
    return entries, nodes


def _result_from_output(output: str, nodeid: str) -> dict[str, object]:
    match = re.search(
        rf"(?m)^{re.escape(nodeid)}\s+(PASSED|FAILED|SKIPPED)\b", output
    )
    outcome = match.group(1).lower() if match else "missing"
    result: dict[str, object] = {"nodeid": nodeid, "outcome": outcome}
    if outcome == "failed":
        fingerprint = normalize_failure(output)
        result["fingerprint"] = fingerprint
        result["fingerprintSha256"] = fingerprint_sha256(fingerprint)
    return result


def _capture_run(
    source: Path,
    *,
    source_kind: str,
    run_index: int,
    output_dir: Path,
    head: str,
    archive_sha: str | None,
    nodeids: list[str],
) -> dict[str, object]:
    artifact_dir = output_dir / source_kind / f"run-{run_index}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = artifact_dir / "tmp"
    temp_dir.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(_environment()["envOverrides"])
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source.resolve()), str((source / "src").resolve())]
    )
    origin_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,codex_usage_hud,codex_usage_hud.cli; "
            "print(json.dumps({'package':codex_usage_hud.__file__,'cli':codex_usage_hud.cli.__file__}))",
        ],
        cwd=source,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    module_origins = json.loads(origin_probe.stdout)
    expected_root = source.resolve()
    for module, origin in module_origins.items():
        try:
            Path(origin).resolve().relative_to(expected_root)
        except ValueError as exc:
            raise RuntimeError(
                f"baseline imported {module} outside source: {module_origins}"
            ) from exc
    started = time.time()
    results: list[dict[str, object]] = []
    commands: list[list[str]] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    exit_codes: list[int] = []
    for index, nodeid in enumerate(nodeids):
        command = [
            sys.executable,
            "-m",
            "pytest",
            "--rootdir",
            ".",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(temp_dir / str(index)),
            nodeid,
            "-vv",
            "--tb=short",
        ]
        result = subprocess.run(
            command,
            cwd=source,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        combined = result.stdout + result.stderr
        commands.append(command)
        exit_codes.append(result.returncode)
        stdout_parts.append(result.stdout)
        stderr_parts.append(result.stderr)
        results.append(_result_from_output(combined, nodeid))
    duration_ms = round((time.time() - started) * 1000)
    stdout_path = artifact_dir / "stdout.txt"
    stderr_path = artifact_dir / "stderr.txt"
    stdout_path.write_text("\n".join(stdout_parts), encoding="utf-8")
    stderr_path.write_text("\n".join(stderr_parts), encoding="utf-8")
    return {
        "schemaVersion": 1,
        "sourceKind": source_kind,
        "sourceRoot": str(source.resolve()),
        "head": head,
        "archiveSha256": archive_sha,
        "runIndex": run_index,
        "commands": commands,
        "cwd": "." if source_kind == "clean-head" else str(ROOT),
        "startedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "durationMs": duration_ms,
        "exitCodes": exit_codes,
        "environment": _environment(),
        "moduleOrigins": module_origins,
        "artifacts": {
            "stdout": stdout_path.relative_to(output_dir).as_posix(),
            "stderr": stderr_path.relative_to(output_dir).as_posix(),
            "stdoutSha256": _sha256(stdout_path),
            "stderrSha256": _sha256(stderr_path),
        },
        "results": results,
    }


def _index_modes() -> dict[str, str]:
    output = subprocess.check_output(
        ["git", "ls-files", "--stage", "-z"], cwd=ROOT
    )
    modes: dict[str, str] = {}
    for raw in output.split(b"\0"):
        if not raw:
            continue
        metadata, relative = raw.split(b"\t", 1)
        modes[os.fsdecode(relative)] = metadata.split(b" ", 1)[0].decode("ascii")
    return modes


def _worktree_content_manifest() -> tuple[list[dict[str, str]], str]:
    paths = subprocess.check_output(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT
    ).split(b"\0")
    entries: list[dict[str, str]] = []
    index_modes = _index_modes()
    for raw in paths:
        if not raw:
            continue
        relative = os.fsdecode(raw)
        path = ROOT / relative
        normalized = Path(relative).as_posix()
        if path.is_symlink():
            entries.append(
                {
                    "path": normalized,
                    "kind": "symlink",
                    "mode": index_modes.get(relative, "120000"),
                    "target": os.readlink(path),
                }
            )
        elif path.is_file():
            entries.append(
                {
                    "path": normalized,
                    "kind": "file",
                    "mode": index_modes.get(relative, "100755" if os.access(path, os.X_OK) else "100644"),
                    "sha256": _sha256(path),
                }
            )
    entries.sort(key=lambda entry: entry["path"])
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return entries, hashlib.sha256(encoded).hexdigest()


def _worktree_signature() -> dict[str, object]:
    files, content_sha = _worktree_content_manifest()
    return {
        "head": str(_git("rev-parse", "HEAD")).strip(),
        "gitStatusSha256": hashlib.sha256(
            _git("status", "--porcelain=v1", binary=True)
        ).hexdigest(),
        "trackedPatchSha256": hashlib.sha256(
            _git("diff", "--binary", "HEAD", binary=True)
        ).hexdigest(),
        "worktreeContentSha256": content_sha,
        "worktreeFiles": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/p0_baseline")
    args = parser.parse_args()
    if args.runs < 3:
        raise SystemExit("--runs must be at least 3 for a formal P0 baseline")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    before = _worktree_signature()
    head = str(before["head"])
    _entries, nodeids = _focused_nodeids()

    with tempfile.TemporaryDirectory(prefix="codex-hud-p0-") as temp:
        temp_root = Path(temp)
        archive = temp_root / "head.zip"
        with archive.open("wb") as stream:
            subprocess.run(
                ["git", "archive", "--format=zip", "HEAD"],
                cwd=ROOT,
                stdout=stream,
                check=True,
            )
        clean_source = temp_root / "source"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(clean_source)
        records = []
        for source_kind, source, archive_sha in (
            ("clean-head", clean_source, _sha256(archive)),
            ("worktree", ROOT, None),
        ):
            for run_index in range(1, args.runs + 1):
                records.append(
                    _capture_run(
                        source,
                        source_kind=source_kind,
                        run_index=run_index,
                        output_dir=output_dir,
                        head=head,
                        archive_sha=archive_sha,
                        nodeids=nodeids,
                    )
                )
    after = _worktree_signature()
    comparable_keys = {
        "head",
        "gitStatusSha256",
        "trackedPatchSha256",
        "worktreeContentSha256",
    }
    if any(before[key] != after[key] for key in comparable_keys):
        raise RuntimeError("worktree changed during baseline capture; discard artifacts")
    worktree_files = before.pop("worktreeFiles")
    (output_dir / "worktree-content.json").write_text(
        json.dumps(worktree_files, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    status_path = output_dir / "git-status.porcelain"
    status_path.write_bytes(_git("status", "--porcelain=v1", binary=True))
    patch_path = output_dir / "tracked.patch"
    patch_path.write_bytes(_git("diff", "--binary", "HEAD", binary=True))
    summary = {
        "schemaVersion": 1,
        "requestedRuns": args.runs,
        "head": head,
        "dirty": bool(str(_git("status", "--porcelain=v1")).strip()),
        "gitStatusSha256": before["gitStatusSha256"],
        "trackedPatchSha256": before["trackedPatchSha256"],
        "worktreeContentSha256": before["worktreeContentSha256"],
        "worktreeContentManifest": "worktree-content.json",
        "gitStatusArtifact": "git-status.porcelain",
        "trackedPatchArtifact": "tracked.patch",
        "records": records,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    print(output_dir / "summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
