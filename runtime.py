"""Managed HY Memory runtime helpers.

The Hermes plugin process owns Hermes provider/OAuth/model routing. The HY Memory SDK and its heavy vector dependencies can live in a managed Python runtime and communicate with the plugin over a small JSONL protocol. When the worker needs an LLM, it sends a callback request to the parent; the parent calls HermesHostLLMProvider and returns a normalized response.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

try:
    from .config import HyMemoryConfig
except ImportError:  # Source-local pytest imports modules top-level.
    from config import HyMemoryConfig


class JsonlWorkerProcess:
    """Small JSONL subprocess client with parent-side LLM callback handling."""

    def __init__(
        self,
        command: List[str],
        *,
        llm_provider_factory: Optional[Callable[[], Any]] = None,
        cwd: str | Path | None = None,
        env: Optional[Mapping[str, str]] = None,
    ):
        self.command = [str(part) for part in command]
        self.llm_provider_factory = llm_provider_factory
        self.cwd = str(cwd) if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._stderr_lines: List[str] = []

    @property
    def started(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def start(self) -> None:
        if self.started:
            return
        self._process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._collect_stderr, name="hy-memory-worker-stderr", daemon=True).start()

    def request(self, message: Dict[str, Any]) -> Any:
        with self._lock:
            self.start()
            msg = dict(message)
            msg_id = str(msg.get("id") or uuid.uuid4().hex)
            msg["id"] = msg_id
            self._send(msg)
            while True:
                incoming = self._read_message()
                msg_type = incoming.get("type")
                if msg_type == "llm_request":
                    self._handle_llm_request(incoming)
                    continue
                if msg_type == "log":
                    continue
                if msg_type != "response":
                    raise RuntimeError(f"Unexpected worker message type: {msg_type!r}")
                if incoming.get("id") != msg_id:
                    raise RuntimeError(f"Unexpected worker response id: {incoming.get('id')!r}")
                if incoming.get("error"):
                    raise RuntimeError(str(incoming.get("error")))
                return incoming.get("result")

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self.request({"type": "shutdown"})
            except Exception:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        self._process = None

    def _send(self, message: Mapping[str, Any]) -> None:
        process = self._require_process()
        assert process.stdin is not None
        process.stdin.write(json.dumps(message, ensure_ascii=False, default=_json_default) + "\n")
        process.stdin.flush()

    def _read_message(self) -> Dict[str, Any]:
        process = self._require_process()
        assert process.stdout is not None
        line = process.stdout.readline()
        if not line:
            code = process.poll()
            stderr = "\n".join(self._stderr_lines[-20:])
            raise RuntimeError(f"HY Memory worker exited unexpectedly code={code}; stderr={stderr}")
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid HY Memory worker JSON: {line[:500]}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("HY Memory worker returned non-object JSON")
        return data

    def _handle_llm_request(self, message: Mapping[str, Any]) -> None:
        request_id = str(message.get("id") or "")
        payload = message.get("payload") if isinstance(message.get("payload"), Mapping) else {}
        try:
            if self.llm_provider_factory is None:
                raise RuntimeError("No Hermes LLM provider factory configured for worker callback")
            provider = self.llm_provider_factory()
            llm_kwargs = {
                "max_tokens": payload.get("max_tokens"),
                "temperature": payload.get("temperature"),
                "tools": payload.get("tools"),
                "tool_choice": payload.get("tool_choice"),
            }
            if payload.get("stop") is not None:
                llm_kwargs["stop"] = payload.get("stop")
            if payload.get("messages") is not None and hasattr(provider, "complete_messages"):
                response = provider.complete_messages(messages=payload.get("messages"), **llm_kwargs)
            else:
                response = provider.complete(payload.get("prompt", ""), **llm_kwargs)
            self._send({"type": "llm_response", "id": request_id, "result": _response_to_dict(response)})
        except Exception as exc:
            self._send({"type": "llm_response", "id": request_id, "error": str(exc)})

    def _collect_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr_lines.append(line.rstrip())
            del self._stderr_lines[:-100]

    def _require_process(self) -> subprocess.Popen[str]:
        if self._process is None:
            raise RuntimeError("HY Memory worker is not started")
        return self._process


class ManagedVenvRuntime:
    """Create/inspect the profile-scoped HY Memory Python runtime."""

    def __init__(self, config: HyMemoryConfig):
        self.config = config
        self.runtime = dict(config.runtime)
        self.venv_path = Path(self.runtime["venv_path"])
        self.venv_python = self.venv_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.worker_script = Path(self.runtime["worker_script"])
        self.package = str(self.runtime.get("package") or "hy-memory")
        self.auto_install = bool(self.runtime.get("auto_install", True))
        self.bootstrap_python = str(self.runtime.get("python") or sys.executable)

    def command(self) -> List[str]:
        self.ensure()
        return [str(self.venv_python), str(self.worker_script)]

    def ensure(self) -> None:
        uv = shutil.which("uv")
        if self.venv_python.exists():
            if self.check_sdk_available():
                return
            if not self.auto_install:
                raise RuntimeError(f"Managed HY Memory runtime exists but hy_memory is not installed: {self.venv_python}")
            self._install_package(uv)
            return
        if not self.auto_install:
            raise RuntimeError(f"Managed HY Memory runtime is not installed: {self.venv_python}")
        self.venv_path.parent.mkdir(parents=True, exist_ok=True)
        if uv:
            subprocess.run([uv, "venv", "--python", self.bootstrap_python, str(self.venv_path)], check=True)
        else:
            subprocess.run([self.bootstrap_python, "-m", "venv", str(self.venv_path)], check=True)
            subprocess.run([str(self.venv_python), "-m", "pip", "install", "-U", "pip"], check=True)
        self._install_package(uv)

    def _install_package(self, uv: str | None) -> None:
        if uv:
            subprocess.run([uv, "pip", "install", "--python", str(self.venv_python), self.package], check=True)
            return
        subprocess.run([str(self.venv_python), "-m", "pip", "install", self.package], check=True)

    def status(self, *, check_sdk: bool = False) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "mode": "managed_venv",
            "venv_path": str(self.venv_path),
            "python": str(self.venv_python),
            "package": self.package,
            "auto_install": self.auto_install,
            "venv_exists": self.venv_python.exists(),
            "worker_script": str(self.worker_script),
            "worker_script_exists": self.worker_script.exists(),
        }
        if check_sdk:
            status["sdk_available"] = self.check_sdk_available()
        return status

    def check_sdk_available(self) -> bool:
        if not self.venv_python.exists():
            return False
        result = subprocess.run(
            [str(self.venv_python), "-c", "import hy_memory"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        return result.returncode == 0


class ManagedHyMemoryWorkerClient:
    """HY Memory client proxy backed by a managed JSONL worker subprocess."""

    def __init__(
        self,
        config: HyMemoryConfig,
        sdk_config: Mapping[str, Any],
        *,
        llm_provider_factory: Optional[Callable[[], Any]] = None,
        process_factory: Optional[Callable[[List[str], Optional[Callable[[], Any]]], JsonlWorkerProcess]] = None,
    ):
        self.config = config
        self.sdk_config = dict(sdk_config)
        self.llm_provider_factory = llm_provider_factory
        self.runtime = ManagedVenvRuntime(config)
        self._process_factory = process_factory
        self._process: JsonlWorkerProcess | None = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    def add(self, data: Any, **kwargs: Any) -> Dict[str, Any]:
        return self._call("add", data, **kwargs)

    def search(self, query: str, **kwargs: Any) -> Any:
        return self._call("search", query, **kwargs)

    def get(self, memory_id: str) -> Any:
        return self._call("get", memory_id)

    def update(self, memory_id: str, content: str) -> Dict[str, Any]:
        return self._call("update", memory_id, content)

    def delete(self, memory_id: str) -> Dict[str, Any]:
        return self._call("delete", memory_id)

    def delete_all(self, **kwargs: Any) -> Dict[str, Any]:
        return self._call("delete_all", **kwargs)

    def list_memories(self, **kwargs: Any) -> Dict[str, Any]:
        return self._call("list_memories", **kwargs)

    def status(self, *, check_sdk: bool = False) -> Dict[str, Any]:
        status = self.runtime.status(check_sdk=check_sdk)
        status["client"] = "worker"
        status["worker_started"] = self._process is not None and self._process.started
        status["worker_pid"] = self._process.pid if self._process is not None else None
        return status

    def close(self) -> None:
        if self._process is not None:
            self._process.close()
        self._process = None
        self._initialized = False

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        message = {"type": "call", "method": method, "args": list(args), "kwargs": kwargs}
        try:
            process = self._ensure_process()
            return process.request(message)
        except (BrokenPipeError, OSError, RuntimeError) as exc:
            if not self._is_worker_process_failure(exc):
                raise
            self._discard_process()
            if method in {"search", "get", "list_memories"}:
                process = self._ensure_process()
                return process.request(message)
            raise RuntimeError(
                f"HY Memory worker crashed during non-idempotent method '{method}'. "
                "The worker was reset, but the operation was not retried to avoid "
                "duplicating or partially reversing memory state. Retry manually only "
                "after confirming the previous operation did not apply."
            ) from exc

    @staticmethod
    def _is_worker_process_failure(exc: BaseException) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "worker exited unexpectedly",
                "broken pipe",
                "connection reset",
                "worker is not initialized",
                "code=-11",
                "sigsegv",
            )
        )

    def _discard_process(self) -> None:
        if self._process is not None:
            try:
                self._process.close()
            except Exception:
                pass
        self._process = None
        self._initialized = False

    def _ensure_process(self) -> JsonlWorkerProcess:
        if self._process is not None and not self._process.started:
            self._discard_process()
        if self._process is None:
            command = self.runtime.command()
            if self._process_factory is not None:
                self._process = self._process_factory(command, self.llm_provider_factory)
            else:
                self._process = JsonlWorkerProcess(command, llm_provider_factory=self.llm_provider_factory)
        if not self._initialized:
            self._process.request({
                "type": "init",
                "sdk_config": self.sdk_config,
                "runtime_config": dict(self.config.runtime),
                "mode": self.config.mode,
                "llm_mode": self.config.llm.get("mode", "hermes"),
            })
            self._initialized = True
        return self._process


def _response_to_dict(response: Any) -> Dict[str, Any]:
    if isinstance(response, Mapping):
        return dict(response)
    return {
        "content": getattr(response, "content", ""),
        "tokens_used": int(getattr(response, "tokens_used", 0) or 0),
        "prompt_tokens": int(getattr(response, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(response, "completion_tokens", 0) or 0),
        "model": getattr(response, "model", ""),
        "finish_reason": getattr(response, "finish_reason", ""),
        "tool_calls": getattr(response, "tool_calls", None),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return {key: _json_default(item) for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)
