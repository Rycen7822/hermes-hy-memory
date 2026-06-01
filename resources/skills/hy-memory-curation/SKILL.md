---
name: hy-memory-curation
description: "Use proactively when complex or iterative work may produce durable HY Memory/Hermes memory: recall, save, verify, clean, or migrate reusable preferences, workflows, debugging lessons, and tool/API quirks without saving noisy task logs."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hy-memory, memory, recall, curation, agent-memory, skills]
    related_skills: [hermes-agent, hermes-agent-skill-authoring]
---

# HY Memory Curation

## Overview

Use this plugin-bundled skill as the lightweight policy layer for HY Memory work in Hermes Agent. It adapts the proactive memory-curation guidance from the EverOS-Hermes plugin to HY Memory's local/managed-runtime behavior: search before asking the user to repeat durable context, save stable facts and reusable lessons without waiting to be prompted, verify visibility when it matters, and keep noisy task logs out of long-term memory.

HY Memory has two user-facing plugin surfaces:

- `memory.provider: hy_memory` enables automatic recall/capture hooks.
- `plugins.enabled: [hy_memory]` exposes explicit `hy_memory_*` tools and this `hy_memory:hy-memory-curation` skill.

HY Memory extraction is selective. `hy_memory_add` may store every accepted item as `l1_raw`, while `hy_memory_search` and `hy_memory_list` mainly return structured layers such as profile, identity, proactive, or normal memories. Durable-looking preference, identity, project convention, and reusable workflow facts are more likely to become searchable than text that says it is temporary, a test, or should be deleted.

This skill is plugin-bundled and qualified-only. Load it as `hy_memory:hy-memory-curation`; it may not appear in ordinary `skills_list` or `hermes skills list` output because plugin skills are resolved through the plugin namespace.

## When to Use

Load this skill before:

- Searching HY Memory for cross-session context, user preferences, project conventions, or reusable implementation lessons.
- Saving information after complex or iterative tasks, debugging sessions, plugin/config migrations, real smoke tests, or reusable workflow discoveries.
- Deciding whether a completed task belongs in HY Memory, a Hermes skill, a local note, the session transcript, or nowhere.
- Verifying that a saved HY Memory item is searchable, listing structured memories, updating exact ids, or deleting test/candidate records.
- Troubleshooting why `hy_memory_add` succeeded but `hy_memory_search` or `hy_memory_list` returned no results.

Do not use this for simple one-turn answers where no recall, write, verification, cleanup, or routing decision is needed.

## Post-task Proactive Curation

Do not wait for the user to say "remember this" after complex or iterative work. Before the final response, decide whether the run produced one of these durable outcomes:

1. A user preference or stable personal/project fact that should be saved with `hy_memory_add`.
2. A reusable procedure, debugging pattern, or tool-specific workflow that belongs in a Hermes skill rather than memory.
3. A local project note or report that should be updated because the user requested persistent task documentation.
4. A transient task log, PR number, commit SHA, one-off test output, temporary id, or implementation milestone that should not be saved.

When saving to HY Memory, write compact declarative facts. Prefer "The user prefers concise final status reports with gate results" over imperative instructions like "Always write concise status reports". Procedures belong in skills; facts and preferences belong in memory.

## HY Memory Tool Routing

Use the explicit tools this way:

| Intent | Preferred tool pattern |
| --- | --- |
| Recall relevant durable context | `hy_memory_search(query=..., limit=..., include_raw=false)`; use `include_raw=true` for debugging visibility. |
| Save stable fact/preference | `hy_memory_add(content=..., metadata={"source": ..., "reason": ...})`, then inspect `success`, `partial_success`, `searchable`, `structured_memory_ids`, and search if visibility matters. |
| Verify a known id | `hy_memory_get(memory_id=...)`. |
| Update/correct an exact item | Search/list first and update structured ids returned by recall; raw ids returned by add are storage records for get/delete cleanup and must not be used with `hy_memory_update`. |
| Delete test/candidate data | Search/list/get exact ids first, then `hy_memory_delete(memory_id=...)` or scoped `all=true, confirm=true` only for isolated test scopes. |
| Diagnose provider state | `hy_memory_status(deep=true)`; confirm managed runtime, vector store, embedder dims, and Hermes LLM mode. |

