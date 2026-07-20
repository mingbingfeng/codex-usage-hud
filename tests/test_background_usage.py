"""Tests for Codex App non-session background usage auditing."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.background_usage import (
    BackgroundUsageScanner,
    BackgroundUsageStore,
    classify_background_feature,
    decode_request_context,
    decode_request_evidence,
    decode_submission_prompt,
)
from codex_usage_hud.background_usage_runtime import BackgroundUsageRuntime
from codex_usage_hud.core.runtime_events import RuntimeEventBus


VISIBLE_ID = "10000000-0000-4000-8000-000000000001"
CHILD_ID = "10000000-0000-4000-8000-000000000002"
BACKGROUND_ID = "10000000-0000-4000-8000-000000000003"
UNKNOWN_ID = "10000000-0000-4000-8000-000000000004"
GRACE_ID = "10000000-0000-4000-8000-000000000005"
APP_PROCESS = "pid:1234:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
WORKER_PROCESS = "pid:2345:aaaaaaaa-bbbb-4ccc-8ddd-ffffffffffff"


def _submission_body(thread_id: str, prompt: str) -> str:
    return (
        f"session_loop{{thread_id={thread_id}}}: Submission "
        'sub=Submission { id: "submission", op: UserInput { items: '
        f"[Text {{ text: {json.dumps(prompt)}, text_elements: [] }}] }} }}"
    )


def _feedback_body(thread_id: str, model: str, cwd: str) -> str:
    return (
        f"session_loop{{thread_id={thread_id}}}:turn{{model={model}}}:"
        f"run_sampling_request{{model={model} cwd={cwd}}}:"
        f"try_run_sampling_request{{model={model}}}:"
        'endpoint_session.stream_encoded_json_with{http.method=POST '
        'api.path="responses"}: endpoint="/responses"'
    )


def _turn_body(
    thread_id: str,
    model: str,
    total_tokens: int,
    estimated_tokens: int,
) -> str:
    return (
        f"session_loop{{thread_id={thread_id}}}:turn{{model={model}}}: "
        "post sampling "
        f"turn_id=turn total_usage_tokens={total_tokens} "
        f"estimated_token_count=Some({estimated_tokens}) "
        "model_needs_follow_up=false"
    )


def _create_logs(path: Path, rows: list[tuple[object, ...]]) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE logs (
                id INTEGER PRIMARY KEY,
                ts INTEGER NOT NULL,
                ts_nanos INTEGER NOT NULL DEFAULT 0,
                level TEXT,
                target TEXT,
                feedback_log_body TEXT,
                module_path TEXT,
                file TEXT,
                line INTEGER,
                thread_id TEXT,
                process_uuid TEXT,
                estimated_bytes INTEGER
            );
            CREATE INDEX idx_logs_ts ON logs(ts DESC, ts_nanos DESC, id DESC);
            """
        )
        connection.executemany(
            """
            INSERT INTO logs(
                id, ts, target, module_path, feedback_log_body,
                thread_id, process_uuid
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _create_state(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT);
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT,
                child_thread_id TEXT,
                status TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO threads(id, source) VALUES(?, 'vscode')",
            (VISIBLE_ID,),
        )
        connection.execute(
            """
            INSERT INTO thread_spawn_edges(parent_thread_id, child_thread_id, status)
            VALUES(?, ?, 'running')
            """,
            (VISIBLE_ID, CHILD_ID),
        )


def _prices() -> dict[str, dict[str, object]]:
    return {
        "custom/gpt-test": {
            "model": "gpt-test",
            "provider": "custom",
            "input": 2.0,
            "cached_input": 0.5,
            "cache_write": 0.0,
            "output": 4.0,
            "reasoning": 4.0,
        }
    }


class BackgroundUsageDecoderTests(unittest.TestCase):
    def test_decoders_project_real_log_shapes(self) -> None:
        prompt = "## Memory Writing Agent: Phase 2 (Consolidation)\nBody"
        submission = _submission_body(BACKGROUND_ID, prompt)
        self.assertEqual(decode_submission_prompt(submission), prompt)
        self.assertEqual(
            classify_background_feature(prompt).key,
            "memory_consolidation",
        )

        model, cwd, endpoint = decode_request_context(
            _feedback_body(BACKGROUND_ID, "gpt-test", r"C:\work tree")
        )
        self.assertEqual(model, "gpt-test")
        self.assertEqual(cwd, r"C:\work tree")
        self.assertEqual(endpoint, "/responses")

        request = decode_request_evidence(
            99,
            123,
            _turn_body(BACKGROUND_ID, "gpt-test", 120, 100),
        )
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.model, "gpt-test")
        self.assertEqual(request.total_tokens, 120)
        self.assertEqual(request.estimated_input_tokens, 100)

    def test_unknown_feature_is_retained(self) -> None:
        feature = classify_background_feature("New internal feature prompt")
        self.assertEqual(feature.key, "unknown")
        self.assertEqual(feature.label, "未知后台任务")


class BackgroundUsageScannerTests(unittest.TestCase):
    def test_scan_excludes_sessions_aggregates_requests_and_persists_confirmation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            audit_path = root / "background-usage.sqlite3"
            base_ts = 1_900_000_000
            title_prompt = (
                "You are a helpful assistant. You will be presented with a user prompt, "
                "and your job is to provide a short title for a task. "
                "Fill the structured description field."
            )
            rows = [
                (
                    1,
                    base_ts,
                    "codex_core::session::handlers",
                    "codex_core::session::handlers",
                    _submission_body(VISIBLE_ID, "Visible user session"),
                    VISIBLE_ID,
                    APP_PROCESS,
                ),
                (
                    2,
                    base_ts + 1,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(VISIBLE_ID, "gpt-test", 10, 8),
                    VISIBLE_ID,
                    APP_PROCESS,
                ),
                (
                    3,
                    base_ts + 2,
                    "codex_core::session::handlers",
                    "codex_core::session::handlers",
                    _submission_body(CHILD_ID, "Explicit child agent"),
                    CHILD_ID,
                    APP_PROCESS,
                ),
                (
                    4,
                    base_ts + 3,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(CHILD_ID, "gpt-test", 10, 8),
                    CHILD_ID,
                    APP_PROCESS,
                ),
                (
                    5,
                    base_ts + 4,
                    "codex_core::session::handlers",
                    "codex_core::session::handlers",
                    _submission_body(BACKGROUND_ID, title_prompt),
                    BACKGROUND_ID,
                    WORKER_PROCESS,
                ),
                (
                    6,
                    base_ts + 5,
                    "feedback_tags",
                    "codex_feedback",
                    _feedback_body(BACKGROUND_ID, "gpt-test", r"C:\project"),
                    BACKGROUND_ID,
                    WORKER_PROCESS,
                ),
                (
                    7,
                    base_ts + 6,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(BACKGROUND_ID, "gpt-test", 100, 80),
                    BACKGROUND_ID,
                    WORKER_PROCESS,
                ),
                (
                    8,
                    base_ts + 7,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(BACKGROUND_ID, "gpt-test", 120, 100),
                    BACKGROUND_ID,
                    WORKER_PROCESS,
                ),
                (
                    9,
                    base_ts + 8,
                    "codex_core::session::handlers",
                    "codex_core::session::handlers",
                    _submission_body(UNKNOWN_ID, "Unrecognized internal prompt"),
                    UNKNOWN_ID,
                    APP_PROCESS,
                ),
                (
                    10,
                    base_ts + 9,
                    "codex_core::session::turn",
                    "codex_core::session::turn",
                    _turn_body(UNKNOWN_ID, "gpt-test", 50, 40),
                    UNKNOWN_ID,
                    APP_PROCESS,
                ),
            ]
            _create_logs(logs_path, rows)
            _create_state(state_path)
            store = BackgroundUsageStore(audit_path)
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=8,
                now=lambda: float(base_ts + 30),
            )

            first = scanner.scan()
            payload = store.query(
                range_key="all",
                now=datetime.fromtimestamp(base_ts + 30).astimezone(),
            )

            self.assertEqual(first.source_cursor, 10)
            self.assertEqual(payload["summary"]["eventCount"], 2)
            by_id = {item["eventId"]: item for item in payload["events"]}
            self.assertNotIn(VISIBLE_ID, by_id)
            self.assertNotIn(CHILD_ID, by_id)
            self.assertEqual(by_id[BACKGROUND_ID]["requestCount"], 2)
            self.assertEqual(by_id[BACKGROUND_ID]["totalTokens"], 220)
            self.assertEqual(
                by_id[BACKGROUND_ID]["featureKey"],
                "title_description",
            )
            self.assertEqual(by_id[UNKNOWN_ID]["featureKey"], "unknown")
            self.assertEqual(
                by_id[UNKNOWN_ID]["appAttribution"],
                "visible_app_thread",
            )
            self.assertNotIn("prompt", json.dumps(payload).casefold())

            detail = store.detail(BACKGROUND_ID)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["prompt"], title_prompt)
            self.assertEqual(len(detail["requests"]), 2)
            self.assertEqual(
                detail["requests"][1]["estimatedCachedTokens"],
                80,
            )
            self.assertEqual(
                detail["requests"][0]["priceSnapshot"]["provider"],
                "custom",
            )

            with closing(sqlite3.connect(logs_path)) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO logs(
                        id, ts, target, module_path, feedback_log_body,
                        thread_id, process_uuid
                    ) VALUES(11, ?, 'codex_core::session::turn',
                             'codex_core::session::turn', ?, ?, ?)
                    """,
                    (
                        base_ts + 31,
                        _turn_body(BACKGROUND_ID, "gpt-test", 140, 110),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                )
            second = scanner.scan()
            self.assertEqual(second.source_cursor, 11)
            self.assertLess(second.processed_rows, first.processed_rows)
            self.assertEqual(store.detail(BACKGROUND_ID)["requestCount"], 3)

            self.assertTrue(
                store.confirm(
                    BACKGROUND_ID,
                    now=datetime.fromtimestamp(base_ts + 40).astimezone(),
                )
            )
            self.assertFalse(store.confirm(BACKGROUND_ID))
            restarted = BackgroundUsageStore(audit_path)
            self.assertNotIn(
                BACKGROUND_ID,
                {item["eventId"] for item in restarted.pending_today()},
            )

    def test_scan_waits_for_state_database_before_classifying(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 30, 20),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            unavailable = scanner.scan()
            self.assertEqual(unavailable.processed_rows, 0)
            self.assertEqual(unavailable.diagnostics, ("state_database_missing",))
            self.assertEqual(store.query(range_key="all")["events"], [])

            _create_state(state_path)
            recovered = scanner.scan()
            self.assertEqual(recovered.source_cursor, 2)
            self.assertEqual(
                store.query(range_key="all")["events"][0]["eventId"],
                BACKGROUND_ID,
            )

    def test_scan_skips_one_malformed_log_row_and_advances_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        "invalid-timestamp",
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 999, 900),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        3,
                        base_ts + 2,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 40, 30),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            result = scanner.scan()
            detail = store.detail(BACKGROUND_ID)
            self.assertEqual(result.source_cursor, 3)
            self.assertIn("malformed_log_row", result.diagnostics)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["requestCount"], 1)
            self.assertEqual(detail["totalTokens"], 40)

    def test_missing_price_is_unavailable_instead_of_zero_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "unpriced-model", 40, 30),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            store = BackgroundUsageStore(root / "audit.sqlite3")
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table={},
                grace_seconds=0,
                now=lambda: float(base_ts + 30),
            )

            scanner.scan()
            payload = store.query(range_key="all")
            self.assertFalse(payload["summary"]["costComplete"])
            self.assertIsNone(payload["summary"]["estimatedCostUsd"])
            self.assertIsNone(payload["events"][0]["estimatedCostUsd"])

    def test_grace_deadline_promotes_without_new_source_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = 1_900_000_000
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(GRACE_ID, prompt),
                        GRACE_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(GRACE_ID, "gpt-test", 30, 20),
                        GRACE_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            store = BackgroundUsageStore(root / "audit.sqlite3")
            clock = [float(base_ts + 5)]
            scanner = BackgroundUsageScanner(
                logs_path=logs_path,
                state_path=state_path,
                store=store,
                provider="custom",
                price_table=_prices(),
                grace_seconds=10,
                now=lambda: clock[0],
            )

            first = scanner.scan()
            self.assertEqual(first.pending_deadline, float(base_ts + 10))
            self.assertEqual(store.query(range_key="all")["events"], [])

            clock[0] = float(base_ts + 11)
            second = scanner.scan()
            self.assertEqual(second.processed_rows, 0)
            self.assertTrue(second.content_changed)
            self.assertEqual(
                store.query(range_key="all")["events"][0]["eventId"],
                GRACE_ID,
            )


