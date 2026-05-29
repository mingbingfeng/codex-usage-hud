#!/usr/bin/env python3
"""Build a single-file, no-console Windows executable for codex-usage-hud.

The build stays pinned to ``src/`` so repo-root noise such as ``tests/``,
``docs/``, ``tools/``, ``.venv/``, ``__pycache__/``, ``.git/``, ``*.sqlite``,
and ``*.log`` never enters PyInstaller's analysis graph.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_BUILD_ROOT = PROJECT_ROOT / "build" / "pyinstaller"
DEFAULT_DIST_ROOT = PROJECT_ROOT / "dist"
BOOTSTRAP_FILENAME = "_codex_hud_exe_entry.py"
DEFAULT_EXE_NAME = "codex-hud"
DEFAULT_LOG_LEVEL = "WARN"

# The package is tiny, but we still keep the graph tight and explicit:
# - collect our own package submodules for reliability across conditional imports
# - exclude Linux/macOS platform branches from the Windows build
# - exclude dev/test helper module names so they cannot be pulled in accidentally
DEFAULT_COLLECT_PACKAGES = ("codex_usage_hud",)
DEFAULT_EXCLUDED_MODULES = (
    "tests",
    "docs",
    "tools",
    "pytest",
    "codex_usage_hud.platforms.linux",
    "codex_usage_hud.platforms.macos",
)


def _resolve_project_path(value: Path) -> Path:
    """Resolve ``value`` relative to the repository root when needed."""
    if value.is_absolute():
        return value
    return (PROJECT_ROOT / value).resolve()


def bootstrap_source() -> str:
    """Return the tiny top-level entry script used by PyInstaller."""
    return (
        '"""PyInstaller bootstrap for codex_usage_hud."""\n\n'
        "from codex_usage_hud.cli import main\n\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )


def write_bootstrap_entry(path: Path) -> Path:
    """Write the PyInstaller bootstrap script and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bootstrap_source(), encoding="utf-8")
    return path


def build_pyinstaller_command(
    *,
    python_executable: Path,
    entry_script: Path,
    src_root: Path,
    dist_root: Path,
    build_root: Path,
    name: str = DEFAULT_EXE_NAME,
    log_level: str = DEFAULT_LOG_LEVEL,
    collect_packages: Sequence[str] = DEFAULT_COLLECT_PACKAGES,
    excluded_modules: Sequence[str] = DEFAULT_EXCLUDED_MODULES,
) -> list[str]:
    """Return the exact PyInstaller command used for the Windows exe build."""
    command = [
        str(python_executable),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        name,
        "--distpath",
        str(dist_root),
        "--workpath",
        str(build_root / "work"),
        "--specpath",
        str(build_root / "spec"),
        "--paths",
        str(src_root),
        "--hidden-import",
        "tkinter.font",
        "--log-level",
        log_level.upper(),
    ]
    for package in collect_packages:
        command.extend(["--collect-submodules", package])
    for module in excluded_modules:
        command.extend(["--exclude-module", module])
    command.append(str(entry_script))
    return command


def format_command(command: Sequence[str]) -> str:
    """Render a shell-friendly command string for dry runs and logging."""
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def build_environment() -> dict[str, str]:
    """Return a small sanitized environment for the build subprocesses."""
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def ensure_pyinstaller(python_executable: Path, *, bootstrap: bool) -> None:
    """Ensure PyInstaller is importable in the active Python environment."""
    if importlib.util.find_spec("PyInstaller") is not None:
        return
    if not bootstrap:
        raise RuntimeError(
            "PyInstaller is not installed in this environment. "
            "Run `python -m pip install PyInstaller` and retry."
        )

    print("[build] PyInstaller not found; installing into the active environment...")
    try:
        subprocess.run(
            [str(python_executable), "-m", "pip", "install", "PyInstaller"],
            cwd=str(PROJECT_ROOT),
            env=build_environment(),
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Unable to install PyInstaller automatically. "
            "Run `python -m pip install PyInstaller` and retry."
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for the build helper."""
    parser = argparse.ArgumentParser(
        description=(
            "Build a single-file Windows exe for codex-usage-hud with PyInstaller."
        )
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_ROOT,
        help="Directory used for PyInstaller work and spec files.",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_ROOT,
        help="Output directory for the final codex-hud.exe.",
    )
    parser.add_argument(
        "--name",
        default=DEFAULT_EXE_NAME,
        help="Executable base name. Default: codex-hud.",
    )
    parser.add_argument(
        "--no-bootstrap-pyinstaller",
        action="store_true",
        help="Fail instead of auto-installing PyInstaller into the active environment.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the PyInstaller command without running it.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Build ``dist/codex-hud.exe`` or print the command in dry-run mode."""
    args = build_parser().parse_args(argv)
    build_root = _resolve_project_path(args.build_dir)
    dist_root = _resolve_project_path(args.dist_dir)
    entry_script = build_root / BOOTSTRAP_FILENAME
    command = build_pyinstaller_command(
        python_executable=Path(sys.executable),
        entry_script=entry_script,
        src_root=SRC_ROOT,
        dist_root=dist_root,
        build_root=build_root,
        name=args.name,
    )

    if args.dry_run:
        print("[dry-run] PyInstaller command:")
        print(format_command(command))
        print(f"[dry-run] Expected output: {dist_root / f'{args.name}.exe'}")
        return 0

    if not sys.platform.startswith("win"):
        print(
            "This build helper is Windows-only; use it on Windows to produce codex-hud.exe.",
            file=sys.stderr,
        )
        return 1

    output_exe = dist_root / f"{args.name}.exe"
    try:
        ensure_pyinstaller(
            Path(sys.executable),
            bootstrap=not args.no_bootstrap_pyinstaller,
        )
        write_bootstrap_entry(entry_script)

        build_root.mkdir(parents=True, exist_ok=True)
        dist_root.mkdir(parents=True, exist_ok=True)

        try:
            output_exe.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

        print("[build] Running PyInstaller...")
        print(format_command(command))
        subprocess.run(
            command,
            cwd=str(build_root),
            env=build_environment(),
            check=True,
        )

        if not output_exe.exists():
            raise RuntimeError(
                f"Build finished but the expected output is missing: {output_exe}"
            )
    except subprocess.CalledProcessError as exc:
        print(
            f"[ERROR] PyInstaller failed with exit code {exc.returncode}",
            file=sys.stderr,
        )
        return exc.returncode or 1
    except (OSError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[build] Created {output_exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
