"""Configuration helpers for the HY Memory Hermes provider."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping


DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "pro",
    "auto_recall": True,
    "auto_capture": True,
    "capture_mode": "turn",
    "user_id": "hermes_default",
    "agent_id": "{identity}",
    "top_k": 10,
    "min_score": 0.4,
    "profile_limit": 5,
    "profile_min_score": 0.4,
    "reader": "",
    "data_dir": "",
    "vector_store": {
        "provider": "chroma",
        "collection_name": "hermes_memories",
    },
    "llm": {
        "mode": "hermes",
        "task": "hy_memory",
        "provider": "",
        "model": "",
        "base_url": "",
        "temperature": 0.2,
        "max_tokens": 1024,
        "timeout": 60,
        "max_retries": 3,
        "extra_body": {},
        "api_key_env": "MEMORY_LLM_API_KEY",
    },
    "embedder": {
        "provider": "openai",
        "model": "BAAI/bge-m3",
        "base_url": "https://api.siliconflow.cn/v1",
        "embedding_dims": 1024,
        "timeout": 60,
        "max_retries": 5,
        "retry_delay": 1.0,
        "extra_headers": {},
        "extra_body": {},
        "api_key_env": "MEMORY_EMBEDDER_API_KEY",
    },
}

OPENCLAW_ALIASES: Dict[str, str] = {
    "autoRecall": "auto_recall",
    "autoCapture": "auto_capture",
    "topK": "top_k",
    "minScore": "min_score",
    "profileLimit": "profile_limit",
    "profileMinScore": "profile_min_score",
    "vectorStore": "vector_store",
    "collectionName": "collection_name",
    "persistDirectory": "persist_directory",
    "baseUrl": "base_url",
    "apiKey": "api_key",
    "dims": "embedding_dims",
    "maxTokens": "max_tokens",
    "maxRetries": "max_retries",
    "retryDelay": "retry_delay",
    "extraBody": "extra_body",
    "extraHeaders": "extra_headers",
}

_SECRET_KEYS = {"api_key", "apiKey", "llm_api_key", "embedder_api_key"}


@dataclass(frozen=True)
class HyMemoryConfig:
    hermes_home: Path
    mode: str
    auto_recall: bool
    auto_capture: bool
    capture_mode: str
    user_id: str
    agent_id: str
    session_id: str
    top_k: int
    min_score: float
    profile_limit: int
    profile_min_score: float
    reader: str
    data_dir: Path
    vector_provider: str
    vector_collection_name: str
    vector_persist_directory: Path
    cache_db_path: Path
    history_db_path: Path
    graph_db_path: Path
    llm: Dict[str, Any]
    embedder: Dict[str, Any]


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)  # type: ignore[index]
        else:
            merged[key] = value
    return merged


def normalize_config_dict(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize OpenClaw/camelCase config to plugin snake_case and drop secrets."""
    warnings: list[str] = []

    def walk(value: Any, path: tuple[str, ...]) -> Any:
        if isinstance(value, Mapping):
            normalized: Dict[str, Any] = {}
            for original_key, item in value.items():
                key = str(original_key)
                mapped_key = OPENCLAW_ALIASES.get(key, key)
                if key in _SECRET_KEYS or mapped_key in _SECRET_KEYS:
                    warnings.append(".".join((*path, key)))
                    continue
                normalized[mapped_key] = walk(item, (*path, mapped_key))
            return normalized
        if isinstance(value, list):
            return [walk(item, path) for item in value]
        return value

    normalized = walk(raw, ())
    if not isinstance(normalized, dict):
        return {}
    if warnings:
        normalized["_secret_warnings"] = sorted(set(warnings))
    return normalized


