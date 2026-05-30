# Hermes HY Memory Provider 实施记录

## 当前完成状态

- `plan.md`：规划文档已完成，但实现未完成。
- 当前执行目标：按 `plan.md` 实现 Hermes Agent `hy_memory` memory provider 插件。
- 进度原则：每个阶段写测试、验证 RED/GREEN、更新本文件；每个阶段结束做 100% 信心复盘，不足则先修复再继续。

## 基线检查（2026-05-30）

- 仓库：`/home/xu/project/tools/hermes-hy-memory`。
- Git 状态：`main...origin/main`，当前未跟踪 `.gitignore`、`plan.md`，`reference/` 已被忽略。
- Python：3.12.13。
- pytest：9.0.2。
- 当前环境未安装 `hy_memory`，因此真实 SDK smoke 需要实现为 `--skip-if-unconfigured` 可跳过；单元测试必须 mock SDK。
- 参考代码位于已忽略的 `reference/`，不能提交外部快照。

## 设计/实现偏差记录

- `plan.md` 中原建议模块名 `tools.py`，但 Hermes core 有顶层 `tools` 包，插件源码根目录下的 `tools.py` 可能在 source-local 测试或动态加载时 shadow `tools.registry`。实现中改用 `tool_handlers.py`，并同步修正计划/文档，避免死文件与 import 冲突。

## 阶段复盘

### Phase 0 前置复盘

- 目前还没有生产代码，无法声明实现完成。
- 最大风险：源码目录直接测试时 import 路径与 Hermes 动态插件加载路径不同；必须写临时 `$HERMES_HOME/plugins/hy_memory` discovery/load 测试覆盖。
- 处理方案：先写 Phase 0 失败测试，再创建最小 `plugin.yaml`、`__init__.py`、`tool_handlers.py` 等骨架。

### Phase 0 完成记录

- RED：新增 `tests/test_discovery.py`，初次运行 `pytest -q tests/test_discovery.py` 失败，原因是 `hy_memory` provider 尚未被发现且无 `hy_memory_status` 工具。
- GREEN：新增 `plugin.yaml` 与最小 `__init__.py`，实现 `HyMemoryProvider`、`register(ctx)`、`initialize()`、`is_available()` 与 `hy_memory_status`。
- 验证：`pytest -q tests/test_discovery.py` -> `2 passed`；`python3 -m py_compile __init__.py` -> 通过。
- 100% 信心复盘：对 Phase 0 的最小发现/加载入口有事实信心；剩余不确定性属于后续阶段（配置、adapter、工具、lifecycle），不阻塞继续。需要注意后续拆分模块时必须保持 discovery 测试持续通过。

### Phase 1 完成记录

- RED：新增 `tests/test_config.py` 覆盖默认配置、JSON/runtime 合并、secret 不落盘、setup schema；新增 `tests/test_provider_config.py` 覆盖 provider 暴露 schema/save_config。初始失败为缺少 `config.py` 与 provider 默认 schema 为空。
- GREEN：新增 `config.py`，实现 `HyMemoryConfig`、默认配置、`load_hy_memory_config()`、`save_hy_memory_config()`、`get_config_schema()`；更新 provider 使用这些函数。
- 修复：pytest 会把仓库根 `__init__.py` 作为顶层测试包导入，导致相对导入失败；已在 `__init__.py` 中加入包导入优先、source-local 顶层导入后备，并由测试覆盖。
- 验证：`python3 -m py_compile __init__.py config.py` 通过；`pytest -q tests/test_discovery.py tests/test_config.py tests/test_provider_config.py` -> `7 passed`。
- 100% 信心复盘：对配置合并、路径隔离、setup schema 与 provider save_config 有事实信心；仍需后续验证 adapter 初始化是否正确把这些路径传给 HY Memory SDK，这是 Phase 2 范围。

### Phase 2 完成记录

- RED：新增 `tests/test_client_adapter.py`，初始失败为缺少 `client_adapter.py`。
- GREEN：新增 `client_adapter.py`，实现 `build_sdk_config_dict()`、`normalize_search_memories()`、`HyMemoryClientAdapter` lazy init、add/search/get/update/delete/delete_all/list/status/close。
- 复用点：search normalization 按 OpenClaw 逻辑展平 `profile -> proactive -> normal -> other array fields`；SDK 调用使用 `HyMemoryClient.from_config(config_dict, mode=...)`，不复制 HY Memory 引擎。
- 验证：`python3 -m py_compile client_adapter.py` 通过；`pytest -q tests/test_discovery.py tests/test_config.py tests/test_provider_config.py tests/test_client_adapter.py` -> `10 passed`。
- 100% 信心复盘：对 adapter 的参数转发、lazy 初始化、profile-scoped 路径、search normalization 有 mock 级事实信心；当前环境未安装真实 `hy_memory`，真实 SDK 初始化只能在最终 `scripts/smoke_hy_memory.py --skip-if-unconfigured` 或用户提供环境后验证。这个缺口已被 adapter mock 和 final smoke gate 明确覆盖，不需要引入 HTTP fallback 或过度兼容。

### Phase 3 完成记录

