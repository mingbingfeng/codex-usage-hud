from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from codex_usage_hud.core.background_usage import BackgroundUsageStore
from codex_usage_hud.core.codex_file_manager import CodexFileManager, CodexRoots
from codex_usage_hud.core.safe_cleanup import (
    CacheDefinition,
    CleanupPlanError,
    MaintenanceAction,
    MaintenanceActionResult,
    MaintenancePlan,
    MaintenanceResult,
    SQLiteTarget,
    SafeCleanupError,
    SafeCleanupManager,
    audit_sqlite_target,
    platform_cache_definitions,
    read_maintenance_plan,
    read_maintenance_result,
    reveal_cleanup_path,
    run_maintenance_plan,
    run_maintenance_plan_file,
    write_maintenance_plan,
)


NOW = 2_000_000_000.0
DAY = 24 * 60 * 60


class _Tokens:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"token_{self.value:08d}"


def _set_mtime(path: Path, value: float) -> None:
    os.utime(path, (value, value))


def _create_logs_database(path: Path, timestamps: list[int]) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE _sqlx_migrations (
                version BIGINT PRIMARY KEY,
                description TEXT NOT NULL,
                installed_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN NOT NULL,
                checksum BLOB NOT NULL,
                execution_time BIGINT NOT NULL
            );
            CREATE TABLE logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                ts_nanos INTEGER NOT NULL,
                level TEXT NOT NULL,
                target TEXT NOT NULL,
                feedback_log_body TEXT,
                module_path TEXT,
                file TEXT,
                line INTEGER,
                thread_id TEXT,
                process_uuid TEXT,
                estimated_bytes INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX idx_logs_ts ON logs(ts DESC, ts_nanos DESC, id DESC);
            CREATE INDEX idx_logs_thread_id ON logs(thread_id);
            CREATE INDEX idx_logs_thread_id_ts
                ON logs(thread_id, ts DESC, ts_nanos DESC, id DESC);
            CREATE INDEX idx_logs_process_uuid_threadless_ts
                ON logs(process_uuid, ts DESC, ts_nanos DESC, id DESC)
                WHERE thread_id IS NULL;
            INSERT INTO _sqlx_migrations(
                version, description, success, checksum, execution_time
            ) VALUES
                (1, 'logs', 1, X'00', 1),
                (2, 'logs feedback log body', 1, X'00', 1);
            """
        )
        connection.executemany(
            """
            INSERT INTO logs(
                id, ts, ts_nanos, level, target, feedback_log_body, module_path,
                thread_id, process_uuid
            ) VALUES(?, ?, 0, 'INFO', 'target', 'body', 'module', 'thread', 'process')
            """,
            [(index, timestamp) for index, timestamp in enumerate(timestamps, 1)],
        )


def _logs_timestamps(path: Path) -> list[int]:
    with closing(sqlite3.connect(path)) as connection:
        return [
            int(row[0]) for row in connection.execute("SELECT ts FROM logs ORDER BY id")
        ]


def _insert_background_fixture(path: Path, cutoff: int) -> None:
    BackgroundUsageStore(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO scan_state(source_key, last_log_id, initialized_at, updated_at)
            VALUES('source', 99, ?, ?)
            """,
            (cutoff - 10, cutoff + 10),
        )
        connection.executemany(
            """
            INSERT INTO process_evidence(process_uuid, app_evidence, last_seen_at)
            VALUES(?, 'evidence', ?)
            """,
            (("old-process", cutoff - 1), ("new-process", cutoff)),
        )
        connection.executemany(
            """
            INSERT INTO background_events(
                event_id, thread_id, first_seen_at, last_seen_at
            ) VALUES(?, ?, ?, ?)
            """,
            (
                ("old-event", "old-thread", cutoff - 100, cutoff - 1),
                ("new-event", "new-thread", cutoff, cutoff),
            ),
        )
        connection.executemany(
            """
            INSERT INTO background_requests(
                request_id, event_id, source_log_id, occurred_at,
                total_tokens, estimated_input_tokens,
                estimated_cached_tokens, estimated_output_tokens
            ) VALUES(?, ?, ?, ?, 10, 4, 3, 3)
            """,
            (
                ("old-request", "old-event", 1, cutoff - 1),
                ("new-request", "new-event", 2, cutoff),
            ),
        )


