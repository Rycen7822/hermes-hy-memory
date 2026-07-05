#!/usr/bin/env python3
"""HY Memory managed worker for the Hermes plugin.

This script runs inside the isolated HY Memory Python runtime. It owns `hy_memory`, Chroma, Kuzu, and other heavy SDK dependencies. It intentionally does not import Hermes Agent. LLM calls are bridged back to the parent plugin over the same JSONL stdin/stdout channel.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from hy_memory_llm_patch import HyMemoryLLMPatch
except ImportError as exc:  # pragma: no cover - startup failure reported to parent.
    HyMemoryLLMPatch = None  # type: ignore[assignment]
    _PATCH_IMPORT_ERROR = exc
else:
    _PATCH_IMPORT_ERROR = None

_client: Any = None
_llm_patch: Any = None


def send(message: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, default=json_default) + "\n")
    sys.stdout.flush()


def read_message() -> Dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise EOFError("parent closed stdin")
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


class ParentBridgeLLMProvider:
    """HY Memory-compatible async LLM provider that asks the Hermes parent process."""

    def __init__(self, *args: Any, **kwargs: Any):
        self.total_calls = 0
        self.total_tokens = 0
        self.errors = 0

    async def complete(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        stop: Any = None,
        tools: Any = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Any:
        payload = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stop": stop,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        payload.update({key: value for key, value in kwargs.items() if key in {"messages", "extra_body"}})
        return await self._complete_payload(payload)

    async def complete_messages(
        self,
        messages: list,
        max_tokens: int = 500,
        temperature: float = 0.7,
        stop: Any = None,
        tools: Any = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Any:
        prompt = "\n".join(str(item.get("content", "")) for item in messages if isinstance(item, dict))
        payload = {
            "messages": messages,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stop": stop,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        payload.update({key: value for key, value in kwargs.items() if key in {"extra_body"}})
        return await self._complete_payload(payload)

    async def _complete_payload(self, payload: Dict[str, Any]) -> Any:
        try:
            response = await asyncio.to_thread(request_parent_llm, payload)
            self.total_calls += 1
            self.total_tokens += int(response.get("tokens_used", 0) or 0)
            return SimpleNamespace(
                content=response.get("content", ""),
                tokens_used=int(response.get("tokens_used", 0) or 0),
                prompt_tokens=int(response.get("prompt_tokens", 0) or 0),
                completion_tokens=int(response.get("completion_tokens", 0) or 0),
                model=response.get("model", ""),
                finish_reason=response.get("finish_reason", ""),
                tool_calls=response.get("tool_calls"),
            )
        except Exception:
            self.errors += 1
            raise

    async def _call_llm(self, prompt: str, **kwargs: Any) -> Any:
        return await self.complete(prompt, **kwargs)

    async def chat(self, messages: list, max_tokens: int = 500, temperature: float = 0.7, **kwargs: Any) -> Any:
        return await self.complete_messages(messages, max_tokens=max_tokens, temperature=temperature, **kwargs)

    def get_stats(self) -> Dict[str, Any]:
        attempts = self.total_calls + self.errors
        return {
            "total_calls": self.total_calls,
            "total_tokens": self.total_tokens,
            "errors": self.errors,
            "avg_tokens_per_call": self.total_tokens / self.total_calls if self.total_calls else 0,
            "error_rate": self.errors / attempts if attempts else 0,
        }


def request_parent_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
    request_id = uuid.uuid4().hex
    send({"type": "llm_request", "id": request_id, "payload": payload})
    while True:
        message = read_message()
        if message.get("type") != "llm_response" or message.get("id") != request_id:
            raise RuntimeError(f"unexpected parent message while waiting for LLM response: {message.get('type')}")
        if message.get("error"):
            raise RuntimeError(str(message.get("error")))
        result = message.get("result") or {}
        return result if isinstance(result, dict) else {"content": str(result)}


def _as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_memory_at(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            try:
                parsed = datetime.fromtimestamp(float(text), tz=timezone.utc)
            except ValueError:
                raise ValueError("memory_at must be an ISO timestamp or Unix timestamp") from exc
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    raise ValueError("memory_at must be an ISO timestamp, Unix timestamp, datetime, or null")


def _configure_chroma_vdb_pool(sdk_config: Mapping[str, Any], runtime_config: Mapping[str, Any]) -> Dict[str, Any]:
    """Limit HY Memory's Chroma thread pool before the SDK client is built.

    The upstream hy_memory Chroma store creates a module-level 64-thread executor.
    In this WSL profile that correlated with repeated native SIGSEGVs in
    chromadb_rust_bindings. Patch the owning module at worker startup instead of
    editing the managed venv's site-packages, so reinstalls keep the mitigation.
    """
    vector_store = sdk_config.get("vector_store") if isinstance(sdk_config.get("vector_store"), Mapping) else {}
    provider = str(vector_store.get("provider") or "chroma").lower()
    if provider != "chroma":
        return {"status": "skipped", "reason": f"vector_provider={provider}"}

    pool_size = _as_int(runtime_config.get("vdb_pool_size"), 4, minimum=1, maximum=16)
    try:
        import concurrent.futures
        import hy_memory.data.vector_store_chroma as chroma_store

        previous_pool_size = getattr(chroma_store, "_VDB_POOL_SIZE", None)
        old_executor = getattr(chroma_store, "_vdb_executor", None)
        if int(previous_pool_size or 0) == pool_size and getattr(old_executor, "_max_workers", None) == pool_size:
            return {"status": "ok", "pool_size": pool_size, "previous_pool_size": previous_pool_size, "unchanged": True}
        if old_executor is not None and hasattr(old_executor, "shutdown"):
            old_executor.shutdown(wait=False, cancel_futures=True)
        chroma_store._VDB_POOL_SIZE = pool_size
        chroma_store._vdb_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=pool_size,
            thread_name_prefix="vdb",
        )
        return {"status": "ok", "pool_size": pool_size, "previous_pool_size": previous_pool_size}
    except Exception as exc:
        return {"status": "error", "pool_size": pool_size, "message": str(exc)}


def handle_init(message: Mapping[str, Any]) -> Dict[str, Any]:
    global _client, _llm_patch
    import hy_memory

    sdk_config = message.get("sdk_config") if isinstance(message.get("sdk_config"), Mapping) else {}
    runtime_config = message.get("runtime_config") if isinstance(message.get("runtime_config"), Mapping) else {}
    llm_mode = str(message.get("llm_mode") or sdk_config.get("llm", {}).get("mode") or "hermes")
    if llm_mode == "hermes":
        if HyMemoryLLMPatch is None:
            raise RuntimeError(f"failed to import HyMemoryLLMPatch: {_PATCH_IMPORT_ERROR}")
        _llm_patch = HyMemoryLLMPatch(lambda *args, **kwargs: ParentBridgeLLMProvider(*args, **kwargs))
        patch_status = _llm_patch.install()
    else:
        patch_status = {"installed": False, "patched": [], "missing": [], "restored": False}

    chroma_vdb_pool = _configure_chroma_vdb_pool(sdk_config, runtime_config)
    client_cls = getattr(hy_memory, "HyMemoryClient")
    _client = client_cls.from_config(dict(sdk_config), mode=message.get("mode"))
    return {"initialized": True, "pid": os.getpid(), "llm_patch": patch_status, "chroma_vdb_pool": chroma_vdb_pool}


def handle_call(message: Mapping[str, Any]) -> Any:
    if _client is None:
        raise RuntimeError("HY Memory worker is not initialized")
    method = str(message.get("method") or "")
    if method not in {"add", "search", "get", "update", "delete", "delete_all", "list_memories"}:
        raise RuntimeError(f"unsupported HY Memory worker method: {method}")
    raw_args = message.get("args")
    args: list[Any] = raw_args if isinstance(raw_args, list) else []
    raw_kwargs = message.get("kwargs")
    kwargs: Dict[str, Any] = dict(raw_kwargs) if isinstance(raw_kwargs, Mapping) else {}
    if method == "add" and "memory_at" in kwargs:
        kwargs["memory_at"] = _normalize_memory_at(kwargs.get("memory_at"))
    return getattr(_client, method)(*args, **kwargs)


def handle_status() -> Dict[str, Any]:
    return {"worker": "ok", "pid": os.getpid(), "client_initialized": _client is not None}


def close_client() -> None:
    global _client, _llm_patch
    if _client is not None and hasattr(_client, "close"):
        _client.close()
    _client = None
    if _llm_patch is not None:
        _llm_patch.restore()
    _llm_patch = None


def main() -> int:
    while True:
        try:
            message = read_message()
            msg_id = message.get("id")
            msg_type = message.get("type")
            if msg_type == "init":
                result = handle_init(message)
            elif msg_type == "call":
                result = handle_call(message)
            elif msg_type == "status":
                result = handle_status()
            elif msg_type == "shutdown":
                close_client()
                send({"type": "response", "id": msg_id, "result": {"closed": True}})
                return 0
            else:
                raise RuntimeError(f"unsupported HY Memory worker message type: {msg_type}")
            send({"type": "response", "id": msg_id, "result": result})
        except EOFError:
            return 0
        except Exception as exc:
            send({"type": "response", "id": locals().get("msg_id", ""), "error": str(exc)})


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return {key: json_default(item) for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
