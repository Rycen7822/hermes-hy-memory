from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import dashboard
from dashboard import (
    DashboardFilters,
    DashboardPaths,
    collect_activity,
    collect_memories,
    collect_overview,
    collect_trace,
    collect_usage,
    open_sqlite_readonly,
)


def _insert_vector_memory(
    conn: sqlite3.Connection,
    row_id: int,
    memory_id: str,
    content: str,
    layer: str,
    *,
    status: str = "active",
    is_latest: int = 1,
    user_id: str = "user-a",
    agent_id: str = "agent-a",
    session_id: str = "session-a",
    gmt_created: int = 1780260000,
) -> None:
    conn.execute(
        "INSERT INTO embeddings(id, segment_id, embedding_id, seq_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (row_id, "metadata-segment", memory_id, row_id, "2026-06-01 09:00:00"),
    )
    metadata = {
        "node_id": memory_id,
        "content": content,
        "layer": layer,
        "user_id": user_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "status": status,
        "is_latest": is_latest,
        "memory_at": gmt_created,
        "gmt_created": gmt_created,
        "gmt_modified": gmt_created,
        "source_raw_memory_id": "",
        "tags": "[]",
        "custom": "{}",
    }
    for key, value in metadata.items():
        string_value = None
        int_value = None
        bool_value = None
        if key == "is_latest":
            bool_value = int(bool(value))
        elif key in {"memory_at", "gmt_created", "gmt_modified"}:
            int_value = int(value)
        else:
            string_value = str(value)
        conn.execute(
            "INSERT INTO embedding_metadata(id, key, string_value, int_value, float_value, bool_value) VALUES (?, ?, ?, ?, ?, ?)",
            (row_id, key, string_value, int_value, None, bool_value),
        )


