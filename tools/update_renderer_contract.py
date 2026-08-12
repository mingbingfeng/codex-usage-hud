#!/usr/bin/env python3
"""Check or update the checked-in Renderer bundle contract.

Renderer source is split across ordered Python fragments, while tests also
freeze the final injected bundle.  Keeping the derived byte lengths, hashes,
and lifecycle counts in one command prevents manual contract drift after a
small Renderer edit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "tests/contracts/renderer_contract.json"
MANIFEST_PATH = ROOT / "src/codex_usage_hud/renderer_assets/manifest.py"


def _ensure_source_path() -> None:
    source = str(ROOT / "src")
    if source not in sys.path:
        sys.path.insert(0, source)


def _digest(text: str) -> dict[str, object]:
    encoded = text.encode("utf-8")
    return {
        "byteLength": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _write_text_preserving_newlines(path: Path, text: str) -> None:
    """Write without letting Windows convert the repository's LF endings."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def collect_renderer_contract() -> dict[str, object]:
    """Return all derived Renderer values maintained by the contract files."""
    _ensure_source_path()
    from codex_usage_hud.renderer_assets import manifest
    from codex_usage_hud.renderer_catalog import RENDERER_HUD_SCRIPT

    template = manifest.RENDERER_HUD_SCRIPT_TEMPLATE
    return {
        "template": _digest(template),
        "currentBundle": _digest(RENDERER_HUD_SCRIPT),
        "lifecycleCounts": {
            "mutationObservers": RENDERER_HUD_SCRIPT.count(
                "new MutationObserver"
            ),
            "resizeObservers": RENDERER_HUD_SCRIPT.count("new ResizeObserver"),
            "setIntervals": RENDERER_HUD_SCRIPT.count("ctx.lifecycle.interval("),
            "setTimeouts": RENDERER_HUD_SCRIPT.count("ctx.lifecycle.timeout("),
            "kernelSetIntervals": RENDERER_HUD_SCRIPT.count("setInterval("),
            "kernelSetTimeouts": RENDERER_HUD_SCRIPT.count("setTimeout("),
        },
    }


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def contract_mismatches(
    actual: dict[str, object],
    contract: dict[str, Any],
) -> list[str]:
    """Describe stale derived values without mutating either contract file."""
    _ensure_source_path()
    from codex_usage_hud.renderer_assets import manifest

    mismatches: list[str] = []
    template = actual["template"]
    current = actual["currentBundle"]
    lifecycle = actual["lifecycleCounts"]
    assert isinstance(template, dict)
    assert isinstance(current, dict)
    assert isinstance(lifecycle, dict)

    expected_template = {
        "byteLength": manifest.P6_7_TEMPLATE_BYTE_LENGTH,
        "sha256": manifest.P6_7_TEMPLATE_SHA256,
    }
    template_labels = {"byteLength": "BYTE_LENGTH", "sha256": "SHA256"}
    for key, expected in expected_template.items():
        observed = template[key]
        if observed != expected:
            mismatches.append(
               f"manifest.P6_7_TEMPLATE_{template_labels[key]} expected {expected}, "
                f"actual {observed}"
            )

    expected_current = contract.get("currentBundle") or {}
    for key in ("byteLength", "sha256"):
        expected = expected_current.get(key)
        observed = current[key]
        if observed != expected:
            mismatches.append(
                f"renderer_contract.currentBundle.{key} expected {expected}, "
                f"actual {observed}"
            )

    expected_lifecycle = contract.get("lifecycleCounts") or {}
    for key, observed in lifecycle.items():
        expected = expected_lifecycle.get(key)
        if observed != expected:
            mismatches.append(
                f"renderer_contract.lifecycleCounts.{key} expected {expected}, "
                f"actual {observed}"
            )
    return mismatches


def _replace_once(
    text: str,
    pattern: str,
    replacement: str,
    *,
    label: str,
    flags: int = 0,
) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"could not update {label}; expected one match, got {count}")
    return updated


def _render_updated_manifest(path: Path, template: dict[str, object]) -> str:
    text = path.read_text(encoding="utf-8")
    text = _replace_once(
        text,
        r"(?m)^P6_7_TEMPLATE_BYTE_LENGTH = \d+$",
        f"P6_7_TEMPLATE_BYTE_LENGTH = {template['byteLength']}",
        label="P6_7 template byte length",
    )
    text = _replace_once(
        text,
        r'(?m)^P6_7_TEMPLATE_SHA256 = "[0-9a-f]+"$',
        f'P6_7_TEMPLATE_SHA256 = "{template["sha256"]}"',
        label="P6_7 template SHA256",
    )
    return text


def _render_updated_contract(path: Path, actual: dict[str, object]) -> str:
    text = path.read_text(encoding="utf-8")
    current = actual["currentBundle"]
    lifecycle = actual["lifecycleCounts"]
    assert isinstance(current, dict)
    assert isinstance(lifecycle, dict)

    text = _replace_once(
        text,
        r'("currentBundle"\s*:\s*\{\s*"phase"\s*:\s*"[^"]+"\s*,\s*)'
        r'("sha256"\s*:\s*)"[0-9a-f]+"(\s*,\s*)'
        r'("byteLength"\s*:\s*)\d+',
        rf'\g<1>\g<2>"{current["sha256"]}"\g<3>\g<4>{current["byteLength"]}',
        label="renderer current bundle hash and length",
        flags=re.DOTALL,
    )

    for key, value in lifecycle.items():
        text = _replace_once(
            text,
            rf'(?m)^(\s*"{re.escape(key)}"\s*:\s*)\d+(\s*,?)$',
            rf"\g<1>{value}\g<2>",
            label=f"lifecycle count {key}",
        )
    return text


def update_contract_files(
    actual: dict[str, object],
    *,
    manifest_path: Path = MANIFEST_PATH,
    contract_path: Path = CONTRACT_PATH,
) -> None:
    template = actual["template"]
    assert isinstance(template, dict)
    # Render both files before writing either one, so a malformed contract
    # cannot leave the worktree half-updated.
    manifest_text = _render_updated_manifest(manifest_path, template)
    contract_text = _render_updated_contract(contract_path, actual)
    _write_text_preserving_newlines(manifest_path, manifest_text)
    _write_text_preserving_newlines(contract_path, contract_text)


def _format_actual(actual: dict[str, object]) -> str:
    return json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check or update the Renderer bundle contract."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="check derived values without writing files (default)",
    )
    mode.add_argument(
        "--update",
        action="store_true",
        help="write the current derived values into the contract files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    actual = collect_renderer_contract()
    contract = load_contract()
    mismatches = contract_mismatches(actual, contract)
    if not mismatches:
        print("Renderer contract is up to date.")
        return 0
    if not args.update:
        print("Renderer contract is stale:")
        for mismatch in mismatches:
            print(f"- {mismatch}")
        print()
        print("Review the bundle change, then run:")
        print("  python tools/update_renderer_contract.py --update")
        print()
        print("Derived values:")
        print(_format_actual(actual))
        return 1

    update_contract_files(actual)
    print("Updated Renderer contract files:")
    print(f"- {MANIFEST_PATH.relative_to(ROOT)}")
    print(f"- {CONTRACT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
