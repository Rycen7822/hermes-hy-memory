"""Lazy HY Memory SDK adapter used by the Hermes provider."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from typing import Any, Dict, List, Optional

try:
    from .config import HyMemoryConfig
except ImportError:  # Source-local pytest imports modules top-level.
    from config import HyMemoryConfig


def normalize_search_memories(raw: Any) -> List[Dict[str, Any]]:
    """Flatten HY Memory/OpenClaw grouped search results into a stable list."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []

    results: List[Dict[str, Any]] = []
    preferred = {"profile", "proactive", "normal"}
    for key in ("profile", "proactive", "normal"):
        value = raw.get(key)
        if isinstance(value, list):
            results.extend(item for item in value if isinstance(item, dict))
    for key, value in raw.items():
        if key not in preferred and isinstance(value, list):
            results.extend(item for item in value if isinstance(item, dict))
    return results


def build_sdk_config_dict(config: HyMemoryConfig) -> Dict[str, Any]:
    """Build a hy_memory.MemoryConfig-compatible dict from resolved config."""
    return {
        "mode": config.mode,
        "enable_graph": config.mode == "ultra",
        "vector_store": {
            "provider": config.vector_provider,
            "collection_name": config.vector_collection_name,
            "persist_directory": str(config.vector_persist_directory),
            "embedding_dims": config.embedder.get("embedding_dims"),
        },
        "graph_store": {
            "provider": "kuzu",
            "db_path": str(config.graph_db_path),
        },
        "cache": {
            "backend": "sqlite",
            "db_path": str(config.cache_db_path),
        },
        "history": {
            "enable": True,
            "db_path": str(config.history_db_path),
            "record_searches": True,
        },
        "llm": dict(config.llm),
        "embedder": dict(config.embedder),
    }


def _sdk_available() -> bool:
    if "hy_memory" in sys.modules:
        return True
    try:
        return importlib.util.find_spec("hy_memory") is not None
    except (ImportError, ValueError):
        return False


class HyMemoryClientAdapter:
    """Thread-safe lazy wrapper around hy_memory.HyMemoryClient."""

    def __init__(self, config: HyMemoryConfig):
        self.config = config
        self._client: Any = None
        self._client_lock = threading.Lock()
        self._last_error = ""

    @property
    def client_initialized(self) -> bool:
        return self._client is not None

    @property
    def last_error(self) -> str:
        return self._last_error

    def is_ready(self) -> bool:
        return _sdk_available()

    def get_client(self) -> Any:
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                hy_memory = importlib.import_module("hy_memory")
                client_cls = getattr(hy_memory, "HyMemoryClient")
                self._client = client_cls.from_config(build_sdk_config_dict(self.config), mode=self.config.mode)
                self._last_error = ""
                return self._client
            except Exception as exc:
                self._last_error = str(exc)
                raise

    def add(
        self,
        data: Any,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        memory_at: Any = None,
    ) -> Dict[str, Any]:
        return self.get_client().add(
            data,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            metadata=metadata,
            memory_at=memory_at,
        )

    def search(
        self,
        query: str,
        *,
        user_ids: List[str],
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
        limit: int = 10,
        min_score: float = 0.4,
        profile_limit: int = 5,
        profile_min_score: float = 0.4,
        reader: str = "",
    ) -> Dict[str, Any]:
        raw = self.get_client().search(
            query,
            user_ids=user_ids,
            agent_ids=agent_ids,
            session_ids=session_ids,
            limit=limit,
            min_score=min_score,
            profile_limit=profile_limit,
            profile_min_score=profile_min_score,
            reader=reader,
        )
        return {"raw": raw, "results": normalize_search_memories(raw)}

    def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return self.get_client().get(memory_id)

    def update(self, memory_id: str, content: str) -> Dict[str, Any]:
        return self.get_client().update(memory_id, content)

    def delete(self, memory_id: str) -> Dict[str, Any]:
        return self.get_client().delete(memory_id)

    def delete_all(
        self,
        *,
        user_id: str,
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self.get_client().delete_all(user_id=user_id, agent_ids=agent_ids, session_ids=session_ids)

    def list_memories(
        self,
        *,
        user_id: str,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc",
    ) -> Dict[str, Any]:
        return self.get_client().list_memories(
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
            order=order,
        )

    def status(self) -> Dict[str, Any]:
        return {
            "configured": True,
            "sdk_available": self.is_ready(),
            "client_initialized": self.client_initialized,
            "mode": self.config.mode,
            "user_id": self.config.user_id,
            "agent_id": self.config.agent_id,
            "data_dir": str(self.config.data_dir),
            "vector_store": self.config.vector_provider,
            "auto_recall": self.config.auto_recall,
            "auto_capture": self.config.auto_capture,
            "last_error": self._last_error,
        }

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None and hasattr(self._client, "close"):
                self._client.close()
            self._client = None