For real search smoke tests, do not put "temporary", "delete", or "test only" in the memory content. Put cleanup intent in metadata and use durable-looking content such as "The user prefers amber smoke-test banners when verifying HY Memory search" so the extractor can produce a structured searchable memory. Always delete the isolated test scope afterwards.

## What to Save

Save these proactively when they will still matter later:

- User preferences, corrections, communication style, stable workflows, and recurring project conventions.
- Environment facts that are expensive to rediscover and unlikely to become stale quickly, such as a stable plugin path or managed runtime location.
- Tool/API quirks discovered through real debugging, especially when they prevent future false diagnoses.
- Reusable smoke-test or verification patterns after they have been proven by real tool output.

Do not save:

- Secrets, API keys, OAuth tokens, raw credentials, or unredacted environment files.
- Raw transcripts, noisy task logs, temporary TODO state, commit SHAs, PR numbers, issue numbers, one-off IDs, or "phase done" progress notes.
- Data likely to become stale within a week unless the user explicitly asks for a short-lived reminder.
- Memory-context blocks injected into the prompt; treat them as recalled context, not new facts to re-save.

## Search and Visibility Rules

Treat `hy_memory_add` success as acceptance, not proof that the item is searchable. If visibility matters, run a search query that asks for the meaning of the fact, not only a random marker string. A partial backend/LLM failure returns `success=false` with `partial_success=true`, keeps `memory_id/raw_memory_id` for cleanup, and sets `searchable=false`; do not report that as a successful searchable memory.

Successful string adds may include `structured_memory_ids` and `structured_count`. Use those structured ids, or ids returned by `hy_memory_search`/`hy_memory_list`, when correcting recall with `hy_memory_update`. The raw id returned by add is safe for `hy_memory_get` and cleanup, but it is not the id to update when search/list expose a separate structured memory.

If `get` succeeds but `search/list` returns empty, inspect the layer. An `l1_raw` record may be intentionally excluded from structured search/list results until HY Memory extracts a durable layer. Rewrite future smoke content as a stable preference/identity fact instead of treating this as a storage failure.

Use `include_raw=true` only for debugging or verification output. Keep final summaries compact and do not paste raw backend payloads unless the user asks for them.

## Cleanup and Safety

Before destructive operations, collect exact ids or use an intentionally isolated `user_id`/`agent_id`/`session_id` scope. Never bulk-delete the user's default scope as a shortcut.

After tests, verify cleanup with `hy_memory_list` or `hy_memory_search` on the same isolated scope. If a structured memory was created from a raw item, delete both the raw id and the structured id or use scoped deletion for the test-only scope.

When keys or provider settings are involved, store secrets only in `$HERMES_HOME/.env` and keep plugin config values redacted in reports. Do not write credentials to skills, notes, tests, README examples, or committed fixtures.

## Common Pitfalls

1. Assuming `hy_memory_add` plus `memory_id` means search will immediately find the content. It may only be `l1_raw`; verify with semantically phrased search or get/list diagnostics.
2. Writing smoke content that says it is temporary or should be deleted. HY Memory may correctly avoid promoting it to durable search layers.
3. Saving procedures as memory. Promote reusable multi-step workflows to Hermes skills; save only compact facts about stable preferences or environment conventions.
4. Saving stale task artifacts such as SHAs, issue numbers, transient failures, or milestone logs. Use session transcripts or local notes for task history.
5. Deleting broadly without confirming scope. Use exact ids or isolated test scopes, and verify after deletion.
6. Expecting a just-added bundled plugin skill to appear in the current session. Plugin skill catalogs are loaded at session start; use `/reset` or a new Hermes process after installation or code changes.
7. Misreading stale worker state after out-of-band bulk writes as failed persistence. Normal `hy_memory_add` calls through the current provider should not require a refresh, but a separate migration/provider process can update the same Chroma-backed store while the current Hermes worker still holds an old client view; verify with a fresh provider or `/reset` before concluding that imported data is missing.

