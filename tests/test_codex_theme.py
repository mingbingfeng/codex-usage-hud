"""Unit tests for Codex theme parsing and runtime fallback behavior."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms import codex_theme
from codex_usage_hud.platforms.codex_theme import (
    CODEX_THEME_SHARE_PREFIX,
    CodexThemeExport,
    CodexThemeProbe,
    CodexThemeSnapshot,
    HudThemeTokens,
)


class CodexThemeExportTests(unittest.TestCase):
    def test_share_string_round_trips_theme_payload(self) -> None:
        raw = (
            'codex-theme-v1:{"codeThemeId":"codex","theme":{"accent":"#339cff",'
            '"contrast":60,"fonts":{"code":null,"ui":null},"ink":"#ffffff",'
            '"opaqueWindows":false,"semanticColors":{"diffAdded":"#40c977",'
            '"diffRemoved":"#fa423e","skill":"#ad7bf9"},"surface":"#181818"},'
            '"variant":"dark"}'
        )

        parsed = CodexThemeExport.from_share_string(raw)

        self.assertEqual(parsed.code_theme_id, "codex")
        self.assertEqual(parsed.variant, "dark")
        self.assertEqual(parsed.theme.accent, "#339cff")
        self.assertEqual(parsed.theme.surface, "#181818")
        self.assertEqual(parsed.theme.semantic_colors.diff_added, "#40c977")
        self.assertTrue(parsed.to_share_string().startswith(CODEX_THEME_SHARE_PREFIX))
        self.assertEqual(
            CodexThemeExport.from_share_string(parsed.to_share_string()).to_dict(),
            parsed.to_dict(),
        )

    def test_hud_tokens_follow_export_variant_and_semantic_colors(self) -> None:
        parsed = CodexThemeExport.from_share_string(
            'codex-theme-v1:{"codeThemeId":"linear","theme":{"accent":"#5e6ad2",'
            '"contrast":60,"fonts":{"code":null,"ui":"Inter"},"ink":"#e3e4e6",'
            '"opaqueWindows":true,"semanticColors":{"diffAdded":"#69c967",'
            '"diffRemoved":"#ff7e78","skill":"#c2a1ff"},"surface":"#0f0f11"},'
            '"variant":"dark"}'
        )

        tokens = HudThemeTokens.from_theme(parsed)

        self.assertEqual(tokens.variant, "dark")
        self.assertEqual(tokens.surface, "#0f0f11")
        self.assertEqual(tokens.accent, "#5e6ad2")
        self.assertEqual(tokens.success, "#69c967")
        self.assertEqual(tokens.error, "#ff7e78")
        self.assertEqual(tokens.info, "#c2a1ff")
        self.assertEqual(tokens.request_surface.startswith("#"), True)


class CodexThemeSnapshotTests(unittest.TestCase):
    def test_probe_result_uses_light_theme_when_mode_is_light(self) -> None:
        snapshot = CodexThemeSnapshot.from_probe_result(
            {
                "mode": "light",
                "effectiveVariant": "light",
                "lightCodeThemeId": "codex",
                "darkCodeThemeId": "linear",
                "lightTheme": {
                    "accent": "#339cff",
                    "contrast": 40,
                    "fonts": {"code": None, "ui": None},
                    "ink": "#171717",
                    "opaqueWindows": False,
                    "semanticColors": {
                        "diffAdded": "#40c977",
                        "diffRemoved": "#fa423e",
                        "skill": "#ad7bf9",
                    },
                    "surface": "#f7f8fb",
                },
                "darkTheme": {
                    "accent": "#5e6ad2",
                    "contrast": 60,
                    "fonts": {"code": None, "ui": "Inter"},
                    "ink": "#e3e4e6",
                    "opaqueWindows": True,
                    "semanticColors": {
                        "diffAdded": "#69c967",
                        "diffRemoved": "#ff7e78",
                        "skill": "#c2a1ff",
                    },
                    "surface": "#0f0f11",
                },
            }
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.effective_variant, "light")
        self.assertEqual(snapshot.effective_theme.variant, "light")
        self.assertEqual(snapshot.effective_theme.theme.surface, "#f7f8fb")
        self.assertEqual(snapshot.effective_theme.code_theme_id, "codex")

    def test_probe_result_uses_dark_theme_when_mode_is_dark(self) -> None:
        snapshot = CodexThemeSnapshot.from_probe_result(
            {
                "mode": "dark",
                "effectiveVariant": "dark",
                "lightCodeThemeId": "codex",
                "darkCodeThemeId": "linear",
                "lightTheme": {
                    "accent": "#339cff",
                    "contrast": 40,
                    "fonts": {"code": None, "ui": None},
                    "ink": "#171717",
                    "opaqueWindows": False,
                    "semanticColors": {
                        "diffAdded": "#40c977",
                        "diffRemoved": "#fa423e",
                        "skill": "#ad7bf9",
                    },
                    "surface": "#f7f8fb",
                },
                "darkTheme": {
                    "accent": "#5e6ad2",
                    "contrast": 60,
                    "fonts": {"code": None, "ui": "Inter"},
                    "ink": "#e3e4e6",
                    "opaqueWindows": True,
                    "semanticColors": {
                        "diffAdded": "#69c967",
                        "diffRemoved": "#ff7e78",
                        "skill": "#c2a1ff",
                    },
                    "surface": "#0f0f11",
                },
            }
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.effective_variant, "dark")
        self.assertEqual(snapshot.effective_theme.variant, "dark")
        self.assertEqual(snapshot.effective_theme.theme.surface, "#0f0f11")
        self.assertEqual(snapshot.effective_theme.code_theme_id, "linear")

    def test_probe_result_prefers_runtime_css_theme_when_available(self) -> None:
        snapshot = CodexThemeSnapshot.from_probe_result(
            {
                "mode": "dark",
                "effectiveVariant": "dark",
                "darkCodeThemeId": "codex",
                "cssTheme": {
                    "accent": "#339cff",
                    "surface": "#181818",
                    "ink": "#ffffff",
                    "diffAdded": "#40c977",
                    "diffRemoved": "#fa423e",
                    "skill": "#ad7bf9",
                },
            }
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.source, "cdp")
        self.assertEqual(snapshot.effective_variant, "dark")
        self.assertEqual(snapshot.effective_theme.theme.surface, "#181818")
        self.assertEqual(snapshot.hud_tokens.accent, "#339cff")


class CodexThemeProbeTests(unittest.TestCase):
    def test_probe_reads_persisted_theme_from_desktop_config(self) -> None:
        config_text = """
