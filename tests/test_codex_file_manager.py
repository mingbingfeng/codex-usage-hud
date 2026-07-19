from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from codex_usage_hud.core import codex_file_manager as file_manager_module
from codex_usage_hud.core.codex_file_manager import (
    CodexFileManager,
    CodexFileManagerWorker,
    CodexRoots,
    FileManagementError,
    resolve_codex_roots,
)


class CodexFileManagerTests(unittest.TestCase):
    def make_manager(self, root: Path, **kwargs) -> CodexFileManager:
        options = {
            "clock": lambda: 2_000_000_000.0,
            "process_gate": lambda: False,
            "lock_probe": lambda _path: False,
            "temp_min_age_seconds": 0,
        }
        options.update(kwargs)
        return CodexFileManager(
            CodexRoots(root),
            **options,
        )

    def test_constructor_does_not_scan_or_read_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "secret.txt").write_text("metadata only", encoding="utf-8")
            manager = self.make_manager(root)
            self.assertEqual(manager.snapshot()["revision"], "")
            self.assertEqual(manager.snapshot()["items"], [])

    def test_config_root_resolution_is_deferred_until_explicit_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.toml").write_text('sqlite_home = "sqlite"\n', encoding="utf-8")
            with patch.object(
                file_manager_module,
                "_read_config",
                wraps=file_manager_module._read_config,
            ) as read_config:
                manager = CodexFileManager(
                    env={"CODEX_HOME": str(root)},
                    process_gate=lambda: False,
                    lock_probe=lambda _path: False,
                )
                read_config.assert_not_called()
                manager.scan()
                self.assertGreaterEqual(read_config.call_count, 1)
            self.assertEqual(manager.roots.sqlite_homes, (root / "sqlite",))

    def test_root_resolver_prefers_code_home_and_supports_sqlite_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            configured = base / "configured"
            sqlite = base / "sqlite"
            roots = resolve_codex_roots(
                env={"CODEX_HOME": str(configured), "CODEX_SQLITE_HOME": str(sqlite)},
                home=base / "home",
                platform_candidates=(base / "legacy",),
            )
            self.assertEqual(roots.codex_home, configured)
            self.assertEqual(roots.sqlite_homes, (sqlite,))
            self.assertEqual(resolve_codex_roots(env={}, home=base).codex_home, base / ".codex")

    def test_unavailable_toml_parser_fails_closed_for_temp_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.toml").write_text(
                'sqlite_home = "sqlite"\nsource = ".tmp/bundled-marketplaces/openai-bundled"\n',
                encoding="utf-8",
            )
            candidate = root / ".tmp" / "staging-old"
            candidate.parent.mkdir()
            candidate.write_bytes(b"x")
            with patch.object(file_manager_module, "tomllib", None):
                roots = resolve_codex_roots(env={"CODEX_HOME": str(root)})
                self.assertEqual(roots.sqlite_homes, (root / "sqlite",))
                payload = self.make_manager(root).scan()
            item = next(item for item in payload["items"] if item["relativePath"] == ".tmp/staging-old")
            self.assertEqual(item["policy"], "blocked")

    def test_inventory_metadata_policy_and_size_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.toml").write_text(
                'plugin_source = ".tmp/bundled-marketplaces/openai-bundled"\n',
                encoding="utf-8",
            )
            (root / ".tmp").mkdir()
            (root / ".tmp" / "staging-old").mkdir()
            (root / ".tmp" / "staging-old" / "payload.bin").write_bytes(b"1234")
            (root / ".tmp" / "bundled-marketplaces").mkdir()
            (root / ".tmp" / "bundled-marketplaces" / "openai-bundled").mkdir()
            (root / ".tmp" / "bundled-marketplaces" / "openai-bundled" / "active").write_text(
                "x", encoding="utf-8"
            )
            (root / "unknown.bin").write_bytes(b"12")
            (root / "auth.json").write_text("{}", encoding="utf-8")
            (root / "logs_2.sqlite").write_bytes(b"db")
            (root / "logs_2.sqlite-wal").write_bytes(b"wal")
            (root / "logs_2.sqlite-shm").write_bytes(b"shm")
            (root / "sessions").mkdir()
            (root / "sessions" / "thread-1.jsonl").write_text("sensitive", encoding="utf-8")
            (root / "plugins").mkdir()
            (root / "plugins" / "demo-plugin").mkdir()
            (root / "plugins" / "demo-plugin" / "bundle.js").write_text("x", encoding="utf-8")
            (root / "plugins" / ".plugin-appserver").mkdir()
            (root / ".sandbox-bin").mkdir()
            (root / ".sandbox-bin" / "runner").write_bytes(b"runner")

            payload = self.make_manager(root).scan()
            self.assertGreater(payload["totals"]["bytes"], 0)
            self.assertEqual(payload["rootLabel"], "CODEX_HOME")
            items = payload["items"]
            serialized = str(items)
            self.assertNotIn("sensitive", serialized)
            self.assertNotIn(str(root), serialized)
            policies = {item["relativePath"]: item["policy"] for item in items}
            self.assertEqual(policies[".tmp/staging-old"], "candidate")
            self.assertEqual(policies[".tmp/bundled-marketplaces/openai-bundled"], "blocked")
            self.assertEqual(policies["unknown.bin"], "unknown")
            self.assertEqual(policies["auth.json"], "managed")
            self.assertEqual(policies["logs_2.sqlite"], "blocked")
            self.assertEqual(policies["logs_2.sqlite-wal"], "blocked")
            self.assertEqual(policies["sessions/thread-1.jsonl"], "managed")
            self.assertEqual(policies["plugins/demo-plugin"], "managed")
            self.assertEqual(policies["plugins/.plugin-appserver"], "blocked")
            self.assertEqual(policies[".sandbox-bin"], "blocked")

    def test_symlink_is_blocked_and_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            (target / "data").write_bytes(b"x")
            link = root / ".tmp"
            link.mkdir()
            try:
                (link / "staging-link").symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available")
            payload = self.make_manager(root).scan()
            item = next(item for item in payload["items"] if item["relativePath"] == ".tmp/staging-link")
            self.assertEqual(item["policy"], "blocked")

    def test_revision_opaque_ids_confirmation_and_toctou_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / ".tmp"
            candidate.mkdir()
            target = candidate / "staging-old"
            target.write_bytes(b"before")
            manager = self.make_manager(root)
            inventory = manager.scan()
            item = next(item for item in inventory["items"] if item["policy"] == "candidate")
            self.assertNotIn(str(target), item["id"])
            preview = manager.preview([item["id"]], inventory["revision"])
            token = preview["operation"]["confirmationToken"]
            target.write_bytes(b"after")
            result = manager.execute([item["id"]], inventory["revision"], token)
            self.assertEqual(result["operation"]["state"], "partial")
            self.assertTrue(target.exists())
            with self.assertRaises(FileManagementError):
                manager.preview([item["id"]], "stale-revision")

    def test_process_gate_queues_and_execute_queued_after_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / ".tmp" / "staging-old"
            candidate.parent.mkdir()
            candidate.write_bytes(b"x")
            active = [True]
            manager = self.make_manager(root, process_gate=lambda: active[0])
            inventory = manager.scan()
            item = next(item for item in inventory["items"] if item["policy"] == "candidate")
            preview = manager.preview([item["id"]], inventory["revision"])
            queued = manager.execute(
                [item["id"]], inventory["revision"], preview["operation"]["confirmationToken"]
            )
            self.assertEqual(queued["operation"]["state"], "queued_exit")
            self.assertTrue(candidate.exists())
            active[0] = False
            completed = manager.execute_queued()
            self.assertEqual(completed["operation"]["state"], "completed")
            self.assertFalse(candidate.exists())

    def test_lock_gate_and_partial_error_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            temp = root / ".tmp"
            temp.mkdir()
            locked = temp / "staging-locked"
            failing = temp / "clone-failing"
            locked.write_bytes(b"a")
            failing.write_bytes(b"b")
            manager = self.make_manager(root, lock_probe=lambda path: path == locked)
            inventory = manager.scan()
            candidates = [item for item in inventory["items"] if item["policy"] == "candidate"]
            preview = manager.preview([item["id"] for item in candidates], inventory["revision"])
            result = manager.execute(
                [item["id"] for item in candidates],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )
            self.assertEqual(result["operation"]["state"], "partial")
            self.assertTrue(locked.exists())
            self.assertFalse(failing.exists())

    def test_managed_action_uses_official_command_without_raw_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir()
            transcript = sessions / "thread-abc.jsonl"
            transcript.write_text("prompt must never be returned", encoding="utf-8")
            commands: list[list[str]] = []
            manager = self.make_manager(root, command_runner=lambda command: commands.append(list(command)) or 0)
            inventory = manager.scan()
            item = next(item for item in inventory["items"] if item["relativePath"] == "sessions/thread-abc.jsonl")
            preview = manager.preview(
                [item["id"]], inventory["revision"], action="delete_session"
            )
            result = manager.execute_managed(
                "delete_session",
                [item["id"]],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )
            self.assertEqual(result["operation"]["state"], "completed")
            self.assertEqual(commands, [["codex", "delete", "thread-abc"]])
            self.assertTrue(transcript.exists())

    def test_worker_ack_is_immediate_and_idle_does_not_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "unknown.txt").write_text("x", encoding="utf-8")
            updates: list[dict[str, object]] = []
            ready = threading.Event()
            worker = CodexFileManagerWorker(
                self.make_manager(root),
                on_update=lambda payload: (updates.append(payload), ready.set()),
            )
            try:
                started = time.monotonic()
                ack = worker.enqueue({"action": "scan", "requestId": "r1"})
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertEqual(ack["status"], "accepted")
                self.assertTrue(ready.wait(2.0))
                self.assertEqual(updates[-1]["operation"]["state"], "completed")
                before = len(updates)
                time.sleep(0.05)
                self.assertEqual(len(updates), before)
            finally:
                worker.close()

    def test_cancelled_scan_and_item_limit_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".tmp").mkdir()
            for index in range(10):
                (root / ".tmp" / f"staging-{index}").write_text("x", encoding="utf-8")
            manager = self.make_manager(root, item_limit=2)
            payload = manager.scan()
            self.assertTrue(payload["totals"]["truncated"])
            self.assertEqual(manager.snapshot()["operation"]["state"], "completed")

    def test_scan_cancellation_stops_worker_without_inventory_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".tmp").mkdir()
            for index in range(20):
                (root / ".tmp" / f"staging-{index}").write_text("x", encoding="utf-8")
            calls = 0
            holder: dict[str, CodexFileManager] = {}

            def token_factory() -> str:
                nonlocal calls
                calls += 1
                if calls == 4 and "manager" in holder:
                    holder["manager"].cancel()
                return f"opaque-{calls}"

            manager = self.make_manager(root, token_factory=token_factory)
            holder["manager"] = manager
            payload = manager.scan()
            self.assertEqual(payload["operation"]["state"], "cancelled")
            self.assertEqual(payload["revision"], "")


if __name__ == "__main__":
    unittest.main()
