#!/usr/bin/env python3
"""Pre-release sanity check for the first public codex-usage-hud release."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REQUIRED_PATHS = (
    Path("pyproject.toml"),
    Path("LICENSE"),
    Path("README.md"),
    Path("docs/PRIVACY.md"),
)


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _style(text: str, code: str) -> str:
    if not _use_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def _ok(text: str) -> str:
    return _style(text, "1;32")


def _warn(text: str) -> str:
    return _style(text, "1;33")


def _fail(text: str) -> str:
    return _style(text, "1;31")


def _info(text: str) -> str:
    return _style(text, "1;36")


def main() -> int:
    root = Path.cwd()

    print(_info("codex-usage-hud pre-release check"))
    print(f"Working directory: {root}")
    print()

    missing: list[Path] = []
    for relative in REQUIRED_PATHS:
        candidate = root / relative
        if candidate.is_file():
            print(f"{_ok('[OK]')} {relative}")
        elif candidate.exists():
            print(f"{_warn('[WARN]')} {relative} exists but is not a file")
            missing.append(relative)
        else:
            print(f"{_fail('[MISS]')} {relative}")
            missing.append(relative)

    print()
    if missing:
        print(_fail("Pre-release check failed."))
        print("Please add the missing release files before pushing.")
        return 1

    print(_ok("All required release files are present."))
    print("You can safely execute git push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
