from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.config import UserConfig
from codex_usage_hud.provider_registry import discover_provider_registry


class ProviderRegistryTests(unittest.TestCase):
    def test_registry_uses_base_provider_profiles_saved_settings_and_recent_history(self) -> None:
        now = datetime(2026, 7, 16, tzinfo=timezone.utc)
        config_text = """
model_provider = "custom"

[model_providers]
[model_providers.custom]
name = "OpenAI"
[model_providers.muyuan]
name = "Muyuan"

[profiles.muyuan]
model_provider = "muyuan"
[profiles.shared]
model_provider = "custom"
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.toml"
            config_path.write_text(config_text, encoding="utf-8")
            session_path = root / "sessions" / "2026" / "recent.jsonl"
            session_path.parent.mkdir(parents=True)
            session_path.write_text(
                json.dumps(
                    {
                        "timestamp": (now - timedelta(days=2)).isoformat(),
                        "type": "session_meta",
                        "payload": {"model_provider": "history-only"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            archived = root / "archived_sessions" / "old.jsonl"
            archived.parent.mkdir(parents=True)
            archived.write_text(
                json.dumps(
                    {
                        "timestamp": (now - timedelta(days=31)).isoformat(),
                        "type": "session_meta",
                        "payload": {"model_provider": "too-old"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config = UserConfig.from_dict(
                {"provider_settings": {"saved-only": {"model_prices": {}}}}
            )
            registry = discover_provider_registry(
                user_config=config,
                config_path=config_path,
                sessions_root=root / "sessions",
                now=now,
            )

        self.assertEqual(registry.app_provider, "custom")
        self.assertEqual(
            registry.providers(),
            ("custom", "muyuan", "saved-only", "history-only"),
        )
        self.assertEqual(registry.entries["muyuan"].profile_names, ("muyuan",))
        self.assertIn("[model_providers.muyuan]", registry.entries["muyuan"].config_text)
        self.assertEqual(registry.entries["custom"].profile_names, ("shared",))
        self.assertIn("[model_providers.custom]", registry.entries["custom"].config_text)
        self.assertTrue(registry.entries["history-only"].historical_only)
        self.assertIn("saved-only", registry.entries)
        self.assertNotIn("too-old", registry.entries)

    def test_registry_keeps_unknown_only_when_a_recent_session_lacks_provider(self) -> None:
        now = datetime(2026, 7, 16, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "sessions" / "unknown.jsonl"
            session_path.parent.mkdir(parents=True)
            session_path.write_text(
                json.dumps(
                    {
                        "timestamp": now.isoformat(),
                        "type": "session_meta",
                        "payload": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            registry = discover_provider_registry(
                user_config=UserConfig.defaults(),
                config_path=root / "missing.toml",
                sessions_root=root / "sessions",
                now=now,
            )

        self.assertIn("unknown", registry.entries)
        self.assertTrue(registry.entries["unknown"].historical_only)

    def test_registry_can_skip_history_discovery_for_foreground_provider_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "sessions" / "2026" / "recent.jsonl"
            session_path.parent.mkdir(parents=True)
            session_path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-07-16T00:00:00+00:00",
                        "type": "session_meta",
                        "payload": {"model_provider": "history-only"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            registry = discover_provider_registry(
                user_config=UserConfig.defaults(),
                config_path=root / "missing.toml",
                sessions_root=root / "sessions",
                include_history=False,
            )

        self.assertNotIn("history-only", registry.entries)


if __name__ == "__main__":
    unittest.main()
