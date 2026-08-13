from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from codex_usage_hud.background_control import (
    BackgroundControlService,
)
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

            self.assertEqual(result["verificationState"], "verified")
            self.assertTrue(result["canDisable"])
            self.assertTrue(result["canEnable"])
            self.assertEqual(read_codex_config(config)["features"]["memories"], False)
            text = config.read_text(encoding="utf-8")
            self.assertIn("memories.generate_memories = false", text)
            self.assertIn("memories.use_memories = false", text)
            self.assertIn('[desktop]', text)
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

            self.assertEqual(result["verificationState"], "verified")
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
            self.assertEqual(disabled["effectiveState"], "disabled")
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

    def test_memories_readback_is_immediately_effective_without_restart_callbacks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.toml"
            service = BackgroundControlService(root, codex_config_path=config)

            result = service.set("memory_consolidation", "disabled", 0, "event-1")

            self.assertEqual(result["verificationState"], "verified")
            self.assertEqual(result["effectiveState"], "disabled")
            self.assertFalse(result["requiresRestart"])
            self.assertFalse(result["restartAvailable"])
            self.assertIn("部分 Codex 版本可能需要重启", result["message"])
            self.assertEqual(read_codex_config(config)["features"]["memories"], False)
