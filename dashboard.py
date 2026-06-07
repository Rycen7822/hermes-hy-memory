"""Local read-only dashboard data collectors for HY Memory."""

from __future__ import annotations

import json
import sqlite3
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from config import HyMemoryConfig


@dataclass(frozen=True)
class DashboardPaths:
    hermes_home: Path
    data_dir: Path
    history_db_path: Path
    cache_db_path: Path
    vector_db_path: Path | None = None


@dataclass(frozen=True)
class DashboardFilters:
    user_id: str = ""
    agent_id: str = ""
    layer: str = ""
    query: str = ""
    limit: int = 100
    offset: int = 0


def paths_from_config(config: HyMemoryConfig) -> DashboardPaths:
    return DashboardPaths(
        hermes_home=config.hermes_home,
        data_dir=config.data_dir,
        history_db_path=config.history_db_path,
        cache_db_path=config.cache_db_path,
        vector_db_path=config.vector_persist_directory / "chroma.sqlite3",
    )


def _vector_db_path(paths: DashboardPaths) -> Path:
    return paths.vector_db_path or (paths.data_dir / "data" / "vector_db" / "chroma.sqlite3")


def open_sqlite_readonly(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def truncate_text(text: str | None, limit: int = 240) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _clamp_limit(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 100
    return min(500, max(1, parsed))


def _clamp_offset(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return min(1_000_000, max(0, parsed))


def _health(paths: DashboardPaths) -> dict[str, Any]:
    errors: list[str] = []

    def status(path: Path, table: str) -> str:
        conn = open_sqlite_readonly(path)
        if conn is None:
            return "missing" if not path.exists() else "error"
        try:
            return "ok" if table_exists(conn, table) else "missing"
        except sqlite3.Error as exc:
            errors.append(f"{path.name}.{table}: {exc}")
            return "error"
        finally:
            conn.close()

    def vector_status(path: Path) -> str:
        conn = open_sqlite_readonly(path)
        if conn is None:
            return "missing" if not path.exists() else "error"
        try:
            return "ok" if table_exists(conn, "embeddings") and table_exists(conn, "embedding_metadata") else "missing"
        except sqlite3.Error as exc:
            errors.append(f"{path.name}.vector: {exc}")
            return "error"
        finally:
            conn.close()

    return {
        "history_db": status(paths.history_db_path, "memory_history"),
        "cache_db": status(paths.cache_db_path, "memory_operations"),
        "vector_db": vector_status(_vector_db_path(paths)),
        "errors": errors,
    }


def _fetch_all(path: Path, table: str, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    conn = open_sqlite_readonly(path)
    if conn is None:
        return []
    try:
        if not table_exists(conn, table):
            return []
        return list(conn.execute(sql, tuple(params)).fetchall())
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _count(path: Path, table: str) -> int:
    rows = _fetch_all(path, table, f"SELECT COUNT(*) AS count FROM {table}")
    return int(rows[0]["count"]) if rows else 0


def _matches_filters(item: dict[str, Any], filters: DashboardFilters) -> bool:
    if filters.user_id and item.get("user_id") != filters.user_id:
        return False
    if filters.agent_id and item.get("agent_id") != filters.agent_id:
        return False
    if filters.layer and item.get("layer") != filters.layer:
        return False
    if filters.query:
        needle = filters.query.lower()
        haystack = " ".join(str(item.get(key) or "") for key in ("memory_id", "content", "summary", "layer", "user_id", "agent_id", "kind"))
        if needle not in haystack.lower():
            return False
    return True


def _bucket_key(timestamp: str, bucket: str) -> str:
    if bucket == "month":
        return timestamp[:7]
    if bucket == "day":
        return timestamp[:10]
    return timestamp[:13]


def _is_false_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    text = str(value).strip().lower()
    return text in {"0", "false", "no", "off"}


def _epoch_to_iso(value: Any) -> str:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return ""
    try:
        return datetime.fromtimestamp(parsed, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return ""


def _active_timestamp(row: sqlite3.Row) -> int:
    for key in ("memory_at", "gmt_created", "gmt_modified"):
        try:
            value = row[key]
        except (KeyError, IndexError):
            value = None
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _active_structured_memory_items(paths: DashboardPaths, filters: DashboardFilters) -> list[dict[str, Any]]:
    conn = open_sqlite_readonly(_vector_db_path(paths))
    if conn is None:
        return []
    try:
        if not (table_exists(conn, "embeddings") and table_exists(conn, "embedding_metadata")):
            return []
        rows = conn.execute(
            """
            SELECT
              e.embedding_id,
              MAX(CASE WHEN m.key='node_id' THEN m.string_value END) AS node_id,
              MAX(CASE WHEN m.key='content' THEN m.string_value END) AS content,
              MAX(CASE WHEN m.key='layer' THEN m.string_value END) AS layer,
              MAX(CASE WHEN m.key='user_id' THEN m.string_value END) AS user_id,
              MAX(CASE WHEN m.key='agent_id' THEN m.string_value END) AS agent_id,
              MAX(CASE WHEN m.key='session_id' THEN m.string_value END) AS session_id,
              MAX(CASE WHEN m.key='status' THEN m.string_value END) AS status,
              MAX(CASE WHEN m.key='is_latest' THEN COALESCE(m.bool_value, m.int_value) END) AS is_latest,
              MAX(CASE WHEN m.key='memory_at' THEN m.int_value END) AS memory_at,
              MAX(CASE WHEN m.key='gmt_created' THEN m.int_value END) AS gmt_created,
              MAX(CASE WHEN m.key='gmt_modified' THEN m.int_value END) AS gmt_modified,
              MAX(CASE WHEN m.key='source_raw_memory_id' THEN m.string_value END) AS source_raw_memory_id,
              MAX(CASE WHEN m.key='tags' THEN m.string_value END) AS tags,
              MAX(CASE WHEN m.key='custom' THEN m.string_value END) AS custom
            FROM embeddings e
            JOIN embedding_metadata m ON m.id = e.id
            GROUP BY e.id, e.embedding_id
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    sorted_items: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        memory_id = str(row["node_id"] or row["embedding_id"] or "")
        layer = str(row["layer"] or "")
        status = str(row["status"] or "").strip().lower()
        if not memory_id or not layer or layer.lower() == "l1_raw" or (status and status != "active") or _is_false_value(row["is_latest"]):
            continue
        timestamp = _active_timestamp(row)
        item = {
            "memory_id": memory_id,
            "content": str(row["content"] or ""),
            "layer": layer,
            "user_id": row["user_id"] or "",
            "agent_id": row["agent_id"] or "",
            "session_id": row["session_id"] or "",
            "created_at": _epoch_to_iso(timestamp),
            "source_raw_memory_id": row["source_raw_memory_id"] or "",
            "tags": _parse_json_list(row["tags"]),
            "custom": parse_json_object(row["custom"]),
        }
        if _matches_filters(item, filters):
            sorted_items.append((timestamp, item))
    return [item for _, item in sorted(sorted_items, key=lambda pair: pair[0], reverse=True)]


def _current_memory_items(paths: DashboardPaths, filters: DashboardFilters) -> list[dict[str, Any]]:
    return _active_structured_memory_items(paths, filters)


def collect_memories(paths: DashboardPaths, filters: DashboardFilters) -> dict[str, Any]:
    items = _current_memory_items(paths, filters)
    offset = _clamp_offset(filters.offset)
    limit = _clamp_limit(filters.limit)
    return {
        "source": "vector_db.chroma_active",
        "items": items[offset : offset + limit],
        "count": len(items),
        "limit": limit,
        "offset": offset,
    }


def collect_history_records(paths: DashboardPaths, filters: DashboardFilters) -> dict[str, Any]:
    rows = _fetch_all(
        paths.history_db_path,
        "memory_history",
        """
        SELECT memory_id, isolation_key, event, old_memory, new_memory, layer, actor_id, role, created_at
        FROM memory_history
        ORDER BY created_at DESC, id DESC
        """,
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        user_id = str(row["isolation_key"] or "").split(":", 1)[0]
        content = row["new_memory"] or row["old_memory"] or ""
        item = {
            "source": "history_db.memory_history",
            "memory_id": row["memory_id"] or "",
            "content": str(content or ""),
            "summary": truncate_text(content),
            "layer": row["layer"] or "",
            "user_id": user_id,
            "agent_id": row["actor_id"] or "",
            "event": str(row["event"] or "").upper(),
            "kind": str(row["event"] or "").upper(),
            "role": row["role"] or "",
            "created_at": row["created_at"] or "",
        }
        if _matches_filters(item, filters):
            items.append(item)

    offset = _clamp_offset(filters.offset)
    limit = _clamp_limit(filters.limit)
    return {
        "source": "history_db.memory_history",
        "items": items[offset : offset + limit],
        "count": len(items),
        "limit": limit,
        "offset": offset,
    }


def collect_overview(paths: DashboardPaths, filters: DashboardFilters) -> dict[str, Any]:
    history_rows = _fetch_all(
        paths.history_db_path,
        "memory_history",
        "SELECT event, layer, created_at FROM memory_history",
    )
    event_counts = {"ADD": 0, "SEARCH": 0, "UPDATE": 0, "DELETE": 0}
    latest = {"ADD": None, "SEARCH": None, "UPDATE": None, "DELETE": None}
    for row in history_rows:
        event = str(row["event"] or "").upper()
        if event in event_counts:
            event_counts[event] += 1
            created_at = row["created_at"] or ""
            if created_at and (latest[event] is None or created_at > latest[event]):
                latest[event] = created_at

    memory_items = _current_memory_items(paths, filters)
    layer_counts: dict[str, int] = {}
    for item in memory_items:
        layer = item.get("layer") or "unknown"
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

    history_layer_counts: dict[str, int] = {}
    for row in history_rows:
        layer = row["layer"] or ""
        if layer:
            history_layer_counts[layer] = history_layer_counts.get(layer, 0) + 1

    return {
        "profile": {
            "hermes_home": str(paths.hermes_home),
            "data_dir": str(paths.data_dir),
        },
        "health": _health(paths),
        "totals": {
            "history_events": len(history_rows),
            "memory_records": len(memory_items),
            "pipeline_logs": _count(paths.cache_db_path, "pipeline_logs"),
            "system_metric_minutes": _count(paths.cache_db_path, "system_metrics"),
        },
        "event_counts": event_counts,
        "layer_counts": layer_counts,
        "record_layer_counts": layer_counts,
        "history_layer_counts": history_layer_counts,
        "latest": latest,
    }


def collect_usage(paths: DashboardPaths, bucket: str, filters: DashboardFilters) -> dict[str, Any]:
    bucket = bucket if bucket in {"hour", "day", "month"} else "hour"
    buckets: dict[str, dict[str, Any]] = {}

    def entry(timestamp: str) -> dict[str, Any]:
        key = _bucket_key(timestamp, bucket)
        if key not in buckets:
            buckets[key] = {"bucket": key, "add": 0, "search": 0, "update": 0, "delete": 0, "recall_pipeline": 0}
        return buckets[key]

    history_rows = _fetch_all(
        paths.history_db_path,
        "memory_history",
        "SELECT event, created_at FROM memory_history WHERE created_at IS NOT NULL",
    )
    event_to_key = {"ADD": "add", "SEARCH": "search", "UPDATE": "update", "DELETE": "delete"}
    for row in history_rows:
        event = str(row["event"] or "").upper()
        created_at = str(row["created_at"] or "")
        if event in event_to_key and created_at:
            entry(created_at)[event_to_key[event]] += 1

    pipeline_rows = _fetch_all(
        paths.cache_db_path,
        "pipeline_logs",
        "SELECT step, created_at FROM pipeline_logs WHERE created_at IS NOT NULL",
    )
    for row in pipeline_rows:
        step = str(row["step"] or "")
        created_at = str(row["created_at"] or "")
        if step.startswith("READ_") and created_at:
            entry(created_at)["recall_pipeline"] += 1

    return {"bucket": bucket, "series": [buckets[key] for key in sorted(buckets)]}


def collect_activity(paths: DashboardPaths, filters: DashboardFilters) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    history_rows = _fetch_all(
        paths.history_db_path,
        "memory_history",
        """
        SELECT memory_id, isolation_key, event, old_memory, new_memory, layer, actor_id, extra, created_at
        FROM memory_history
        ORDER BY created_at DESC, id DESC
        """,
    )
    for row in history_rows:
        extra = parse_json_object(row["extra"])
        kind = str(row["event"] or "").upper()
        summary = extra.get("query") if kind == "SEARCH" else (row["new_memory"] or row["old_memory"] or "")
        user_id = str(row["isolation_key"] or "").split(":", 1)[0]
        item = {
            "source": "history",
            "kind": kind,
            "timestamp": row["created_at"] or "",
            "user_id": user_id,
            "agent_id": row["actor_id"] or "",
            "memory_id": row["memory_id"] or "",
            "request_id": "",
            "summary": truncate_text(summary),
            "result_count": int(extra.get("results_count") or 0),
            "layer": row["layer"] or "",
            "elapsed_ms": 0,
        }
        if _matches_filters(item, filters):
            items.append(item)

    operation_rows = _fetch_all(
        paths.cache_db_path,
        "memory_operations",
        """
        SELECT request_id, user_id, agent_id, op, memory_id, content, layer, created_at
        FROM memory_operations
        ORDER BY created_at DESC, id DESC
        """,
    )
    for row in operation_rows:
        item = {
            "source": "operation",
            "kind": row["op"] or "",
            "timestamp": row["created_at"] or "",
            "user_id": row["user_id"] or "",
            "agent_id": row["agent_id"] or "",
            "memory_id": row["memory_id"] or "",
            "request_id": row["request_id"] or "",
            "summary": truncate_text(row["content"]),
            "result_count": 0,
            "layer": row["layer"] or "",
            "elapsed_ms": 0,
        }
        if _matches_filters(item, filters):
            items.append(item)

    pipeline_rows = _fetch_all(
        paths.cache_db_path,
        "pipeline_logs",
        """
        SELECT request_id, user_id, agent_id, step, prompt, response, memory_ids, elapsed_ms, created_at
        FROM pipeline_logs
        ORDER BY created_at DESC, id DESC
        """,
    )
    for row in pipeline_rows:
        memory_ids = _parse_json_list(row["memory_ids"])
        item = {
            "source": "pipeline",
            "kind": row["step"] or "",
            "timestamp": row["created_at"] or "",
            "user_id": row["user_id"] or "",
            "agent_id": row["agent_id"] or "",
            "memory_id": "",
            "request_id": row["request_id"] or "",
            "summary": truncate_text(row["prompt"] or row["response"]),
            "result_count": len(memory_ids),
            "layer": "",
            "elapsed_ms": row["elapsed_ms"] or 0,
        }
        if _matches_filters(item, filters):
            items.append(item)

    items.sort(key=lambda item: item["timestamp"], reverse=True)
    offset = _clamp_offset(filters.offset)
    limit = _clamp_limit(filters.limit)
    return {"items": items[offset : offset + limit], "count": len(items), "limit": limit, "offset": offset}


def collect_trace(paths: DashboardPaths, request_id: str) -> dict[str, Any]:
    rows = _fetch_all(
        paths.cache_db_path,
        "pipeline_logs",
        """
        SELECT request_id, user_id, agent_id, step, prompt, response, parsed, memory_ids, elapsed_ms, created_at
        FROM pipeline_logs
        WHERE request_id=?
        ORDER BY created_at ASC, id ASC
        """,
        (request_id,),
    )
    steps = []
    for row in rows:
        steps.append(
            {
                "request_id": row["request_id"] or "",
                "user_id": row["user_id"] or "",
                "agent_id": row["agent_id"] or "",
                "step": row["step"] or "",
                "prompt": truncate_text(row["prompt"]),
                "response": truncate_text(row["response"]),
                "parsed": parse_json_object(row["parsed"]),
                "memory_ids": _parse_json_list(row["memory_ids"]),
                "elapsed_ms": row["elapsed_ms"] or 0,
                "created_at": row["created_at"] or "",
            }
        )
    return {"request_id": request_id, "steps": steps, "count": len(steps)}


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HY Memory Dashboard</title>
  <style>
    :root {
      color-scheme: dark;
      --font-sans: Inter, Geist, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", sans-serif;
      --font-mono: "JetBrains Mono", "IBM Plex Mono", "Berkeley Mono", SFMono-Regular, Consolas, "Liberation Mono", monospace;
      --bg-deep: #020202;
      --bg-0: #050505;
      --bg-1: #070707;
      --bg-2: #0a0908;
      --bg-warm: #080706;
      --surface-0: #0b0b0b;
      --surface-1: #101010;
      --surface-2: #171514;
      --surface-3: #1f1d1c;
      --surface-warm: #17140f;
      --surface-warm-1: #1f1b15;
      --line-0: rgba(184,179,176,.11);
      --line-1: rgba(184,179,176,.18);
      --line-2: rgba(184,179,176,.28);
      --line-3: rgba(184,179,176,.38);
      --line-warm: rgba(239,111,46,.16);
      --line-accent: rgba(239,111,46,.38);
      --text-0: rgba(238,238,238,.96);
      --text-1: rgba(238,238,238,.84);
      --text-2: rgba(184,179,176,.72);
      --text-3: rgba(138,131,128,.78);
      --text-warm-1: rgba(232,226,219,.84);
      --text-invert: #080706;
      --accent-0: #ef6f2e;
      --accent-1: #f28a4d;
      --accent-2: #f2b077;
      --accent-dim: rgba(239,111,46,.15);
      --accent-faint: rgba(239,111,46,.065);
      --ok: #7fd6a1;
      --warn: #e0b96a;
      --danger: #e06055;
      --info: #8fa8c8;
      --radius-micro: 2px;
      --radius-xxs: 4px;
      --radius-xs: 6px;
      --radius-control: 8px;
      --radius-panel: 10px;
      --space-1: 4px;
      --space-2: 8px;
      --space-3: 12px;
      --space-4: 16px;
      --space-5: 20px;
      --space-6: 24px;
      --space-8: 32px;
      --shadow-panel: 0 24px 80px rgba(0,0,0,.48);
      --inset-highlight: inset 0 1px 0 rgba(255,255,255,.055);
      --ease-standard: cubic-bezier(.2,.8,.2,1);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; background: var(--bg-0); }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: var(--font-sans);
      color: var(--text-0);
      background:
        radial-gradient(880px 420px at 74% 6%, rgba(239,111,46,.085), transparent 60%),
        radial-gradient(760px 380px at 12% 18%, rgba(184,179,176,.026), transparent 56%),
        linear-gradient(180deg, var(--bg-1) 0%, var(--bg-0) 58%, var(--bg-deep) 100%);
      -webkit-font-smoothing: antialiased;
      text-rendering: geometricPrecision;
    }
    body::before,
    body::after {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
    }
    body::before {
      opacity: .48;
      background-image: linear-gradient(rgba(184,179,176,.026) 1px, transparent 1px), linear-gradient(90deg, rgba(184,179,176,.026) 1px, transparent 1px);
      background-size: 24px 24px;
      mask-image: radial-gradient(circle at 50% 16%, black 0%, rgba(0,0,0,.55) 36%, transparent 72%);
    }
    body::after {
      opacity: .026;
      background-image: radial-gradient(circle at 1px 1px, rgba(184,179,176,.62) 1px, transparent 0);
      background-size: 6px 6px;
      mix-blend-mode: screen;
    }
    ::selection { background: rgba(239,111,46,.26); color: var(--text-0); }
    :focus-visible { outline: 0; box-shadow: 0 0 0 3px rgba(239,111,46,.20), 0 0 0 1px rgba(239,111,46,.58); }
    .topbar { position: fixed; inset: 0 0 auto; z-index: 60; height: 72px; padding: 0 36px; background: transparent; border: 0; overflow: visible; }
    .topbar-blur { pointer-events: none; position: absolute; inset-inline: 0; top: -20px; z-index: 0; height: 124px; background: linear-gradient(180deg, rgba(5,5,5,.52), rgba(5,5,5,.18) 52%, transparent 76%); backdrop-filter: blur(64px) saturate(1.5); -webkit-backdrop-filter: blur(64px) saturate(1.5); border-bottom: 1px solid rgba(61,58,57,.24); }
    .topbar-inner { position: relative; z-index: 50; max-width: 1920px; height: 72px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 36px; }
    .brand { display: flex; align-items: center; gap: 10px; min-width: 176px; color: rgb(238,238,238); }
    .brand-mark { display: inline-grid; place-items: center; width: 20px; height: 20px; border: 1px solid rgba(238,238,238,.62); border-radius: 3px; background: rgba(238,238,238,.055); color: rgb(238,238,238); font: 700 9px/1 var(--font-mono); letter-spacing: -.04em; }
    .brand-name { margin: 0; font-size: 16px; line-height: 1; letter-spacing: -.025em; font-weight: 500; color: rgb(238,238,238); }
    h1 { margin: 0; font-size: 21px; line-height: 1; letter-spacing: -.055em; font-weight: 640; color: var(--text-0); }
    h2 { margin: 0; font-size: 24px; line-height: 1.05; letter-spacing: -.045em; font-weight: 620; color: var(--text-0); }
    .eyebrow { color: var(--text-3); font: 600 11px/1.2 var(--font-mono); letter-spacing: .16em; text-transform: uppercase; }
    .top-actions { display: flex; align-items: center; justify-content: flex-end; gap: 36px; flex: 1; min-width: 0; }
    .section-nav { display: flex; flex-wrap: nowrap; gap: 32px; align-items: center; justify-content: flex-end; }
    .nav-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
    button, input, select {
      min-height: 34px;
      border: 1px solid var(--line-1);
      border-radius: var(--radius-xxs);
      padding: 8px 10px;
      font: inherit;
      color: var(--text-1);
      background: rgba(16,16,16,.82);
    }
    button { cursor: pointer; font-weight: 650; transition: transform 160ms var(--ease-standard), border-color 160ms var(--ease-standard), background 160ms var(--ease-standard), color 160ms var(--ease-standard), opacity 160ms var(--ease-standard); }
    button:hover { transform: translateY(-1px); border-color: var(--line-2); background: rgba(184,179,176,.060); color: var(--text-0); }
    .nav-pill { position: relative; min-height: 0; height: 12px; border: 0; border-radius: 0; padding: 0; background: transparent; color: rgb(238,238,238); font: 600 12px/1 var(--font-mono); letter-spacing: -.015rem; text-transform: uppercase; opacity: .82; }
    .nav-pill::after { content: ""; position: absolute; left: 0; bottom: -5px; width: 0; height: 1px; background: currentColor; opacity: .84; transition: width 220ms var(--ease-standard); }
    .nav-pill:hover { transform: none; border-color: transparent; background: transparent; color: rgb(238,238,238); opacity: 1; }
    .nav-pill.active { color: rgb(238,238,238); border-color: transparent; background: transparent; box-shadow: none; opacity: 1; }
    .nav-pill.active::after, .nav-pill:hover::after { width: 100%; }
    .top-button { height: 25px; min-height: 25px; border-radius: 3px; border-color: transparent; padding: 0 12px; font: 600 11px/1 var(--font-mono); letter-spacing: .02em; text-transform: uppercase; }
    .primary { color: rgb(238,238,238); background: rgb(31,29,28); box-shadow: none; }
    .primary:hover { background: rgb(238,238,238); color: #020202; border-color: transparent; }
    .nav-status { height: 25px; min-height: 25px; border-radius: 3px; padding: 0 12px; font: 600 11px/1 var(--font-mono); text-transform: uppercase; }
    .dashboard-shell { position: relative; z-index: 1; width: min(1240px, calc(100vw - 48px)); margin: 0 auto; padding: 120px 0 48px; display: grid; gap: var(--space-6); }
    .view-section { display: none; scroll-margin-top: 120px; }
    .view-section.active { display: block; }
    .card {
      position: relative;
      background: linear-gradient(180deg, rgba(184,179,176,.038), rgba(184,179,176,.012)), rgba(16,16,16,.94);
      border: 1px solid var(--line-1);
      border-radius: var(--radius-panel);
      padding: var(--space-5);
      box-shadow: var(--inset-highlight), var(--shadow-panel), 0 0 0 1px rgba(0,0,0,.68);
      overflow: hidden;
    }
    .card::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: linear-gradient(90deg, rgba(239,111,46,.74), rgba(239,111,46,.26), transparent 72%); pointer-events: none; }
    .card + .card { margin-top: var(--space-6); }
    .section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-4); margin-bottom: var(--space-4); padding-bottom: var(--space-3); border-bottom: 1px solid var(--line-0); }
    .subtle { color: var(--text-2); }
    .overview-mount { min-width: 0; }
    .overview-console { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr); gap: var(--space-4); align-items: stretch; }
    .overview-hero { position: relative; min-width: 0; min-height: 336px; display: flex; flex-direction: column; justify-content: space-between; gap: var(--space-4); overflow: hidden; padding: var(--space-5); border: 1px solid var(--line-2); border-radius: var(--radius-control); background: radial-gradient(circle at 18% 8%, rgba(239,111,46,.16), transparent 34%), linear-gradient(135deg, rgba(31,29,28,.92), rgba(8,8,8,.98) 58%), var(--surface-1); box-shadow: var(--inset-highlight), 0 28px 80px rgba(0,0,0,.34); }
    .overview-hero::before { content: ""; position: absolute; inset: 0; pointer-events: none; background-image: linear-gradient(rgba(184,179,176,.055) 1px, transparent 1px), linear-gradient(90deg, rgba(184,179,176,.040) 1px, transparent 1px); background-size: 28px 28px; mask-image: linear-gradient(120deg, rgba(0,0,0,.62), transparent 72%); }
    .overview-hero::after { content: ""; position: absolute; left: 0; top: 0; width: 3px; height: 100%; background: linear-gradient(180deg, var(--accent-0), rgba(239,111,46,.22), transparent); opacity: .72; }
    .overview-hero > * { position: relative; z-index: 1; }
    .overview-hero-top { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
    .overview-hero-title { margin: 6px 0 0; font-size: clamp(28px, 3.1vw, 46px); line-height: .96; letter-spacing: -.07em; font-weight: 620; color: var(--text-0); }
    .overview-primary { font-size: clamp(64px, 9vw, 118px); line-height: .82; letter-spacing: -.085em; font-weight: 660; color: var(--text-0); text-shadow: 0 0 32px rgba(239,111,46,.16); }
    .overview-primary-label { margin-top: 10px; color: var(--text-warm-1); font: 700 12px/1.2 var(--font-mono); letter-spacing: .13em; text-transform: uppercase; }
    .overview-hero-caption { max-width: 620px; margin-top: var(--space-2); color: var(--text-2); line-height: 1.55; }
    .overview-source-rail { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .source-node { min-width: 0; display: grid; gap: 6px; padding: 10px; border: 1px solid var(--line-0); border-radius: var(--radius-xxs); background: rgba(5,5,5,.50); }
    .source-node-head { display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--text-2); font: 700 10px/1.1 var(--font-mono); letter-spacing: .12em; text-transform: uppercase; }
    .source-dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 2px; background: var(--danger); box-shadow: 0 0 14px rgba(224,96,85,.42); }
    .source-node.ok .source-dot { background: var(--ok); box-shadow: 0 0 14px rgba(127,214,161,.36); }
    .source-node-status { color: var(--text-3); font: 600 11px/1 var(--font-mono); text-transform: uppercase; }
    .overview-paths { display: grid; gap: 7px; padding-top: var(--space-3); border-top: 1px solid var(--line-0); }
    .overview-path-row { min-width: 0; display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 12px; align-items: baseline; }
    .overview-path-label { color: var(--text-3); font: 700 10px/1.2 var(--font-mono); letter-spacing: .12em; text-transform: uppercase; }
    .overview-path-value { max-width: 100%; min-width: 0; overflow: hidden; text-overflow: ellipsis; overflow-wrap: anywhere; white-space: nowrap; color: var(--text-warm-1); font: 600 12px/1.35 var(--font-mono); }
    .overview-matrix { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); min-width: 0; }
    .overview-stat { position: relative; min-width: 0; min-height: 126px; overflow: hidden; display: flex; flex-direction: column; justify-content: space-between; gap: var(--space-2); padding: var(--space-4); border: 1px solid var(--line-0); border-radius: var(--radius-xs); background: linear-gradient(180deg, rgba(184,179,176,.030), rgba(184,179,176,.010)), rgba(12,12,12,.92); box-shadow: var(--inset-highlight); }
    .overview-stat::before { content: ""; position: absolute; inset: 0 0 auto; height: 1px; background: linear-gradient(90deg, rgba(239,111,46,.55), transparent 70%); opacity: .55; }
    .overview-label { color: var(--text-3); font: 700 10px/1.2 var(--font-mono); letter-spacing: .13em; text-transform: uppercase; }
    .overview-value { max-width: 100%; min-width: 0; overflow: hidden; text-overflow: ellipsis; overflow-wrap: anywhere; word-break: break-word; white-space: normal; color: var(--text-0); font-size: clamp(28px, 3vw, 42px); line-height: .96; letter-spacing: -.06em; font-weight: 650; }
    .overview-value-compact { font-family: var(--font-mono); font-size: clamp(16px, 1.45vw, 22px); line-height: 1.16; letter-spacing: -.025em; white-space: nowrap; }
    .overview-hint { color: var(--text-3); font: 600 11px/1.35 var(--font-mono); overflow-wrap: anywhere; }
    .overview-panels { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr); gap: var(--space-4); }
    .overview-panel { min-width: 0; padding: var(--space-4); border: 1px solid var(--line-0); border-radius: var(--radius-control); background: rgba(5,5,5,.54); box-shadow: var(--inset-highlight); }
    .overview-panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-3); }
    .overview-panel-title { margin: 0; color: var(--text-0); font-size: 15px; line-height: 1.1; letter-spacing: -.02em; font-weight: 650; }
    .overview-layer-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
    .overview-mini-title { margin-bottom: 8px; color: var(--text-3); font: 700 10px/1.2 var(--font-mono); letter-spacing: .12em; text-transform: uppercase; }
    .overview-layer-list, .overview-event-list { display: grid; gap: 8px; }
    .overview-layer-row, .overview-event-row { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; }
    .overview-layer-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; overflow-wrap: anywhere; color: var(--text-1); font: 650 12px/1.25 var(--font-mono); }
    .overview-layer-count, .overview-event-count { color: var(--text-2); font: 700 12px/1 var(--font-mono); }
    .overview-track { grid-column: 1 / -1; height: 7px; overflow: hidden; border-radius: var(--radius-micro); background: rgba(184,179,176,.055); border: 1px solid var(--line-0); }
    .overview-fill { height: 100%; min-width: 3px; background: linear-gradient(90deg, var(--accent-0), rgba(239,111,46,.28)); }
    .overview-event-row .overview-fill.badge-kind-add { background: var(--accent-0); }
    .overview-event-row .overview-fill.badge-kind-search { background: rgba(126,166,188,.70); }
    .overview-event-row .overview-fill.badge-kind-update { background: rgba(215,195,140,.78); }
    .overview-event-row .overview-fill.badge-kind-delete { background: rgba(224,96,85,.80); }
    @media (max-width: 980px) { .overview-console, .overview-panels { grid-template-columns: 1fr; } .overview-hero { min-height: 300px; } }
    @media (max-width: 680px) { .overview-source-rail, .overview-matrix, .overview-layer-columns { grid-template-columns: 1fr; } .overview-path-row { grid-template-columns: 1fr; gap: 4px; } .overview-path-value { white-space: normal; } }
    .badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: var(--radius-xxs); font: 650 12px/1.2 var(--font-mono); letter-spacing: .02em; background: rgba(184,179,176,.080); color: var(--text-2); border: 1px solid var(--line-1); }
    .badge.ok { background: rgba(127,214,161,.070); color: var(--ok); border-color: rgba(127,214,161,.22); }
    .badge.error { background: rgba(224,96,85,.070); color: var(--danger); border-color: rgba(224,96,85,.24); }
    .badge-kind-add { background: rgba(239,111,46,.14); color: var(--accent-2); border-color: rgba(239,111,46,.34); }
    .badge-kind-search { background: rgba(126,166,188,.12); color: #a9ccdc; border-color: rgba(126,166,188,.30); }
    .badge-kind-update { background: rgba(215,195,140,.12); color: #e4cb8f; border-color: rgba(215,195,140,.30); }
    .badge-kind-delete { background: rgba(224,96,85,.10); color: var(--danger); border-color: rgba(224,96,85,.30); }
    .badge-kind-pipeline { background: rgba(172,135,255,.10); color: #c8b7ff; border-color: rgba(172,135,255,.25); }
    .badge-kind-default { background: rgba(184,179,176,.080); color: var(--text-2); border-color: var(--line-1); }
    .toolbar { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; margin-bottom: var(--space-3); }
    input, select { min-width: 220px; color: var(--text-1); background: rgba(5,5,5,.76); border-color: var(--line-1); }
    input::placeholder { color: var(--text-3); }
    select option { background: var(--bg-1); color: var(--text-0); }
    code { font-family: var(--font-mono); font-size: 12px; color: var(--text-warm-1); }
    .bars { display: grid; gap: var(--space-2); }
    .usage-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 22px; margin-bottom: var(--space-4); }
    .usage-toolbar select { flex: 0 0 auto; }
    .usage-legend { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px 14px; margin: 0; padding: 0; border: 0; background: transparent; box-shadow: none; min-width: 0; flex: 1 1 520px; }
    .legend-item { display: inline-flex; align-items: center; gap: 7px; color: var(--text-2); font: 650 11px/1.15 var(--font-mono); letter-spacing: .055em; text-transform: uppercase; }
    .legend-swatch { width: 18px; height: 8px; border-radius: 2px; border: 1px solid rgba(184,179,176,.22); box-shadow: inset 0 1px 0 rgba(255,255,255,.050); }
    .legend-swatch.seg-add { background: var(--accent-0); }
    .legend-swatch.seg-search { background: rgba(184,179,176,.36); }
    .legend-swatch.seg-update { background: var(--accent-2); }
    .legend-swatch.seg-delete { background: var(--danger); }
    .legend-swatch.seg-pipeline { background: rgba(239,111,46,.28); }
    .bar { display: grid; grid-template-columns: 140px 1fr; gap: var(--space-3); align-items: center; }
    .track { height: 12px; border-radius: var(--radius-micro); overflow: hidden; background: rgba(184,179,176,.040); display: flex; border: 1px solid var(--line-0); }
    .seg-add { background: var(--accent-0); }
    .seg-search { background: rgba(184,179,176,.36); }
    .seg-update { background: var(--accent-2); }
    .seg-delete { background: var(--danger); }
    .seg-pipeline { background: rgba(239,111,46,.28); }
    .table-shell { border: 1px solid var(--line-2); border-radius: var(--radius-control); overflow: hidden; background: rgba(5,5,5,.68); box-shadow: var(--inset-highlight); }
    .table-meta { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: 10px 12px; border-bottom: 1px solid var(--line-0); color: var(--text-3); font: 600 12px/1.2 var(--font-mono); background: rgba(184,179,176,.018); }
    .table-wrap { max-height: min(62vh, 720px); overflow: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
    th, td { padding: 10px; border-top: 1px solid rgba(184,179,176,.20); text-align: left; vertical-align: top; color: var(--text-1); }
    th + th, td + td { border-left: 1px solid rgba(184,179,176,.16); }
    tbody tr:first-child td { border-top: 0; }
    tbody tr:nth-child(odd) td { background: rgba(184,179,176,.014); }
    tbody tr:hover td { background: rgba(239,111,46,.040); }
    th { position: sticky; top: 0; z-index: 2; background: rgba(16,16,16,.98); color: var(--text-3); font: 700 11px/1.1 var(--font-mono); letter-spacing: .12em; text-transform: uppercase; border-bottom: 1px solid var(--line-1); }
    .line-clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; text-overflow: ellipsis; overflow-wrap: anywhere; line-height: 1.45; max-height: 2.9em; color: var(--text-1); }
    .content-cell { cursor: pointer; border: 1px solid transparent; border-radius: var(--radius-xxs); margin: -3px -4px; padding: 3px 4px; transition: border-color .14s ease, background .14s ease, color .14s ease; }
    .content-cell:hover, .content-cell:focus-visible { outline: none; color: var(--text-0); border-color: var(--line-accent); background: rgba(239,111,46,.045); }
    .content-cell.expanded { display: block; -webkit-line-clamp: unset; -webkit-box-orient: initial; max-height: none; overflow: visible; white-space: pre-wrap; color: var(--text-0); background: rgba(239,111,46,.055); border-color: rgba(239,111,46,.24); }
    .table-shell.content-expanded .table-wrap { max-height: none; overflow: visible; }
    .mono-cell { overflow-wrap: anywhere; font-family: var(--font-mono); color: var(--text-2); }
    .pager { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-2); padding: 10px 12px; border-top: 1px solid var(--line-0); background: rgba(184,179,176,.018); }
    .pager-controls { display: flex; align-items: center; gap: var(--space-2); }
    .pager button { min-width: 74px; color: var(--text-1); background: rgba(16,16,16,.86); }
    .pager button:not(:disabled) { border-color: var(--line-accent); color: var(--accent-2); }
    .pager button:disabled { cursor: not-allowed; opacity: .34; transform: none; }
    .empty { padding: var(--space-5); color: var(--text-2); }
    .error-line { color: var(--danger); font-weight: 700; }
    .trace-grid { display: grid; gap: var(--space-3); }
    .trace-step { border: 1px solid var(--line-1); border-radius: var(--radius-control); padding: var(--space-4); background: rgba(16,16,16,.82); box-shadow: var(--inset-highlight); }
    @media (max-width: 980px) {
      .topbar { height: auto; min-height: 72px; padding: 0 16px; }
      .topbar-blur { height: 220px; }
      .topbar-inner { height: auto; min-height: 72px; align-items: flex-start; flex-direction: column; gap: 14px; padding: 16px 0 14px; }
      .brand { min-width: 0; }
      .top-actions { width: 100%; justify-content: flex-start; flex-wrap: wrap; gap: 14px; }
      .section-nav { justify-content: flex-start; flex-wrap: wrap; gap: 14px 18px; }
      .nav-actions { justify-content: flex-start; }
      .dashboard-shell { width: min(100vw - 32px, 1240px); padding-top: 150px; }
      .view-section { scroll-margin-top: 150px; }
      .bar { grid-template-columns: 1fr; }
      table { min-width: 920px; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; scroll-behavior: auto !important; }
    }
  </style>
</head>
<body>
<header class="topbar">
  <div class="topbar-blur" aria-hidden="true"></div>
  <div class="topbar-inner">
    <div class="brand" aria-label="Local read-only HY Memory Dashboard">
      <span class="brand-mark" aria-hidden="true">HY</span>
      <h1 class="brand-name">HY Memory</h1>
    </div>
    <div class="top-actions">
      <nav id="sectionNav" class="section-nav" aria-label="Dashboard sections">
        <button class="nav-pill active" type="button" data-view="overview">Overview</button>
        <button class="nav-pill" type="button" data-view="usage">Usage</button>
        <button class="nav-pill" type="button" data-view="activity">Activity</button>
        <button class="nav-pill" type="button" data-view="memories">Structured</button>
        <button class="nav-pill" type="button" data-view="history">Raw / History</button>
        <button class="nav-pill" type="button" data-view="trace">Trace</button>
      </nav>
      <div class="nav-actions">
        <button id="refreshButton" class="primary top-button" type="button">Refresh</button>
        <span id="healthBadge" class="badge nav-status">Loading</span>
      </div>
    </div>
  </div>
</header>
<main class="dashboard-shell">
  <section id="view-overview" class="view-section active" data-view-panel="overview">
    <div class="card">
      <div class="section-head"><div><div class="eyebrow">State</div><h2>Current Memory Overview</h2></div><div class="subtle">Active state, history, and latest recall status.</div></div>
      <div id="overview" class="overview-mount"></div>
    </div>
  </section>
  <section id="view-usage" class="view-section" data-view-panel="usage">
    <div class="card">
      <div class="section-head"><div><div class="eyebrow">Timeline</div><h2>Memory Usage</h2></div></div>
      <div class="toolbar usage-toolbar"><select id="bucket"><option value="hour">Hourly</option><option value="day">Daily</option><option value="month">Monthly</option></select><div id="usageLegend" class="usage-legend" role="list" aria-label="Memory usage color legend">
        <span class="legend-item" role="listitem" data-legend-key="add"><span class="legend-swatch seg-add" aria-hidden="true"></span>Add</span>
        <span class="legend-item" role="listitem" data-legend-key="search"><span class="legend-swatch seg-search" aria-hidden="true"></span>Search</span>
        <span class="legend-item" role="listitem" data-legend-key="update"><span class="legend-swatch seg-update" aria-hidden="true"></span>Update</span>
        <span class="legend-item" role="listitem" data-legend-key="delete"><span class="legend-swatch seg-delete" aria-hidden="true"></span>Delete</span>
        <span class="legend-item" role="listitem" data-legend-key="recall_pipeline"><span class="legend-swatch seg-pipeline" aria-hidden="true"></span>Recall pipeline</span>
      </div></div>
      <div id="usage" class="bars"></div>
    </div>
  </section>
  <section id="view-activity" class="view-section" data-view-panel="activity" data-page-size="25">
    <div class="card">
      <div class="section-head"><div><div class="eyebrow">Audit stream</div><h2>Recent Activity</h2></div><div class="subtle">History, operation, and recall pipeline events are paginated instead of fully rendered.</div></div>
      <div id="activity"></div>
    </div>
  </section>
  <section id="view-memories" class="view-section" data-view-panel="memories" data-page-size="25">
    <div class="card">
      <div class="section-head"><div><div class="eyebrow">Active records</div><h2>Current Structured Memory Records</h2></div></div>
      <div class="toolbar"><input id="memoryQuery" placeholder="Search memory content"><select id="layerFilter"><option value="">All current structured layers</option></select></div>
      <p class="subtle">Memory Records shows active structured records from local vector metadata (<code>vector_db/chroma.sqlite3</code>). Raw L1/history events are visible in Recent Activity and counted separately in the overview; <code>cache.db.memory_operations</code> remains an operation/audit log.</p>
      <div id="memories"></div>
    </div>
  </section>
  <section id="view-history" class="view-section" data-view-panel="history" data-page-size="25">
    <div class="card">
      <div class="section-head"><div><div class="eyebrow">Raw events</div><h2>Raw / History Memory Records</h2></div></div>
      <div class="toolbar"><input id="historyQuery" placeholder="Search raw/history content"><select id="historyLayerFilter"><option value="">All history layers</option></select></div>
      <p class="subtle">This read-only view comes from <code>history.db.memory_history</code>, so it includes raw <code>l1_raw</code> events and historical <code>l3_*</code> rows when they exist.</p>
      <div id="historyRecords"></div>
    </div>
  </section>
  <section id="view-trace" class="view-section" data-view-panel="trace">
    <div class="card">
      <div class="section-head"><div><div class="eyebrow">Pipeline detail</div><h2>Trace</h2></div><div class="subtle">Click an activity request id to inspect recall pipeline steps.</div></div>
      <div id="trace" class="subtle">No trace selected yet.</div>
    </div>
  </section>
</main>
<script>
const PAGE_SIZE = 25;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function shortText(value, limit = 180) {
  const text = String(value ?? '');
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}
const textCell = (value, limit = 180) => `<div class="line-clamp" title="${esc(value)}">${esc(shortText(value, limit))}</div>`;
function badgeClassFor(value) {
  const kind = String(value ?? '').toUpperCase();
  if (!kind) return 'badge-kind-default';
  if (kind.includes('DELETE') || kind.includes('REMOVE')) return 'badge-kind-delete';
  if (kind.includes('SEARCH')) return 'badge-kind-search';
  if (kind.includes('UPDATE') || kind.includes('SUPERSEDE') || kind.includes('UPSERT')) return 'badge-kind-update';
  if (kind.includes('ADD') || kind.includes('SAVE') || kind.includes('CREATE')) return 'badge-kind-add';
  if (kind.startsWith('READ_') || kind.includes('RECALL') || kind.includes('RERANK') || kind.includes('PIPELINE')) return 'badge-kind-pipeline';
  return 'badge-kind-default';
}
function badgeFor(value) {
  const kind = String(value ?? '').toUpperCase() || 'UNKNOWN';
  return `<span class="badge ${badgeClassFor(kind)}" data-badge-kind="${esc(kind)}">${esc(kind)}</span>`;
}
function contentCell(value, limit = 180, kind = 'content') {
  const text = String(value ?? '');
  const short = shortText(text, limit);
  return `<div class="line-clamp content-cell" role="button" tabindex="0" aria-expanded="false" data-expandable-content data-content-kind="${esc(kind)}" data-short-text="${esc(short)}" data-full-text="${esc(text)}" title="Click to show full content">${esc(short)}</div>`;
}
function toggleContentCell(cell) {
  const expanded = !cell.classList.contains('expanded');
  cell.classList.toggle('expanded', expanded);
  cell.setAttribute('aria-expanded', String(expanded));
  cell.textContent = expanded ? cell.dataset.fullText : cell.dataset.shortText;
  cell.title = expanded ? 'Click to collapse content' : 'Click to show full content';
  const shell = cell.closest('.table-shell');
  if (shell) {
    shell.classList.toggle('content-expanded', Boolean(shell.querySelector('[data-expandable-content].expanded')));
  }
}
async function api(path) { const res = await fetch(path); if (!res.ok) throw new Error(`${res.status} ${res.statusText}`); return await res.json(); }
function formatCount(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number.toLocaleString() : esc(value);
}
function latestSaveTime(latest) {
  const values = [latest?.ADD, latest?.UPDATE, latest?.DELETE].filter(Boolean).sort();
  return values.length ? values[values.length - 1] : '—';
}
function sourceNode(label, status) {
  const ok = status === 'ok';
  return `<div class="source-node ${ok ? 'ok' : 'error'}" data-source-status="${esc(label)}:${esc(status || 'missing')}"><div class="source-node-head"><span class="source-dot" aria-hidden="true"></span><span>${esc(label)}</span></div><div class="source-node-status">${ok ? 'online' : esc(status || 'missing')}</div></div>`;
}
function localOffsetLabel(date) {
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? '+' : '-';
  const absolute = Math.abs(offsetMinutes);
  const hours = Math.floor(absolute / 60);
  const minutes = absolute % 60;
  return minutes ? `UTC${sign}${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}` : `UTC${sign}${hours}`;
}
function localTimestamp(value, withSeconds = false) {
  const text = String(value || '—');
  const parsed = new Date(text);
  if (!text || text === '—' || Number.isNaN(parsed.getTime())) return text;
  const month = String(parsed.getMonth() + 1).padStart(2, '0');
  const day = String(parsed.getDate()).padStart(2, '0');
  const hour = String(parsed.getHours()).padStart(2, '0');
  const minute = String(parsed.getMinutes()).padStart(2, '0');
  const second = String(parsed.getSeconds()).padStart(2, '0');
  return `${month}-${day} ${hour}:${minute}${withSeconds ? `:${second}` : ''} ${localOffsetLabel(parsed)}`;
}
function utcDateFromTimestamp(value) {
  const text = String(value || '').trim();
  if (!text || text === '—') return null;
  const isoLike = text.includes('T') ? text : text.replace(' ', 'T');
  const timestamp = /(?:Z|[+-][0-9]{2}:?[0-9]{2})$/.test(isoLike) ? isoLike : `${isoLike}Z`;
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
const beijingDateTimeFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
  hourCycle: 'h23',
});
function beijingTimestamp(value) {
  const text = String(value || '—').trim();
  const parsed = utcDateFromTimestamp(text);
  if (!parsed) return text || '—';
  const parts = Object.fromEntries(beijingDateTimeFormatter.formatToParts(parsed).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} 北京时间`;
}
function compactTimestamp(value) {
  return localTimestamp(value, false);
}
function overviewStat(label, value, hint = '', titleValue = value) {
  const text = String(value ?? '—');
  const title = String(titleValue ?? text);
  const compact = text.length > 10;
  return `<div class="overview-stat"><div class="overview-label">${esc(label)}</div><div class="overview-value ${compact ? 'overview-value-compact' : ''}" title="${esc(title)}">${esc(text)}</div><div class="overview-hint">${esc(hint)}</div></div>`;
}
function overviewPathRow(label, value, titleValue = value) {
  const text = String(value || '—');
  const title = String(titleValue || text);
  return `<div class="overview-path-row"><div class="overview-path-label">${esc(label)}</div><div class="overview-path-value" title="${esc(title)}">${esc(text)}</div></div>`;
}
function overviewLayerRows(counts, emptyLabel) {
  const entries = Object.entries(counts || {}).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0) || String(a[0]).localeCompare(String(b[0])));
  if (!entries.length) return `<div class="empty">${esc(emptyLabel)}</div>`;
  const max = Math.max(...entries.map(([, count]) => Number(count || 0)), 1);
  return `<div class="overview-layer-list">${entries.map(([layer, count]) => {
    const numeric = Number(count || 0);
    const width = Math.max(3, Math.round((numeric / max) * 100));
    return `<div class="overview-layer-row" data-overview-layer="${esc(layer)}"><div class="overview-layer-name" title="${esc(layer)}">${esc(layer)}</div><div class="overview-layer-count">${esc(numeric)}</div><div class="overview-track" aria-hidden="true"><div class="overview-fill" style="width:${width}%"></div></div></div>`;
  }).join('')}</div>`;
}
function overviewEventRows(counts) {
  const entries = ['ADD', 'SEARCH', 'UPDATE', 'DELETE'].map(event => [event, Number((counts || {})[event] || 0)]);
  const max = Math.max(...entries.map(([, count]) => count), 1);
  return `<div class="overview-event-list">${entries.map(([event, count]) => {
    const width = Math.max(3, Math.round((count / max) * 100));
    return `<div class="overview-event-row" data-overview-event="${esc(event)}"><div>${badgeFor(event)}</div><div class="overview-event-count">${esc(count)}</div><div class="overview-track" aria-hidden="true"><div class="overview-fill ${badgeClassFor(event)}" style="width:${width}%"></div></div></div>`;
  }).join('')}</div>`;
}
const activityState = { offset: 0, count: 0 };
const memoryState = { offset: 0, count: 0 };
const historyState = { offset: 0, count: 0 };
function pageBounds(state, pageSize) {
  const start = state.count ? state.offset + 1 : 0;
  const end = Math.min(state.offset + pageSize, state.count);
  const page = Math.floor(state.offset / pageSize) + 1;
  const pages = Math.max(1, Math.ceil(state.count / pageSize));
  return { start, end, page, pages };
}
function renderPager(target, state, pageSize) {
  const b = pageBounds(state, pageSize);
  return `<div class="pager" data-pager="${esc(target)}"><div class="subtle">Rows ${b.start}-${b.end} of ${esc(state.count)} · Page ${b.page}/${b.pages}</div><div class="pager-controls"><button type="button" data-page-target="${esc(target)}" data-page-dir="prev" ${state.offset <= 0 ? 'disabled' : ''}>Prev</button><button type="button" data-page-target="${esc(target)}" data-page-dir="next" ${state.offset + pageSize >= state.count ? 'disabled' : ''}>Next</button></div></div>`;
}
function tableShell(target, state, pageSize, columns, rows) {
  const b = pageBounds(state, pageSize);
  if (!rows) rows = `<tr><td colspan="${columns.length}"><div class="empty">No records for this page.</div></td></tr>`;
  return `<div class="table-shell"><div class="table-meta"><span>${esc(target)} table</span><span>Showing ${b.start}-${b.end} of ${esc(state.count)}; long cells are truncated.</span></div><div class="table-wrap"><table><thead><tr>${columns.map(col => `<th style="width:${esc(col.width || 'auto')}">${esc(col.label)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>${renderPager(target, state, pageSize)}</div>`;
}
function showView(view) {
  document.querySelectorAll('[data-view-panel]').forEach(section => section.classList.toggle('active', section.dataset.viewPanel === view));
  document.querySelectorAll('#sectionNav [data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  const panel = document.querySelector(`[data-view-panel="${CSS.escape(view)}"]`);
  if (panel) panel.scrollIntoView({ block: 'start' });
}
async function loadOverview() {
  try {
    const data = await api('/api/overview');
    const healthOk = data.health.history_db === 'ok' && data.health.cache_db === 'ok' && data.health.vector_db === 'ok';
    document.getElementById('healthBadge').className = `badge ${healthOk ? 'ok' : 'error'}`;
    document.getElementById('healthBadge').textContent = healthOk ? 'Healthy' : 'Check databases';
    const recordLayerCounts = data.record_layer_counts || data.layer_counts || {};
    const historyLayerCounts = data.history_layer_counts || {};
    const countPrefix = (counts, prefix) => Object.entries(counts).reduce((total, [layer, count]) => total + (String(layer).startsWith(prefix) ? Number(count || 0) : 0), 0);
    const currentL3 = countPrefix(recordLayerCounts, 'l3');
    const historyL1 = countPrefix(historyLayerCounts, 'l1');
    const latestSave = latestSaveTime(data.latest || {});
    const latestSearch = (data.latest || {}).SEARCH || '—';
    document.getElementById('overview').innerHTML = `
      <div class="overview-console">
        <section class="overview-hero" data-overview-hero>
          <div class="overview-hero-top">
            <div><div class="eyebrow">Local state</div><h3 class="overview-hero-title">Memory command surface</h3></div>
            <span class="badge ${healthOk ? 'ok' : 'error'}">${healthOk ? 'Healthy' : 'Check databases'}</span>
          </div>
          <div>
            <div class="overview-primary" title="${esc(data.totals.memory_records)}">${formatCount(data.totals.memory_records)}</div>
            <div class="overview-primary-label">Current structured records</div>
            <div class="overview-hero-caption">Active non-raw structured nodes from local vector metadata. Raw history, audit operations, and recall pipeline telemetry stay visible without becoming the current state.</div>
          </div>
          <div class="overview-source-rail" aria-label="Database source health">
            ${sourceNode('history db', data.health.history_db)}
            ${sourceNode('cache db', data.health.cache_db)}
            ${sourceNode('vector db', data.health.vector_db)}
          </div>
          <div class="overview-paths">
            ${overviewPathRow('Hermes home', data.profile.hermes_home)}
            ${overviewPathRow('Data dir', data.profile.data_dir)}
          </div>
        </section>
        <aside class="overview-matrix" data-overview-matrix>
          ${overviewStat('History events', formatCount(data.totals.history_events), 'history table events')}
          ${overviewStat('Pipeline logs', formatCount(data.totals.pipeline_logs), 'recall-side READ_* traces')}
          ${overviewStat('ADD / SEARCH', `${formatCount(data.event_counts.ADD)} / ${formatCount(data.event_counts.SEARCH)}`, 'save-side / recall-side history')}
          ${overviewStat('History raw L1', formatCount(historyL1), 'raw l1 events from history table')}
          ${overviewStat('Current L3 records', formatCount(currentL3), 'active structured summary nodes')}
          ${overviewStat('Latest search', compactTimestamp(latestSearch), 'most recent recall timestamp', latestSearch)}
        </aside>
        <div class="overview-panels">
          <section class="overview-panel" data-overview-layers>
            <div class="overview-panel-head"><div><div class="eyebrow">Topology</div><h3 class="overview-panel-title">Layer distribution</h3></div><div class="subtle">Current vs history</div></div>
            <div class="overview-layer-columns">
              <div><div class="overview-mini-title">Current records</div>${overviewLayerRows(recordLayerCounts, 'No current structured layers')}</div>
              <div><div class="overview-mini-title">History events</div>${overviewLayerRows(historyLayerCounts, 'No history layers')}</div>
            </div>
          </section>
          <section class="overview-panel" data-overview-events>
            <div class="overview-panel-head"><div><div class="eyebrow">Operations</div><h3 class="overview-panel-title">Event composition</h3></div><div class="subtle">History table</div></div>
            ${overviewEventRows(data.event_counts)}
            <div class="overview-paths" style="margin-top:14px">
              ${overviewPathRow('Latest save', localTimestamp(latestSave, true), latestSave)}
              ${overviewPathRow('Latest search', localTimestamp(latestSearch, true), latestSearch)}
            </div>
          </section>
        </div>
      </div>`;
    const layerSelect = document.getElementById('layerFilter');
    const current = layerSelect.value;
    layerSelect.innerHTML = '<option value="">All current structured layers</option>' + Object.keys(data.record_layer_counts || data.layer_counts || {}).sort().map(layer => `<option value="${esc(layer)}">${esc(layer)} (${(data.record_layer_counts || data.layer_counts || {})[layer]})</option>`).join('');
    layerSelect.value = current;
    const historyLayerSelect = document.getElementById('historyLayerFilter');
    const currentHistoryLayer = historyLayerSelect.value;
    historyLayerSelect.innerHTML = '<option value="">All history layers</option>' + Object.keys(data.history_layer_counts || {}).sort().map(layer => `<option value="${esc(layer)}">${esc(layer)} (${(data.history_layer_counts || {})[layer]})</option>`).join('');
    historyLayerSelect.value = currentHistoryLayer;
  } catch (err) { document.getElementById('overview').innerHTML = `<div class="error-line">${esc(err.message)}</div>`; }
}
async function loadUsage() {
  try {
    const bucket = document.getElementById('bucket').value;
    const data = await api(`/api/usage?bucket=${encodeURIComponent(bucket)}`);
    const max = Math.max(1, ...data.series.map(row => row.add + row.search + row.update + row.delete + row.recall_pipeline));
    document.getElementById('usage').innerHTML = data.series.map(row => {
      const total = row.add + row.search + row.update + row.delete + row.recall_pipeline;
      const seg = (cls, value) => `<span class="${cls}" style="width:${(value / max) * 100}%"></span>`;
      return `<div class="bar"><code>${esc(row.bucket)}</code><div><div class="track">${seg('seg-add', row.add)}${seg('seg-search', row.search)}${seg('seg-update', row.update)}${seg('seg-delete', row.delete)}${seg('seg-pipeline', row.recall_pipeline)}</div><div class="subtle">total ${total}</div></div></div>`;
    }).join('') || '<div class="subtle">No activity yet.</div>';
  } catch (err) { document.getElementById('usage').innerHTML = `<div class="error-line">${esc(err.message)}</div>`; }
}
async function loadActivity() {
  try {
    const pageSize = PAGE_SIZE;
    const data = await api(`/api/activity?limit=${pageSize}&offset=${activityState.offset}`);
    activityState.count = Number(data.count || 0);
    activityState.offset = Number(data.offset || 0);
    const rows = data.items.map(item => `<tr><td class="mono-cell"><code>${esc(item.timestamp)}</code></td><td>${esc(item.source)}</td><td>${badgeFor(item.kind)}</td><td class="mono-cell">${item.request_id ? `<button type="button" data-request-id="${esc(item.request_id)}">${esc(shortText(item.request_id, 10))}</button>` : '—'}</td><td>${textCell(item.summary, 180)}</td><td>${esc(item.result_count)}</td></tr>`).join('');
    document.getElementById('activity').innerHTML = tableShell('activity', activityState, pageSize, [
      {label:'Time', width:'18%'}, {label:'Source', width:'9%'}, {label:'Kind', width:'15%'}, {label:'Request', width:'12%'}, {label:'Summary', width:'38%'}, {label:'Results', width:'8%'}
    ], rows);
  } catch (err) { document.getElementById('activity').innerHTML = `<div class="error-line">${esc(err.message)}</div>`; }
}
async function loadMemories() {
  try {
    const pageSize = PAGE_SIZE;
    const q = document.getElementById('memoryQuery').value;
    const layer = document.getElementById('layerFilter').value;
    const data = await api(`/api/memories?limit=${pageSize}&offset=${memoryState.offset}&query=${encodeURIComponent(q)}&layer=${encodeURIComponent(layer)}`);
    memoryState.count = Number(data.count || 0);
    memoryState.offset = Number(data.offset || 0);
    const rows = data.items.map(item => `<tr><td class="mono-cell"><code title="${esc(item.memory_id)}">${esc(shortText(item.memory_id, 12))}</code></td><td>${esc(item.layer)}</td><td>${esc(shortText(item.user_id, 18))}</td><td>${esc(shortText(item.agent_id, 18))}</td><td>${contentCell(item.content, 190, 'structured')}</td></tr>`).join('');
    document.getElementById('memories').innerHTML = tableShell('memories', memoryState, pageSize, [
      {label:'ID', width:'16%'}, {label:'Layer', width:'14%'}, {label:'User', width:'14%'}, {label:'Agent', width:'14%'}, {label:'Content', width:'42%'}
    ], rows);
  } catch (err) { document.getElementById('memories').innerHTML = `<div class="error-line">${esc(err.message)}</div>`; }
}
async function loadHistoryRecords() {
  try {
    const pageSize = PAGE_SIZE;
    const q = document.getElementById('historyQuery').value;
    const layer = document.getElementById('historyLayerFilter').value;
    const data = await api(`/api/history-records?limit=${pageSize}&offset=${historyState.offset}&query=${encodeURIComponent(q)}&layer=${encodeURIComponent(layer)}`);
    historyState.count = Number(data.count || 0);
    historyState.offset = Number(data.offset || 0);
    const rows = data.items.map(item => `<tr><td class="mono-cell"><code title="${esc(item.created_at)}">${esc(beijingTimestamp(item.created_at))}</code></td><td class="mono-cell"><code title="${esc(item.memory_id)}">${esc(shortText(item.memory_id, 12))}</code></td><td>${esc(item.layer)}</td><td>${badgeFor(item.event)}</td><td>${esc(shortText(item.user_id, 18))}</td><td>${esc(shortText(item.agent_id, 18))}</td><td>${contentCell(item.content, 190, 'history')}</td></tr>`).join('');
    document.getElementById('historyRecords').innerHTML = tableShell('history', historyState, pageSize, [
      {label:'Time (Beijing)', width:'17%'}, {label:'ID', width:'13%'}, {label:'Layer', width:'12%'}, {label:'Event', width:'10%'}, {label:'User', width:'11%'}, {label:'Agent', width:'11%'}, {label:'Content', width:'26%'}
    ], rows);
  } catch (err) { document.getElementById('historyRecords').innerHTML = `<div class="error-line">${esc(err.message)}</div>`; }
}
async function loadTrace(requestId) {
  try {
    const data = await api(`/api/trace?request_id=${encodeURIComponent(requestId)}`);
    document.getElementById('trace').innerHTML = `<div class="trace-grid">${data.steps.map(step => `<div class="trace-step"><div><span class="badge">${esc(step.step)}</span> <code>${esc(step.created_at)}</code></div><div class="subtle line-clamp" title="${esc(step.memory_ids.join(', '))}">${esc(shortText(step.memory_ids.join(', '), 180))}</div><p class="line-clamp" title="${esc(step.prompt)}">${esc(shortText(step.prompt, 220))}</p><p class="line-clamp" title="${esc(step.response)}">${esc(shortText(step.response, 220))}</p></div>`).join('') || '<div class="subtle">No trace steps found.</div>'}</div>`;
    showView('trace');
  } catch (err) { document.getElementById('trace').innerHTML = `<div class="error-line">${esc(err.message)}</div>`; showView('trace'); }
}
function resetStateAndLoad(state, loader) { state.offset = 0; loader(); }
function refreshAll() { loadOverview(); loadUsage(); loadActivity(); loadMemories(); loadHistoryRecords(); }
document.getElementById('refreshButton').addEventListener('click', refreshAll);
document.getElementById('sectionNav').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-view]');
  if (button) showView(button.dataset.view);
});
document.getElementById('bucket').addEventListener('change', loadUsage);
document.getElementById('memoryQuery').addEventListener('input', () => resetStateAndLoad(memoryState, loadMemories));
document.getElementById('layerFilter').addEventListener('change', () => resetStateAndLoad(memoryState, loadMemories));
document.getElementById('historyQuery').addEventListener('input', () => resetStateAndLoad(historyState, loadHistoryRecords));
document.getElementById('historyLayerFilter').addEventListener('change', () => resetStateAndLoad(historyState, loadHistoryRecords));
document.addEventListener('click', (event) => {
  const pageButton = event.target.closest('button[data-page-target]');
  if (pageButton) {
    const target = pageButton.dataset.pageTarget;
    const state = target === 'activity' ? activityState : target === 'memories' ? memoryState : historyState;
    const loader = target === 'activity' ? loadActivity : target === 'memories' ? loadMemories : loadHistoryRecords;
    const dir = pageButton.dataset.pageDir === 'next' ? 1 : -1;
    state.offset = Math.max(0, state.offset + (dir * PAGE_SIZE));
    loader();
    return;
  }
  const contentCellButton = event.target.closest('[data-expandable-content]');
  if (contentCellButton) {
    toggleContentCell(contentCellButton);
    return;
  }
  const traceButton = event.target.closest('button[data-request-id]');
  if (traceButton) {
    loadTrace(traceButton.dataset.requestId);
  }
});
document.addEventListener('keydown', (event) => {
  const contentCellButton = event.target.closest('[data-expandable-content]');
  if (contentCellButton && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    toggleContentCell(contentCellButton);
  }
});
refreshAll();
</script>
</body>
</html>
"""


def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return values[0] if values else default


def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(_first(query, key, str(default)) or default)
    except ValueError:
        return default


def _filters_from_query(query: dict[str, list[str]]) -> DashboardFilters:
    return DashboardFilters(
        user_id=_first(query, "user_id"),
        agent_id=_first(query, "agent_id"),
        layer=_first(query, "layer"),
        query=_first(query, "query") or _first(query, "q"),
        limit=_clamp_limit(_int_query(query, "limit", 100)),
        offset=_clamp_offset(_int_query(query, "offset", 0)),
    )


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "HYMemoryDashboard/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - stdlib signature
        return

    @property
    def paths(self) -> DashboardPaths:
        return self.server.dashboard_paths  # type: ignore[attr-defined]

    def do_POST(self) -> None:
        json_response(self, {"error": "method_not_allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        filters = _filters_from_query(query)
        if parsed.path == "/":
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/overview":
            json_response(self, collect_overview(self.paths, filters))
            return
        if parsed.path == "/api/usage":
            json_response(self, collect_usage(self.paths, _first(query, "bucket", "hour"), filters))
            return
        if parsed.path == "/api/activity":
            json_response(self, collect_activity(self.paths, filters))
            return
        if parsed.path == "/api/memories":
            json_response(self, collect_memories(self.paths, filters))
            return
        if parsed.path == "/api/history-records":
            json_response(self, collect_history_records(self.paths, filters))
            return
        if parsed.path == "/api/trace":
            json_response(self, collect_trace(self.paths, _first(query, "request_id")))
            return
        if parsed.path == "/api/health":
            json_response(self, _health(self.paths))
            return
        json_response(self, {"error": "not_found"}, HTTPStatus.NOT_FOUND)


def validate_dashboard_bind(host: str, port: int) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("dashboard host must be localhost-only: 127.0.0.1 or localhost")
    if int(port) < 0 or int(port) > 65535:
        raise ValueError("dashboard port must be between 0 and 65535")


def build_server(config: HyMemoryConfig, host: str, port: int) -> ThreadingHTTPServer:
    validate_dashboard_bind(host, port)
    server = ThreadingHTTPServer((host, int(port)), DashboardRequestHandler)
    server.dashboard_paths = paths_from_config(config)  # type: ignore[attr-defined]
    return server


def run_dashboard(config: HyMemoryConfig, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    server = build_server(config, host, port)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}"
    print(f"HY Memory dashboard: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
