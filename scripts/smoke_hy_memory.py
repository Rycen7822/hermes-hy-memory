#!/usr/bin/env python3
"""Optional end-to-end smoke test for the HY Memory Hermes provider."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import uuid
from pathlib import Path


def _ensure_hermes_agent_on_path() -> None:
    try:
        import agent.memory_provider  # noqa: F401
        return
    except Exception:
        pass
    candidate = Path.home() / ".hermes" / "hermes-agent"
    if candidate.exists():
        sys.path.insert(0, str(candidate))


def _sdk_available() -> bool:
    try:
        return importlib.util.find_spec("hy_memory") is not None
    except Exception:
        return False


def _managed_runtime_sdk_available(hermes_home: str) -> bool:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from config import load_hy_memory_config
        from runtime import ManagedVenvRuntime

        cfg = load_hy_memory_config(hermes_home, {"agent_identity": "smoke", "session_id": "hy-memory-smoke"})
        if cfg.runtime.get("mode") != "managed_venv":
            return False
        return ManagedVenvRuntime(cfg).check_sdk_available()
    except Exception:
        return False


def _load_provider_class():
    _ensure_hermes_agent_on_path()
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "hy_memory_provider_smoke",
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load provider module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HyMemoryProvider


def _call(provider, name: str, args: dict) -> dict:
    raw = provider.handle_tool_call(name, args)
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"])
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run HY Memory provider add/search/get/delete smoke test")
    parser.add_argument("--hermes-home", default=str(Path.home() / ".hermes"))
    parser.add_argument("--skip-if-unconfigured", action="store_true", help="Exit 0 when hy_memory SDK is not importable")
    parser.add_argument("--deep", action="store_true", help="Run deep provider status before the add/search/get/delete smoke")
    parser.add_argument("--user-id", default="hy_memory_smoke")
    args = parser.parse_args(argv)

    if not _sdk_available() and not _managed_runtime_sdk_available(args.hermes_home):
        if args.skip_if_unconfigured:
            print("SKIP: hy_memory SDK/runtime is not installed in this environment")
            return 0
        print("FAIL: hy_memory SDK/runtime is not installed", file=sys.stderr)
        return 2

    provider = _load_provider_class()()
    provider.initialize("hy-memory-smoke", hermes_home=args.hermes_home, user_id=args.user_id, agent_identity="smoke")
    marker = f"hy-memory smoke {uuid.uuid4()}"
    content = f"The user prefers amber smoke-test banners when verifying HY Memory search. Tracking marker: {marker}."
    try:
        if args.deep:
            status = _call(provider, "hy_memory_status", {"deep": True})
            print(json.dumps({"status": status.get("checks", {})}, ensure_ascii=False))
        added = _call(provider, "hy_memory_add", {"content": content, "metadata": {"source": "smoke", "marker": marker}})
        if added.get("partial_success") or added.get("success") is False:
            memory_id = added.get("memory_id") or added.get("raw_memory_id") or added.get("id")
            raise RuntimeError(f"smoke add was not searchable success: {json.dumps(added, ensure_ascii=False)}")
        memory_id = added.get("memory_id") or added.get("id")
        searched = _call(provider, "hy_memory_search", {
            "query": "What banner color does the user prefer when verifying HY Memory search?",
            "limit": 5,
            "min_score": 0,
            "profile_min_score": 0,
            "include_raw": True,
        })
        if not searched.get("count"):
            raise RuntimeError("smoke search returned no results")
        if not memory_id:
            memory_id = searched["results"][0].get("id")
        if memory_id:
            _call(provider, "hy_memory_get", {"memory_id": memory_id})
            _call(provider, "hy_memory_delete", {"memory_id": memory_id})
        print("PASS: HY Memory add/search/get/delete smoke completed")
        return 0
    except Exception as exc:
        if args.skip_if_unconfigured:
            print(f"SKIP: HY Memory smoke could not run in this environment: {exc}")
            return 0
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            _call(provider, "hy_memory_delete", {"all": True, "confirm": True, "user_id": args.user_id, "agent_id": "smoke", "session_id": "hy-memory-smoke"})
        except Exception:
            pass
        provider.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
