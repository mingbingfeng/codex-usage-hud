from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from codex_usage_hud.background_control import (
    BackgroundControlService,
    default_memory_restart_probe,
)
from codex_usage_hud.codex_app_runtime import CodexDesktopProcess
from codex_usage_hud.platforms.codex_theme import read_codex_config


class BackgroundControlServiceTests(unittest.TestCase):
    def test_memories_disable_preserves_feature_subkeys_and_reads_back(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            config.write_text(
                '[features]\nmemories = true\nmemories.generate_memories = false\nmemories.use_memories = false\n\n[desktop]\ntheme = "dark"\n',
                encoding="utf-8",
            )
            service = BackgroundControlService(root, codex_config_path=config)

            result = service.set("memory_consolidation", "disabled", 0, "event-1")

            self.assertEqual(result["verificationState"], "configured_unverified")
            self.assertTrue(result["canDisable"])
            self.assertTrue(result["canEnable"])
            self.assertEqual(read_codex_config(config)["features"]["memories"], False)
            text = config.read_text(encoding="utf-8")
            self.assertIn("memories.generate_memories = false", text)
            self.assertIn("memories.use_memories = false", text)
            self.assertIn('[desktop]', text)
            saved = json.loads((root / "background-policies.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["memory_consolidation"]["desired_state"], "disabled")
            self.assertTrue((root / "background-policy-audit.jsonl").is_file())

            enabled = service.set(
                "memory_consolidation",
                "enabled",
                result["policyRevision"],
                "event-1",
            )
            self.assertTrue(enabled["canDisable"])
            self.assertFalse(enabled["canEnable"])

    def test_memories_key_matching_does_not_replace_dotted_feature_subkeys(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            config.write_text(
                "[features]\nmemories.generate_memories = false\n",
                encoding="utf-8",
            )
            service = BackgroundControlService(root, codex_config_path=config)

            result = service.set("memory_consolidation", "enabled", 0)

            self.assertEqual(result["verificationState"], "configured_unverified")
            text = config.read_text(encoding="utf-8")
            self.assertIn("memories.generate_memories = false", text)
            self.assertIn("memories = true", text)

    def test_enable_and_disable_round_trip_preserves_effective_state_contract(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            config.write_text("[features]\nmemories = false\n", encoding="utf-8")
            service = BackgroundControlService(root, codex_config_path=config)

            enabled = service.set("memory_consolidation", "enabled", 0)
            self.assertEqual(enabled["desiredState"], "enabled")
            self.assertEqual(enabled["effectiveState"], "enabled")
            self.assertEqual(read_codex_config(config)["features"]["memories"], True)

            disabled = service.set(
                "memory_consolidation", "disabled", enabled["policyRevision"]
            )
            self.assertEqual(disabled["desiredState"], "disabled")
            self.assertEqual(disabled["effectiveState"], "enabled")
            self.assertEqual(read_codex_config(config)["features"]["memories"], False)

    def test_revision_conflict_leaves_existing_policy_unchanged(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = BackgroundControlService(root, codex_config_path=root / "config.toml")
            service.set("memory_consolidation", "disabled", 0)

            result = service.set("memory_consolidation", "enabled", 0)

            self.assertEqual(result["verificationState"], "failed")
            self.assertEqual(result["error"]["code"], "revision_conflict")
            self.assertEqual(service.query("memory_consolidation")["desiredState"], "disabled")

    def test_unsupported_task_never_writes_a_codex_config(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            service = BackgroundControlService(root, codex_config_path=config)

            result = service.set("title_description", "disabled", 0)

            self.assertEqual(result["verificationState"], "unsupported")
            self.assertFalse(config.exists())
            query = service.query("title_description")
            self.assertFalse(query["canDisable"])
            self.assertIn("未发现可验证", str(query["message"]))

    def test_suggestion_safety_uses_linked_user_action_not_toml(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            opened = []
            service = BackgroundControlService(root, codex_config_path=root / "config.toml", open_settings=lambda: opened.append(True) or True)

            result = service.set("suggestion_safety", "disabled", 0)

            self.assertEqual(result["verificationState"], "requires_user_action")
            self.assertEqual(opened, [True])
            self.assertFalse((root / "config.toml").exists())
            self.assertEqual(service.query("context_suggestions")["desiredState"], "disabled")

    def test_memories_reports_restart_required_until_restart_is_verified(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            restart_calls: list[bool] = []
            service = BackgroundControlService(
                root,
                codex_config_path=root / "config.toml",
                restart_probe=lambda: {
                    "required": True,
                    "available": True,
                    "message": "当前 Codex 进程仍在使用旧配置。",
                },
                restart_codex=lambda: restart_calls.append(True)
                or {"ok": True, "verified": True},
            )

            configured = service.set("memory_consolidation", "disabled", 0)

            self.assertEqual(configured["verificationState"], "configured_unverified")
            self.assertTrue(configured["requiresRestart"])
            self.assertTrue(configured["restartAvailable"])
            self.assertEqual(configured["effectiveState"], "enabled")

            verified = service.set(
                "memory_consolidation",
                "disabled",
                configured["policyRevision"],
                "event-1",
                "usage_detail",
                True,
            )

            self.assertEqual(restart_calls, [True])
            self.assertEqual(verified["verificationState"], "verified")
            self.assertEqual(verified["effectiveState"], "disabled")
            self.assertFalse(verified["requiresRestart"])

    def test_restart_unavailable_never_claims_verified(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = BackgroundControlService(
                root,
                codex_config_path=root / "config.toml",
                restart_probe=lambda: {
                    "required": True,
                    "available": False,
                    "code": "standalone_cli_running",
                    "message": "检测到运行中的 Codex CLI，HUD 不会强制终止。",
                },
                restart_codex=lambda: {"ok": False, "verified": False},
            )

            configured = service.set("memory_consolidation", "disabled", 0)
            result = service.set(
                "memory_consolidation",
                "disabled",
                configured["policyRevision"],
                "event-1",
                "usage_detail",
                True,
            )

            self.assertEqual(result["verificationState"], "configured_unverified")
            self.assertNotEqual(result["effectiveState"], "disabled")
            self.assertTrue(result["requiresRestart"])
            self.assertEqual(result["error"]["code"], "restart_unavailable")

    def test_manual_desktop_restart_clears_stale_restart_banner(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            config.write_text("[features]\nmemories = true\n", encoding="utf-8")
            probe_state = {"restarted": False}
            service = BackgroundControlService(
                root,
                codex_config_path=config,
                restart_probe=lambda **_kwargs: {
                    "required": not probe_state["restarted"],
                    "available": True,
                    "code": (
                        "process_restart_verified"
                        if probe_state["restarted"]
                        else "desktop_restart_available"
                    ),
                    "message": (
                        "new process generation"
                        if probe_state["restarted"]
                        else "restart required"
                    ),
                },
            )
            configured = service.set("memory_consolidation", "disabled", 0)
            self.assertTrue(configured["requiresRestart"])

            # Re-open the policy after the app has been restarted. The probe
            # reports a new process generation and the config still matches.
            probe_state["restarted"] = True
            verified = service.query("memory_consolidation")

            self.assertEqual(verified["verificationState"], "verified")
            self.assertEqual(verified["effectiveState"], "disabled")
            self.assertFalse(verified["requiresRestart"])
            self.assertNotIn("等待重启", str(verified["message"]))
            saved = json.loads((root / "background-policies.json").read_text(encoding="utf-8"))
            self.assertEqual(
                saved["memory_consolidation"]["verification_state"],
                "verified",
            )

    def test_restart_probe_distinguishes_old_and_new_process_generations(self) -> None:
        old_cli = CodexDesktopProcess(
            11,
            "codex.exe",
            "C:/Users/test/AppData/Roaming/npm/codex.exe",
            "",
            "2026-08-10T11:00:00Z",
        )
        new_cli = CodexDesktopProcess(
            12,
            "codex.exe",
            "C:/Users/test/AppData/Roaming/npm/codex.exe",
            "",
            "2026-08-10T12:00:00Z",
        )
        with patch(
            "codex_usage_hud.codex_app_runtime.running_standalone_codex_cli_processes",
            return_value=[old_cli],
        ), patch(
            "codex_usage_hud.codex_app_runtime.audited_running_codex_desktop_processes",
            return_value=[],
        ):
            pending = default_memory_restart_probe("2026-08-10T11:47:54Z")
        self.assertEqual(pending["code"], "standalone_cli_running")
        self.assertFalse(pending["available"])

        with patch(
            "codex_usage_hud.codex_app_runtime.running_standalone_codex_cli_processes",
            return_value=[new_cli],
        ), patch(
            "codex_usage_hud.codex_app_runtime.audited_running_codex_desktop_processes",
            return_value=[],
        ):
            verified = default_memory_restart_probe("2026-08-10T11:47:54Z")
        self.assertEqual(verified["code"], "process_restart_verified")
        self.assertFalse(verified["required"])