[desktop]
appearanceTheme = "light"
appearanceLightCodeThemeId = "github"
appearanceDarkCodeThemeId = "github"

[desktop.appearanceLightChromeTheme]
accent = "#0969da"
contrast = 40
ink = "#1f2328"
opaqueWindows = false
surface = "#ffffff"

[desktop.appearanceLightChromeTheme.fonts]
code = null
ui = null

[desktop.appearanceLightChromeTheme.semanticColors]
diffAdded = "#1a7f37"
diffRemoved = "#cf222e"
skill = "#8250df"
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(config_text.strip() + "\n", encoding="utf-8")

            probe = CodexThemeProbe(
                enabled=False,
                config_path=config_path,
            )
            snapshot = probe.snapshot()

        self.assertEqual(snapshot.source, "persisted")
        self.assertEqual(snapshot.effective_variant, "light")
        self.assertEqual(snapshot.effective_theme.code_theme_id, "github")
        self.assertEqual(snapshot.effective_theme.theme.surface, "#ffffff")
        self.assertEqual(snapshot.effective_theme.theme.semantic_colors.skill, "#8250df")
        self.assertEqual(probe.last_status, "persisted")

    def test_probe_failure_uses_persisted_theme_when_available(self) -> None:
        config_text = """
[desktop]
appearanceTheme = "dark"
appearanceDarkCodeThemeId = "linear"

[desktop.appearanceDarkChromeTheme]
accent = "#5e6ad2"
contrast = 60
ink = "#e3e4e6"
opaqueWindows = true
surface = "#0f0f11"

[desktop.appearanceDarkChromeTheme.fonts]
code = null
ui = "Inter"

[desktop.appearanceDarkChromeTheme.semanticColors]
diffAdded = "#69c967"
diffRemoved = "#ff7e78"
skill = "#c2a1ff"
"""
        originals = (
            codex_theme.list_targets,
            codex_theme.pick_page_target,
            codex_theme.send_cdp_command,
        )

        def failing_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            raise RuntimeError("CDP unavailable")

        codex_theme.list_targets = failing_list_targets
        codex_theme.pick_page_target = lambda targets: targets[0]  # type: ignore[assignment]
        codex_theme.send_cdp_command = lambda *args, **kwargs: {}  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.toml"
                config_path.write_text(config_text.strip() + "\n", encoding="utf-8")

                probe = CodexThemeProbe(
                    port=9229,
                    timeout_seconds=0.01,
                    cache_seconds=0.0,
                    failure_cooldown_seconds=10.0,
                    enabled=True,
                    config_path=config_path,
                )
                snapshot = probe.snapshot()
        finally:
            (
                codex_theme.list_targets,
                codex_theme.pick_page_target,
                codex_theme.send_cdp_command,
            ) = originals

        self.assertEqual(snapshot.source, "persisted")
        self.assertEqual(snapshot.effective_variant, "dark")
        self.assertEqual(snapshot.effective_theme.code_theme_id, "linear")
        self.assertEqual(snapshot.effective_theme.theme.surface, "#0f0f11")
        self.assertEqual(probe.last_status, "persisted")
        self.assertIn("CDP unavailable", probe.last_error)

    def test_probe_failure_cooldown_returns_fallback_without_retrying_immediately(self) -> None:
        originals = (
            codex_theme.list_targets,
            codex_theme.pick_page_target,
            codex_theme.send_cdp_command,
        )
        calls = {"list_targets": 0}

        def failing_list_targets(port: int, timeout_seconds: float) -> list[dict[str, object]]:
            del port, timeout_seconds
            calls["list_targets"] += 1
            raise RuntimeError("CDP unavailable")

        codex_theme.list_targets = failing_list_targets
        codex_theme.pick_page_target = lambda targets: targets[0]  # type: ignore[assignment]
        codex_theme.send_cdp_command = lambda *args, **kwargs: {}  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.toml"
                probe = CodexThemeProbe(
                    port=9229,
                    timeout_seconds=0.01,
                    cache_seconds=0.0,
                    failure_cooldown_seconds=10.0,
                    enabled=True,
                    config_path=config_path,
                )
                first = probe.snapshot()
                second = probe.snapshot()
        finally:
            (
                codex_theme.list_targets,
                codex_theme.pick_page_target,
                codex_theme.send_cdp_command,
            ) = originals

        self.assertEqual(first.source, "fallback")
        self.assertEqual(second.source, "fallback")
        self.assertEqual(probe.last_status, "cooldown")
        self.assertEqual(calls["list_targets"], 1)


if __name__ == "__main__":
    unittest.main()