def redact_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a copy with secret-like values redacted for status/docs/tests."""
    secret_names = _SECRET_KEYS | {"authorization", "bearer_token", "access_token", "refresh_token", "token", "secret", "password"}

    def is_secret_key(key: str) -> bool:
        normalized_key = key.lower().replace("-", "_")
        return (
            normalized_key in secret_names
            or normalized_key.endswith("_api_key")
            or normalized_key.endswith("_token")
            or normalized_key.endswith("_secret")
            or normalized_key.endswith("_password")
        )

    def walk(value: Any, key: str = "") -> Any:
        if isinstance(value, Mapping):
            return {str(k): walk(v, str(k)) for k, v in value.items()}
        if is_secret_key(key):
            return "[REDACTED]" if value else value
        return value

    return walk(config)


def _drop_internal_metadata(config: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in config.items() if not str(key).startswith("_")}


def _env_value(name: str) -> str:
    return os.environ.get(name, "").strip()


def _read_file_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _as_float(value: Any, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _expand_path(raw: Any, hermes_home: Path, default_relative: str) -> Path:
    text = str(raw or default_relative).strip() or default_relative
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = hermes_home / path
    return path


def load_hy_memory_config(hermes_home: str | Path, runtime: Mapping[str, Any] | None = None) -> HyMemoryConfig:
    """Load profile-scoped HY Memory configuration.

    Runtime user scope wins over JSON config so gateway users remain isolated.
    """
    home = Path(hermes_home).expanduser().resolve()
    runtime = runtime or {}
    file_config = normalize_config_dict(_read_file_config(home / "hy_memory.json"))
    raw = _deep_merge(DEFAULT_CONFIG, _drop_internal_metadata(file_config))

    identity = str(runtime.get("agent_identity") or "hermes")
    runtime_user = runtime.get("user_id") or runtime.get("user_id_alt")
    user_id = str(runtime_user or raw.get("user_id") or DEFAULT_CONFIG["user_id"])
    agent_id = str(raw.get("agent_id") or DEFAULT_CONFIG["agent_id"]).replace("{identity}", identity)
    session_id = str(runtime.get("session_id") or "")

    data_dir = _expand_path(raw.get("data_dir"), home, "hy_memory")
    vector_store = raw.get("vector_store") if isinstance(raw.get("vector_store"), Mapping) else {}
    llm = dict(raw.get("llm") if isinstance(raw.get("llm"), Mapping) else DEFAULT_CONFIG["llm"])
    llm["mode"] = str(llm.get("mode") or "hermes")
    if llm["mode"] not in {"hermes", "direct"}:
        llm["mode"] = "hermes"
    llm["task"] = str(llm.get("task") or "hy_memory")
    llm["api_key_env"] = str(llm.get("api_key_env") or "MEMORY_LLM_API_KEY")
    llm.pop("api_key", None)
    if llm["mode"] == "direct":
        api_key = _env_value(llm["api_key_env"])
        if api_key:
            llm["api_key"] = api_key

    embedder = dict(raw.get("embedder") if isinstance(raw.get("embedder"), Mapping) else DEFAULT_CONFIG["embedder"])
    embedder["embedding_dims"] = _as_int(embedder.get("embedding_dims"), 1024, minimum=0)
    embedder["api_key_env"] = str(embedder.get("api_key_env") or "MEMORY_EMBEDDER_API_KEY")
    embedder.pop("api_key", None)
    embedder_api_key = _env_value(embedder["api_key_env"])
    if embedder_api_key:
        embedder["api_key"] = embedder_api_key

    mode = str(raw.get("mode") or DEFAULT_CONFIG["mode"])
    if mode not in {"lite", "pro", "ultra"}:
        mode = DEFAULT_CONFIG["mode"]

    return HyMemoryConfig(
        hermes_home=home,
        mode=mode,
        auto_recall=_as_bool(raw.get("auto_recall"), True),
        auto_capture=_as_bool(raw.get("auto_capture"), True),
        capture_mode=str(raw.get("capture_mode") or "turn"),
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        top_k=_as_int(raw.get("top_k"), 10, minimum=1, maximum=50),
        min_score=_as_float(raw.get("min_score"), 0.4, minimum=0.0, maximum=1.0),
        profile_limit=_as_int(raw.get("profile_limit"), 5, minimum=0, maximum=50),
        profile_min_score=_as_float(raw.get("profile_min_score"), 0.4, minimum=0.0, maximum=1.0),
        reader=str(raw.get("reader") or ""),
        data_dir=data_dir,
        vector_provider=str(vector_store.get("provider") or "chroma"),
        vector_collection_name=str(vector_store.get("collection_name") or "hermes_memories"),
        vector_persist_directory=_expand_path(vector_store.get("persist_directory"), home, str(data_dir / "data" / "vector_db")),
        cache_db_path=data_dir / "data" / "cache.db",
        history_db_path=data_dir / "data" / "history.db",
        graph_db_path=data_dir / "data" / "kuzu_db",
        llm=llm,
        embedder=embedder,
    )


def save_hy_memory_config(values: Mapping[str, Any], hermes_home: str | Path) -> None:
    """Persist non-secret setup values to `$HERMES_HOME/hy_memory.json`."""
    home = Path(hermes_home).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    path = home / "hy_memory.json"
    existing = normalize_config_dict(_read_file_config(path))
    normalized_values = normalize_config_dict(values)
    cleaned_values = _drop_internal_metadata(normalized_values)
    cleaned_existing = _drop_internal_metadata(existing)
    merged = _deep_merge(cleaned_existing, cleaned_values)
    path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_config_schema() -> list[dict[str, Any]]:
    return [
        {"key": "mode", "description": "HY Memory mode", "default": "pro", "choices": ["lite", "pro", "ultra"]},
        {"key": "user_id", "description": "Default user id", "default": "hermes_default"},
        {"key": "agent_id", "description": "Default agent id; supports {identity}", "default": "{identity}"},
        {"key": "auto_recall", "description": "Enable automatic recall", "default": "true", "choices": ["true", "false"]},
        {"key": "auto_capture", "description": "Enable automatic turn capture", "default": "true", "choices": ["true", "false"]},
        {"key": "top_k", "description": "Recall result limit", "default": "10"},
        {"key": "min_score", "description": "Normal recall min score", "default": "0.4"},
        {"key": "llm_api_key", "description": "LLM API key", "secret": True, "required": False, "env_var": "MEMORY_LLM_API_KEY"},
        {"key": "embedder_api_key", "description": "Embedder API key", "secret": True, "required": False, "env_var": "MEMORY_EMBEDDER_API_KEY"},
    ]
