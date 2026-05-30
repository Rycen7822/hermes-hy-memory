"""Hermes-hosted LLM adapter for HY Memory."""

from __future__ import annotations

import asyncio
import importlib
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


@dataclass
class HermesLLMResponse:
    content: str
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    finish_reason: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    raw: Any = field(default=None, repr=False)


class HermesHostLLMProvider:
    """HY Memory-compatible LLM provider that routes through Hermes auxiliary LLM calls."""

    def __init__(self, config: Any = None, llm_config: Optional[Mapping[str, Any]] = None):
        self.config = config
        self.llm_config: Dict[str, Any] = dict(llm_config or getattr(config, "llm", {}) or {})
        self.task = str(self.llm_config.get("task") or "hy_memory")
        self.total_calls = 0
        self.total_tokens = 0
        self.errors = 0

    def complete(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        stop: Any = None,
        tools: Optional[list] = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> HermesLLMResponse:
        del stop
        messages = [{"role": "user", "content": str(prompt)}]
        return self.complete_messages(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )

    def complete_messages(
        self,
        messages: list,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list] = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> HermesLLMResponse:
        try:
            self._validate_tool_choice(tool_choice)
            call_kwargs = self._build_call_kwargs(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                **kwargs,
            )
            aux = importlib.import_module("agent.auxiliary_client")
            raw = _run_coro(aux.async_call_llm(**call_kwargs))
            response = self._normalize_response(raw)
            self.total_calls += 1
            self.total_tokens += response.tokens_used
            return response
        except Exception:
            self.errors += 1
            raise

    def _call_llm(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        stop: Any = None,
        tools: Optional[list] = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> HermesLLMResponse:
        return self.complete(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )

    def chat(
        self,
        messages: list,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> HermesLLMResponse:
        return self.complete_messages(messages=messages, max_tokens=max_tokens, temperature=temperature, **kwargs)

    def get_stats(self) -> Dict[str, Any]:
        attempts = self.total_calls + self.errors
        return {
            "total_calls": self.total_calls,
            "total_tokens": self.total_tokens,
            "errors": self.errors,
            "avg_tokens_per_call": self.total_tokens / self.total_calls if self.total_calls else 0,
            "error_rate": self.errors / attempts if attempts else 0,
        }

    def _build_call_kwargs(
        self,
        *,
        messages: list,
        max_tokens: Optional[int],
        temperature: Optional[float],
        tools: Optional[list],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        call_kwargs: Dict[str, Any] = {
            "task": self.task,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.llm_config.get("temperature"),
            "max_tokens": max_tokens if max_tokens is not None else self.llm_config.get("max_tokens"),
            "tools": tools,
            "timeout": kwargs.get("timeout", self.llm_config.get("timeout")),
            "extra_body": dict(self.llm_config.get("extra_body") or {}),
        }
        if isinstance(kwargs.get("extra_body"), Mapping):
            call_kwargs["extra_body"].update(kwargs["extra_body"])
        for key in ("provider", "model", "base_url", "api_key"):
            value = kwargs.get(key, self.llm_config.get(key))
            if value:
                call_kwargs[key] = value
        return call_kwargs

    @staticmethod
    def _validate_tool_choice(tool_choice: Any) -> None:
        if tool_choice in (None, "auto"):
            return
        raise ValueError("Hermes auxiliary LLM routing supports tool_choice=None or 'auto' only")

    def _normalize_response(self, raw: Any) -> HermesLLMResponse:
        if isinstance(raw, str):
            return HermesLLMResponse(content=raw, raw=raw)

        content = _value(raw, "content", "")
        if not content:
            choices = _value(raw, "choices", None)
            if isinstance(choices, list) and choices:
                first = choices[0]
                message = _value(first, "message", {})
                content = _value(message, "content", "") or _value(first, "text", "")

        usage = _value(raw, "usage", {}) or {}
        prompt_tokens = _int_value(usage, "prompt_tokens")
        completion_tokens = _int_value(usage, "completion_tokens")
        total_tokens = _int_value(usage, "total_tokens") or prompt_tokens + completion_tokens
        tool_calls = _value(raw, "tool_calls", None)
        if tool_calls is None:
            choices = _value(raw, "choices", None)
            if isinstance(choices, list) and choices:
                tool_calls = _value(_value(choices[0], "message", {}), "tool_calls", None)

        return HermesLLMResponse(
            content=str(content or ""),
            tokens_used=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=str(_value(raw, "model", "") or ""),
            finish_reason=str(_value(raw, "finish_reason", "") or ""),
            tool_calls=tool_calls if isinstance(tool_calls, list) else None,
            raw=raw,
        )


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _int_value(obj: Any, key: str) -> int:
    try:
        return int(_value(obj, key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _run_coro(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: Dict[str, Any] = {}

    def worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - re-raised in caller.
            result["error"] = exc

    thread = threading.Thread(target=worker, name="hy-memory-hermes-llm", daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")
