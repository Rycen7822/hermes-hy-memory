"""Configuration helpers for the HY Memory Hermes provider."""

from __future__ import annotations

import json
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
        "provider": "openai",
        "model": "gpt-4.1-nano",
        "base_url": "",
    },
    "embedder": {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "base_url": "",
        "embedding_dims": 1536,
    },
}

_SECRET_KEYS = {"llm_api_key", "embedder_api_key"}


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
    file_config = _read_file_config(home / "hy_memory.json")
    raw = _deep_merge(DEFAULT_CONFIG, file_config)

    identity = str(runtime.get("agent_identity") or "hermes")
    runtime_user = runtime.get("user_id") or runtime.get("user_id_alt")
    user_id = str(runtime_user or raw.get("user_id") or DEFAULT_CONFIG["user_id"])
    agent_id = str(raw.get("agent_id") or DEFAULT_CONFIG["agent_id"]).replace("{identity}", identity)
    session_id = str(runtime.get("session_id") or "")

    data_dir = _expand_path(raw.get("data_dir"), home, "hy_memory")
    vector_store = raw.get("vector_store") if isinstance(raw.get("vector_store"), Mapping) else {}
    llm = dict(raw.get("llm") if isinstance(raw.get("llm"), Mapping) else DEFAULT_CONFIG["llm"])
    embedder = dict(raw.get("embedder") if isinstance(raw.get("embedder"), Mapping) else DEFAULT_CONFIG["embedder"])
    embedder["embedding_dims"] = _as_int(embedder.get("embedding_dims"), 1536, minimum=0)

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
        vector_persist_directory=data_dir / "data" / "vector_db",
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
    existing = _read_file_config(path)
    for key, value in values.items():
        if key in _SECRET_KEYS or value is None:
            continue
        existing[key] = value
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
