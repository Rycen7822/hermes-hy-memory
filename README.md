# Hermes HY Memory Provider

Hermes HY Memory Provider is a single Hermes Agent memory-provider plugin for the `hy-memory` Python SDK. It provides automatic recall/capture plus explicit `hy_memory_*` tools without requiring a separate default MCP server.

## What is included

- Hermes provider entrypoint: `__init__.py`
- Profile-scoped non-secret config: `$HERMES_HOME/hy_memory.json`
- Hermes-hosted LLM routing adapter: `hermes_llm.py`
- HY Memory SDK LLMProvider injection: `hy_memory_llm_patch.py`
- SDK adapter around `hy_memory.HyMemoryClient`: `client_adapter.py`
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

After enabling the plugin, changing provider config, or changing `$HERMES_HOME/.env`, restart Hermes Agent or start a new/reset session so the memory provider and tools are reloaded.

## Configuration split

| Location | Owner | Contents |
|---|---|---|
| `$HERMES_HOME/hy_memory.json` | This plugin | HY Memory mode, recall/capture settings, vector store, direct embedder settings, and direct LLM settings only when `llm.mode` is `direct` |
| `$HERMES_HOME/config.yaml` | Hermes Agent | Active provider/model and optional `auxiliary.hy_memory` overrides used by the default Hermes-hosted memory LLM |
| `$HERMES_HOME/.env` | Hermes Agent | `MEMORY_EMBEDDER_API_KEY`; `MEMORY_LLM_API_KEY` only when `llm.mode` is `direct` |

Default mode is `llm.mode = "hermes"`. In that mode HY Memory extraction calls route through Hermes Agent's existing model path via `agent.auxiliary_client.async_call_llm(task="hy_memory")`; the plugin does not store or read a separate LLM API key. Configure the model with Hermes itself:

```bash
hermes config set auxiliary.hy_memory.provider auto
hermes config set auxiliary.hy_memory.timeout 60
```

For an explicit auxiliary model endpoint:

```bash
hermes config set auxiliary.hy_memory.provider openrouter
hermes config set auxiliary.hy_memory.model tencent/hy3-preview
hermes config set auxiliary.hy_memory.base_url https://openrouter.ai/api/v1
```

Embeddings are direct OpenAI-compatible API calls because Hermes Agent does not expose a general embedding router. Put the embedder key in `$HERMES_HOME/.env`:

```env
MEMORY_EMBEDDER_API_KEY=<set in operator environment>
```

## Initialize or inspect config

Create a normalized `hy_memory.json`:

```bash
python cli.py init --hermes-home "$HERMES_HOME" --llm-mode hermes --embedder-provider openai --embedder-model BAAI/bge-m3 --embedder-base-url https://api.siliconflow.cn/v1 --embedder-dims 1024 --vector-store chroma --collection-name hermes_memories --non-interactive
```

Show redacted resolved config:

```bash
python cli.py config show --hermes-home "$HERMES_HOME"
```

Set an allowed dotted path:

```bash
python cli.py config set embedder.model BAAI/bge-m3 --hermes-home "$HERMES_HOME"
```

## Typical non-secret config

```json
{
  "mode": "pro",
  "auto_recall": true,
  "auto_capture": true,
  "user_id": "hermes_default",
  "agent_id": "{identity}",
  "top_k": 10,
  "min_score": 0.4,
  "llm": {
    "mode": "hermes",
    "task": "hy_memory",
    "temperature": 0.2,
    "max_tokens": 1024,
    "timeout": 60
  },
  "embedder": {
    "provider": "openai",
    "model": "BAAI/bge-m3",
    "base_url": "https://api.siliconflow.cn/v1",
    "embedding_dims": 1024
  },
  "vector_store": {
    "provider": "chroma",
    "collection_name": "hermes_memories"
  }
}
```

## OpenClaw field mapping

The plugin accepts common OpenClaw-style camelCase config and stores snake_case internally.

| OpenClaw key | Internal key |
|---|---|
| `autoRecall` | `auto_recall` |
| `autoCapture` | `auto_capture` |
| `topK` | `top_k` |
| `minScore` | `min_score` |
| `llm.baseUrl` | `llm.base_url` |
| `embedder.baseUrl` | `embedder.base_url` |
| `embedder.dims` | `embedder.embedding_dims` |
| `vectorStore` | `vector_store` |
| `collectionName` | `collection_name` |
| `persistDirectory` | `persist_directory` |
| `llm.apiKey`, `embedder.apiKey` | env only; never written to `hy_memory.json` |

## Verify

Run the unit suite:

```bash
pytest -q
```

Check local-only provider status without requiring backend calls:

```bash
python cli.py status --hermes-home "$HERMES_HOME"
```

Run explicit deep status checks when the SDK and credentials are configured:

```bash
python cli.py status --deep --hermes-home "$HERMES_HOME"
```

Run the optional real-backend smoke. It exits successfully with `SKIP` when the current Python environment lacks `hy_memory` or required backend configuration:

```bash
python scripts/smoke_hy_memory.py --skip-if-unconfigured --hermes-home "$HERMES_HOME"
python scripts/smoke_hy_memory.py --skip-if-unconfigured --deep --hermes-home "$HERMES_HOME"
```

## Notes

- `reference/` is a local ignored cache of upstream `openclaw-hy-memory` and `hy-memory` artifacts. It is not part of the plugin source.
- The implementation reuses upstream behavior at the interface level: HY Memory SDK calls stay in `client_adapter.py`, OpenClaw-style grouped search results are normalized, and tool safety semantics are preserved with `hy_memory_*` namespacing.
- Automatic capture is disabled for Hermes `agent_context` values `cron`, `flush`, and `subagent` to avoid polluting primary user memory.
