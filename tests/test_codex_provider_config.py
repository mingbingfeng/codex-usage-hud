from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.codex_provider_config import (
    _delete_user_environment_value,
    delete_provider_config,
    read_provider_definitions,
    save_provider_configs,
    verify_provider_connectivity,
)


class CodexProviderConfigTests(unittest.TestCase):
    def test_delete_user_environment_value_uses_winreg_signature(self) -> None:
        fake_winreg = MagicMock()
        handle = object()
        fake_winreg.CreateKeyEx.return_value.__enter__.return_value = handle

        with patch.object(
            sys.modules["os"], "name", "nt"
        ), patch.dict(sys.modules, {"winreg": fake_winreg}), patch(
            "codex_usage_hud.codex_provider_config._broadcast_environment_change"
        ):
            _delete_user_environment_value("MUYUAN_API_KEY")

        fake_winreg.DeleteValue.assert_called_once_with(handle, "MUYUAN_API_KEY")

    def test_default_provider_cannot_be_deleted(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "默认 Codex App Provider"):
                delete_provider_config("custom", config_path=path)
            self.assertEqual(path.read_text(encoding="utf-8"), config_text)

    def test_delete_provider_removes_related_tables_and_profile(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n\n'
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://muyuan.example/v1"\n\n'
            "[model_providers.muyuan.headers]\n"
            'x-tenant = "one"\n\n'
            "[profiles.muyuan]\n"
            'model_provider = "muyuan"\n'
            'model = "gpt-5"\n\n'
            "[features]\n"
            "memories = true\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            profile = root / "muyuan.config.toml"
            profile.write_text('model_provider = "muyuan"\n', encoding="utf-8")

            result = delete_provider_config("muyuan", config_path=path)
            updated = path.read_text(encoding="utf-8")

        self.assertTrue(result["changed"])
        self.assertNotIn("[model_providers.muyuan]", updated)
        self.assertNotIn("[profiles.muyuan]", updated)
        self.assertIn('model_provider = "custom"', updated)
        self.assertIn("[features]\nmemories = true", updated)
        self.assertFalse(profile.exists())

    def test_default_custom_provider_cannot_be_edited(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            '[model_providers]\n'
            '[model_providers.custom]\n'
            'name = "OpenAI"\n'
            'base_url = "https://old.example/v1"\n'
            'wire_api = "responses"\n'
            'requires_openai_auth = true\n'
            'experimental_bearer_token = "keep-out-of-hud"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "默认 Codex App Provider"):
                save_provider_configs(
                    [
                        {
                            "provider_id": "custom",
                            "base_url": "https://new.example/v1",
                            "env_key": "",
                            "api_key": "",
                        }
                    ],
                    config_path=path,
                )
            updated = path.read_text(encoding="utf-8")

        self.assertEqual(updated, config_text)

    def test_windows_newlines_are_preserved(self) -> None:
        config_text = (
            'model_provider = "custom"\r\n'
            '\r\n'
            '[model_providers]\r\n'
            '[model_providers.custom]\r\n'
            'name = "OpenAI"\r\n'
            '\r\n'
            '[model_providers.muyuan]\r\n'
            'name = "Muyuan"\r\n'
            'base_url = "https://old.example/v1"\r\n'
            'env_key = "MUYUAN_API_KEY"\r\n'
            'wire_api = "responses"\r\n'
            '\r\n'
            '[features]\r\n'
            'memories = true\r\n'
        )
        environment = {"MUYUAN_API_KEY": "old-secret"}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_bytes(config_text.encode("utf-8"))
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                save_provider_configs(
                    {
                        "muyuan": {
                            "base_url": "https://new.example/v1",
                            "env_key": "MUYUAN_API_KEY",
                            "api_key": "new-secret",
                        }
                    },
                    config_path=path,
                )
            updated_bytes = path.read_bytes()

        self.assertIn(b"\r\n", updated_bytes)
        self.assertNotIn(b"\n", updated_bytes.replace(b"\r\n", b""))

    def test_existing_provider_update_preserves_unrelated_toml(self) -> None:
        config_text = (
            'model_provider = "custom"\n'
            "\n"
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            "\n"
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://old.example/v1"\n'
            'env_key = "OLD_API_KEY"\n'
            'wire_api = "responses"\n'
            "\n"
            "[features]\n"
            "memories = true\n"
        )
        environment = {"OLD_API_KEY": "old-secret", "NEW_API_KEY": ""}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                result = save_provider_configs(
                    [
                        {
                            "provider_id": "muyuan",
                            "base_url": "https://new.example/v1",
                            "env_key": "NEW_API_KEY",
                            "api_key": "new-secret",
                        }
                    ],
                    config_path=path,
                )
            updated = path.read_text(encoding="utf-8")
            definitions = read_provider_definitions(path)

        self.assertTrue(result["changed"])
        self.assertEqual(environment["NEW_API_KEY"], "new-secret")
        self.assertIn('model_provider = "custom"', updated)
        self.assertIn("[features]\nmemories = true", updated)
        self.assertIn('name = "Muyuan"', updated)
        self.assertIn('base_url = "https://new.example/v1"', updated)
        self.assertIn('env_key = "NEW_API_KEY"', updated)
        self.assertEqual(definitions["muyuan"].base_url, "https://new.example/v1")
        self.assertEqual(definitions["muyuan"].env_key, "NEW_API_KEY")

    def test_editor_section_text_replaces_full_provider_body(self) -> None:
        config_text = (
            'model_provider = "custom"\n'
            "\n"
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
            "\n"
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://old.example/v1"\n'
            'env_key = "MUYUAN_API_KEY"\n'
            'wire_api = "responses"\n'
            "\n"
            "[features]\n"
            "memories = true\n"
        )
        section_text = (
            "[model_providers.muyuan]\n"
            'name = "Edited display name"\n'
            'base_url = "https://edited.example/v1"\n'
            'env_key = "EDITED_API_KEY"\n'
            'wire_api = "responses"\n'
            "supports_websockets = true"
        )
        environment = {"MUYUAN_API_KEY": "old-secret", "EDITED_API_KEY": ""}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                save_provider_configs(
                    {
                        "provider_id": "muyuan",
                        "section_text": section_text,
                        "api_key": "edited-secret",
                    },
                    config_path=path,
                )
            updated = path.read_text(encoding="utf-8")
            definition = read_provider_definitions(path)["muyuan"]

        self.assertIn('name = "Edited display name"', updated)
        self.assertIn("supports_websockets = true", updated)
        self.assertNotIn('name = "Muyuan"', updated)
        self.assertEqual(environment["EDITED_API_KEY"], "edited-secret")
        self.assertEqual(
            definition.section_text.replace("\r\n", "\n").rstrip(),
            section_text,
        )

    def test_new_provider_creates_profile_and_defaults_scope_metadata(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        environment = {"CUNAI_API_KEY": ""}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                result = save_provider_configs(
                    [
                        {
                            "provider_id": "cunai",
                            "base_url": "https://cuna.example/v1",
                            "env_key": "CUNAI_API_KEY",
                            "api_key": "secret",
                            "is_new": True,
                        }
                    ],
                    config_path=path,
                )
            profile = root / "cunai.config.toml"
            updated = path.read_text(encoding="utf-8")
            profile_text = profile.read_text(encoding="utf-8")

        self.assertTrue(result["changed"])
        self.assertEqual(environment["CUNAI_API_KEY"], "secret")
        self.assertIn("[model_providers.cunai]", updated)
        self.assertIn('base_url = "https://cuna.example/v1"', updated)
        self.assertEqual(profile_text, 'model_provider = "cunai"\n')

    def test_new_provider_accepts_editor_section_text(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        section_text = (
            "[model_providers.cunai]\n"
            'name = "CUN.AI"\n'
            'base_url = "https://www.cun.ai/v1"\n'
            'env_key = "CUNAI_API_KEY"\n'
            'wire_api = "responses"\n'
            "supports_websockets = true"
        )
        environment = {"CUNAI_API_KEY": ""}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                side_effect=lambda name: environment.get(name, ""),
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=lambda name, value: environment.__setitem__(name, value),
            ):
                save_provider_configs(
                    {
                        "provider_id": "cunai",
                        "section_text": section_text,
                        "api_key": "secret",
                        "is_new": True,
                    },
                    config_path=path,
                )
            self.assertEqual((root / "cunai.config.toml").read_text(encoding="utf-8"), 'model_provider = "cunai"\n')
            self.assertEqual(environment["CUNAI_API_KEY"], "secret")
            self.assertIn("supports_websockets = true", path.read_text(encoding="utf-8"))

    def test_editor_section_text_rejects_nested_table(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "其它 TOML 表头"):
                save_provider_configs(
                    {
                        "provider_id": "cunai",
                        "section_text": (
                            "[model_providers.cunai]\n"
                            'base_url = "https://www.cun.ai/v1"\n'
                            'env_key = "CUNAI_API_KEY"\n'
                            "[model_providers.cunai.headers]\n"
                            'x-api-key = "bad"\n'
                        ),
                        "api_key": "secret",
                        "is_new": True,
                    },
                    config_path=path,
                )

    def test_failed_environment_write_rolls_back_config_and_profile(self) -> None:
        config_text = (
            "[model_providers]\n"
            "[model_providers.custom]\n"
            'name = "OpenAI"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._user_environment_value",
                return_value="",
            ), patch(
                "codex_usage_hud.codex_provider_config._set_user_environment_value",
                side_effect=RuntimeError("registry unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
                    save_provider_configs(
                        [
                            {
                                "provider_id": "broken",
                                "base_url": "https://broken.example/v1",
                                "env_key": "BROKEN_API_KEY",
                                "api_key": "secret",
                                "is_new": True,
                            }
                        ],
                        config_path=path,
                    )
            self.assertEqual(path.read_text(encoding="utf-8"), config_text)
            self.assertFalse((root / "broken.config.toml").exists())

    def test_delete_provider_removes_user_environment_variable(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenAI"\n\n'
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://muyuan.example/v1"\n'
            'env_key = "MUYUAN_API_KEY"\n'
        )
        deleted = []
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._delete_user_environment_value",
                side_effect=lambda name: deleted.append(name),
            ):
                result = delete_provider_config("muyuan", config_path=path)

        self.assertEqual(deleted, ["MUYUAN_API_KEY"])
        self.assertIn("environmentKeys", result)
        self.assertEqual(result["environmentKeys"], ["MUYUAN_API_KEY"])

    def test_delete_provider_without_env_key_does_not_touch_environment(self) -> None:
        config_text = (
            'model_provider = "custom"\n\n'
            "[model_providers.custom]\n"
            'name = "OpenAI"\n\n'
            "[model_providers.muyuan]\n"
            'name = "Muyuan"\n'
            'base_url = "https://muyuan.example/v1"\n'
        )
        deleted = []
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(config_text, encoding="utf-8")
            with patch(
                "codex_usage_hud.codex_provider_config._delete_user_environment_value",
                side_effect=lambda name: deleted.append(name),
            ):
                result = delete_provider_config("muyuan", config_path=path)

        self.assertEqual(deleted, [])
        self.assertEqual(result["environmentKeys"], [])


class VerifyProviderConnectivityTests(unittest.TestCase):
    def test_verify_provider_connectivity_calls_models_endpoint_with_bearer(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, length: int = -1) -> bytes:
                del length
                return b'{"data": [{"id": "gpt-4o"}]}'

        def fake_urlopen(request, timeout: float = 8.0):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse()

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=fake_urlopen,
        ):
            connected = verify_provider_connectivity(
                "https://api.example.com/v1/", "sk-test-secret"
            )

        self.assertEqual(captured["url"], "https://api.example.com/v1/models")
        self.assertEqual(captured["authorization"], "Bearer sk-test-secret")
        self.assertTrue(connected)

    def test_verify_provider_connectivity_accepts_response_without_data_list(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, length: int = -1) -> bytes:
                del length
                return b'{"object": "list", "data": []}'

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=lambda request, timeout: FakeResponse(),
        ):
            self.assertTrue(
                verify_provider_connectivity(
                    "https://api.example.com/v1", "key"
                )
            )

    def test_verify_provider_connectivity_rejects_missing_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "base_url"):
            verify_provider_connectivity("", "key")
        with self.assertRaisesRegex(ValueError, "API key"):
            verify_provider_connectivity("https://api.example.com/v1", "")

    def test_verify_provider_connectivity_surfaces_http_error(self) -> None:
        from urllib.error import HTTPError

        def fake_urlopen(request, timeout: float = 8.0):
            del request, timeout
            raise HTTPError("https://api.example.com/v1/models", 401, "Unauthorized", None, None)

        with patch(
            "codex_usage_hud.codex_provider_config.urlopen",
            side_effect=fake_urlopen,
        ):
            with self.assertRaisesRegex(ValueError, "HTTP 401"):
                verify_provider_connectivity("https://api.example.com/v1", "key")


if __name__ == "__main__":
    unittest.main()
