"""Lazy HY Memory SDK adapter used by the Hermes provider."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from typing import Any, Dict, List, Optional

try:
    from .config import HyMemoryConfig, redact_config
    from .hy_memory_llm_patch import HyMemoryLLMPatch
    from .hermes_llm import HermesHostLLMProvider, _run_coro
    from .runtime import ManagedHyMemoryWorkerClient, ManagedVenvRuntime
except ImportError:  # Source-local pytest imports modules top-level.
    from config import HyMemoryConfig, redact_config
    from hy_memory_llm_patch import HyMemoryLLMPatch
    from hermes_llm import HermesHostLLMProvider, _run_coro
    from runtime import ManagedHyMemoryWorkerClient, ManagedVenvRuntime


def normalize_search_memories(raw: Any) -> List[Dict[str, Any]]:
    """Flatten HY Memory/OpenClaw grouped search results into a stable list."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []

    results: List[Dict[str, Any]] = []
    nested_memories = raw.get("memories")
    if isinstance(nested_memories, dict):
        results.extend(normalize_search_memories(nested_memories))
    elif isinstance(nested_memories, list):
        results.extend(item for item in nested_memories if isinstance(item, dict))
    preferred = {"profile", "proactive", "normal", "memories"}
    for key in ("profile", "proactive", "normal"):
        value = raw.get(key)
        if isinstance(value, list):
            results.extend(item for item in value if isinstance(item, dict))
    for key, value in raw.items():
        if key not in preferred and isinstance(value, list):
            results.extend(item for item in value if isinstance(item, dict))
    return results


def _effective_embedding_dims(embedder: Dict[str, Any]) -> int:
    dims = int(embedder.get("embedding_dims") or 0)
    model = str(embedder.get("model") or "").lower()
    base_url = str(embedder.get("base_url") or "").lower()
    if "qwen3" in model and "siliconflow" in base_url:
        return 0
    return dims


