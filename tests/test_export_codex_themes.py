"""Unit tests for static Codex theme export helpers."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import export_codex_themes


class ExportCodexThemesTests(unittest.TestCase):
    def test_logical_module_name_strips_hashes_with_hyphens(self) -> None:
        self.assertEqual(
            export_codex_themes._logical_module_name(
                "webview/assets/gruvbox-light-soft-Ci-j7O4Q.js"
            ),
            "gruvbox-light-soft",
        )
        self.assertEqual(
            export_codex_themes._logical_module_name(
                "webview/assets/material-theme-CbI9oEC-.js"
            ),
            "material-theme",
        )

    def test_extract_theme_record_supports_explicit_chrome_theme_exports(self) -> None:
        module_text = """
const name = "Linear Dark";
const chrome = {
  accent: "#606acc",
  contrast: 60,
  fonts: { code: null, ui: "Inter" },
  ink: "#e3e4e6",
  opaqueWindows: true,
  semanticColors: { diffAdded: "#69c967", diffRemoved: "#ff7e78", skill: "#c2a1ff" },
  surface: "#0f0f11"
};
const colors = {
  "editor.background": "#0f0f11",
  "editor.foreground": "#e3e4e6"
};
export { chrome as chromeTheme, colors as colors, name as name };
"""

        record = export_codex_themes._extract_theme_record(
            path="webview/assets/linear-dark-BLNhFjHH.js",
            module_text=module_text,
            known_ids=["linear"],
        )

        assert record is not None
        self.assertEqual(record["codeThemeId"], "linear")
        self.assertEqual(record["displayName"], "Linear Dark")
        self.assertEqual(record["variant"], "dark")
        self.assertEqual(record["sourceKind"], "chromeTheme")
        self.assertEqual(record["theme"]["accent"], "#606acc")

    def test_extract_theme_record_supports_default_json_theme_modules(self) -> None:
        module_text = """
var e = Object.freeze(JSON.parse(`{
  "name":"Ayu Dark",
  "displayName":"Ayu Dark",
  "type":"dark",
  "colors":{
    "editor.background":"#0a0e14",
    "editor.foreground":"#b3b1ad",
    "focusBorder":"#ffb454",
    "gitDecoration.addedResourceForeground":"#7fd962",
    "gitDecoration.deletedResourceForeground":"#f26d78",
    "terminal.ansiMagenta":"#d2a6ff"
  },
  "tokenColors":[]
}`));
export { e as default };
"""

        record = export_codex_themes._extract_theme_record(
            path="webview/assets/ayu-dark-DPLgql8t.js",
            module_text=module_text,
            known_ids=["ayu"],
        )

        assert record is not None
        self.assertEqual(record["codeThemeId"], "ayu")
        self.assertEqual(record["displayName"], "Ayu Dark")
        self.assertEqual(record["variant"], "dark")
        self.assertEqual(record["sourceKind"], "vscodeTheme")
        self.assertEqual(record["theme"]["surface"], "#0a0e14")
        self.assertEqual(record["theme"]["accent"], "#ffb454")

    def test_extract_theme_record_maps_vscode_plus_aliases(self) -> None:
        module_text = """
var e = Object.freeze(JSON.parse(`{
  "name":"Dark Plus",
  "displayName":"Dark Plus",
  "type":"dark",
  "colors":{
    "editor.background":"#1e1e1e",
    "editor.foreground":"#d4d4d4",
    "focusBorder":"#007acc"
  },
  "tokenColors":[]
}`));
export { e as default };
"""

        record = export_codex_themes._extract_theme_record(
            path="webview/assets/dark-plus-IqHvZYHD.js",
            module_text=module_text,
            known_ids=["vscode-plus"],
        )

        assert record is not None
        self.assertEqual(record["codeThemeId"], "vscode-plus")
        self.assertEqual(record["moduleName"], "dark-plus")

    def test_build_theme_families_preserves_multiple_variants_per_family(self) -> None:
        families = export_codex_themes._build_theme_families(
            [
                {
                    "codeThemeId": "github",
                    "variant": "dark",
                    "displayName": "GitHub Dark",
                    "moduleName": "github-dark",
                    "modulePath": "webview/assets/github-dark-A.js",
                    "sourceKind": "vscodeTheme",
                    "theme": {"surface": "#0d1117"},
                    "shareString": "codex-theme-v1:{...}",
                },
                {
                    "codeThemeId": "github",
                    "variant": "dark",
                    "displayName": "GitHub Dark High Contrast",
                    "moduleName": "github-dark-high-contrast",
                    "modulePath": "webview/assets/github-dark-high-contrast-B.js",
                    "sourceKind": "vscodeTheme",
                    "theme": {"surface": "#010409"},
                    "shareString": "codex-theme-v1:{...}",
                },
                {
                    "codeThemeId": "github",
                    "variant": "light",
                    "displayName": "GitHub Light",
                    "moduleName": "github-light",
                    "modulePath": "webview/assets/github-light-C.js",
                    "sourceKind": "vscodeTheme",
                    "theme": {"surface": "#ffffff"},
                    "shareString": "codex-theme-v1:{...}",
                },
            ]
        )

        self.assertEqual(families["github"]["themeCount"], 3)
        self.assertEqual(len(families["github"]["variants"]["dark"]), 2)
        self.assertEqual(len(families["github"]["variants"]["light"]), 1)
        self.assertEqual(
            families["github"]["variants"]["dark"][0]["displayName"],
            "GitHub Dark",
        )


if __name__ == "__main__":
    unittest.main()
