"""Version consistency tests."""

from __future__ import annotations

from pathlib import Path
import re
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_VERSION_RE = re.compile(r"__version__\s*=\s*[\"']([^\"']+)[\"']")


def _read_version(path: Path) -> str:
    match = _VERSION_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        raise AssertionError(f"missing __version__ in {path}")
    return match.group(1)


class VersionConsistencyTests(unittest.TestCase):
    def test_source_package_and_development_shim_versions_match(self) -> None:
        self.assertEqual(
            _read_version(PROJECT_ROOT / "src" / "codex_usage_hud" / "__init__.py"),
            _read_version(PROJECT_ROOT / "codex_usage_hud" / "__init__.py"),
        )


if __name__ == "__main__":
    unittest.main()