def build_sdk_config_dict(config: HyMemoryConfig) -> Dict[str, Any]:
    """Build a hy_memory.MemoryConfig-compatible dict from resolved config."""
    embedder = dict(config.embedder)
    embedder["embedding_dims"] = _effective_embedding_dims(embedder)
    llm = dict(config.llm)
    if llm.get("mode") == "hermes":
        llm.pop("api_key", None)
    return {
        "mode": config.mode,
        "enable_graph": config.mode == "ultra",
        "vector_store": {
            "provider": config.vector_provider,
            "collection_name": config.vector_collection_name,
            "persist_directory": str(config.vector_persist_directory),
            "embedding_dims": embedder.get("embedding_dims"),
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
        "llm": llm,
        "embedder": embedder,
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

    def __init__(self, config: HyMemoryConfig, llm_provider_factory: Any = None, runtime_client_factory: Any = None):
        self.config = config
        self._client: Any = None
        self._client_lock = threading.Lock()
        self._last_error = ""
        self._llm_provider_factory = llm_provider_factory
        self._runtime_client_factory = runtime_client_factory
        self._llm_patch: HyMemoryLLMPatch | None = None
        self._llm_patch_status: Dict[str, Any] = {"installed": False, "patched": [], "missing": [], "restored": False}

    @property
    def client_initialized(self) -> bool:
        return self._client is not None

    @property
    def last_error(self) -> str:
        return self._last_error

    def is_ready(self) -> bool:
        if self._uses_managed_runtime():
            runtime_status = ManagedVenvRuntime(self.config).status(check_sdk=False)
            return bool(runtime_status.get("worker_script_exists"))
        return _sdk_available()

    def _uses_managed_runtime(self) -> bool:
        return str(self.config.runtime.get("mode") or "managed_venv") == "managed_venv"

    def get_client(self) -> Any:
        with self._client_lock:
            if self._client is not None:
                return self._client
            try:
                if self._uses_managed_runtime():
                    self._client = self._create_managed_client()
                    self._last_error = ""
                    return self._client
                hy_memory = importlib.import_module("hy_memory")
                self._install_llm_patch_if_needed()
                client_cls = getattr(hy_memory, "HyMemoryClient")
                self._client = client_cls.from_config(build_sdk_config_dict(self.config), mode=self.config.mode)
                self._last_error = ""
                return self._client
            except Exception as exc:
                self._last_error = str(exc)
                raise

    def _install_llm_patch_if_needed(self) -> None:
        if self.config.llm.get("mode") != "hermes":
            return
        if self._llm_patch is None:
            self._llm_patch = HyMemoryLLMPatch(self._make_llm_provider)
        self._llm_patch_status = self._llm_patch.install()

    def _create_managed_client(self) -> Any:
        if self._runtime_client_factory is not None:
            return self._runtime_client_factory(self.config, llm_provider_factory=self._make_llm_provider)
        return ManagedHyMemoryWorkerClient(
            self.config,
            build_sdk_config_dict(self.config),
            llm_provider_factory=self._make_llm_provider,
        )

    def _make_llm_provider(self, *args: Any, **kwargs: Any) -> Any:
        if self._llm_provider_factory is not None:
            return self._llm_provider_factory(*args, **kwargs)
        return HermesHostLLMProvider(config=self.config, llm_config=self.config.llm)

    def _restore_llm_patch(self) -> None:
        if self._llm_patch is not None:
            self._llm_patch_status = self._llm_patch.restore()
            self._llm_patch = None

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

    def status(self, deep: bool = False) -> Dict[str, Any]:
        runtime_status = self._runtime_status(check_sdk=False)
        status = {
            "configured": True,
            "sdk_available": self.is_ready(),
            "client_initialized": self.client_initialized,
            "runtime": runtime_status,
            "mode": self.config.mode,
            "llm_mode": self.config.llm.get("mode", "hermes"),
            "llm_task": self.config.llm.get("task", "hy_memory"),
            "llm_patch_installed": bool(self._llm_patch_status.get("installed")),
            "llm_patch_targets": list(self._llm_patch_status.get("patched", [])),
            "user_id": self.config.user_id,
            "agent_id": self.config.agent_id,
            "data_dir": str(self.config.data_dir),
            "vector_store": {
                "provider": self.config.vector_provider,
                "collection_name": self.config.vector_collection_name,
            },
            "embedder": redact_config(dict(self.config.embedder)),
            "auto_recall": self.config.auto_recall,
            "auto_capture": self.config.auto_capture,
            "last_error": self._last_error,
        }
        if deep:
            status["deep"] = True
            status["runtime"] = self._runtime_status(check_sdk=True)
            status["checks"] = {
                "sdk_import": self._check_sdk_import(),
                "vector_store": self._check_vector_store(),
                "embedder": self._check_embedder(),
                "llm": self._check_llm(),
            }
        return status

    def _runtime_status(self, *, check_sdk: bool) -> Dict[str, Any]:
        if self._uses_managed_runtime():
            if self._client is not None and hasattr(self._client, "status"):
                try:
                    status = dict(self._client.status(check_sdk=check_sdk))
                except TypeError:
                    status = dict(self._client.status())
                status.setdefault("mode", "managed_venv")
                status.setdefault("client", "worker")
                return status
            status = ManagedVenvRuntime(self.config).status(check_sdk=check_sdk)
            status["client"] = "worker"
            status["worker_started"] = False
            status["worker_pid"] = None
            return status
        return {"mode": "in_process", "client": "sdk", "sdk_available": _sdk_available()}

    def _check_sdk_import(self) -> Dict[str, Any]:
        if self._uses_managed_runtime():
            runtime_status = self._runtime_status(check_sdk=True)
            if runtime_status.get("sdk_available"):
                return {"status": "ok", "runtime": "managed_venv"}
            if not runtime_status.get("venv_exists"):
                return {"status": "runtime_not_installed", "runtime": "managed_venv"}
            return {"status": "sdk_missing", "runtime": "managed_venv"}
        return {"status": "ok"} if self.is_ready() else {"status": "sdk_missing"}

    def _check_vector_store(self) -> Dict[str, Any]:
        if self._uses_managed_runtime() and self._client is None:
            return {"status": "skipped", "reason": "managed_runtime_not_started"}
        try:
            client = self.get_client()
            if getattr(client, "_vector_store", None) is not None:
                return {"status": "ok"}
            if hasattr(client, "list_memories"):
                client.list_memories(user_id=self.config.user_id, agent_id=self.config.agent_id, limit=1, offset=0, order="desc")
                return {"status": "ok"}
            return {"status": "skipped"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def _check_embedder(self) -> Dict[str, Any]:
        if self._uses_managed_runtime() and self._client is None:
            if not self.config.embedder.get("api_key"):
                return {"status": "missing_api_key", "env_var": self.config.embedder.get("api_key_env", "MEMORY_EMBEDDER_API_KEY")}
            return {"status": "skipped", "reason": "managed_runtime_not_started"}
        if not self.config.embedder.get("api_key"):
            return {"status": "missing_api_key", "env_var": self.config.embedder.get("api_key_env", "MEMORY_EMBEDDER_API_KEY")}
        try:
            client = self.get_client()
            embed_service = getattr(client, "_embed_service", None)
            if embed_service is None or not hasattr(embed_service, "embed"):
                return {"status": "ok", "dims": self.config.embedder.get("embedding_dims", 0)}
            vector = _run_coro(embed_service.embed("health check"))
            return {"status": "ok", "dims": len(vector) if isinstance(vector, list) else self.config.embedder.get("embedding_dims", 0)}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def _check_llm(self) -> Dict[str, Any]:
        mode = str(self.config.llm.get("mode") or "hermes")
        if mode == "direct" and not self.config.llm.get("api_key"):
            return {"status": "missing_api_key", "mode": "direct", "env_var": self.config.llm.get("api_key_env", "MEMORY_LLM_API_KEY")}
        try:
            if mode == "hermes":
                provider = self._make_llm_provider()
                provider.complete_messages(
                    messages=[{"role": "user", "content": "health check"}],
                    max_tokens=4,
                    temperature=0,
                )
                return {"status": "ok", "mode": "hermes", "task": self.config.llm.get("task", "hy_memory")}
            return {"status": "ok", "mode": "direct"}
        except Exception as exc:
            return {"status": "error", "mode": mode, "message": str(exc)}

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None and hasattr(self._client, "close"):
                self._client.close()
            self._client = None
            self._restore_llm_patch()