- RED：新增 `tests/test_tool_handlers.py` 覆盖 `hy_memory_add/search/get/update/delete/list/status` schema、scope 默认值、limit clamp、缺参报错、bulk delete `confirm=true` 门禁；新增 `tests/test_provider_runtime.py` 覆盖 provider 暴露完整工具面并通过 adapter status 返回配置。
- GREEN：新增 `tool_handlers.py`，实现显式工具 schema 与统一 dispatch；修复 `__init__.py`，将 Phase 0 的 `hy_memory_status` 单工具替换为完整工具面，初始化 `HyMemoryClientAdapter`，并把 provider runtime defaults 传入 tool handlers。
- 调试记录：`tests/test_provider_runtime.py` 初始失败为 `NameError: STATUS_SCHEMA`，根因是 Phase 0 占位代码在删除 `STATUS_SCHEMA` 后仍残留；已用 provider runtime 测试固定该集成路径。
- 验证：`python3 -m py_compile __init__.py tool_handlers.py` 通过；`pytest -q` -> `17 passed`。
- 100% 信心复盘：对工具 schema、handler 参数归一化、危险 bulk delete 门禁、provider->adapter 分发路径有事实信心；真实 SDK 写入/搜索仍由 Phase 6 smoke 处理。没有保留 `tools.py` 死文件，避免 shadow Hermes core `tools` 包。

### Phase 4 完成记录

- RED：新增 `tests/test_prefetch_capture.py` 覆盖 recall 格式化、memory-context 清洗、trivial turn 跳过、provider `queue_prefetch/prefetch/sync_turn/on_memory_write/on_session_switch/shutdown`。
- GREEN：新增 `formatting.py` 和 `capture.py`；扩展 provider 生命周期：后台 prefetch generation guard、自动写入线程、`agent_context in {cron, flush, subagent}` 跳过写入、session switch 清缓存、shutdown join 并 close adapter。
- 验证：`python3 -m py_compile __init__.py formatting.py capture.py` 通过；`pytest -q` -> `23 passed`。
- 100% 信心复盘：对自动召回缓存、注入上下文清理、自动 turn capture、内置 memory tool add 镜像、session switch、shutdown join 有单测事实信心；实际后端 latency 下 prefetch 第一轮可能返回空，这是 plan 明确接受的 MVP 行为。没有引入队列框架或 HTTP fallback，避免过度设计。

### Phase 5 完成记录

- RED：新增 `tests/test_cli_docs.py`，初始失败为缺少 `cli.py`、`scripts/smoke_hy_memory.py`、`README.md`，以及 `.gitignore` 未忽略 Python/test 生成物。
- GREEN：新增 `cli.py`（status/search/add/list/delete）、`scripts/smoke_hy_memory.py`（真实 add/search/get/delete；`--skip-if-unconfigured`）、`scripts/install_dev.sh`、`README.md`；扩展 `.gitignore` 忽略 `__pycache__/`、`*.py[cod]`、`.pytest_cache/`。
- 修复：CLI 全局参数原本只能放在子命令前，测试按 README 风格放在子命令后失败；已用 subparser common parent 支持 `python cli.py status --hermes-home ...`。
- 验证：`python3 -m py_compile cli.py scripts/smoke_hy_memory.py` 通过；`pytest -q` -> `27 passed`；`python3 cli.py status --hermes-home /tmp/hy-memory-cli-test` 输出 status；`python3 scripts/smoke_hy_memory.py --skip-if-unconfigured --hermes-home /tmp/hy-memory-smoke-test` -> `SKIP: hy_memory SDK is not installed in this Python environment`。
- 100% 信心复盘：对 CLI/status、文档关键安装命令、smoke skip 行为有事实信心；真实 smoke 在当前环境因缺少 `hy_memory` SDK 被正确跳过，不应伪造 PASS。最终还需 Phase 6 做临时 HERMES_HOME 插件发现、全量门禁、secret/git 清理。

### Phase 6 完成记录

- 临时 `$HERMES_HOME` 端到端验证：创建临时 home，symlink 当前仓库到 `plugins/hy_memory`，通过 Hermes `plugins.memory.discover_memory_providers/load_memory_provider` 发现并加载；初始化后完整工具面为 `hy_memory_add/search/get/update/delete/list/status`，status 使用 `phase6_user` 与 profile-scoped data dir。
- 全量门禁：`PYTHONPYCACHEPREFIX=/tmp/hy-memory-pycache python3 ... py_compile` -> `py_compile_ok`；`pytest -q` -> `27 passed`。
- CLI/smoke：`python3 cli.py status --hermes-home /tmp/hy-memory-final-cli` 成功；`python3 scripts/smoke_hy_memory.py --skip-if-unconfigured --hermes-home /tmp/hy-memory-final-smoke` 在当前未安装 SDK 环境中正确 `SKIP`。
- 安全/清理：非 reference 源码/文档 staged secret scan -> `0` findings；`git check-ignore` 确认 `/reference/`、`.pytest_cache/`、`__pycache__/` 被忽略；已移除当前生成的 cache 目录。
- 100% 信心复盘：对 plan.md Phase 0–6 的实现与验证闭环有事实信心；唯一未执行真实后端 add/search/delete 的原因是当前 Python 环境没有 `hy_memory` SDK，已由可跳过 smoke 明确报告而非伪造结果。

### Git 同步记录

- 实现提交已创建并推送；后续仅允许 note-only 同步记录更新。
- 当前远程同步状态以 `git rev-parse HEAD`、`git rev-parse origin/main`、`git ls-remote origin refs/heads/main` 的实时验证为准，避免在本文件中记录会随 note-only 提交变化而过期的最终 SHA。