class SafeCleanupInventoryTests(unittest.TestCase):
    def make_manager(self, runtime: Path, **kwargs: object) -> SafeCleanupManager:
        options: dict[str, object] = {
            "platform": "win32",
            "hud_runtime_root": runtime,
            "cache_definitions": (),
            "clock": lambda: NOW,
            "token_factory": _Tokens(),
            "running_process_names": lambda: set(),
            "pid_active": lambda _pid: False,
            "lock_probe": lambda _path: False,
        }
        options.update(kwargs)
        return SafeCleanupManager(**options)

    def test_constructor_is_idle_and_hud_whitelist_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            runtime.mkdir()
            safe_names = {
                "crash.log",
                "renderer_fallback.log.1",
                "daemon.log.12",
                "window_tracker.log",
                "hud_geometry.log.2",
                "work-overlay-transitions.jsonl",
                "work-overlay-222-1-commands.jsonl",
            }
            protected_names = {
                "daemon.log.old",
                "hud_settings.json",
                "renderer_cdp_state.json",
                "background-usage.sqlite3",
                "work-overlay-111-1.json",
                "unknown.bin",
            }
            for name in safe_names | protected_names:
                (runtime / name).write_bytes(name.encode("ascii"))
            (runtime / "unknown.bin").write_text("sensitive-payload-marker")
            manager = self.make_manager(
                runtime,
                pid_active=lambda pid: pid == 111,
            )
            self.assertEqual(manager.snapshot()["groups"], [])

            payload = manager.scan()
            safe = [group for group in payload["groups"] if group["tier"] == "safe"]
            protected = [
                group for group in payload["groups"] if group["tier"] == "protected"
            ]
            self.assertEqual(len(safe), len(safe_names))
            self.assertEqual(len(protected), len(protected_names))
            self.assertEqual(payload["defaultSelectedIds"], [])
            self.assertTrue(all(group["requiresOffline"] for group in safe))
            self.assertTrue(all(not group["requiresBackup"] for group in safe))
            offline_safe_ids = {
                group["id"]
                for group in safe
                if group["requiresOffline"]
            }
            self.assertTrue(offline_safe_ids)
            # Offline HUD diagnostics stay selectable but are not one-click defaults.
            for item_id in offline_safe_ids:
                manager.preview([item_id], payload["revision"])
            encoded = json.dumps(payload)
            visible_paths = {Path(str(group["path"])) for group in payload["groups"]}
            self.assertEqual(
                visible_paths,
                {runtime.resolve() / name for name in safe_names | protected_names},
            )
            self.assertNotIn("sensitive-payload-marker", encoded)

    def test_active_overlay_command_history_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / "work-overlay-111-1-commands.jsonl").write_text("{}\n")
            (runtime / "work-overlay-222-1-commands.jsonl").write_text("{}\n")
            manager = self.make_manager(runtime, pid_active=lambda pid: pid == 111)

            groups = manager.scan()["groups"]
            history = [
                group for group in groups if group["category"] == "hud_overlay_history"
            ]
            self.assertEqual(
                [group["tier"] for group in history], ["safe", "protected"]
            )
            self.assertIn("active", history[1]["blockedReason"].lower())

    def test_hud_log_symlink_is_never_a_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "outside.log"
            target.write_text("secret")
            runtime = root / "runtime"
            runtime.mkdir()
            try:
                (runtime / "crash.log").symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            group = self.make_manager(runtime).scan()["groups"][0]
            self.assertEqual(group["tier"], "protected")
            self.assertTrue(target.exists())

    def test_cleanup_payload_exposes_exact_local_target_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            temp_root = root / "temp"
            target = temp_root / "old-bundle"
            target.mkdir(parents=True)
            (target / "payload.bin").write_bytes(b"payload")
            _set_mtime(target / "payload.bin", NOW - (2 * DAY))
            _set_mtime(target, NOW - (2 * DAY))
            definition = CacheDefinition(
                key="temp",
                category="user_temp",
                path=temp_root,
                label="Temporary data",
                impact="Rebuilt later.",
                mode="expired_children",
                min_age_seconds=DAY,
            )
            manager = self.make_manager(
                root / "missing-runtime",
                cache_definitions=(definition,),
            )

            inventory = manager.scan()
            group = inventory["groups"][0]

            self.assertEqual(group["path"], str(target.resolve()))
            self.assertEqual(group["pathKind"], "directory")
            self.assertTrue(str(group["modifiedAt"]).endswith("Z"))
            self.assertEqual(
                manager.resolve_reveal_path(group["id"], inventory["revision"]),
                target.resolve(),
            )
            with self.assertRaisesRegex(SafeCleanupError, "stale"):
                manager.resolve_reveal_path(group["id"], "old-revision")
            with self.assertRaisesRegex(SafeCleanupError, "stale"):
                manager.resolve_reveal_path(
                    group["id"],
                    SafeCleanupManager.scanning_revision("pending-scan"),
                )
            with self.assertRaisesRegex(SafeCleanupError, "unknown"):
                manager.resolve_reveal_path("unknown-item", inventory["revision"])

            # Disappeared targets must fail before any Explorer/Finder launch.
            shutil.rmtree(target)
            with self.assertRaisesRegex(SafeCleanupError, "no longer available|escaped"):
                manager.resolve_reveal_path(group["id"], inventory["revision"])

    def test_native_reveal_uses_argument_vectors_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target_file = root / "file with spaces.txt"
            target_file.write_text("payload")
            calls: list[tuple[list[str], dict[str, object]]] = []

            def launch(command: list[str], **kwargs: object) -> object:
                calls.append((command, kwargs))
                return object()

            self.assertEqual(
                reveal_cleanup_path(root, platform="win32", launcher=launch),
                ("explorer.exe", str(root.resolve())),
            )
            self.assertEqual(
                reveal_cleanup_path(
                    target_file, platform="win32", launcher=launch
                ),
                ("explorer.exe", "/select,", str(target_file.resolve())),
            )
            self.assertEqual(
                reveal_cleanup_path(root, platform="darwin", launcher=launch),
                ("open", str(root.resolve())),
            )
            self.assertEqual(
                reveal_cleanup_path(
                    target_file, platform="darwin", launcher=launch
                ),
                ("open", "-R", str(target_file.resolve())),
            )
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(call[1]["shell"] is False for call in calls))

    def test_native_reveal_rejects_reparse_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")

            with self.assertRaisesRegex(SafeCleanupError, "reparse"):
                reveal_cleanup_path(
                    link,
                    platform="win32",
                    launcher=lambda *_args, **_kwargs: None,
                )

    def test_only_expired_exact_cleanup_backup_names_are_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            old = runtime / "logs_2.sqlite.pre-cleanup-old"
            recent = runtime / "background-usage.sqlite3.pre-cleanup-recent"
            deceptive = runtime / "logs_2.sqlite.pre-cleanup-"
            old.write_bytes(b"old")
            recent.write_bytes(b"recent")
            deceptive.write_bytes(b"not a recognized backup")
            _set_mtime(old, NOW - (8 * DAY))
            _set_mtime(recent, NOW)

            groups = self.make_manager(runtime).scan()["groups"]
            backups = [
                group for group in groups if group["category"] == "cleanup_backups"
            ]
            self.assertEqual(
                [group["tier"] for group in backups], ["safe", "protected"]
            )

    def test_cache_process_gate_and_expired_temp_children(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            browser = root / "browser-cache"
            browser.mkdir()
            (browser / "entry").write_bytes(b"cache")
            temp_root = root / "temp"
            old = temp_root / "old"
            recent = temp_root / "recent"
            old.mkdir(parents=True)
            recent.mkdir()
            (old / "file").write_bytes(b"old")
            (recent / "file").write_bytes(b"recent")
            _set_mtime(old / "file", NOW - (2 * DAY))
            _set_mtime(old, NOW - (2 * DAY))
            _set_mtime(recent / "file", NOW)
            _set_mtime(recent, NOW)
            definitions = (
                CacheDefinition(
                    key="browser",
                    category="browser_cache",
                    path=browser,
                    label="Browser cache",
                    impact="Rebuilt later.",
                    related_processes=("chrome",),
                ),
                CacheDefinition(
                    key="temp",
                    category="user_temp",
                    path=temp_root,
                    label="Temporary data",
                    impact="Rebuilt later.",
                    mode="expired_children",
                    min_age_seconds=DAY,
                ),
            )
            manager = self.make_manager(
                root / "missing-runtime",
                cache_definitions=definitions,
                running_process_names=lambda: {"chrome.exe"},
            )

            groups = manager.scan()["groups"]
            browser_group = next(
                group for group in groups if group["category"] == "browser_cache"
            )
            temp_groups = [
                group for group in groups if group["category"] == "user_temp"
            ]
            self.assertEqual(browser_group["tier"], "protected")
            self.assertIn("running", browser_group["blockedReason"].lower())
            self.assertEqual(
                [group["tier"] for group in temp_groups], ["safe", "protected"]
            )

    def test_old_platform_diagnostics_require_consent_but_not_sqlite_backup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "diagnostics"
            reports.mkdir()
            old = reports / "old-report.dmp"
            recent = reports / "recent-report.dmp"
            old.write_bytes(b"old diagnostics")
            recent.write_bytes(b"recent diagnostics")
            _set_mtime(old, NOW - (8 * DAY))
            _set_mtime(recent, NOW)
            definition = CacheDefinition(
                key="diagnostics",
                category="diagnostic_history",
                path=reports,
                label="Old operating-system diagnostics",
                impact="Old diagnostics will no longer be available.",
                tier="consent",
                mode="expired_children",
                min_age_seconds=7 * DAY,
            )
            manager = self.make_manager(
                root / "missing-runtime",
                cache_definitions=(definition,),
            )

            inventory = manager.scan()
            diagnostic_groups = [
                group
                for group in inventory["groups"]
                if group["category"] == "diagnostic_history"
            ]
            self.assertEqual(
                [group["tier"] for group in diagnostic_groups],
                ["consent", "protected"],
            )
            self.assertEqual(inventory["defaultSelectedIds"], [])
            consent_id = diagnostic_groups[0]["id"]
            with self.assertRaisesRegex(SafeCleanupError, "separate consent"):
                manager.preview([consent_id], inventory["revision"])

            preview = manager.preview(
                [consent_id], inventory["revision"], consent=True
            )
            self.assertTrue(preview["operation"]["includesConsent"])
            self.assertFalse(preview["operation"]["requiresBackup"])
            plan = manager.create_plan(
                [consent_id],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )
            self.assertEqual(plan.actions[0].tier, "consent")
            self.assertEqual(plan.actions[0].kind, "delete_path")

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
            )
            self.assertEqual(result.actions[0].state, "deleted")
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())

    def test_windows_and_macos_adapters_only_return_known_cache_subtrees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            win_home = base / "win-home"
            local = base / "local"
            roaming = base / "roaming"
            profile = local / "Google" / "Chrome" / "User Data" / "Default"
            profile.mkdir(parents=True)
            windows = platform_cache_definitions(
                platform="win32",
                home=win_home,
                env={
                    "LOCALAPPDATA": str(local),
                    "APPDATA": str(roaming),
                    "TEMP": str(base / "temp"),
                },
            )
            self.assertTrue(
                {
                    "user_temp",
                    "developer_cache",
                    "editor_cache",
                    "system_cache",
                    "diagnostic_history",
                }.issubset(
                    {item.category for item in windows}
                )
            )
            windows_by_key = {item.key: item for item in windows}
            self.assertEqual(
                windows_by_key["directx_shader"].path,
                local / "D3DSCache",
            )
            self.assertEqual(windows_by_key["directx_shader"].tier, "safe")
            for key in (
                "yarn",
                "pnpm",
                "bun",
                "go_mod",
                "cargo_registry",
                "maven",
                "uv",
                "poetry",
                "composer",
                "huggingface",
                "torch",
                "modelscope",
                "ollama_models",
                "playwright",
                "cypress",
                "electron",
                "ccache",
                "sccache",
                "android_cache",
                "scoop_cache",
            ):
                self.assertIn(key, windows_by_key)
                self.assertEqual(windows_by_key[key].tier, "safe")
            self.assertEqual(
                windows_by_key["huggingface"].path,
                win_home / ".cache" / "huggingface",
            )
            self.assertEqual(
                windows_by_key["playwright"].path,
                local / "ms-playwright",
            )
            for key in (
                "windows_crash_dumps",
                "windows_error_archive",
                "windows_error_queue",
            ):
                self.assertEqual(windows_by_key[key].tier, "consent")
                self.assertEqual(windows_by_key[key].mode, "expired_children")
                self.assertEqual(windows_by_key[key].min_age_seconds, 7 * DAY)
            # User-scoped Recycle Bin SID folders are optional candidates when readable.
            self.assertTrue(
                all(
                    item.mode == "expired_children"
                    for item in windows
                    if "recycle_bin" in item.key
                )
            )
            browser_paths = [
                item.path for item in windows if item.category == "browser_cache"
            ]
            self.assertTrue(browser_paths)
            self.assertTrue(
                all(
                    path.name
                    in {
                        "Cache",
                        "Code Cache",
                        "GPUCache",
                        "GrShaderCache",
                        "ShaderCache",
                        "DawnCache",
                    }
                    for path in browser_paths
                )
            )
            self.assertFalse(
                any(
                    path.name in {"Cookies", "Login Data", "Bookmarks"}
                    for path in browser_paths
                )
            )

            mac_home = base / "mac-home"
            mac_profile = (
                mac_home / "Library" / "Caches" / "Google" / "Chrome" / "Default"
            )
            mac_profile.mkdir(parents=True)
            mac = platform_cache_definitions(
                platform="darwin",
                home=mac_home,
                env={"TMPDIR": str(base / "mac-temp")},
            )
            self.assertTrue(any(item.category == "browser_cache" for item in mac))
            mac_by_key = {item.key: item for item in mac}
            self.assertEqual(mac_by_key["homebrew"].tier, "safe")
            self.assertEqual(mac_by_key["xcode_derived_data"].tier, "safe")
            for key in (
                "yarn",
                "pnpm",
                "bun",
                "go_mod",
                "cargo_registry",
                "maven",
                "uv",
                "poetry",
                "huggingface",
                "playwright",
                "ollama_models",
            ):
                self.assertIn(key, mac_by_key)
                self.assertEqual(mac_by_key[key].tier, "safe")
            self.assertEqual(
                mac_by_key["uv"].path,
                mac_home / "Library" / "Caches" / "uv",
            )
            self.assertNotIn("scoop_cache", mac_by_key)
            for key in ("macos_diagnostic_reports", "macos_crash_reports"):
                self.assertEqual(mac_by_key[key].tier, "consent")
                self.assertEqual(mac_by_key[key].mode, "expired_children")
                self.assertEqual(mac_by_key[key].min_age_seconds, 7 * DAY)
            self.assertFalse(any(item.path.name == "Cookies" for item in mac))
            self.assertTrue(
                any(item.key == "recycle_bin" for item in mac)
                or not (mac_home / ".Trash").exists()
            )

    def test_codex_temp_candidates_join_default_cleanup_without_sqlite_backup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex"
            candidate = codex_home / ".tmp" / "staging-old"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"staging")
            _set_mtime(candidate, NOW - (8 * DAY))
            file_manager = CodexFileManager(
                CodexRoots(codex_home),
                clock=lambda: NOW,
                process_gate=lambda: False,
                lock_probe=lambda _path: False,
            )

            def candidates():
                payload = file_manager.scan(request_id="safe-cleanup")
                self.assertEqual(payload["operation"]["state"], "completed")
                return file_manager.cleanup_candidates()

            manager = self.make_manager(
                root / "missing-runtime",
                codex_candidate_provider=candidates,
            )
            inventory = manager.scan()
            group = next(
                value
                for value in inventory["groups"]
                if value["category"] == "codex_temp"
            )
            self.assertEqual(group["tier"], "safe")
            self.assertTrue(group["requiresOffline"])
            self.assertTrue(group["requiresCodexClose"])
            self.assertFalse(group["requiresBackup"])
            preview = manager.preview([group["id"]], inventory["revision"])
            self.assertTrue(preview["operation"]["requiresCodexClose"])
            self.assertFalse(preview["operation"]["requiresBackup"])
            self.assertFalse(preview["operation"]["includesConsent"])
            plan = manager.create_plan(
                [group["id"]],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )
            self.assertTrue(plan.actions[0].requires_codex_close)

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
            )

            self.assertEqual(result.actions[0].state, "deleted")
            self.assertFalse(candidate.exists())

    def test_codex_temp_becoming_configured_after_preview_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex"
            candidate = codex_home / ".tmp" / "clone-old"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"clone")
            _set_mtime(candidate, NOW - (8 * DAY))
            file_manager = CodexFileManager(
                CodexRoots(codex_home),
                clock=lambda: NOW,
                process_gate=lambda: False,
                lock_probe=lambda _path: False,
            )

            def candidates():
                file_manager.scan(request_id="safe-cleanup")
                return file_manager.cleanup_candidates()

            manager = self.make_manager(
                root / "missing-runtime",
                codex_candidate_provider=candidates,
            )
            inventory = manager.scan()
            group = next(
                value
                for value in inventory["groups"]
                if value["category"] == "codex_temp"
            )
            preview = manager.preview([group["id"]], inventory["revision"])
            (codex_home / "config.toml").write_text(
                'plugin_source = ".tmp/clone-old"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SafeCleanupError, "no longer"):
                manager.create_plan(
                    [group["id"]],
                    inventory["revision"],
                    preview["operation"]["confirmationToken"],
                )

            self.assertTrue(candidate.exists())

    def test_scan_emits_phased_progress_without_confirmation_token(self) -> None:
        events: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hud = root / "hud"
            hud.mkdir()
            (hud / "crash.log").write_text("x" * 32, encoding="utf-8")
            manager = SafeCleanupManager(
                platform="win32",
                home=root,
                env={},
                hud_runtime_root=hud,
                cache_definitions=(),
                sqlite_targets=(),
                clock=lambda: NOW,
                token_factory=_Tokens(),
                running_process_names=lambda: (),
            )
            manager.progress_publisher = events.append
            snapshot = manager.scan(request_id="scan-progress-1")
        self.assertTrue(events, "expected progressive scan events")
        first = events[0]
        op = first["operation"]
        self.assertEqual(op["state"], "scanning")
        self.assertIn(op["phase"], {"hud", "codex", "processes", "caches", "backups", "sqlite"})
        self.assertLess(int(op["progress"]), 100)
        self.assertTrue(str(first.get("revision") or "").startswith("scanning:"))
        self.assertNotIn("confirmationToken", op)
        final_op = snapshot["operation"]
        self.assertEqual(final_op["state"], "completed")
        self.assertEqual(int(final_op["progress"]), 100)
        self.assertFalse(str(snapshot.get("revision") or "").startswith("scanning:"))
        # progressive events must never issue a confirmation token
        for event in events:
            self.assertNotIn("confirmationToken", event.get("operation") or {})





    def test_run_maintenance_plan_emits_per_action_progress(self) -> None:
        events: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "a.cache"
            second = root / "b.cache"
            first.write_bytes(b"one")
            second.write_bytes(b"two-bytes")
            manager = SafeCleanupManager(
                platform="win32",
                home=root,
                env={},
                hud_runtime_root=root / "hud",
                cache_definitions=(
                    CacheDefinition(
                        key="a",
                        category="developer_cache",
                        path=first,
                        label="A cache",
                        impact="rebuild",
                    ),
                    CacheDefinition(
                        key="b",
                        category="developer_cache",
                        path=second,
                        label="B cache",
                        impact="rebuild",
                    ),
                ),
                sqlite_targets=(),
                clock=lambda: NOW,
                token_factory=_Tokens(),
                running_process_names=lambda: (),
                lock_probe=lambda _path: False,
            )
            (root / "hud").mkdir()
            inventory = manager.scan(request_id="exec-progress")
            ids = [
                group["id"]
                for group in inventory["groups"]
                if group["tier"] == "safe" and not group.get("blockedReason")
            ]
            self.assertGreaterEqual(len(ids), 2)
            preview = manager.preview(ids[:2], inventory["revision"], request_id="exec-progress")
            plan = manager.create_plan(
                ids[:2],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )
            result = run_maintenance_plan(plan, progress_callback=events.append)
            self.assertIn(result.state, {"completed", "partial"})
            self.assertTrue(events, "expected per-action execute progress events")
            self.assertTrue(any(int(event.get("progress") or 0) >= 25 for event in events))
            self.assertTrue(any(event.get("stage") == "start" for event in events))
            self.assertTrue(any(event.get("stage") == "done" for event in events))
            self.assertEqual(max(int(event.get("total") or 0) for event in events), 2)


class SafeCleanupConfirmationTests(unittest.TestCase):
    def make_manager(
        self,
        runtime: Path,
        *,
        sqlite_targets: tuple[SQLiteTarget, ...] = (),
        vacuum_min_reclaim_bytes: int = 0,
    ) -> SafeCleanupManager:
        return SafeCleanupManager(
            platform="win32",
            hud_runtime_root=runtime,
            cache_definitions=(),
            sqlite_targets=sqlite_targets,
            clock=lambda: NOW,
            token_factory=_Tokens(),
            running_process_names=lambda: set(),
            pid_active=lambda _pid: False,
            lock_probe=lambda _path: False,
            vacuum_min_reclaim_bytes=vacuum_min_reclaim_bytes,
        )

    def test_preview_token_is_one_use_and_toctou_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            log = runtime / "crash.log"
            log.write_bytes(b"before")
            manager = self.make_manager(runtime)
            inventory = manager.scan()
            item_id = next(
                group["id"]
                for group in inventory["groups"]
                if group["tier"] == "safe" and group["category"] == "hud_diagnostics"
            )
            preview = manager.preview([item_id], inventory["revision"])
            token = preview["operation"]["confirmationToken"]
            log.write_bytes(b"x")
            with self.assertRaisesRegex(SafeCleanupError, "changed"):
                manager.create_plan([item_id], inventory["revision"], token)
            with self.assertRaisesRegex(SafeCleanupError, "missing or expired"):
                manager.create_plan([item_id], inventory["revision"], token)

    def test_hud_log_append_growth_remains_cleanable_after_hud_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            log = runtime / "daemon.log"
            log.write_bytes(b"before")
            manager = self.make_manager(runtime)
            inventory = manager.scan()
            item_id = next(
                group["id"]
                for group in inventory["groups"]
                if group["tier"] == "safe" and group["category"] == "hud_diagnostics"
            )
            preview = manager.preview([item_id], inventory["revision"])
            with log.open("ab") as handle:
                handle.write(b" during-preview")
            plan = manager.create_plan(
                [item_id],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )
            self.assertTrue(plan.actions[0].allows_growth)
            with log.open("ab") as handle:
                handle.write(b" during-exit")

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
            )

            self.assertEqual(result.actions[0].state, "deleted")
            self.assertFalse(log.exists())

    def test_related_process_start_after_preview_blocks_plan_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "browser-cache"
            cache.mkdir()
            (cache / "entry").write_bytes(b"cache")
            running: set[str] = set()
            manager = SafeCleanupManager(
                platform="win32",
                hud_runtime_root=root / "missing-runtime",
                cache_definitions=(
                    CacheDefinition(
                        key="browser",
                        category="browser_cache",
                        path=cache,
                        label="Browser cache",
                        impact="Rebuilt later.",
                        related_processes=("chrome",),
                    ),
                ),
                clock=lambda: NOW,
                token_factory=_Tokens(),
                running_process_names=lambda: running,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
            )
            inventory = manager.scan()
            item_id = inventory["defaultSelectedIds"][0]
            preview = manager.preview([item_id], inventory["revision"])
            running.add("chrome.exe")

            with self.assertRaisesRegex(SafeCleanupError, "started"):
                manager.create_plan(
                    [item_id],
                    inventory["revision"],
                    preview["operation"]["confirmationToken"],
                )

            self.assertTrue(cache.is_dir())

    def test_stale_revision_and_protected_selection_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / "hud_settings.json").write_text("{}")
            manager = self.make_manager(runtime)
            inventory = manager.scan()
            protected_id = inventory["groups"][0]["id"]
            with self.assertRaisesRegex(SafeCleanupError, "protected"):
                manager.preview([protected_id], inventory["revision"])
            with self.assertRaisesRegex(SafeCleanupError, "stale"):
                manager.preview([protected_id], "old-revision")

    def test_sqlite_requires_separate_consent_and_mandatory_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            database = root / "logs_2.sqlite"
            cutoff = int(NOW - DAY)
            _create_logs_database(database, [cutoff - 1, cutoff])
            manager = self.make_manager(
                runtime,
                sqlite_targets=(SQLiteTarget(database, "logs"),),
            )
            inventory = manager.scan()
            consent_id = next(
                group["id"]
                for group in inventory["groups"]
                if group["tier"] == "consent"
            )
            with self.assertRaisesRegex(SafeCleanupError, "separate consent"):
                manager.preview([consent_id], inventory["revision"])
            with self.assertRaisesRegex(SafeCleanupError, "backup directory"):
                manager.preview([consent_id], inventory["revision"], consent=True)

            preview = manager.preview(
                [consent_id],
                inventory["revision"],
                consent=True,
                backup_directory=root / "backup",
            )
            self.assertTrue(preview["operation"]["includesConsent"])
            self.assertFalse((root / "backup").exists())
            plan = manager.create_plan(
                [consent_id],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )
            self.assertEqual(plan.backup_directory, str((root / "backup").resolve()))
            self.assertEqual(plan.actions[0].cutoff, cutoff)
            self.assertEqual(plan.actions[0].expected_rows, 1)
            self.assertTrue(plan.actions[0].requires_offline)
            self.assertTrue(plan.actions[0].requires_backup)

    def test_preview_reports_same_and_cross_volume_net_reclaim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            database = root / "logs_2.sqlite"
            cutoff = int(NOW - DAY)
            _create_logs_database(database, [cutoff - 1, cutoff])
            source = database.resolve()

            for same_volume in (True, False):
                with self.subTest(same_volume=same_volume):
                    backup = root / (
                        "same-volume-backups"
                        if same_volume
                        else "other-volume-backups"
                    )
                    backup.mkdir()
                    manager = self.make_manager(
                        runtime,
                        sqlite_targets=(SQLiteTarget(database, "logs"),),
                    )
                    inventory = manager.scan()
                    consent_id = next(
                        group["id"]
                        for group in inventory["groups"]
                        if group["tier"] == "consent"
                    )

                    def device(path: Path) -> int:
                        if Path(path).resolve() == source:
                            return 1
                        return 1 if same_volume else 2

                    with patch(
                        "codex_usage_hud.core.safe_cleanup._path_device",
                        side_effect=device,
                    ):
                        operation = manager.preview(
                            [consent_id],
                            inventory["revision"],
                            consent=True,
                            backup_directory=backup,
                        )["operation"]

                    backup_bytes = operation["backupBytes"]
                    estimated_bytes = operation["estimatedBytes"]
                    self.assertGreater(backup_bytes, 0)
                    self.assertEqual(
                        operation["sameVolumeBackupBytes"],
                        backup_bytes if same_volume else 0,
                    )
                    self.assertEqual(
                        operation["netEstimatedBytes"],
                        max(0, estimated_bytes - backup_bytes)
                        if same_volume
                        else estimated_bytes,
                    )
                    self.assertEqual(operation["backupDirectoryLabel"], backup.name)
                    self.assertEqual(operation["backupLabel"], backup.name)
                    self.assertTrue(operation["backupVolumeLabel"])

    def test_result_projection_keeps_backup_location_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            backup_directory = root / "backup-vault"
            backup_directory.mkdir()
            backup = backup_directory / "logs_2.sqlite.pre-cleanup-plan_12345678"
            backup.write_bytes(b"backup")
            result = MaintenanceResult(
                plan_id="plan_12345678",
                state="completed",
                started_at=NOW,
                completed_at=NOW + 1,
                actions=(
                    MaintenanceActionResult(
                        item_id="sqlite-item",
                        category="codex_logs_history",
                        state="completed",
                        estimated_bytes=8192,
                        actual_bytes=4096,
                        deleted_rows=12,
                        backup_path=str(backup),
                        backup_bytes=6144,
                    ),
                ),
            )

            operation = self.make_manager(runtime).apply_maintenance_result(result)[
                "operation"
            ]

            self.assertEqual(operation["backupBytes"], 6144)
            self.assertEqual(operation["backupFiles"], [backup.name])
            self.assertEqual(
                operation["backupDirectoryLabel"], backup_directory.name
            )
            self.assertTrue(operation["backupVolumeLabel"])
            self.assertEqual(
                operation["results"][0]["backupDirectoryLabel"],
                backup_directory.name,
            )
            encoded = json.dumps(operation, ensure_ascii=False)
            self.assertNotIn(str(root), encoded)
            self.assertNotIn(str(backup), encoded)

    def test_plan_creation_rejects_backup_inside_selected_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            (cache / "file").write_bytes(b"x")
            database = root / "logs_2.sqlite"
            cutoff = int(NOW - DAY)
            _create_logs_database(database, [cutoff - 1])
            manager = SafeCleanupManager(
                platform="win32",
                hud_runtime_root=root / "missing-runtime",
                cache_definitions=(
                    CacheDefinition(
                        key="cache",
                        category="developer_cache",
                        path=cache,
                        label="Cache",
                        impact="Rebuilt later.",
                    ),
                ),
                sqlite_targets=(SQLiteTarget(database, "logs"),),
                clock=lambda: NOW,
                token_factory=_Tokens(),
                running_process_names=lambda: set(),
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
            )
            inventory = manager.scan()
            selected = [
                group["id"]
                for group in inventory["groups"]
                if group["tier"] != "protected"
            ]
            with self.assertRaisesRegex(SafeCleanupError, "inside"):
                manager.preview(
                    selected,
                    inventory["revision"],
                    consent=True,
                    backup_directory=cache / "backups",
                )
            self.assertFalse((cache / "backups").exists())


