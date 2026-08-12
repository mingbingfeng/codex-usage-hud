from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.update_renderer_contract import (
    CONTRACT_PATH,
    MANIFEST_PATH,
    collect_renderer_contract,
    contract_mismatches,
    load_contract,
    update_contract_files,
)


ROOT = Path(__file__).resolve().parents[1]


def test_renderer_contract_tool_matches_checked_in_values() -> None:
    actual = collect_renderer_contract()
    mismatches = contract_mismatches(actual, load_contract())
    assert not mismatches, (
        "Renderer contract is stale: "
        + "; ".join(mismatches)
        + ". Run `python tools/update_renderer_contract.py --update` "
        "after reviewing the bundle change."
    )


def test_renderer_contract_check_command_is_clean() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/update_renderer_contract.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "up to date" in completed.stdout
    assert CONTRACT_PATH.is_file()
    assert MANIFEST_PATH.is_file()


def test_renderer_contract_update_writes_both_derived_files(tmp_path: Path) -> None:
    actual = collect_renderer_contract()
    manifest_copy = tmp_path / "manifest.py"
    contract_copy = tmp_path / "renderer_contract.json"
    manifest_copy.write_bytes(MANIFEST_PATH.read_bytes())
    contract_copy.write_bytes(CONTRACT_PATH.read_bytes())

    update_contract_files(
        actual,
        manifest_path=manifest_copy,
        contract_path=contract_copy,
    )

    updated_contract = load_contract(contract_copy)
    assert {
        key: updated_contract["currentBundle"][key]
        for key in ("byteLength", "sha256")
    } == actual["currentBundle"]
    assert updated_contract["lifecycleCounts"] == actual["lifecycleCounts"]
    manifest_text = manifest_copy.read_text(encoding="utf-8")
    assert f"P6_7_TEMPLATE_BYTE_LENGTH = {actual['template']['byteLength']}" in manifest_text
    assert f'P6_7_TEMPLATE_SHA256 = "{actual["template"]["sha256"]}"' in manifest_text
    assert b"\r\n" not in manifest_copy.read_bytes()
    assert b"\r\n" not in contract_copy.read_bytes()
