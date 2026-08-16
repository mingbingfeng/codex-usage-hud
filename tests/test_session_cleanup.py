from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from codex_usage_hud.core.session_cleanup import (
    SessionCleanupError,
    SessionDeleteCapability,
    SessionCleanupManager,
)
from codex_usage_hud.core.deleted_usage import DeletedUsageLedger


ROOT_ID = "10000000-0000-4000-8000-000000000001"
CHILD_ID = "10000000-0000-4000-8000-000000000002"
SECOND_ID = "10000000-0000-4000-8000-000000000003"


def _create_state(path: Path, rows: list[tuple[object, ...]]) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0,
                updated_at_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT NOT NULL,
                child_thread_id TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO threads(id, rollout_path, title, cwd, archived, updated_at_ms)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


class SessionCleanupManagerTests(unittest.TestCase):
    def _fixture(self, *, child_status: str = "closed"):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        sessions = root / "sessions"
        archived = root / "archived_sessions"
        sessions.mkdir()
        archived.mkdir()
        rollouts = {
            ROOT_ID: sessions / f"rollout-{ROOT_ID}.jsonl",
            CHILD_ID: sessions / f"rollout-{CHILD_ID}.jsonl",
            SECOND_ID: archived / f"rollout-{SECOND_ID}.jsonl",
        }
        metadata = {
            ROOT_ID: {
                "model_provider": "openai-custom",
                "originator": "Codex Desktop",
                "source": "vscode",
            },
            CHILD_ID: {
                "model_provider": "openai-custom",
                "originator": "Codex Desktop",
                "source": "vscode",
            },
            SECOND_ID: {
                "model_provider": "routin",
                "originator": "codex-tui",
                "source": "cli",
            },
        }
        for session_id, path in rollouts.items():
            path.write_text(
                json.dumps({"type": "session_meta", "payload": metadata[session_id]})
                + "\n",
                encoding="utf-8",
            )
        state_db = root / "state_5.sqlite"
        _create_state(
            state_db,
            [
                (ROOT_ID, str(rollouts[ROOT_ID]), "Root", str(root / "project-a"), 0, 1_700_000_000_000),
                (CHILD_ID, str(rollouts[CHILD_ID]), "Child", str(root / "project-a"), 0, 1_700_000_100_000),
                (SECOND_ID, str(rollouts[SECOND_ID]), "Archived", str(root / "project-b"), 1, 1_600_000_000_000),
            ],
        )
        with closing(sqlite3.connect(state_db)) as connection, connection:
            connection.execute(
                "INSERT INTO thread_spawn_edges VALUES(?, ?, ?)",
                (ROOT_ID, CHILD_ID, child_status),
            )
        index_path = root / "session_index.jsonl"
        index_path.write_text(
            "\n".join(
                json.dumps({"id": item, "thread_name": f"Name {item[-1]}"})
                for item in (ROOT_ID, CHILD_ID, SECOND_ID)
            )
            + "\n",
            encoding="utf-8",
        )
        manager = SessionCleanupManager(
            state_db_path=state_db,
            sessions_root=sessions,
            session_index_path=index_path,
            token_factory=iter(f"token-{index}" for index in range(100)).__next__,
        )
        return temporary, root, state_db, index_path, rollouts, manager

    def test_scan_groups_children_and_keeps_sensitive_identifiers_private(self) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="scan-1")

        self.assertEqual(payload["totals"]["sessions"], 2)
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        self.assertEqual(root_row["descendantCount"], 1)
        self.assertTrue(root_row["selectable"])
        self.assertEqual(root_row["clientKind"], "app")
        self.assertEqual(root_row["modelProvider"], "openai-custom")
        self.assertEqual(
            root_row["updatedAt"],
            datetime.fromtimestamp(1_700_000_100_000 / 1000.0)
            .astimezone()
            .isoformat(timespec="seconds"),
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(ROOT_ID, serialized)
        self.assertNotIn(CHILD_ID, serialized)
        self.assertNotIn(str(root), serialized)

    def test_workdir_for_item_rechecks_current_inventory_directory(self) -> None:
        fixture = self._fixture()
        temporary, root, state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        project = root / "project-a"
        project.mkdir()

        payload = manager.scan()
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        self.assertEqual(
            manager.workdir_for_item(root_row["id"], payload["revision"]), project
        )
        self.assertIsNone(manager.workdir_for_item("unknown", payload["revision"]))
        self.assertIsNone(manager.workdir_for_item(root_row["id"], "stale-revision"))

        with closing(sqlite3.connect(state)) as connection, connection:
            connection.execute(
                "UPDATE threads SET cwd = ? WHERE id = ?",
                ("relative-workdir", ROOT_ID),
            )
        self.assertIsNone(
            manager.workdir_for_item(root_row["id"], payload["revision"])
        )

    def test_scan_reports_the_protection_phase_and_progress(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        progress_payloads: list[dict[str, object]] = []
        operations: list[dict[str, object]] = []

        def publish(payload: dict[str, object]) -> None:
            progress_payloads.append(dict(payload))
            operations.append(dict(payload["operation"]))

        manager.progress_publisher = publish

        manager.scan(request_id="scan-progress")

        phase_three = [
            operation
            for operation in operations
            if operation.get("phaseIndex") == 3
        ]
        self.assertTrue(phase_three)
        self.assertEqual(phase_three[0]["phaseLabel"], "Checking deletion protection")
        self.assertEqual(phase_three[0]["progress"], 60)
        self.assertEqual(phase_three[-1]["progress"], 99)
        self.assertTrue(progress_payloads)
        self.assertTrue(all("sessions" not in payload for payload in progress_payloads))

    def test_current_and_running_session_trees_are_blocked(self) -> None:
        fixture = self._fixture(child_status="running")
        temporary, _root, state, index, _rollouts, _manager = fixture
        self.addCleanup(temporary.cleanup)
        manager = SessionCleanupManager(
            state_db_path=state,
            sessions_root=state.parent / "sessions",
            session_index_path=index,
            current_session_ids=lambda: (SECOND_ID,),
            token_factory=iter(f"blocked-{index}" for index in range(100)).__next__,
        )

        payload = manager.scan()
        rows = {row["title"]: row for row in payload["sessions"]}

        self.assertEqual(rows["Root"]["status"], "running")
        self.assertFalse(rows["Root"]["selectable"])
        self.assertEqual(rows["Archived"]["status"], "current")
        self.assertFalse(rows["Archived"]["selectable"])

    def test_historical_open_spawn_edge_does_not_claim_runtime_activity(self) -> None:
        fixture = self._fixture(child_status="open")
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan()
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        self.assertEqual(root_row["status"], "idle")
        self.assertTrue(root_row["selectable"])

    def test_provider_history_delete_removes_matching_session_tree(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        expected_bytes = sum(
            rollouts[session_id].stat().st_size
            for session_id in (ROOT_ID, CHILD_ID)
        )

        result = manager.delete_provider_history("OPENAI-CUSTOM", request_id="provider-1")

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(result["operation"]["provider"], "openai-custom")
        self.assertEqual(result["operation"]["sessionCount"], 1)
        self.assertEqual(result["operation"]["deletedCount"], 1)
        self.assertEqual(result["operation"]["actualBytes"], expected_bytes)
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())
        self.assertFalse((state.parent / ".hud-session-delete-staging").exists())
        with closing(sqlite3.connect(state)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM threads").fetchone()[0], 1)

    def test_provider_history_delete_refuses_protected_matching_tree(self) -> None:
        fixture = self._fixture(child_status="running")
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        with self.assertRaisesRegex(SessionCleanupError, "受保护会话"):
            manager.delete_provider_history("openai-custom")

    def test_session_transfer_copy_keeps_source_and_uses_private_session_context(self) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        calls: list[tuple[str, str, str]] = []
        verified: list[tuple[str, str]] = []
        target_id = "10000000-0000-4000-8000-000000000004"

        def fork(session_id: str, provider: str, cwd: str) -> str:
            calls.append((session_id, provider, cwd))
            return target_id

        def verify(session_id: str, provider: str) -> bool:
            verified.append((session_id, provider))
            return bool(session_id and provider)

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "OPENAI-CUSTOM",
            "ROUTIN",
            "copy",
            fork=fork,
            verify=verify,
            request_id="transfer-copy",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["migratedCount"], 0)
        self.assertEqual(calls, [(ROOT_ID, "routin", str(root / "project-a"))])
        self.assertEqual(verified, [(target_id, "routin")])
        self.assertTrue(_rollouts[ROOT_ID].exists())

    def test_session_transfer_copy_keeps_source_when_target_is_not_persistent(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "copy",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000009",
            verify=lambda *_args: False,
            request_id="transfer-copy-unverified",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "partial")
        self.assertEqual(operation["copiedCount"], 0)
        self.assertEqual(operation["failedCount"], 1)
        self.assertIn("持久化验证未通过", operation["results"][0]["error"])
        self.assertTrue(rollouts[ROOT_ID].exists())

    def test_session_transfer_migrate_deletes_source_only_after_fork(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        forked: list[str] = []

        def fork(session_id: str, _provider: str, _cwd: str) -> str:
            forked.append(session_id)
            return "10000000-0000-4000-8000-000000000005"

        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=fork,
            verify=lambda session_id, provider: bool(session_id and provider),
            request_id="transfer-migrate",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["migratedCount"], 1)
        self.assertEqual(forked, [ROOT_ID])
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())
        with closing(sqlite3.connect(state)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM threads").fetchone()[0], 1)

    def test_session_transfer_migrate_batches_source_deletion_after_all_forks(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        # Make the archived root part of the same source-provider selection.
        rollouts[SECOND_ID].write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "model_provider": "openai-custom",
                        "originator": "Codex Desktop",
                        "source": "vscode",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        payload = manager.scan(request_id="transfer-scan")
        selected = [
            row["id"]
            for row in payload["sessions"]
            if row["title"] in {"Root", "Archived"}
        ]
        target_ids = iter(
            (
                "10000000-0000-4000-8000-000000000006",
                "10000000-0000-4000-8000-000000000007",
            )
        )

        def fork(_session_id: str, _provider: str, _cwd: str) -> str:
            return next(target_ids)

        progress: list[dict[str, object]] = []
        manager.progress_publisher = progress.append
        with patch.object(manager, "preview", wraps=manager.preview) as preview, patch.object(
            manager, "execute", wraps=manager.execute
        ) as execute:
            result = manager.transfer(
                selected,
                payload["revision"],
                "openai-custom",
                "routin",
                "migrate",
                fork=fork,
                verify=lambda session_id, provider: bool(session_id and provider),
                request_id="transfer-batch",
            )

        operation = result["operation"]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["migratedCount"], 2)
        self.assertEqual(preview.call_count, 1)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(len(preview.call_args.args[0]), 2)
        self.assertEqual(len(execute.call_args.args[0]), 2)
        self.assertTrue(progress)
        self.assertTrue(all("results" not in row["operation"] for row in progress))
        self.assertTrue(all("sessions" not in row for row in progress))

    def test_session_transfer_migrate_keeps_source_when_target_is_not_persistent(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="transfer-scan")
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        result = manager.transfer(
            [root_row["id"]],
            payload["revision"],
            "openai-custom",
            "routin",
            "migrate",
            fork=lambda *_args: "10000000-0000-4000-8000-000000000008",
            verify=lambda *_args: False,
            request_id="transfer-unverified",
        )

        operation = result["operation"]
        self.assertEqual(operation["state"], "partial")
        self.assertEqual(operation["copiedCount"], 1)
        self.assertEqual(operation["migratedCount"], 0)
        self.assertIn("持久化验证未通过", operation["results"][0]["error"])
        self.assertTrue(rollouts[ROOT_ID].exists())
        self.assertTrue(rollouts[CHILD_ID].exists())

    def test_ambiguous_spawn_relation_blocks_every_affected_root(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        with closing(sqlite3.connect(state)) as connection, connection:
            connection.execute(
                "INSERT INTO thread_spawn_edges VALUES(?, ?, ?)",
                (SECOND_ID, CHILD_ID, "closed"),
            )

        payload = manager.scan()

        self.assertGreaterEqual(payload["totals"]["unresolved"], 1)
        self.assertTrue(payload["sessions"])
        self.assertTrue(all(not row["selectable"] for row in payload["sessions"]))
        self.assertTrue(all(row["status"] == "unresolved" for row in payload["sessions"]))

    def test_preview_and_execute_delete_parent_cascade_in_one_local_transaction(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([root_row["id"]], scan["revision"])
        token = preview["operation"]["confirmationToken"]

        result = manager.execute(
            [root_row["id"]],
            scan["revision"],
            token,
            request_id="delete-1",
        )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(result["operation"]["deletedCount"], 1)
        self.assertEqual(
            [row["title"] for row in result["sessions"]],
            ["Archived"],
        )
        with closing(sqlite3.connect(state)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM threads").fetchone()[0], 1)
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())
        self.assertFalse((_root / ".hud-session-delete-staging").exists())

    def test_delete_commits_prepared_usage_only_after_cascade_verification(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        events: list[tuple[str, object]] = []

        def prepare(item) -> str:
            self.assertTrue(all(path.exists() for path in item._rollout_paths))
            events.append(("prepare", item._session_id))
            return "usage-receipt"

        def commit(receipt: object) -> None:
            self.assertTrue(all(not path.exists() for path in selected_paths))
            events.append(("commit", receipt))

        manager.usage_snapshot_prepare = prepare
        manager.usage_snapshot_commit = commit
        manager.usage_snapshot_discard = lambda receipt: events.append(
            ("discard", receipt)
        )
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        selected_paths = {rollouts[ROOT_ID], rollouts[CHILD_ID]}
        preview = manager.preview([root_row["id"]], scan["revision"])

        result = manager.execute(
            [root_row["id"]],
            scan["revision"],
            preview["operation"]["confirmationToken"],
        )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(
            events,
            [("prepare", ROOT_ID), ("commit", "usage-receipt")],
        )

    def test_delete_commit_is_idempotent_after_usage_refresh_recovers_snapshot(self) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        now = datetime.now(timezone.utc)
        ledger = DeletedUsageLedger(root / "deleted-session-usage.json")

        class Parser:
            @staticmethod
            def load_records_lenient(path: Path) -> Path:
                return path

            @staticmethod
            def usage_events(_path: Path) -> tuple[dict[str, object], ...]:
                return (
                    {
                        "timestamp": now,
                        "model": "gpt-test",
                        "total_tokens": 10,
                        "input_tokens": 8,
                        "output_tokens": 2,
                    },
                )

            @staticmethod
            def session_model_provider(_path: Path) -> str:
                return "custom"

        parser = Parser()

        def prepare(item) -> str:
            return ledger.prepare(
                session_id=item._session_id,
                family_session_ids=(item._session_id, *item._descendant_ids),
                title=item.title,
                workdir_name=item.workdir_name,
                rollout_paths=item._rollout_paths,
                parser=parser,
                now=now,
            )

        manager.usage_snapshot_prepare = prepare
        manager.usage_snapshot_commit = lambda receipt: ledger.commit(
            str(receipt), now=now
        )
        manager.usage_snapshot_discard = lambda receipt: ledger.discard(str(receipt))
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([root_row["id"]], scan["revision"])
        verify_deleted = manager._verify_deleted_batch

        def verify_and_refresh(items) -> None:
            verify_deleted(items)
            self.assertEqual(len(ledger.sessions(now=now)), 1)

        with patch.object(
            manager,
            "_verify_deleted_batch",
            side_effect=verify_and_refresh,
        ):
            result = manager.execute(
                [root_row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        sessions = ledger.sessions(now=now)
        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(sessions[0].events), 2)

    def test_delete_failure_discards_pending_usage_snapshot(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        events: list[tuple[str, object]] = []
        manager.usage_snapshot_prepare = lambda item: "pending-usage"
        manager.usage_snapshot_commit = lambda receipt: events.append(
            ("commit", receipt)
        )
        manager.usage_snapshot_discard = lambda receipt: events.append(
            ("discard", receipt)
        )
        scan = manager.scan()
        row = next(row for row in scan["sessions"] if row["title"] == "Archived")
        preview = manager.preview([row["id"]], scan["revision"])

        with patch.object(
            manager,
            "_delete_local_batch",
            side_effect=SessionCleanupError("local delete rejected"),
        ):
            result = manager.execute(
                [row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "failed")
        self.assertEqual(events, [("discard", "pending-usage")])

    def test_usage_snapshot_failure_prevents_local_delete(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([row["id"]], scan["revision"])

        def fail_prepare(_item) -> object:
            raise SessionCleanupError("usage snapshot unavailable")

        manager.usage_snapshot_prepare = fail_prepare
        delete_batch = MagicMock()
        with patch.object(manager, "_delete_local_batch", delete_batch):
            result = manager.execute(
                [row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "failed")
        delete_batch.assert_not_called()
        self.assertIn(
            "usage snapshot unavailable",
            result["operation"]["results"][0]["error"],
        )

    def test_confirmation_is_single_use_and_revision_bound(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        item_id = scan["sessions"][0]["id"]
        preview = manager.preview([item_id], scan["revision"])
        token = preview["operation"]["confirmationToken"]
        with self.assertRaises(SessionCleanupError):
            manager.execute([item_id], "stale", token)

    def test_missing_local_store_capability_disables_delete(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)

        with patch.object(
            manager,
            "probe_capability",
            return_value=SessionDeleteCapability(
                False,
                "Codex local session store is unavailable.",
            ),
        ):
            payload = manager.scan()

        self.assertFalse(payload["capability"]["available"])
        self.assertEqual(payload["totals"]["selectable"], 0)
        self.assertTrue(all(not row["selectable"] for row in payload["sessions"]))
    def test_batch_delete_fails_as_a_whole_before_any_local_mutation(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        selected = [row["id"] for row in scan["sessions"]]
        preview = manager.preview(selected, scan["revision"])

        with patch.object(
            manager,
            "_delete_local_batch",
            side_effect=SessionCleanupError("local transaction rejected"),
        ):
            result = manager.execute(
                selected,
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "failed")
        self.assertEqual(result["operation"]["deletedCount"], 0)
        self.assertEqual(result["operation"]["failedCount"], 2)

    def test_batch_delete_probes_capability_once_before_revalidating_items(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        selected = [row["id"] for row in scan["sessions"]]
        preview = manager.preview(selected, scan["revision"])

        with patch.object(manager, "probe_capability", wraps=manager.probe_capability) as probe:
            result = manager.execute(
                selected,
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(probe.call_count, 1)

    def test_batch_delete_reuses_one_preflight_and_one_final_verification(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        selected = [row["id"] for row in scan["sessions"]]
        preview = manager.preview(selected, scan["revision"])

        with patch.object(manager, "_load_state", wraps=manager._load_state) as load_state:
            result = manager.execute(
                selected,
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "completed")
        self.assertEqual(load_state.call_count, 3)

    def test_batch_delete_failure_preserves_all_selected_session_trees(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        archived_row = next(row for row in scan["sessions"] if row["title"] == "Archived")
        preview = manager.preview(
            [root_row["id"], archived_row["id"]],
            scan["revision"],
        )

        with patch.object(
            manager,
            "_delete_local_batch",
            side_effect=SessionCleanupError("local transaction rejected"),
        ):
            result = manager.execute(
                [root_row["id"], archived_row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["deletedCount"], 0)
        self.assertEqual(result["operation"]["failedCount"], 2)
        self.assertFalse(result["operation"]["interrupted"])
        self.assertEqual(len(manager.scan()["sessions"]), 2)

    def test_delete_verification_checks_index_rows_without_titles(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, index, _rollouts, manager = fixture
        self.addCleanup(temporary.cleanup)
        index.write_text(
            json.dumps({"id": ROOT_ID}) + "\n" + json.dumps({"id": SECOND_ID}) + "\n",
            encoding="utf-8",
        )
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([root_row["id"]], scan["revision"])

        def delete_without_index(items) -> None:
            family = (items[0]._session_id, *items[0]._descendant_ids)
            placeholders = ",".join("?" for _ in family)
            with closing(sqlite3.connect(_state)) as connection, connection:
                connection.execute(
                    f"DELETE FROM thread_spawn_edges WHERE parent_thread_id IN ({placeholders}) "
                    f"OR child_thread_id IN ({placeholders})",
                    (*family, *family),
                )
                connection.execute(f"DELETE FROM threads WHERE id IN ({placeholders})", family)
            for path in items[0]._rollout_paths:
                path.unlink()

        with patch.object(manager, "_delete_local_batch", side_effect=delete_without_index):
            result = manager.execute(
                [root_row["id"]],
                scan["revision"],
                preview["operation"]["confirmationToken"],
            )

        self.assertEqual(result["operation"]["state"], "failed")
        self.assertIn("session index", result["operation"]["results"][0]["error"])


if __name__ == "__main__":
    unittest.main()