## Local Dashboard Inspection

Use the local dashboard when the user asks to visually inspect HY Memory save/recall activity, memory contents, or current memory counts. The dashboard is a browser view over existing local HY Memory data files; it is not a memory management console.

Start it from the installed plugin path, not from the development repository:

```bash
PLUGIN_CLI="${HERMES_HOME:-$HOME/.hermes}/plugins/hy_memory/cli.py"
python "$PLUGIN_CLI" dashboard --hermes-home "${HERMES_HOME:-$HOME/.hermes}" --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` in a browser. The page uses a sticky top navigation to switch between Overview, Usage, Recent Activity, Current Structured Memory Records, Raw / History Memory Records, and Trace; do not expect every long table to be visible at once. The Overview page uses a command-surface overview layout with a large structured-record status surface, database health rail, telemetry matrix, layer distribution, and event composition panels. Activity, memory records, and raw/history records are paginated at 25 rows per page, with long table cells line-clamped/truncated for scanability. Recent Activity KIND and Raw / History EVENT use type-colored KIND/EVENT badges so ADD, SEARCH, UPDATE, DELETE, and recall pipeline rows are visually distinct. On Current Structured Memory Records and Raw / History Memory Records, click a Content cell to expand that specific cell to its full content, then click it again to collapse back to the truncated preview. The page shows:

- Current overview: database health, history event count, current memory record count, pipeline log count, latest save/search timestamps, `History raw L1` from the history table, and `Current L3 records` from active vector metadata.
- Memory usage: ADD, SEARCH, UPDATE, DELETE, and recall pipeline counts grouped by hour/day/month.
- Recent activity: history rows, memory operations, and pipeline steps in reverse chronological order.
- Memory records: current structured memory ids, layers, users, agents, and content snippets with local filtering. The layer filter is intentionally scoped to current structured records from local active vector metadata in `vector_db/chroma.sqlite3`; shadowed UPDATE predecessors and raw `l1_raw` nodes are not current records.
- Raw / History Memory Records: history rows from `history.db.memory_history` with independent query and layer filters. Use this view to inspect raw `l1_raw` rows and historical `l3_*` rows without mixing them into current structured records.
- Trace details: pipeline steps for a selected request id.

Data source map:

- `history.db.memory_history` supplies ADD/SEARCH/UPDATE/DELETE history, save/recall timestamps, and Raw / History Memory Records rows including `l1_raw` and historical `l3_*` layers.
- `vector_db/chroma.sqlite3` supplies local active vector metadata for Current Structured Memory Records. This active-state view excludes shadowed UPDATE predecessors and raw `l1_raw` nodes.
- `cache.db.memory_operations` supplies the operation/audit log for save-side ADD/UPDATE/SUPERSEDE activity in Recent Activity; it is not current structured memory state.
- `cache.db.pipeline_logs` supplies recall pipeline steps, request ids, result ids, prompts/responses in truncated form, and elapsed time.
- `cache.db.system_metrics` supplies local runtime metric snapshots.

Safety rules:

- The dashboard is read-only and localhost-only.
- It uses GET-only local API endpoints and never exposes add/update/delete/import/export/forget actions.
- It does not start HY Memory runtime installation or managed worker setup; it only reads existing SQLite files with read-only connections.
- Do not paste full prompt/response payloads into reports. The dashboard truncates displayed prompt, response, and content text by default.

## Verification Checklist

- [ ] Searched existing HY Memory or session history before asking the user to repeat durable context.
- [ ] Chose the right destination: HY Memory for facts/preferences, skills for procedures, notes for requested task records, no save for noise.
- [ ] Redacted secrets and avoided saving transient task progress.
- [ ] Used declarative, compact memory text rather than imperative self-instructions.
- [ ] Verified search/list/get visibility when the saved item must be retrievable.
- [ ] Cleaned isolated smoke-test memories and verified the scope is empty.
- [ ] Told the user when `/reset` or a restart is required for newly registered plugin skills/tools.