def _write_fixture_dbs(tmp_path: Path) -> DashboardPaths:
    data_dir = tmp_path / "hy_memory" / "data"
    data_dir.mkdir(parents=True)
    history_db_path = data_dir / "history.db"
    cache_db_path = data_dir / "cache.db"

    history_long_content = "Raw L1 user message about dashboard visibility. " + ("raw-full-content " * 40)
    structured_long_content = "Active dashboard summary from vector metadata. " + ("structured-full-content " * 40)

    history = sqlite3.connect(history_db_path)
    history.execute(
        """
        CREATE TABLE memory_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT,
            isolation_key TEXT,
            event TEXT,
            old_memory TEXT,
            new_memory TEXT,
            old_status TEXT,
            new_status TEXT,
            change_reason TEXT,
            layer TEXT,
            actor_id TEXT,
            role TEXT,
            extra TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    history.executemany(
        """
        INSERT INTO memory_history(memory_id, isolation_key, event, old_memory, new_memory, layer, actor_id, role, extra, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "raw-1",
                "user-a:agent-a",
                "ADD",
                None,
                history_long_content,
                "l1_raw",
                "agent-a",
                "user",
                json.dumps({"source": "unit"}),
                "2026-06-01T09:00:30",
                "2026-06-01T09:00:30",
            ),
            (
                "mem-1",
                "user-a:agent-a",
                "ADD",
                None,
                "The user prefers compact dashboard status summaries with real gate output.",
                "l3_procedure",
                "agent-a",
                "assistant",
                json.dumps({"source": "unit"}),
                "2026-06-01T09:00:00",
                "2026-06-01T09:00:00",
            ),
            (
                "mem-2",
                "user-a:agent-a",
                "ADD",
                None,
                "HY Memory dashboard should stay localhost-only and read-only.",
                "l4_identity",
                "agent-a",
                "assistant",
                json.dumps({"source": "unit"}),
                "2026-06-01T09:05:00",
                "2026-06-01T09:05:00",
            ),
            (
                "search-1",
                "user-a:agent-a",
                "SEARCH",
                None,
                None,
                "",
                "agent-a",
                "assistant",
                json.dumps({"query": "dashboard recall", "results_count": 2}),
                "2026-06-01T09:10:00",
                "2026-06-01T09:10:00",
            ),
            (
                "mem-1",
                "user-a:agent-a",
                "UPDATE",
                "old dashboard note",
                "Updated dashboard note",
                "l3_procedure",
                "agent-a",
                "assistant",
                json.dumps({"source": "unit"}),
                "2026-06-01T09:20:00",
                "2026-06-01T09:20:00",
            ),
            (
                "deleted-mem",
                "user-a:agent-a",
                "DELETE",
                "deleted content",
                None,
                "l2_preference",
                "agent-a",
                "assistant",
                json.dumps({"source": "unit"}),
                "2026-06-01T09:25:00",
                "2026-06-01T09:25:00",
            ),
        ],
    )
    history.commit()
    history.close()

    cache = sqlite3.connect(cache_db_path)
    cache.execute(
        """
        CREATE TABLE memory_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            user_id TEXT,
            agent_id TEXT,
            op TEXT,
            memory_id TEXT,
            old_memory_id TEXT,
            content TEXT,
            layer TEXT,
            reason TEXT,
            created_at TEXT,
            supersedes TEXT
        )
        """
    )
    cache.execute(
        """
        CREATE TABLE pipeline_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            user_id TEXT,
            agent_id TEXT,
            step TEXT,
            prompt TEXT,
            response TEXT,
            parsed TEXT,
            memory_ids TEXT,
            elapsed_ms REAL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            created_at TEXT
        )
        """
    )
    cache.execute(
        """
        CREATE TABLE system_metrics (
            minute_ts TEXT PRIMARY KEY,
            data TEXT,
            created_at TEXT
        )
        """
    )
    long_content = "Dashboard memory content " + ("x" * 400)
    cache.executemany(
        """
        INSERT INTO memory_operations(request_id, user_id, agent_id, op, memory_id, content, layer, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("req-add-1", "user-a", "agent-a", "ADD", "mem-1", long_content, "l3_procedure", "unit", "2026-06-01T09:01:00"),
            ("req-add-2", "user-a", "agent-a", "ADD", "deleted-mem", "deleted content", "l2_preference", "unit", "2026-06-01T09:02:00"),
            ("req-upd-1", "user-a", "agent-a", "UPDATE", "mem-1", "Updated dashboard note", "l3_procedure", "unit", "2026-06-01T09:21:00"),
        ],
    )
    cache.executemany(
        """
        INSERT INTO pipeline_logs(request_id, user_id, agent_id, step, prompt, response, parsed, memory_ids, elapsed_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("req-search-1", "user-a", "agent-a", "READ_RECALL_VEC", "find dashboard memory " + ("p" * 400), "found mem-1", "{}", json.dumps(["mem-1", "mem-2"]), 12.5, "2026-06-01T09:11:00"),
            ("req-search-1", "user-a", "agent-a", "READ_RERANK", "rerank", "mem-1", "{}", json.dumps(["mem-1"]), 5.0, "2026-06-01T09:12:00"),
        ],
    )
    cache.execute(
        "INSERT INTO system_metrics(minute_ts, data, created_at) VALUES (?, ?, ?)",
        ("2026-06-01T09:00", json.dumps({"rss_mb": 42}), "2026-06-01T09:00:59"),
    )
    cache.commit()
    cache.close()

    vector_dir = data_dir / "vector_db"
    vector_dir.mkdir(parents=True)
    vector = sqlite3.connect(vector_dir / "chroma.sqlite3")
    vector.execute(
        """
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY,
            segment_id TEXT NOT NULL,
            embedding_id TEXT NOT NULL,
            seq_id BLOB NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    vector.execute(
        """
        CREATE TABLE embedding_metadata (
            id INTEGER,
            key TEXT NOT NULL,
            string_value TEXT,
            int_value INTEGER,
            float_value REAL,
            bool_value INTEGER,
            PRIMARY KEY (id, key)
        )
        """
    )
    _insert_vector_memory(
        vector,
        1,
        "shadow-old",
        "Dashboard memory content from the old operation log.",
        "l3_summary",
        status="shadow",
        is_latest=0,
        gmt_created=1780260001,
    )
    _insert_vector_memory(
        vector,
        2,
        "active-l3",
        structured_long_content,
        "l3_summary",
        gmt_created=1780260002,
    )
    _insert_vector_memory(
        vector,
        3,
        "active-l4",
        "HY Memory dashboard should stay localhost-only and read-only.",
        "l4_identity",
        gmt_created=1780260003,
    )
    _insert_vector_memory(
        vector,
        4,
        "raw-active",
        "Raw active memory should stay outside current structured records.",
        "l1_raw",
        gmt_created=1780260004,
    )
    vector.commit()
    vector.close()

    return DashboardPaths(
        hermes_home=tmp_path,
        data_dir=tmp_path / "hy_memory",
        history_db_path=history_db_path,
        cache_db_path=cache_db_path,
    )


def test_open_sqlite_readonly_does_not_create_missing_file(tmp_path):
    missing = tmp_path / "missing.db"

    conn = open_sqlite_readonly(missing)

    assert conn is None
    assert not missing.exists()


def test_collect_overview_counts_events_layers_and_health(tmp_path):
    paths = _write_fixture_dbs(tmp_path)

    overview = collect_overview(paths, DashboardFilters())

    assert overview["health"]["history_db"] == "ok"
    assert overview["health"]["cache_db"] == "ok"
    assert overview["health"]["vector_db"] == "ok"
    assert overview["totals"]["history_events"] == 6
    assert overview["totals"]["memory_records"] == 2
    assert overview["totals"]["pipeline_logs"] == 2
    assert overview["event_counts"]["ADD"] == 3
    assert overview["event_counts"]["SEARCH"] == 1
    assert overview["event_counts"]["UPDATE"] == 1
    assert overview["event_counts"]["DELETE"] == 1
    assert overview["layer_counts"]["l3_summary"] == 1
    assert overview["record_layer_counts"]["l3_summary"] == 1
    assert overview["record_layer_counts"]["l4_identity"] == 1
    assert overview["history_layer_counts"]["l1_raw"] == 1
    assert overview["history_layer_counts"]["l3_procedure"] == 2
    assert overview["history_layer_counts"]["l4_identity"] == 1
    assert overview["history_layer_counts"]["l2_preference"] == 1
    assert overview["latest"]["SEARCH"] == "2026-06-01T09:10:00"


def test_collect_history_records_filters_l1_and_l3_layers(tmp_path):
    paths = _write_fixture_dbs(tmp_path)

    l1_records = dashboard.collect_history_records(paths, DashboardFilters(layer="l1_raw"))
    assert l1_records["source"] == "history_db.memory_history"
    assert l1_records["count"] == 1
    assert l1_records["items"][0]["layer"] == "l1_raw"
    assert l1_records["items"][0]["content"].startswith("Raw L1 user message about dashboard visibility.")
    assert "raw-full-content" in l1_records["items"][0]["content"]
    assert len(l1_records["items"][0]["content"]) > 240
    assert not l1_records["items"][0]["content"].endswith("…")

    l3_records = dashboard.collect_history_records(paths, DashboardFilters(layer="l3_procedure", query="dashboard"))
    assert l3_records["count"] == 2
    assert {item["event"] for item in l3_records["items"]} == {"ADD", "UPDATE"}
    assert all(item["layer"] == "l3_procedure" for item in l3_records["items"])


def test_collect_usage_groups_save_and_recall_activity(tmp_path):
    paths = _write_fixture_dbs(tmp_path)

    usage = collect_usage(paths, "hour", DashboardFilters())

    assert usage["bucket"] == "hour"
    assert usage["series"][0]["bucket"] == "2026-06-01T09"
    assert usage["series"][0]["add"] == 3
    assert usage["series"][0]["search"] == 1
    assert usage["series"][0]["update"] == 1
    assert usage["series"][0]["delete"] == 1
    assert usage["series"][0]["recall_pipeline"] == 2


def test_collect_activity_merges_sources_desc_and_truncates(tmp_path):
    paths = _write_fixture_dbs(tmp_path)

    activity = collect_activity(paths, DashboardFilters(limit=20))

    items = activity["items"]
    assert items == sorted(items, key=lambda item: item["timestamp"], reverse=True)
    assert {item["source"] for item in items} >= {"history", "operation", "pipeline"}
    search = next(item for item in items if item["kind"] == "SEARCH")
    assert search["result_count"] == 2
    pipeline = next(item for item in items if item["kind"] == "READ_RECALL_VEC")
    assert pipeline["result_count"] == 2
    assert len(pipeline["summary"]) <= 241
    assert pipeline["summary"].endswith("…")


def test_collect_memories_excludes_deleted_and_filters_query(tmp_path):
    paths = _write_fixture_dbs(tmp_path)

    memories = collect_memories(paths, DashboardFilters(query="Active dashboard summary", limit=10))

    assert memories["source"] == "vector_db.chroma_active"
    assert memories["count"] == 1
    assert memories["items"][0]["memory_id"] == "active-l3"
    assert memories["items"][0]["content"].startswith("Active dashboard summary from vector metadata.")
    assert "structured-full-content" in memories["items"][0]["content"]
    assert len(memories["items"][0]["content"]) > 240
    assert not memories["items"][0]["content"].endswith("…")

    old_operation_text = collect_memories(paths, DashboardFilters(query="Dashboard memory content", limit=10))
    assert old_operation_text["count"] == 0

    all_memories = collect_memories(paths, DashboardFilters(limit=10))
    ids = {item["memory_id"] for item in all_memories["items"]}
    assert ids == {"active-l3", "active-l4"}
    assert "deleted-mem" not in ids
    assert "shadow-old" not in ids
    assert "raw-active" not in ids


def test_collect_trace_returns_request_steps_with_truncated_prompt(tmp_path):
    paths = _write_fixture_dbs(tmp_path)

    trace = collect_trace(paths, "req-search-1")

    assert trace["request_id"] == "req-search-1"
    assert [step["step"] for step in trace["steps"]] == ["READ_RECALL_VEC", "READ_RERANK"]
    assert trace["steps"][0]["memory_ids"] == ["mem-1", "mem-2"]
    assert trace["steps"][0]["prompt"].endswith("…")
