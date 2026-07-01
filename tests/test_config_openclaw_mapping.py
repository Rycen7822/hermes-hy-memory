from __future__ import annotations

import json

from client_adapter import build_sdk_config_dict
from config import load_hy_memory_config, normalize_config_dict, redact_config, save_hy_memory_config


def test_openclaw_aliases_normalize_to_snake_case_without_storing_secrets(tmp_path):
    raw = {
        "autoRecall": False,
        "autoCapture": True,
        "topK": 8,
        "minScore": 0.61,
        "vectorStore": {
            "provider": "chroma",
            "collectionName": "openclaw_memories",
            "persistDirectory": "openclaw-vector",
        },
        "llm": {
            "mode": "hermes",
            "baseUrl": "https://llm.example/v1",
            "apiKey": "secret-value-that-must-not-persist",
        },
        "embedder": {
            "provider": "openai",
            "model": "BAAI/bge-m3",
            "baseUrl": "https://api.siliconflow.cn/v1",
            "dims": 1024,
            "apiKey": "another-secret-value-that-must-not-persist",
        },
    }

    normalized = normalize_config_dict(raw)

    assert normalized["auto_recall"] is False
    assert normalized["auto_capture"] is True
    assert normalized["top_k"] == 8
    assert normalized["min_score"] == 0.61
    assert normalized["vector_store"]["collection_name"] == "openclaw_memories"
    assert normalized["vector_store"]["persist_directory"] == "openclaw-vector"
    assert normalized["llm"]["base_url"] == "https://llm.example/v1"
    assert normalized["embedder"]["base_url"] == "https://api.siliconflow.cn/v1"
    assert normalized["embedder"]["embedding_dims"] == 1024
    persisted_shape = {k: v for k, v in normalized.items() if not k.startswith("_")}
    assert "apiKey" not in json.dumps(persisted_shape)
    assert "api_key" not in json.dumps(persisted_shape)
    assert "secret-value" not in json.dumps(persisted_shape)
    assert sorted(normalized["_secret_warnings"]) == ["embedder.apiKey", "llm.apiKey"]

    save_hy_memory_config(raw, tmp_path)
    saved_text = (tmp_path / "hy_memory.json").read_text(encoding="utf-8")
    assert "apiKey" not in saved_text
    assert "api_key" not in saved_text
    assert "secret-value" not in saved_text


def test_default_llm_mode_is_hermes_and_direct_mode_uses_env_key(tmp_path, monkeypatch):
    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})

    assert cfg.llm["mode"] == "hermes"
    assert cfg.llm["task"] == "hy_memory"
    assert "api_key" not in cfg.llm
    assert cfg.llm.get("api_key_env") == "MEMORY_LLM_API_KEY"

    save_hy_memory_config({"llm": {"mode": "direct", "provider": "openai", "model": "gpt-test", "baseUrl": "https://llm.example/v1"}}, tmp_path)
    direct_without_key = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    assert direct_without_key.llm["mode"] == "direct"
    assert direct_without_key.llm["api_key_env"] == "MEMORY_LLM_API_KEY"
    assert "api_key" not in direct_without_key.llm

    monkeypatch.setenv("MEMORY_LLM_API_KEY", "runtime-secret-key")
    direct_with_key = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    assert direct_with_key.llm["api_key"] == "runtime-secret-key"
    assert redact_config({"llm": direct_with_key.llm})["llm"]["api_key"] == "[REDACTED]"


def test_qwen3_online_embedding_omits_request_dims_but_preserves_collection_dims(tmp_path):
    save_hy_memory_config({
        "embedder": {
            "provider": "openai",
            "model": "Qwen/Qwen3-Embedding-8B",
            "baseUrl": "https://api.siliconflow.cn/v1",
            "dims": 4096,
        }
    }, tmp_path)

    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    assert cfg.embedder["embedding_dims"] == 4096

    sdk_config = build_sdk_config_dict(cfg)
    assert sdk_config["embedder"]["embedding_dims"] == 0
    assert sdk_config["vector_store"]["embedding_dims"] == 4096


def test_bge_m3_siliconflow_embedding_omits_unsupported_dimensions(tmp_path):
    save_hy_memory_config({
        "embedder": {
            "provider": "openai",
            "model": "BAAI/bge-m3",
            "baseUrl": "https://api.siliconflow.cn/v1",
            "dims": 1024,
        }
    }, tmp_path)

    cfg = load_hy_memory_config(tmp_path, {"agent_identity": "coder"})
    assert cfg.embedder["embedding_dims"] == 1024

    sdk_config = build_sdk_config_dict(cfg)
    assert sdk_config["embedder"]["embedding_dims"] == 0
    assert sdk_config["vector_store"]["embedding_dims"] == 1024
