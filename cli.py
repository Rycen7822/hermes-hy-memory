#!/usr/bin/env python3
"""Small developer CLI for the HY Memory Hermes provider."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict


def _ensure_hermes_agent_on_path() -> None:
    try:
        import agent.memory_provider  # noqa: F401
        return
    except Exception:
        pass
    candidate = Path.home() / ".hermes" / "hermes-agent"
    if candidate.exists():
        sys.path.insert(0, str(candidate))


def _load_provider_class():
    _ensure_hermes_agent_on_path()
    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "hy_memory_provider_cli",
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load provider module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HyMemoryProvider


def _provider(args):
    provider = _load_provider_class()()
    provider.initialize(
        args.session_id,
        hermes_home=args.hermes_home,
        agent_identity=args.agent_identity,
        user_id=args.user_id,
    )
    return provider


def _print_json(text: str) -> int:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(text)
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 1 if isinstance(data, dict) and data.get("error") else 0


def cmd_status(args) -> int:
    provider = _provider(args)
    try:
        return _print_json(provider.handle_tool_call("hy_memory_status", {}))
    finally:
        provider.shutdown()


def cmd_search(args) -> int:
    provider = _provider(args)
    try:
        return _print_json(provider.handle_tool_call("hy_memory_search", {"query": args.query, "limit": args.limit}))
    finally:
        provider.shutdown()


def cmd_add(args) -> int:
    provider = _provider(args)
    try:
        return _print_json(provider.handle_tool_call("hy_memory_add", {"content": args.content, "metadata": {"source": "hy_memory_cli"}}))
    finally:
        provider.shutdown()


def cmd_list(args) -> int:
    provider = _provider(args)
    try:
        return _print_json(provider.handle_tool_call("hy_memory_list", {"limit": args.limit, "offset": args.offset}))
    finally:
        provider.shutdown()


def cmd_delete(args) -> int:
    provider = _provider(args)
    try:
        return _print_json(provider.handle_tool_call("hy_memory_delete", {"memory_id": args.memory_id}))
    finally:
        provider.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HY Memory Hermes provider developer CLI")
    parser.add_argument("--hermes-home", default=str(Path.home() / ".hermes"), help="Hermes profile home")
    parser.add_argument("--session-id", default="hy-memory-cli", help="Session id for scoped operations")
    parser.add_argument("--agent-identity", default="hermes", help="Agent identity used for {identity} config expansion")
    parser.add_argument("--user-id", default="", help="Override active user id")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--hermes-home", default=argparse.SUPPRESS, help="Hermes profile home")
    common.add_argument("--session-id", default=argparse.SUPPRESS, help="Session id for scoped operations")
    common.add_argument("--agent-identity", default=argparse.SUPPRESS, help="Agent identity used for {identity} config expansion")
    common.add_argument("--user-id", default=argparse.SUPPRESS, help="Override active user id")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", parents=[common], help="Show provider status")
    status.set_defaults(func=cmd_status)

    search = sub.add_parser("search", parents=[common], help="Search HY Memory")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=cmd_search)

    add = sub.add_parser("add", parents=[common], help="Add one explicit memory")
    add.add_argument("content")
    add.set_defaults(func=cmd_add)

    list_cmd = sub.add_parser("list", parents=[common], help="List HY Memory records")
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.add_argument("--offset", type=int, default=0)
    list_cmd.set_defaults(func=cmd_list)

    delete = sub.add_parser("delete", parents=[common], help="Delete one memory by exact id")
    delete.add_argument("memory_id")
    delete.set_defaults(func=cmd_delete)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
