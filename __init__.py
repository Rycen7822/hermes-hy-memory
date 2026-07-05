"""HY Memory provider plugin for Hermes Agent."""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List

from agent.memory_provider import MemoryProvider

try:  # Package import under Hermes plugin loader.
    from .client_adapter import HyMemoryClientAdapter
    from .config import HyMemoryConfig, get_config_schema, load_hy_memory_config, save_hy_memory_config
    from .capture import build_capture_messages, sanitize_memory_context
    from .formatting import format_prefetch_context
    from .tool_handlers import get_tool_schemas as build_tool_schemas
    from .tool_handlers import handle_tool_call as dispatch_tool_call
except ImportError:  # Source-local pytest may import this file as top-level __init__.
    from client_adapter import HyMemoryClientAdapter
    from config import HyMemoryConfig, get_config_schema, load_hy_memory_config, save_hy_memory_config
    from capture import build_capture_messages, sanitize_memory_context
    from formatting import format_prefetch_context
    from tool_handlers import get_tool_schemas as build_tool_schemas
    from tool_handlers import handle_tool_call as dispatch_tool_call


SKILL_NAME = "hy-memory-curation"
SKILL_DESCRIPTION = "Use proactively when complex or iterative work may produce durable HY Memory/Hermes memory: recall, save, verify, clean, or migrate reusable preferences, workflows, debugging lessons, and tool/API quirks without saving noisy task logs."
TOOLSET = "hy_memory"
_PLUGIN_TOOL_SESSION_ID = "hy-memory-plugin-tools"
_tool_provider: "HyMemoryProvider | None" = None
_tool_provider_lock = threading.Lock()


def _skill_path() -> Path:
    return Path(__file__).resolve().parent / "resources" / "skills" / SKILL_NAME / "SKILL.md"


def _register_bundled_skill(ctx: Any) -> None:
    if not hasattr(ctx, "register_skill"):
        return
    path = _skill_path()
    if path.exists():
        ctx.register_skill(SKILL_NAME, path, SKILL_DESCRIPTION)


