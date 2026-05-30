#!/usr/bin/env python3
"""Small developer CLI for the HY Memory Hermes provider."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict

from config import load_hy_memory_config, redact_config, save_hy_memory_config


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


ALLOWED_CONFIG_PATHS = {
    "mode",
    "auto_recall",
    "auto_capture",
    "top_k",
    "min_score",
    "profile_limit",
    "profile_min_score",
    "reader",
    "data_dir",
    "llm.mode",
    "llm.task",
    "llm.provider",
    "llm.model",
    "llm.base_url",
    "llm.temperature",
    "llm.max_tokens",
    "llm.timeout",
    "embedder.provider",
    "embedder.model",
    "embedder.base_url",
    "embedder.embedding_dims",
    "embedder.timeout",
    "embedder.max_retries",
    "vector_store.provider",
    "vector_store.collection_name",
    "vector_store.persist_directory",
}


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _nested_value(path: str, value: Any) -> Dict[str, Any]:
    parts = path.split(".")
    result: Dict[str, Any] = {}
    cursor = result
    for part in parts[:-1]:
        cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value
    return result


def _config_dict(args) -> Dict[str, Any]:
    cfg = load_hy_memory_config(args.hermes_home, {"agent_identity": args.agent_identity, "user_id": args.user_id, "session_id": args.session_id})
    return {
        "mode": cfg.mode,
        "auto_recall": cfg.auto_recall,
        "auto_capture": cfg.auto_capture,
        "user_id": cfg.user_id,
        "agent_id": cfg.agent_id,
        "session_id": cfg.session_id,
        "top_k": cfg.top_k,
        "min_score": cfg.min_score,
        "profile_limit": cfg.profile_limit,
        "profile_min_score": cfg.profile_min_score,
        "reader": cfg.reader,
        "data_dir": str(cfg.data_dir),
        "vector_store": {
            "provider": cfg.vector_provider,
            "collection_name": cfg.vector_collection_name,
            "persist_directory": str(cfg.vector_persist_directory),
        },
        "llm": redact_config(cfg.llm),
        "embedder": redact_config(cfg.embedder),
    }


def cmd_status(args) -> int:
    provider = _provider(args)
    try:
        return _print_json(provider.handle_tool_call("hy_memory_status", {"deep": bool(getattr(args, "deep", False))}))
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


def cmd_init(args) -> int:
    values = {
        "llm": {"mode": args.llm_mode, "task": "hy_memory"},
        "embedder": {
            "provider": args.embedder_provider,
            "model": args.embedder_model,
            "base_url": args.embedder_base_url,
            "embedding_dims": args.embedder_dims,
        },
        "vector_store": {
            "provider": args.vector_store,
            "collection_name": args.collection_name,
        },
    }
    save_hy_memory_config(values, args.hermes_home)
    print(json.dumps({
        "ok": True,
        "config": str(Path(args.hermes_home).expanduser() / "hy_memory.json"),
        "env_vars": ["MEMORY_EMBEDDER_API_KEY", "MEMORY_LLM_API_KEY"],
        "restart": "Restart/reset Hermes Agent after changing provider config or env vars.",
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_config_show(args) -> int:
    print(json.dumps(_config_dict(args), ensure_ascii=False, indent=2))
    return 0


def cmd_config_set(args) -> int:
    if args.path not in ALLOWED_CONFIG_PATHS:
        print(f"Unsupported config path: {args.path}", file=sys.stderr)
        return 2
    save_hy_memory_config(_nested_value(args.path, _parse_scalar(args.value)), args.hermes_home)
    print(json.dumps({"ok": True, "path": args.path}, ensure_ascii=False))
    return 0


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
    status.add_argument("--deep", action="store_true", help="Run explicit SDK/vector/embedder/LLM health checks")
    status.set_defaults(func=cmd_status)

    init = sub.add_parser("init", parents=[common], help="Create profile-scoped hy_memory.json")
    init.add_argument("--llm-mode", choices=["hermes", "direct"], default="hermes")
    init.add_argument("--embedder-provider", default="openai")
    init.add_argument("--embedder-model", default="BAAI/bge-m3")
    init.add_argument("--embedder-base-url", default="https://api.siliconflow.cn/v1")
    init.add_argument("--embedder-dims", type=int, default=1024)
    init.add_argument("--vector-store", default="chroma")
    init.add_argument("--collection-name", default="hermes_memories")
    init.add_argument("--non-interactive", action="store_true", help="Accepted for scriptable setup")
    init.set_defaults(func=cmd_init)

    config = sub.add_parser("config", parents=[common], help="Show or update HY Memory provider config")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_show = config_sub.add_parser("show", parents=[common], help="Show resolved redacted config")
    config_show.set_defaults(func=cmd_config_show)
    config_set = config_sub.add_parser("set", parents=[common], help="Set an allowed dotted config path")
    config_set.add_argument("path")
    config_set.add_argument("value")
    config_set.set_defaults(func=cmd_config_set)

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