class _FakeWatcher:
    instance: "_FakeWatcher | None" = None

    def __init__(self, callback: object, **_kwargs: object) -> None:
        self.callback = callback
        self.specs: list[object] = []
        self.closed = False
        self.event_driven = True
        self.polling_cause = ""
        _FakeWatcher.instance = self

    def update(self, specs: object) -> None:
        self.specs = list(specs)  # type: ignore[arg-type]

    def close(self) -> None:
        self.closed = True

    def trigger(self) -> None:
        self.callback({"background-usage-log"}, set())  # type: ignore[operator]


class BackgroundUsageRuntimeTests(unittest.TestCase):
    def test_runtime_is_event_driven_and_closes_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs_path = root / "logs_2.sqlite"
            state_path = root / "state_5.sqlite"
            base_ts = int(time.time()) - 30
            prompt = (
                "# Overview\nGenerate 0 to 3 hyperpersonalized suggestions "
                "for what this user can do with Codex"
            )
            _create_logs(
                logs_path,
                [
                    (
                        1,
                        base_ts,
                        "codex_core::session::handlers",
                        "codex_core::session::handlers",
                        _submission_body(BACKGROUND_ID, prompt),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                    (
                        2,
                        base_ts + 1,
                        "codex_core::session::turn",
                        "codex_core::session::turn",
                        _turn_body(BACKGROUND_ID, "gpt-test", 30, 20),
                        BACKGROUND_ID,
                        WORKER_PROCESS,
                    ),
                ],
            )
            _create_state(state_path)
            events = RuntimeEventBus()
            runtime = BackgroundUsageRuntime(
                logs_path=logs_path,
                state_path=state_path,
                database_path=root / "audit.sqlite3",
                provider="custom",
                price_table=_prices(),
                event_bus=events,
                watcher_factory=_FakeWatcher,  # type: ignore[arg-type]
            )
            try:
                runtime.start()
                self.assertTrue(runtime.wait_until_idle())
                initial_events = events.drain()
                self.assertEqual(
                    [event.type for event in initial_events],
                    ["background_usage_changed"],
                )
                self.assertEqual(len(runtime.pending_today()), 1)
                self.assertIsNotNone(_FakeWatcher.instance)
                assert _FakeWatcher.instance is not None
                self.assertEqual(len(_FakeWatcher.instance.specs), 2)

                time.sleep(0.05)
                self.assertEqual(events.drain(), [])

                with closing(sqlite3.connect(logs_path)) as connection, connection:
                    connection.execute(
                        """
                        INSERT INTO logs(
                            id, ts, target, module_path, feedback_log_body,
                            thread_id, process_uuid
                        ) VALUES(3, ?, 'codex_core::session::turn',
                                 'codex_core::session::turn', ?, ?, ?)
                        """,
                        (
                            base_ts + 2,
                            _turn_body(BACKGROUND_ID, "gpt-test", 40, 30),
                            BACKGROUND_ID,
                            WORKER_PROCESS,
                        ),
                    )
                _FakeWatcher.instance.trigger()
                self.assertTrue(runtime.wait_until_idle())
                changed_events = events.drain()
                self.assertEqual(
                    [event.type for event in changed_events],
                    ["background_usage_changed"],
                )
                self.assertEqual(runtime.detail(BACKGROUND_ID)["requestCount"], 2)
            finally:
                runtime.close()
            self.assertTrue(_FakeWatcher.instance.closed)


if __name__ == "__main__":
    unittest.main()