class SafeCleanupMaintenanceTests(unittest.TestCase):
    def build_delete_plan(
        self,
        root: Path,
        *,
        result_path: Path | None = None,
        restart_command: tuple[str, ...] = (),
    ) -> tuple[MaintenancePlan, Path]:
        runtime = root / "runtime"
        runtime.mkdir(exist_ok=True)
        target = runtime / "crash.log"
        target.write_bytes(b"diagnostic")
        manager = SafeCleanupManager(
            platform="win32",
            hud_runtime_root=runtime,
            cache_definitions=(),
            clock=lambda: NOW,
            token_factory=_Tokens(),
            running_process_names=lambda: set(),
            pid_active=lambda _pid: False,
            lock_probe=lambda _path: False,
        )
        inventory = manager.scan()
        item_id = next(
            group["id"]
            for group in inventory["groups"]
            if group["tier"] == "safe" and group["category"] == "hud_diagnostics"
        )
        preview = manager.preview([item_id], inventory["revision"])
        plan = manager.create_plan(
            [item_id],
            inventory["revision"],
            preview["operation"]["confirmationToken"],
            result_path=result_path,
            restart_command=list(restart_command),
        )
        return plan, target

    def build_sqlite_plan(
        self,
        root: Path,
        database: Path,
        kind: str,
        *,
        vacuum_min_reclaim_bytes: int = 0,
    ) -> MaintenancePlan:
        runtime = root / "runtime"
        runtime.mkdir(exist_ok=True)
        manager = SafeCleanupManager(
            platform="win32",
            hud_runtime_root=runtime,
            cache_definitions=(),
            sqlite_targets=(SQLiteTarget(database, kind),),
            clock=lambda: NOW,
            token_factory=_Tokens(),
            running_process_names=lambda: set(),
            pid_active=lambda _pid: False,
            lock_probe=lambda _path: False,
            vacuum_min_reclaim_bytes=vacuum_min_reclaim_bytes,
        )
        inventory = manager.scan()
        item_id = next(
            group["id"] for group in inventory["groups"] if group["tier"] == "consent"
        )
        preview = manager.preview(
            [item_id],
            inventory["revision"],
            consent=True,
            backup_directory=root / "backups",
        )
        return manager.create_plan(
            [item_id],
            inventory["revision"],
            preview["operation"]["confirmationToken"],
        )

    def test_logs_24_hour_cutoff_backup_integrity_and_vacuum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "logs_2.sqlite"
            cutoff = int(NOW - DAY)
            original = [cutoff - 10, cutoff - 1, cutoff, cutoff + 1]
            _create_logs_database(database, original)
            plan = self.build_sqlite_plan(root, database, "logs")

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
                running_process_names=lambda: set(),
            )

            self.assertEqual(result.state, "completed")
            self.assertEqual(result.actions[0].deleted_rows, 2)
            self.assertEqual(_logs_timestamps(database), [cutoff, cutoff + 1])
            backup = Path(result.actions[0].backup_path)
            self.assertTrue(backup.is_file())
            self.assertEqual(_logs_timestamps(backup), original)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )

    def test_backup_space_failure_leaves_source_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "logs_2.sqlite"
            cutoff = int(NOW - DAY)
            original = [cutoff - 1, cutoff]
            _create_logs_database(database, original)
            plan = self.build_sqlite_plan(root, database, "logs")

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
                disk_usage=lambda _path: SimpleNamespace(free=0),
                running_process_names=lambda: set(),
            )

            self.assertEqual(result.state, "failed")
            self.assertEqual(result.actions[0].state, "failed")
            self.assertEqual(_logs_timestamps(database), original)
            self.assertFalse(any((root / "backups").iterdir()))

    def test_post_commit_failure_restores_from_preserved_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "logs_2.sqlite"
            cutoff = int(NOW - DAY)
            original = [cutoff - 1, cutoff]
            _create_logs_database(database, original)
            plan = self.build_sqlite_plan(root, database, "logs")

            def fail_after_commit(stage: str, _path: Path) -> None:
                if stage == "after_commit":
                    raise RuntimeError("simulated post-commit failure")

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
                running_process_names=lambda: set(),
                sqlite_failure_hook=fail_after_commit,
            )

            action = result.actions[0]
            self.assertEqual(action.state, "restored")
            self.assertTrue(action.restored)
            self.assertEqual(_logs_timestamps(database), original)
            self.assertTrue(Path(action.backup_path).is_file())
            self.assertTrue(
                database.with_name(f"{database.name}.failed-{plan.id}").is_file()
            )

    def test_background_30_day_cutoff_preserves_schema_and_scan_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "background-usage.sqlite3"
            cutoff = int(NOW - (30 * DAY))
            _insert_background_fixture(database, cutoff)
            plan = self.build_sqlite_plan(root, database, "background")

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
                running_process_names=lambda: set(),
            )

            self.assertEqual(result.state, "completed")
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    [
                        row[0]
                        for row in connection.execute(
                            "SELECT event_id FROM background_events"
                        )
                    ],
                    ["new-event"],
                )
                self.assertEqual(
                    [
                        row[0]
                        for row in connection.execute(
                            "SELECT request_id FROM background_requests"
                        )
                    ],
                    ["new-request"],
                )
                self.assertEqual(
                    [
                        row[0]
                        for row in connection.execute(
                            "SELECT process_uuid FROM process_evidence"
                        )
                    ],
                    ["new-process"],
                )
                self.assertEqual(
                    connection.execute("SELECT last_log_id FROM scan_state").fetchone()[
                        0
                    ],
                    99,
                )
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT value FROM metadata WHERE key='schema_version'"
                    ).fetchone()[0],
                    "2",
                )

    def test_background_schema_fingerprint_retains_known_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "background-usage.sqlite3"
            cutoff = int(NOW - (30 * DAY))
            _insert_background_fixture(database, cutoff)

            version_two = audit_sqlite_target(
                SQLiteTarget(database, "background"),
                now=NOW,
            )
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "UPDATE metadata SET value='1' WHERE key='schema_version'"
                )
            version_one = audit_sqlite_target(
                SQLiteTarget(database, "background"),
                now=NOW,
            )

            self.assertNotEqual(
                version_one.schema_signature,
                version_two.schema_signature,
            )

    def test_unknown_sqlite_schema_is_protected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "logs_2.sqlite"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TABLE logs(id INTEGER PRIMARY KEY, ts INTEGER)"
                )
            manager = SafeCleanupManager(
                platform="win32",
                hud_runtime_root=root / "missing",
                cache_definitions=(),
                sqlite_targets=(SQLiteTarget(database, "logs"),),
                clock=lambda: NOW,
                token_factory=_Tokens(),
                running_process_names=lambda: set(),
                pid_active=lambda _pid: False,
            )
            group = manager.scan()["groups"][0]
            self.assertEqual(group["tier"], "protected")
            self.assertNotIn(group["id"], manager.snapshot()["defaultSelectedIds"])

    def test_logs_schema_rejects_unknown_objects_and_shape_changes(self) -> None:
        cases = (
            (
                "unknown table",
                "CREATE TABLE unrelated_history(id INTEGER PRIMARY KEY)",
                "unknown schema",
            ),
            (
                "failed migration",
                "UPDATE _sqlx_migrations SET success = 0 WHERE version = 2",
                "incomplete migration",
            ),
            (
                "delete trigger",
                """
                CREATE TRIGGER mutate_migrations_after_log_delete
                AFTER DELETE ON logs
                BEGIN
                    UPDATE _sqlx_migrations
                    SET execution_time = execution_time + 1;
                END;
                """,
                "triggers or views",
            ),
            (
                "view",
                "CREATE VIEW old_logs AS SELECT id, ts FROM logs",
                "triggers or views",
            ),
            (
                "unknown index",
                "CREATE INDEX idx_logs_level ON logs(level)",
                "unknown or missing indexes",
            ),
            (
                "extra column",
                "ALTER TABLE logs ADD COLUMN unexpected TEXT",
                "incompatible columns",
            ),
            (
                "timestamp index shape",
                """
                DROP INDEX idx_logs_ts;
                CREATE INDEX idx_logs_ts ON logs(ts);
                """,
                "incompatible indexes",
            ),
        )
        for label, mutation, expected_error in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                database = root / "logs_2.sqlite"
                _create_logs_database(database, [int(NOW - DAY) - 1])
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.executescript(mutation)
                manager = SafeCleanupManager(
                    platform="win32",
                    hud_runtime_root=root / "missing",
                    cache_definitions=(),
                    sqlite_targets=(SQLiteTarget(database, "logs"),),
                    clock=lambda: NOW,
                    token_factory=_Tokens(),
                    running_process_names=lambda: set(),
                    pid_active=lambda _pid: False,
                )

                group = manager.scan()["groups"][0]

                self.assertEqual(group["tier"], "protected")
                self.assertIn(expected_error, group["blockedReason"])
                self.assertNotIn(group["id"], manager.snapshot()["defaultSelectedIds"])

    def test_sqlite_process_gate_blocks_before_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "logs_2.sqlite"
            cutoff = int(NOW - DAY)
            original = [cutoff - 1, cutoff]
            _create_logs_database(database, original)
            plan = self.build_sqlite_plan(root, database, "logs")

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
                running_process_names=lambda: {"codex.exe"},
            )

            action = result.actions[0]
            self.assertEqual(action.state, "failed")
            self.assertIn("before SQLite backup", action.error)
            self.assertEqual(_logs_timestamps(database), original)
            self.assertFalse(any((root / "backups").iterdir()))

    def test_sqlite_process_gate_rechecks_after_backup_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "logs_2.sqlite"
            cutoff = int(NOW - DAY)
            original = [cutoff - 1, cutoff]
            _create_logs_database(database, original)
            plan = self.build_sqlite_plan(root, database, "logs")
            calls = 0

            def running_process_names() -> set[str]:
                nonlocal calls
                calls += 1
                return set() if calls == 1 else {"codex.exe"}

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
                running_process_names=running_process_names,
            )

            action = result.actions[0]
            self.assertEqual(calls, 2)
            self.assertEqual(action.state, "failed")
            self.assertIn("before SQLite maintenance", action.error)
            self.assertEqual(action.deleted_rows, 0)
            self.assertEqual(_logs_timestamps(database), original)
            backup = Path(action.backup_path)
            self.assertTrue(backup.is_file())
            self.assertEqual(_logs_timestamps(backup), original)

    def test_path_fingerprint_and_lock_are_revalidated_by_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            log = runtime / "crash.log"
            log.write_bytes(b"diagnostic")
            manager = SafeCleanupManager(
                platform="win32",
                hud_runtime_root=runtime,
                cache_definitions=(),
                clock=lambda: NOW,
                token_factory=_Tokens(),
                running_process_names=lambda: set(),
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
            )
            inventory = manager.scan()
            item_id = next(
                group["id"]
                for group in inventory["groups"]
                if group["tier"] == "safe" and group["category"] == "hud_diagnostics"
            )
            preview = manager.preview([item_id], inventory["revision"])
            plan = manager.create_plan(
                [item_id],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )

            locked = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda path: path == log,
            )
            self.assertEqual(locked.actions[0].state, "skipped")
            self.assertEqual(locked.state, "partial")
            self.assertTrue(log.exists())
            log.write_bytes(b"x")
            changed = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
                running_process_names=lambda: set(),
            )
            self.assertEqual(changed.actions[0].state, "skipped")
            self.assertEqual(changed.state, "partial")
            self.assertTrue(log.exists())

    def test_related_process_start_after_plan_blocks_helper_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "browser-cache"
            cache.mkdir()
            (cache / "entry").write_bytes(b"cache")
            manager = SafeCleanupManager(
                platform="win32",
                hud_runtime_root=root / "missing-runtime",
                cache_definitions=(
                    CacheDefinition(
                        key="browser",
                        category="browser_cache",
                        path=cache,
                        label="Browser cache",
                        impact="Rebuilt later.",
                        related_processes=("chrome",),
                    ),
                ),
                clock=lambda: NOW,
                token_factory=_Tokens(),
                running_process_names=lambda: set(),
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
            )
            inventory = manager.scan()
            item_id = inventory["defaultSelectedIds"][0]
            preview = manager.preview([item_id], inventory["revision"])
            plan = manager.create_plan(
                [item_id],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )

            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
                running_process_names=lambda: {"chrome.exe"},
            )

            self.assertEqual(result.actions[0].state, "skipped")
            self.assertEqual(result.state, "partial")
            self.assertTrue(cache.is_dir())

    def test_helper_rejects_plan_path_outside_approved_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            log = runtime / "crash.log"
            outside = root / "outside.txt"
            log.write_bytes(b"diagnostic")
            outside.write_bytes(b"must stay")
            manager = SafeCleanupManager(
                platform="win32",
                hud_runtime_root=runtime,
                cache_definitions=(),
                clock=lambda: NOW,
                token_factory=_Tokens(),
                running_process_names=lambda: set(),
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
            )
            inventory = manager.scan()
            item_id = next(
                group["id"]
                for group in inventory["groups"]
                if group["tier"] == "safe" and group["category"] == "hud_diagnostics"
            )
            preview = manager.preview([item_id], inventory["revision"])
            plan = manager.create_plan(
                [item_id],
                inventory["revision"],
                preview["operation"]["confirmationToken"],
            )
            escaped = replace(
                plan,
                actions=(replace(plan.actions[0], path=str(outside)),),
            )

            with self.assertRaises(CleanupPlanError):
                run_maintenance_plan(
                    escaped,
                    clock=lambda: NOW,
                    pid_active=lambda _pid: False,
                    lock_probe=lambda _path: False,
                )
            self.assertTrue(outside.exists())

    def test_active_wait_pid_skips_every_action_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_bytes(b"keep")
            stat_value = os.lstat(target)
            fingerprint_manager = SafeCleanupManager(
                platform="win32",
                hud_runtime_root=root,
                cache_definitions=(),
                clock=lambda: NOW,
                token_factory=_Tokens(),
                running_process_names=lambda: set(),
                pid_active=lambda _pid: False,
            )
            inventory = fingerprint_manager.scan()
            item_id = (
                inventory["defaultSelectedIds"][0]
                if inventory["defaultSelectedIds"]
                else "item_00000001"
            )
            action = MaintenanceAction(
                item_id=item_id,
                kind="delete_path",
                category="hud_diagnostics",
                tier="safe",
                path=str(target),
                approved_root=str(root),
                fingerprint="unused-before-wait-gate",
                lstat=(
                    stat_value.st_dev,
                    stat_value.st_ino,
                    stat_value.st_mode,
                    stat_value.st_size,
                    stat_value.st_mtime_ns,
                ),
                estimated_bytes=4,
            )
            plan = MaintenancePlan(
                id="plan_00000001",
                created_at=NOW,
                expires_at=NOW + 60,
                parent_pid=123,
                wait_pids=(),
                wait_timeout_seconds=0,
                backup_directory="",
                actions=(action,),
            )
            result = run_maintenance_plan(
                plan,
                clock=lambda: NOW,
                pid_active=lambda pid: pid == 123,
                monotonic=lambda: 0,
                sleep=lambda _seconds: None,
            )
            self.assertEqual(result.state, "partial")
            self.assertEqual(result.actions[0].state, "skipped")
            self.assertTrue(target.exists())

    def test_plan_and_result_round_trip_and_digest_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "logs_2.sqlite"
            cutoff = int(NOW - DAY)
            _create_logs_database(database, [cutoff - 1, cutoff])
            plan = self.build_sqlite_plan(root, database, "logs")
            plan_path = root / "plan.json"
            result_path = root / "result.json"
            write_maintenance_plan(plan_path, plan)
            self.assertEqual(read_maintenance_plan(plan_path), plan)
            envelope = json.loads(plan_path.read_text(encoding="utf-8"))

            result = run_maintenance_plan_file(
                plan_path,
                result_path,
                clock=lambda: NOW,
                pid_active=lambda _pid: False,
                lock_probe=lambda _path: False,
            )
            self.assertEqual(read_maintenance_result(result_path), result)
            self.assertFalse(plan_path.exists())

            envelope["payload"]["expiresAt"] = NOW - 1
            tampered_path = root / "tampered-plan.json"
            tampered_path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(CleanupPlanError, "integrity"):
                read_maintenance_plan(tampered_path)

    def test_restart_command_rejects_unsafe_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "logs_2.sqlite"
            _create_logs_database(database, [int(NOW - DAY) - 1])
            raw = self.build_sqlite_plan(root, database, "logs").to_dict()
            executable = str(Path(sys.executable).resolve())
            invalid_commands = (
                "not-a-list",
                ["relative-python", "-m", "codex_usage_hud"],
                [executable, "line\nbreak"],
                [executable, 123],
            )
            for command in invalid_commands:
                with self.subTest(command=command):
                    candidate = dict(raw)
                    candidate["restartCommand"] = command
                    with self.assertRaises(CleanupPlanError):
                        MaintenancePlan.from_dict(candidate)

    def test_result_path_rejections_happen_before_plan_consumption(self) -> None:
        for case in ("outside", "same", "conflict"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                plan_directory = root / "plans"
                result_directory = root / "other"
                plan_directory.mkdir()
                result_directory.mkdir()
                plan, target = self.build_delete_plan(root)
                plan_path = plan_directory / "plan.json"
                explicit_result: Path | None = None
                if case == "outside":
                    plan = replace(
                        plan,
                        result_path=str(result_directory / "result.json"),
                    )
                elif case == "same":
                    plan = replace(plan, result_path=str(plan_path))
                else:
                    plan = replace(
                        plan,
                        result_path=str(plan_directory / "embedded-result.json"),
                    )
                    explicit_result = plan_directory / "explicit-result.json"
                write_maintenance_plan(plan_path, plan)

                with self.assertRaises(CleanupPlanError):
                    run_maintenance_plan_file(
                        plan_path,
                        explicit_result,
                        clock=lambda: NOW,
                        pid_active=lambda _pid: False,
                        lock_probe=lambda _path: False,
                    )

                self.assertTrue(plan_path.is_file())
                self.assertTrue(target.is_file())

    def test_restart_runner_persists_pending_then_launched_or_failed(self) -> None:
        executable = str(Path(sys.executable).resolve())
        for expected_state in ("launched", "failed"):
            with (
                self.subTest(expected_state=expected_state),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                result_path = root / "result.json"
                command = (executable, "-m", "codex_usage_hud", "--daemon")
                plan, target = self.build_delete_plan(
                    root,
                    result_path=result_path,
                    restart_command=command,
                )
                plan_path = root / "plan.json"
                write_maintenance_plan(plan_path, plan)
                observed: list[tuple[str, tuple[str, ...]]] = []

                def restart_runner(argv: object) -> object:
                    pending = read_maintenance_result(result_path)
                    observed.append((pending.restart_state, tuple(argv)))
                    if expected_state == "failed":
                        raise OSError("restart failed")
                    return object()

                result = run_maintenance_plan_file(
                    plan_path,
                    result_path,
                    restart_runner=restart_runner,
                    clock=lambda: NOW,
                    pid_active=lambda _pid: False,
                    lock_probe=lambda _path: False,
                )

                self.assertEqual(observed, [("pending", command)])
                self.assertEqual(result.restart_state, expected_state)
                self.assertEqual(
                    read_maintenance_result(result_path).restart_state,
                    expected_state,
                )
                self.assertFalse(plan_path.exists())
                self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
