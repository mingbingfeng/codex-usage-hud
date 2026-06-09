#!/usr/bin/env python3
"""Build the Windows setup package with Inno Setup."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DIST_ROOT = PROJECT_ROOT / "dist"
DEFAULT_EXE_PATH = DIST_ROOT / "codex-hud.exe"
DEFAULT_INNO_SCRIPT = PROJECT_ROOT / "tools" / "installer" / "CodexUsageHud.iss"
VERSION_FILE = SRC_ROOT / "codex_usage_hud" / "__init__.py"
KNOWN_ISCC_PATHS = (
    Path(r"D:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)
_VERSION_RE = re.compile(r"__version__\s*=\s*[\"']([^\"']+)[\"']")


def read_package_version(path: Path = VERSION_FILE) -> str:
    """Read ``__version__`` without importing package code."""
    match = _VERSION_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError(f"Unable to find __version__ in {path}")
    return match.group(1)


def setup_base_filename(version: str) -> str:
    """Return the canonical Windows setup filename without extension."""
    tag = str(version).strip()
    if not tag.startswith("v"):
        tag = f"v{tag}"
    return f"codex-usage-hud-{tag}-windows-x64-setup"


def find_iscc(explicit: Path | None = None) -> Path:
    """Locate Inno Setup's command-line compiler."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    env_path = os.environ.get("INNO_SETUP_ISCC")
    if env_path:
        candidates.append(Path(env_path))
    which = shutil.which("ISCC.exe") or shutil.which("iscc")
    if which:
        candidates.append(Path(which))
    candidates.extend(KNOWN_ISCC_PATHS)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    raise RuntimeError(
        "Inno Setup compiler not found. Set INNO_SETUP_ISCC or pass --iscc."
    )


def build_exe() -> None:
    """Build the PyInstaller executable used by the installer."""
    command = [sys.executable, str(PROJECT_ROOT / "tools" / "build_exe.py")]
    subprocess.run(command, cwd=str(PROJECT_ROOT), check=True)


def build_inno_command(
    *,
    iscc: Path,
    script: Path,
    version: str,
    source_exe: Path,
    output_dir: Path,
    output_base_filename: str | None = None,
) -> list[str]:
    """Return the ISCC command used for the setup package."""
    base_name = output_base_filename or setup_base_filename(version)
    return [
        str(iscc),
        inno_define("AppVersion", version),
        inno_define("ProjectRoot", PROJECT_ROOT),
        inno_define("SourceExe", source_exe),
        inno_define("OutputDir", output_dir),
        inno_define("OutputBaseFilename", base_name),
        str(script),
    ]


def inno_define(name: str, value: object) -> str:
    """Return an ISPP command-line define."""
    return f"/D{name}={value}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build codex-usage-hud Windows setup.exe with Inno Setup."
    )
    parser.add_argument("--version", default=read_package_version())
    parser.add_argument("--iscc", type=Path, help="Path to ISCC.exe.")
    parser.add_argument("--script", type=Path, default=DEFAULT_INNO_SCRIPT)
    parser.add_argument("--source-exe", type=Path, default=DEFAULT_EXE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DIST_ROOT)
    parser.add_argument(
        "--skip-exe-build",
        action="store_true",
        help="Use the existing dist/codex-hud.exe instead of rebuilding it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ISCC command without running it.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    version = str(args.version).strip()
    source_exe = args.source_exe.resolve()
    output_dir = args.output_dir.resolve()
    script = args.script.resolve()
    try:
        iscc = find_iscc(args.iscc.resolve() if args.iscc else None)
        if not args.skip_exe_build and not args.dry_run:
            build_exe()
        if not source_exe.is_file() and not args.dry_run:
            raise RuntimeError(f"Built executable is missing: {source_exe}")
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_inno_command(
            iscc=iscc,
            script=script,
            version=version,
            source_exe=source_exe,
            output_dir=output_dir,
        )
        if args.dry_run:
            print(subprocess.list2cmdline(command))
            return 0
        subprocess.run(command, cwd=str(PROJECT_ROOT), check=True)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    output = output_dir / f"{setup_base_filename(version)}.exe"
    if not output.is_file():
        print(f"[ERROR] Expected installer is missing: {output}", file=sys.stderr)
        return 1
    print(f"[build] Created {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
