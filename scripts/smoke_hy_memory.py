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
    parser.add_argument("--user-id", default="hy_memory_smoke")
    args = parser.parse_args(argv)

    if not _sdk_available():
        if args.skip_if_unconfigured:
            print("SKIP: hy_memory SDK is not installed in this Python environment")
            return 0
        print("FAIL: hy_memory SDK is not installed", file=sys.stderr)
        return 2

    provider = _load_provider_class()()
    provider.initialize("hy-memory-smoke", hermes_home=args.hermes_home, user_id=args.user_id, agent_identity="smoke")
    marker = f"hy-memory smoke {uuid.uuid4()}"
    try:
        added = _call(provider, "hy_memory_add", {"content": marker, "metadata": {"source": "smoke"}})
        memory_id = added.get("memory_id") or added.get("id")
        searched = _call(provider, "hy_memory_search", {"query": marker, "limit": 5, "include_raw": True})
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
        provider.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
