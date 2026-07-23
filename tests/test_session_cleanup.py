from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_usage_hud.core.session_cleanup import (
    SessionCleanupError,
    SessionCleanupManager,
)


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


class _OfficialDeleteRunner:
    def __init__(
        self,
        *,
        state_db: Path,
        index_path: Path,
        families: dict[str, tuple[str, ...]],
        rollouts: dict[str, Path],
        fail_ids: set[str] | None = None,
        capability: bool = True,
        preserve_index: bool = False,
    ) -> None:
        self.state_db = state_db
        self.index_path = index_path
        self.families = families
        self.rollouts = rollouts
        self.fail_ids = set(fail_ids or ())
        self.capability = capability
        self.preserve_index = preserve_index
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, environment):
        del environment
        argv = tuple(command)
        self.commands.append(argv)
        if argv[-2:] == ("delete", "--help"):
            return subprocess.CompletedProcess(
                argv,
                0 if self.capability else 2,
                stdout="Delete a thread. --force requires a UUID." if self.capability else "",
                stderr="",
            )
        session_id = argv[-1]
        if session_id in self.fail_ids:
            return subprocess.CompletedProcess(
                argv,
                1,
                stdout="",
                stderr="delete rejected",
            )
        family = self.families[session_id]
        placeholders = ",".join("?" for _ in family)
        with closing(sqlite3.connect(self.state_db)) as connection, connection:
            connection.execute(
                f"DELETE FROM thread_spawn_edges WHERE parent_thread_id IN ({placeholders}) "
                f"OR child_thread_id IN ({placeholders})",
                (*family, *family),
            )
            connection.execute(
                f"DELETE FROM threads WHERE id IN ({placeholders})",
                family,
            )
        for member in family:
            self.rollouts[member].unlink(missing_ok=True)
        if not self.preserve_index:
            kept = []
            for line in self.index_path.read_text(encoding="utf-8").splitlines():
                payload = json.loads(line)
                if payload.get("id") not in family:
                    kept.append(line)
            self.index_path.write_text(
                "".join(f"{line}\n" for line in kept),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(argv, 0, stdout="deleted", stderr="")


class SessionCleanupManagerTests(unittest.TestCase):
    def _fixture(self, *, child_status: str = "closed", capability: bool = True):
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
        for index, path in enumerate(rollouts.values(), start=1):
            path.write_text("{}\n" * index, encoding="utf-8")
        state_db = root / "state_5.sqlite"
        _create_state(
            state_db,
            [
                (ROOT_ID, str(rollouts[ROOT_ID]), "Root", str(root / "project-a"), 0, 30),
                (CHILD_ID, str(rollouts[CHILD_ID]), "Child", str(root / "project-a"), 0, 20),
                (SECOND_ID, str(rollouts[SECOND_ID]), "Archived", str(root / "project-b"), 1, 10),
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
        runner = _OfficialDeleteRunner(
            state_db=state_db,
            index_path=index_path,
            families={ROOT_ID: (ROOT_ID, CHILD_ID), SECOND_ID: (SECOND_ID,)},
            rollouts=rollouts,
            capability=capability,
        )
        manager = SessionCleanupManager(
            state_db_path=state_db,
            sessions_root=sessions,
            session_index_path=index_path,
            command_runner=runner,
            token_factory=iter(f"token-{index}" for index in range(100)).__next__,
        )
        return temporary, root, state_db, index_path, rollouts, runner, manager

    def test_scan_groups_children_and_keeps_sensitive_identifiers_private(self) -> None:
        fixture = self._fixture()
        temporary, root, _state, _index, _rollouts, _runner, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan(request_id="scan-1")

        self.assertEqual(payload["totals"]["sessions"], 2)
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")
        self.assertEqual(root_row["descendantCount"], 1)
        self.assertTrue(root_row["selectable"])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(ROOT_ID, serialized)
        self.assertNotIn(CHILD_ID, serialized)
        self.assertNotIn(str(root), serialized)

    def test_current_and_running_session_trees_are_blocked(self) -> None:
        fixture = self._fixture(child_status="running")
        temporary, _root, state, index, _rollouts, runner, _manager = fixture
        self.addCleanup(temporary.cleanup)
        manager = SessionCleanupManager(
            state_db_path=state,
            sessions_root=state.parent / "sessions",
            session_index_path=index,
            command_runner=runner,
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
        temporary, _root, _state, _index, _rollouts, _runner, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan()
        root_row = next(row for row in payload["sessions"] if row["title"] == "Root")

        self.assertEqual(root_row["status"], "idle")
        self.assertTrue(root_row["selectable"])

    def test_ambiguous_spawn_relation_blocks_every_affected_root(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, _rollouts, _runner, manager = fixture
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

    def test_preview_and_execute_use_force_and_verify_parent_cascade(self) -> None:
        fixture = self._fixture()
        temporary, _root, state, _index, rollouts, runner, manager = fixture
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
        self.assertIn(("codex", "delete", "--force", ROOT_ID), runner.commands)
        with closing(sqlite3.connect(state)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM threads").fetchone()[0], 1)
        self.assertFalse(rollouts[ROOT_ID].exists())
        self.assertFalse(rollouts[CHILD_ID].exists())

    def test_delete_commits_prepared_usage_only_after_cascade_verification(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, rollouts, _runner, manager = fixture
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

    def test_delete_failure_discards_pending_usage_snapshot(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, runner, manager = fixture
        self.addCleanup(temporary.cleanup)
        runner.fail_ids.add(SECOND_ID)
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

        result = manager.execute(
            [row["id"]],
            scan["revision"],
            preview["operation"]["confirmationToken"],
        )

        self.assertEqual(result["operation"]["state"], "failed")
        self.assertEqual(events, [("discard", "pending-usage")])

    def test_usage_snapshot_failure_prevents_official_delete(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, runner, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([row["id"]], scan["revision"])
        force_commands_before = [
            command for command in runner.commands if "--force" in command
        ]

        def fail_prepare(_item) -> object:
            raise SessionCleanupError("usage snapshot unavailable")

        manager.usage_snapshot_prepare = fail_prepare
        result = manager.execute(
            [row["id"]],
            scan["revision"],
            preview["operation"]["confirmationToken"],
        )

        self.assertEqual(result["operation"]["state"], "failed")
        self.assertEqual(
            [command for command in runner.commands if "--force" in command],
            force_commands_before,
        )
        self.assertIn(
            "usage snapshot unavailable",
            result["operation"]["results"][0]["error"],
        )

    def test_confirmation_is_single_use_and_revision_bound(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, _runner, manager = fixture
        self.addCleanup(temporary.cleanup)
        scan = manager.scan()
        item_id = scan["sessions"][0]["id"]
        preview = manager.preview([item_id], scan["revision"])
        token = preview["operation"]["confirmationToken"]
        with self.assertRaises(SessionCleanupError):
            manager.execute([item_id], "stale", token)

    def test_missing_force_capability_disables_delete_without_fallback(self) -> None:
        fixture = self._fixture(capability=False)
        temporary, _root, _state, _index, _rollouts, runner, manager = fixture
        self.addCleanup(temporary.cleanup)

        payload = manager.scan()

        self.assertFalse(payload["capability"]["available"])
        self.assertEqual(payload["totals"]["selectable"], 0)
        self.assertTrue(all(not row["selectable"] for row in payload["sessions"]))
        self.assertTrue(all(command[-2:] == ("delete", "--help") for command in runner.commands))

    @patch("codex_usage_hud.core.session_cleanup.shutil.which")
    @patch("codex_usage_hud.core.session_cleanup.subprocess.run")
    def test_default_runner_resolves_path_wrapper_before_capability_probe(
        self,
        run_mock,
        which_mock,
    ) -> None:
        resolved = r"C:\Tools\codex.CMD"
        which_mock.return_value = resolved
        run_mock.return_value = subprocess.CompletedProcess(
            [resolved, "delete", "--help"],
            0,
            stdout="Delete a thread. --force requires a UUID.",
            stderr="",
        )
        manager = SessionCleanupManager(
            state_db_path=Path("state_5.sqlite"),
            sessions_root=Path("sessions"),
            session_index_path=Path("session_index.jsonl"),
            environment={"PATH": r"C:\Tools"},
        )

        capability = manager.probe_capability()

        self.assertTrue(capability.available)
        which_mock.assert_called_once_with("codex", path=r"C:\Tools")
        command = run_mock.call_args.args[0]
        self.assertEqual(command, [resolved, "delete", "--help"])
        self.assertNotIn("shell", run_mock.call_args.kwargs)

    def test_batch_delete_reports_partial_failure(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, _index, _rollouts, runner, manager = fixture
        self.addCleanup(temporary.cleanup)
        runner.fail_ids.add(SECOND_ID)
        scan = manager.scan()
        selected = [row["id"] for row in scan["sessions"]]
        preview = manager.preview(selected, scan["revision"])

        result = manager.execute(
            selected,
            scan["revision"],
            preview["operation"]["confirmationToken"],
        )

        self.assertEqual(result["operation"]["state"], "partial")
        self.assertEqual(result["operation"]["deletedCount"], 1)
        self.assertEqual(result["operation"]["failedCount"], 1)

    def test_delete_verification_checks_index_rows_without_titles(self) -> None:
        fixture = self._fixture()
        temporary, _root, _state, index, _rollouts, runner, manager = fixture
        self.addCleanup(temporary.cleanup)
        runner.preserve_index = True
        index.write_text(
            json.dumps({"id": ROOT_ID}) + "\n" + json.dumps({"id": SECOND_ID}) + "\n",
            encoding="utf-8",
        )
        scan = manager.scan()
        root_row = next(row for row in scan["sessions"] if row["title"] == "Root")
        preview = manager.preview([root_row["id"]], scan["revision"])

        result = manager.execute(
            [root_row["id"]],
            scan["revision"],
            preview["operation"]["confirmationToken"],
        )

        self.assertEqual(result["operation"]["state"], "failed")
        self.assertIn("session index", result["operation"]["results"][0]["error"])


if __name__ == "__main__":
    unittest.main()
