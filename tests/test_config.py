from __future__ import annotations

import json
from pathlib import Path

from config import (
    DEFAULT_CONFIG,
    get_config_schema,
    load_hy_memory_config,
    save_hy_memory_config,
)


def test_load_config_defaults_to_profile_scoped_paths(tmp_path):
    cfg = load_hy_memory_config(
        hermes_home=tmp_path,
        runtime={"agent_identity": "coder", "session_id": "sess-1"},
    )

    assert cfg.mode == DEFAULT_CONFIG["mode"]
    assert cfg.user_id == "hermes_default"
    assert cfg.agent_id == "coder"
    assert cfg.session_id == "sess-1"
    assert cfg.data_dir == tmp_path / "hy_memory"
    assert cfg.vector_persist_directory == tmp_path / "hy_memory" / "data" / "vector_db"
    assert cfg.cache_db_path == tmp_path / "hy_memory" / "data" / "cache.db"
    assert cfg.history_db_path == tmp_path / "hy_memory" / "data" / "history.db"
    assert cfg.graph_db_path == tmp_path / "hy_memory" / "data" / "kuzu_db"


def test_load_config_merges_json_and_runtime_user_scope(tmp_path):
    (tmp_path / "hy_memory.json").write_text(json.dumps({
        "mode": "ultra",
        "auto_recall": "false",
        "auto_capture": "true",
        "user_id": "json-user",
        "agent_id": "agent-{identity}",
        "top_k": "20",
        "min_score": "0.7",
        "profile_limit": "3",
        "profile_min_score": "0.8",
        "reader": "hybrid",
        "data_dir": "custom-data",
        "vector_store": {"provider": "qdrant", "collection_name": "custom_collection"},
        "llm": {"provider": "openai", "model": "gpt-test", "base_url": "https://llm.example"},
        "embedder": {"provider": "openai", "model": "embed-test", "base_url": "https://emb.example", "embedding_dims": "1024"},
    }), encoding="utf-8")

    cfg = load_hy_memory_config(
        hermes_home=tmp_path,
        runtime={"user_id": "runtime-user", "agent_identity": "coder", "session_id": "sess-2"},
    )

    assert cfg.mode == "ultra"
    assert cfg.auto_recall is False
    assert cfg.auto_capture is True
    assert cfg.user_id == "runtime-user"
    assert cfg.agent_id == "agent-coder"
    assert cfg.top_k == 20
    assert cfg.min_score == 0.7
    assert cfg.profile_limit == 3
    assert cfg.profile_min_score == 0.8
    assert cfg.reader == "hybrid"
    assert cfg.data_dir == tmp_path / "custom-data"
    assert cfg.vector_provider == "qdrant"
    assert cfg.vector_collection_name == "custom_collection"
    assert cfg.llm["model"] == "gpt-test"
    assert cfg.embedder["embedding_dims"] == 1024


def test_save_config_omits_secret_values(tmp_path):
    save_hy_memory_config({
        "mode": "lite",
        "llm_api_key": "secret-llm",
        "embedder_api_key": "secret-embed",
        "top_k": "7",
    }, tmp_path)

    saved = json.loads((tmp_path / "hy_memory.json").read_text(encoding="utf-8"))
    assert saved["mode"] == "lite"
    assert saved["top_k"] == "7"
    assert "llm_api_key" not in saved
    assert "embedder_api_key" not in saved


def test_config_schema_contains_secret_env_fields():
    schema = get_config_schema()
    by_key = {item["key"]: item for item in schema}

    assert by_key["mode"]["choices"] == ["lite", "pro", "ultra"]
    assert by_key["llm_api_key"]["secret"] is True
    assert by_key["llm_api_key"]["env_var"] == "MEMORY_LLM_API_KEY"
    assert by_key["embedder_api_key"]["secret"] is True
    assert by_key["embedder_api_key"]["env_var"] == "MEMORY_EMBEDDER_API_KEY"