class HyMemoryProvider(MemoryProvider):
    """Hermes MemoryProvider backed by the hy-memory Python SDK."""

    @property
    def name(self) -> str:
        return "hy_memory"

    def is_available(self) -> bool:
        """Return whether the plugin can run with either managed runtime or in-process SDK."""
        return (Path(__file__).resolve().parent / "hy_memory_worker.py").exists() or importlib.util.find_spec("hy_memory") is not None

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize provider state for a Hermes session."""
        runtime = dict(kwargs)
        runtime["session_id"] = session_id
        hermes_home = runtime.get("hermes_home") or "~/.hermes"
        self._config: HyMemoryConfig = load_hy_memory_config(hermes_home=hermes_home, runtime=runtime)
        self._adapter = HyMemoryClientAdapter(self._config)
        self._session_id = session_id
        self._hermes_home = str(self._config.hermes_home)
        self._write_enabled = str(runtime.get("agent_context") or "primary") not in {"cron", "flush", "subagent"}
        self._prefetch_result = ""
        self._prefetch_generation = 0
        self._prefetch_thread = None
        self._prefetch_lock = threading.Lock()
        self._write_threads: List[threading.Thread] = []
        self._parent_session_id = str(runtime.get("parent_session_id") or "")

    def system_prompt_block(self) -> str:
        return (
            "# HY Memory\n"
            f"Active. User: {self._config.user_id}. Agent: {self._config.agent_id}. Mode: {self._config.mode}.\n"
            "Use hy_memory(action=\"search|add|get|update|delete|list|status\") for explicit memory operations. "
            "Bundled skill: load skill_view(name='hy_memory:hy-memory-curation'); plugin skills are qualified-only and may not appear in skills_list. "
            "For deletion or update, search/list first and use exact structured memory_id; do not fabricate ids. "
            "Raw ids returned by add are storage records for get/delete cleanup, not structured recall ids for update. "
            "If hy_memory(action=\"add\") returns partial_success or searchable=false, treat it as not searchable and verify before relying on recall."
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return build_tool_schemas()

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return get_config_schema()

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        save_hy_memory_config(values, hermes_home)

    def _tool_defaults(self) -> Dict[str, Any]:
        return {
            "user_id": self._config.user_id,
            "agent_id": self._config.agent_id,
            "session_id": self._session_id,
            "top_k": self._config.top_k,
            "min_score": self._config.min_score,
            "profile_limit": self._config.profile_limit,
            "profile_min_score": self._config.profile_min_score,
            "reader": self._config.reader,
        }

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        return dispatch_tool_call(self._adapter, self._tool_defaults(), tool_name, args or {})

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._config.auto_recall or not query:
            return
        with self._prefetch_lock:
            self._prefetch_generation += 1
            generation = self._prefetch_generation

        def worker() -> None:
            try:
                result = self._adapter.search(
                    query,
                    user_ids=[self._config.user_id],
                    agent_ids=[self._config.agent_id] if self._config.agent_id else None,
                    session_ids=[session_id or self._session_id] if (session_id or self._session_id) else None,
                    limit=self._config.top_k,
                    min_score=self._config.min_score,
                    profile_limit=self._config.profile_limit,
                    profile_min_score=self._config.profile_min_score,
                    reader=self._config.reader,
                )
                formatted = format_prefetch_context(result.get("results", []), user_id=self._config.user_id)
            except Exception:
                formatted = ""
            with self._prefetch_lock:
                if generation == self._prefetch_generation:
                    self._prefetch_result = formatted

        thread = threading.Thread(target=worker, name="hy-memory-prefetch", daemon=True)
        self._prefetch_thread = thread
        thread.start()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        thread = self._prefetch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        return result

    def _start_write(self, data: Any, metadata: Dict[str, Any], session_id: str) -> None:
        def worker() -> None:
            try:
                self._adapter.add(
                    data,
                    user_id=self._config.user_id,
                    agent_id=self._config.agent_id,
                    session_id=session_id,
                    metadata=metadata,
                )
            except Exception:
                return

        thread = threading.Thread(target=worker, name="hy-memory-write", daemon=True)
        self._write_threads.append(thread)
        thread.start()

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: List[Dict[str, Any]] | None = None,
    ) -> None:
        if not (self._config.auto_capture and self._write_enabled):
            return
        payload = build_capture_messages(user_content, assistant_content, messages)
        if not payload:
            return
        active_session = session_id or self._session_id
        metadata = {
            "source": "hermes",
            "type": "conversation_turn",
            "session_id": active_session,
        }
        if self._parent_session_id:
            metadata["parent_session_id"] = self._parent_session_id
        self._start_write(payload, metadata, active_session)

    def on_memory_write(self, action: str, target: str, content: str, metadata: Dict[str, Any] | None = None) -> None:
        if not (self._config.auto_capture and self._write_enabled):
            return
        cleaned = sanitize_memory_context(content)
        if action != "add" or not cleaned:
            return
        write_metadata = {
            "source": "hermes_memory_tool",
            "target": target,
            "type": "explicit_memory",
        }
        if metadata:
            write_metadata.update(metadata)
        self._start_write(cleaned, write_metadata, self._session_id)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        self._session_id = new_session_id
        self._parent_session_id = parent_session_id
        if reset:
            with self._prefetch_lock:
                self._prefetch_generation += 1
                self._prefetch_result = ""

    def shutdown(self) -> None:
        thread = self._prefetch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        for write_thread in list(self._write_threads):
            if write_thread.is_alive():
                write_thread.join(timeout=5)
        self._adapter.close()


def _get_tool_provider() -> HyMemoryProvider:
    global _tool_provider
    with _tool_provider_lock:
        if _tool_provider is None:
            _tool_provider = HyMemoryProvider()
            _tool_provider.initialize(_PLUGIN_TOOL_SESSION_ID)
        return _tool_provider


def _provider_available() -> bool:
    return HyMemoryProvider().is_available()


def _make_tool_handler(tool_name: str) -> Callable[..., str]:
    def _handler(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
        provider = _get_tool_provider()
        return provider.handle_tool_call(tool_name, args or {}, **kwargs)

    return _handler


def _register_standalone_tools(ctx: Any) -> None:
    if not hasattr(ctx, "register_tool"):
        return
    provider = HyMemoryProvider()
    for schema in provider.get_tool_schemas():
        name = str(schema.get("name") or "").strip()
        if not name:
            continue
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=_make_tool_handler(name),
            check_fn=_provider_available,
            description=str(schema.get("description") or ""),
            emoji="🧠",
        )


def register(ctx) -> None:
    """Register HY Memory as a Hermes memory provider plugin or standalone skill/tool surface."""
    if hasattr(ctx, "register_memory_provider"):
        ctx.register_memory_provider(HyMemoryProvider())
        return
    _register_standalone_tools(ctx)
    _register_bundled_skill(ctx)
