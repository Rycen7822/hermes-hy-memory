# Hermes HY Memory Provider

Hermes HY Memory Provider is a Hermes Agent plugin that connects the `hy-memory` Python SDK as a Hermes `MemoryProvider`. It exposes automatic recall/capture plus explicit namespaced tools without adding a separate default MCP server.

## What is included

- Hermes provider entrypoint: `__init__.py`
- Profile-scoped config: `$HERMES_HOME/hy_memory.json`
- SDK adapter around `hy_memory.HyMemoryClient`
- Tools: `hy_memory_add`, `hy_memory_search`, `hy_memory_get`, `hy_memory_update`, `hy_memory_delete`, `hy_memory_list`, `hy_memory_status`
- Background prefetch/capture lifecycle hooks
- Developer CLI and optional smoke test

## Install for local development

From this repository:

```bash
bash scripts/install_dev.sh
hermes plugins enable hy_memory
hermes config set memory.provider hy_memory
```

After enabling or changing provider config, restart Hermes Agent or start a new/reset session so the plugin, provider, and tools are loaded.

## Configure

Run Hermes memory setup or edit `$HERMES_HOME/hy_memory.json`:

```bash
hermes memory setup
```

Typical non-secret config:

```json
{
  "mode": "pro",
  "auto_recall": true,
  "auto_capture": true,
  "user_id": "hermes_default",
  "agent_id": "{identity}",
  "top_k": 10,
  "min_score": 0.4,
  "vector_store": {
    "provider": "chroma",
    "collection_name": "hermes_memories"
  }
}
```

Secrets belong in `$HERMES_HOME/.env`, not in `hy_memory.json`. Supported setup fields include `MEMORY_LLM_API_KEY` and `MEMORY_EMBEDDER_API_KEY`.

## Verify

Run the unit suite:

```bash
pytest -q
```

Check provider status without requiring the SDK:

```bash
python cli.py status --hermes-home "$HERMES_HOME"
```

Run the optional real-backend smoke. It exits successfully with `SKIP` when the current Python environment lacks `hy_memory` or required backend configuration:

```bash
python scripts/smoke_hy_memory.py --skip-if-unconfigured --hermes-home "$HERMES_HOME"
```

## Notes

- `reference/` is a local ignored cache of upstream `openclaw-hy-memory` and `hy-memory` artifacts. It is not part of the plugin source.
- The implementation reuses upstream behavior at the interface level: HY Memory SDK calls stay in `client_adapter.py`, OpenClaw-style grouped search results are normalized, and tool safety semantics are preserved with `hy_memory_*` namespacing.
- Automatic capture is disabled for Hermes `agent_context` values `cron`, `flush`, and `subagent` to avoid polluting primary user memory.
