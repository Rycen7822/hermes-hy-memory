# Hermes HY Memory Provider 插件开发规划

## 0. 目标与当前结论

本项目目标是在当前仓库 `/home/xu/project/tools/hermes-hy-memory` 中开发一个 Hermes Agent memory provider 插件，将 `hy-memory` 作为 Hermes 的外部长时记忆后端接入。插件应作为单一 Hermes 插件交付，直接接入 Hermes `MemoryProvider` 生命周期，提供自动召回、自动写入、显式记忆工具、配置向导、状态检查与可验证测试。参考实现为本仓库已下载的 `openclaw-hy-memory` 与 `hy-memory` PyPI 包快照，原则是优先复用可用代码与既有契约，不重写 HY Memory 的记忆引擎，不新增独立 MCP 服务，不把第三方源码快照纳入版本控制。

核心设计结论：MVP 默认采用 Python SDK 直连模式，即 Hermes 插件直接 import 并封装 `hy_memory.HyMemoryClient`。OpenClaw 的本地 HTTP server/venv 自动拉起逻辑保留为参考与后备方案，不作为默认实现。原因是 Hermes memory provider 本身就是 Python 插件接口，直接使用 SDK 可以最大化复用 `hy-memory` 的客户端、配置、后台事件循环、数据层、HTTP server 之外的全部核心能力，同时避免额外常驻进程、端口占用、HTTP 序列化与生命周期管理复杂度。

## 1. 已盘点证据

### 1.1 当前仓库状态

- 仓库路径：`/home/xu/project/tools/hermes-hy-memory`。
- 当前仓库已是 Git 仓库，分支状态曾显示 `main...origin/main`。
- 当前顶层文件包含：`LICENSE`、`.gitignore`、`plan.md`、被忽略的 `reference/`。
- `.gitignore` 已包含 `/reference/`，因此下载的参考代码不会污染正式插件提交。
- `reference/` 内已有 npm 与 PyPI 两类参考快照，并有 `reference/README.md` 与 `reference/metadata/` 记录来源与校验。

### 1.2 Hermes memory provider API

已读取 `/home/xu/.hermes/hermes-agent/agent/memory_provider.py`，插件需要实现或按需覆盖以下接口：

- `name`: provider 短名。
- `is_available()`: 只做本地依赖与配置检查，不做网络调用。
- `initialize(session_id, **kwargs)`: 接收 `hermes_home`、`platform`、`agent_context`、`agent_identity`、`agent_workspace`、`parent_session_id`、`user_id` 等上下文。
- `system_prompt_block()`: 注入静态 provider 状态与工具提示。
- `prefetch(query, session_id="")`: 返回即将注入系统提示的召回上下文，应该快速。
- `queue_prefetch(query, session_id="")`: 可在后台准备下一轮召回。
- `sync_turn(user_content, assistant_content, session_id="", messages=None)`: 每轮结束后写入记忆，应非阻塞。
- `get_tool_schemas()` 与 `handle_tool_call(tool_name, args, **kwargs)`: 暴露并处理显式工具。
- `shutdown()`: flush 队列、等待后台线程、关闭客户端。
- 可选 hooks：`on_turn_start`、`on_session_end`、`on_session_switch`、`on_pre_compress`、`on_memory_write`、`on_delegation`。
- 配置辅助：`get_config_schema()` 与 `save_config(values, hermes_home)`，供 `hermes memory setup` 使用。

### 1.3 Hermes 插件发现与启用规则

已读取 `/home/xu/.hermes/hermes-agent/plugins/memory/__init__.py` 与 `/home/xu/.hermes/hermes-agent/hermes_cli/memory_setup.py`，关键规则如下：

- Hermes 扫描两个位置：内置 `plugins/memory/<name>/` 与用户安装 `$HERMES_HOME/plugins/<name>/`。
- 用户插件目录必须包含 `__init__.py`，且 `__init__.py` 文本中需要出现 `register_memory_provider` 或 `MemoryProvider`，否则不会被当作 memory provider 发现。
- 激活项来自 `config.yaml` 的 `memory.provider`，同时只能激活一个外部 memory provider。
- 插件可通过 `plugin.yaml` 声明 `description` 与 `pip_dependencies`。
- `hermes memory setup` 会读取 provider 的 `get_config_schema()`，把 secret 字段写入 `$HERMES_HOME/.env`，把非 secret 字段交给 provider 的 `save_config()`，再将 `memory.provider` 写入 config。
- 若插件提供 `cli.py` 且实现 `register_cli(subparser)`，Hermes 只会为当前 active memory provider 加载 CLI 子命令。

### 1.4 Hermes 现有 provider 可复用模式

已读取 `supermemory` 与 `mem0` 内置 provider，建议直接复用其工程模式：

- 从 `$HERMES_HOME/<provider>.json` 读取非 secret 配置，从环境变量读取 secret。
- 使用 `tools.registry.tool_error` 统一返回工具错误 JSON。
- 工具名使用 provider 前缀，避免与其他 provider 冲突。
- 自动召回使用后台线程或快速缓存，避免在 `prefetch()` 中长时间阻塞。
- 自动写入在后台线程执行，`shutdown()` join 线程。
- 根据 `agent_context` 对 `cron`、`flush`、`subagent` 禁止自动写入，避免污染主用户记忆。
- `initialize()` 优先使用 Hermes 传入的 `user_id` 做 gateway 场景用户隔离，同时保留 config 默认值供 CLI 单用户场景使用。

### 1.5 OpenClaw HY Memory 参考实现

已读取 `reference/npm/openclaw-hy-memory/source/openclaw.plugin.json` 与 `dist/index.js`，可复用的设计如下：

- 工具契约：`memory_search`、`memory_add`、`memory_get`、`memory_update`、`memory_delete`。
- 配置字段：`serverUrl`、`userId`、`agentId`、`sessionId`、`mode`、`autoRecall`、`autoCapture`、`topK`、`searchThreshold`、`memoryWriteTurnWindow`、`llm`、`embedder`、`vectorStore`。
- SDK 包名：`hy-memory`；OpenClaw 会按 vector store 选择 `hy-memory[qdrant]` 或 `hy-memory[faiss]`。
- HTTP 端点契约：`/healthz`、`/api/v1/status`、`/api/v1/add`、`/api/v1/search`、`/api/v1/list`、`/api/v1/delete_all`、`/api/v1/memories/:id`。
- 搜索结果归一化：HY Memory search 可能返回 `{profile, proactive, normal}` 分组，OpenClaw 将其按 `profile -> proactive -> normal -> other arrays` 展平。
- 自动召回：在 prompt build 前调用 search，注入 `<relevant-memories>`，并支持 evolution chain 格式化。
- 自动捕获：按窗口累计新消息，剥离已注入的 memory context 后写入 HY Memory。
- 安全语义：更新/删除必须先搜索获得 memory ID；批量删除必须显式确认；用户要求删除局部内容时优先 update 剩余完整内容，否则 delete。

### 1.6 HY Memory PyPI 包快照

已读取 `reference/pypi/hy-memory/extracted/hy_memory-1.2.16-py3-none-any.whl/hy_memory/client.py`、`config.py`、`server.py` 与 `METADATA`，可直接复用的能力如下：

- `HyMemoryClient(mode="lite" | "pro" | "ultra")`，内部使用持久后台 event loop，适合从同步插件代码调用。
- 同步 API：`add`、`search`、`get`、`update`、`delete`、`delete_all`、`list_memories`、`get_metrics`、`get_write_status`、`close`。
- `add(data, user_id, agent_id, session_id, metadata, memory_at)` 支持文本或 OpenAI-style messages。
- `search(query, user_ids, agent_ids, session_ids, limit, min_score, profile_limit, profile_min_score, reader)` 返回 profile/proactive/normal 三路召回。
- `get` 返回 `memory_id`、`content`、`layer`、`status`、`tags`、`memory_at`、`gmt_created`、`user_id`、`agent_id` 等字段。
- `config.py` 已支持 `MEMORY_LLM_*`、`MEMORY_EMBEDDER_*`、`MEMORY_VECTOR_*`、`MEMORY_CACHE_*`、`MEMORY_DATA_DIR`、`MEMORY_MODE` 等环境变量。
- `server.py` 是 stdlib HTTP server，主要服务 OpenClaw；Hermes MVP 不需要复制该 server，但可把其 endpoint contract 用作可选 HTTP transport 的兼容目标。

## 2. 范围与非目标

### 2.1 MVP 必须完成

1. 一个可被 Hermes 发现并启用的 user memory provider 插件，推荐开发目录名为 `hy_memory`，通过 symlink 或复制安装到 `$HERMES_HOME/plugins/hy_memory`。
2. 直接封装 `hy_memory.HyMemoryClient`，提供 SDK adapter，不重写 HY Memory 引擎。
3. 支持 `$HERMES_HOME/hy_memory.json` 非 secret 配置与 `$HERMES_HOME/.env` secret 配置。
4. 支持 Hermes `MemoryProvider` 生命周期：初始化、系统提示块、后台预取、自动写入、显式工具、session 切换、shutdown。
5. 暴露 namespaced 工具：`hy_memory_add`、`hy_memory_search`、`hy_memory_get`、`hy_memory_update`、`hy_memory_delete`、`hy_memory_list`、`hy_memory_status`。
6. 自动召回时把 HY Memory 搜索结果格式化为 Hermes memory context，由 MemoryManager 外层统一包进 `<memory-context>`。
7. 自动捕获时按 Hermes turn 写入 messages，并剥离已注入的 memory context，避免自我污染。
8. `on_memory_write` 镜像内置 memory tool 的显式 add 写入，至少支持 `action="add"`。
9. 单元测试覆盖配置、发现、schema、工具参数校验、搜索归一化、自动捕获清洗、背景线程 shutdown。
10. 可选真实环境 smoke test：在具备 LLM/embedding key 或本地 embedding 服务时执行 add/search/get/delete 闭环。

### 2.2 MVP 不做或暂缓

1. 不默认启动独立 HTTP server，不默认占用 `19527` 端口。
2. 不实现 MCP server，不要求用户额外配置 MCP。
3. 不 vendoring 整个 `hy_memory` 包源码；依赖通过 `plugin.yaml` 或用户环境安装。
4. 不在第一版实现 OpenClaw 的完整交互式四步 wizard；先用 Hermes `get_config_schema()` 与 JSON 配置覆盖。
5. 不在第一版默认安装 `qdrant`、`faiss`、`graph`、`redis` 全量 extras；MVP 默认走 `hy-memory` core + Chroma，本地用户需要高级 backend 时再安装 extras 或在后续 `post_setup` 中按选择动态安装。
6. 不修改 Hermes core；所有适配应在插件内完成。只有发现 Hermes core bug 时才另开修复任务。

## 3. 目标架构

```text
Hermes Agent
  └─ MemoryManager
      └─ HyMemoryProvider (MemoryProvider)
          ├─ HyMemoryConfigLoader
          ├─ HyMemoryClientAdapter
          │   └─ hy_memory.HyMemoryClient
          ├─ Tool handlers: hy_memory_add/search/get/update/delete/list/status
          ├─ Background prefetch worker
          ├─ Background capture/write worker
          └─ Formatter / sanitizer / normalization helpers

hy_memory.HyMemoryClient
  ├─ mode: lite | pro | ultra
  ├─ vector store: chroma by default, optional qdrant/faiss/tencent
  ├─ graph store: only for ultra, default Kuzu local path
  ├─ cache/history: local sqlite by default
  └─ LLM/embedder: OpenAI-compatible env/config
```

默认数据落点：`$HERMES_HOME/hy_memory/`，再细分为 `data/vector_db`、`data/kuzu_db`、`data/cache.db`、`data/history.db`、`logs/`。这样同一机器不同 Hermes profile 不会共享同一个 `~/.hy_memory`，也不会污染其他 profile。

## 4. 设计账本：保留、复用、删减、延期

| ID | 决策 | 来源 | 动作 | 理由 | 验证 |
|---|---|---|---|---|---|
| D1 | 使用 Hermes `MemoryProvider`，不走普通 plugin `register_tool` | Hermes `agent/memory_provider.py` 与 `plugins/memory/__init__.py` | 保留 | 这是 Hermes 记忆后端的正确入口，会被 `memory.provider` 激活并参与生命周期 | discovery/load provider 单测 |
| D2 | 插件名使用 `hy_memory` | Hermes 用户插件 import 规则 | 保留 | 避免目录名含 `-` 带来的模块名风险，工具名也自然 namespaced | 临时 `$HERMES_HOME/plugins/hy_memory` 加载测试 |
| D3 | 默认直连 `HyMemoryClient` | HY Memory Python SDK | 保留并复用 | Python SDK 已实现 add/search/get/update/delete/list/close 与后台 loop；Hermes 插件也是 Python | adapter mock 单测；真实 smoke 可选 |
| D4 | OpenClaw HTTP server manager 不进入 MVP | OpenClaw `startPythonServer` | 压缩/延期 | Hermes 直连 SDK 可省掉端口、venv、HTTP、进程回收；只有依赖冲突时再启用后备 HTTP transport | 文档注明 fallback 触发条件 |
| D5 | OpenClaw tool 语义迁移为 `hy_memory_*` | OpenClaw tools | 复用并重命名 | 保留 update/delete 安全语义，避免 generic `memory_*` 与其他 provider 冲突 | schema snapshot/参数校验测试 |
| D6 | OpenClaw search normalization 迁移到 Python helper | OpenClaw `normalizeSearchMemories` | 复用 | HY Memory 返回三路分组，Hermes 工具与 prefetch 需要稳定格式 | 输入数组/分组/异常结构单测 |
| D7 | OpenClaw auto recall 改写为 Hermes `queue_prefetch`/`prefetch` | OpenClaw `before_prompt_build` | 替换 | Hermes MemoryManager 已提供预取入口；不要使用 OpenClaw hook 模型 | prefetch worker 单测 |
| D8 | OpenClaw auto capture 改写为 Hermes `sync_turn(messages=...)` | OpenClaw after-turn capture | 替换 | Hermes 直接传入当前 messages，无需 OpenClaw cursor 机制；仍保留清洗与窗口思想 | capture sanitizer 单测 |
| D9 | Supermemory/Mem0 的 config/thread/tool_error 模式 | Hermes bundled providers | 复用 | 这些是当前 Hermes provider 的稳定工程习惯 | provider behavior 单测 |
| D10 | 不复制 `hy_memory.server.py` | HY Memory server | 删除/压缩 | SDK 直连不需要 HTTP endpoint；保留 endpoint 文档供后续 fallback | plan + README 记录 |
| D11 | 不把 `reference/` 加入版本控制 | 本仓库 `.gitignore` | 保留 | 参考代码是外部快照缓存，不是插件源码 | `git status --ignored` 显示 `!! reference/` |
| D12 | 高级 backend extras 后置 | OpenClaw dynamic pip spec | 延期 | 默认安装全量 extras 过重；MVP 先保证 Chroma/core 路径可靠 | README 标注高级 backend 安装方式 |

## 5. 计划文件结构

建议直接把当前 repo 组织成一个 user-plugin-compatible 目录，开发时用 symlink 安装：`ln -s /home/xu/project/tools/hermes-hy-memory ~/.hermes/plugins/hy_memory`。如果 symlink 名称为 `hy_memory`，Hermes discovery 会用 `hy_memory` 作为 provider 名称，即使真实仓库目录叫 `hermes-hy-memory` 也不会影响加载。

```text
/home/xu/project/tools/hermes-hy-memory/
  __init__.py                  # register(ctx) + HyMemoryProvider 入口，必须包含 register_memory_provider 或 MemoryProvider 文本
  plugin.yaml                  # name/version/description/pip_dependencies
  config.py                    # 默认配置、读取 $HERMES_HOME/hy_memory.json、schema、保存配置、路径解析
  client_adapter.py            # HyMemoryClient lazy init、SDK 调用封装、结果归一化、关闭
  tool_handlers.py             # tool schemas 与 handlers；避免 shadow Hermes core 顶层 tools 包
  formatting.py                # prefetch context 格式化、时间/evolution chain 格式化、JSON 安全输出
  capture.py                   # 消息清洗、trivial 判断、capture payload 构造
  cli.py                       # 可选但建议：status/search/add/list/delete/reset 子命令
  README.md                    # 安装、配置、验证、故障排查
  tests/
    test_config.py
    test_discovery.py
    test_client_adapter.py
    test_tools.py
    test_prefetch_capture.py
    test_formatting.py
  scripts/
    install_dev.sh             # 可选：创建 ~/.hermes/plugins/hy_memory symlink 并提示 restart/reset
    smoke_hy_memory.py         # 可选：真实 SDK add/search/get/delete smoke test
  plan.md                      # 本规划文档
  LICENSE
  .gitignore
```

第一版也可以把 provider 全部写在 `__init__.py` 中，但不推荐。拆成上述模块能降低单文件复杂度，且符合“先复用/压缩，不堆 append-only 逻辑”的目标：`__init__.py` 只做入口与类组装，通用工具函数放到独立模块以便单测。

## 6. 插件元数据规划

`plugin.yaml` 建议内容：

```yaml
name: hy_memory
version: 0.1.0
description: "HY Memory local/remote cognitive long-term memory provider for Hermes Agent. Uses hy-memory SDK with explicit tools, auto recall, and auto capture."
pip_dependencies:
  - hy-memory
```

暂不默认写 `hy-memory[all]`，避免引入过重依赖。README 与 setup 输出中说明高级 backend 的安装方式：

```bash
pip install 'hy-memory[qdrant]'
pip install 'hy-memory[faiss]'
pip install 'hy-memory[graph]'
pip install 'hy-memory[redis]'
```

若后续发现 Hermes `plugin.yaml` 支持 profile-specific 或 optional deps，再考虑把 extras 纳入 `post_setup`。

## 7. 配置设计

### 7.1 配置文件

非 secret 配置保存到 `$HERMES_HOME/hy_memory.json`。默认配置建议：

```json
{
  "mode": "pro",
  "auto_recall": true,
  "auto_capture": true,
  "capture_mode": "turn",
  "user_id": "hermes_default",
  "agent_id": "{identity}",
  "top_k": 10,
  "min_score": 0.4,
  "profile_limit": 5,
  "profile_min_score": 0.4,
  "reader": "",
  "data_dir": "",
  "vector_store": {
    "provider": "chroma",
    "collection_name": "hermes_memories"
  },
  "llm": {
    "provider": "openai",
    "model": "gpt-4.1-nano",
    "base_url": ""
  },
  "embedder": {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "base_url": "",
    "embedding_dims": 1536
  }
}
```

`data_dir` 为空时解析为 `$HERMES_HOME/hy_memory`。`agent_id` 支持 `{identity}` 模板，用 `initialize(... agent_identity=...)` 替换，默认 profile 可以得到稳定隔离。

### 7.2 环境变量与 secret

secret 不写入 JSON，交给 Hermes `.env` 与当前进程环境：

| 目标 | HY Memory env | Hermes setup 字段 | 说明 |
|---|---|---|---|
| LLM API key | `MEMORY_LLM_API_KEY` | `llm_api_key` secret | 可 fallback 到 `LLM_API_KEY` 或 `OPENAI_API_KEY` |
| LLM base URL | `MEMORY_LLM_BASE_URL` | `llm_base_url` non-secret/env | OpenAI-compatible endpoint |
| LLM model | `MEMORY_LLM_MODEL` | `llm_model` | 默认 `gpt-4.1-nano` |
| Embedder API key | `MEMORY_EMBEDDER_API_KEY` | `embedder_api_key` secret | 可与 LLM key 分离 |
| Embedder base URL | `MEMORY_EMBEDDER_BASE_URL` | `embedder_base_url` | OpenAI-compatible embedding endpoint |
| Embedder model | `MEMORY_EMBEDDER_MODEL` | `embedder_model` | 默认 `text-embedding-3-small` |
| Embedding dims | `MEMORY_EMBEDDING_DIMS` | `embedding_dims` | 若模型不支持 dimensions，adapter 需要清零或让用户配置 |
| Thinking mode | `HY_MEMORY_THINKING_MODE` | advanced env only | 复用 OpenClaw 对 Kimi/DeepSeek/Qwen/HY 模型的 thinking 控制思路 |

### 7.3 `get_config_schema()` 建议

首版只暴露最常用字段，避免 setup 过长：

```python
[
    {"key": "mode", "description": "HY Memory mode", "default": "pro", "choices": ["lite", "pro", "ultra"]},
    {"key": "user_id", "description": "Default user id", "default": "hermes_default"},
    {"key": "agent_id", "description": "Default agent id; supports {identity}", "default": "{identity}"},
    {"key": "auto_recall", "description": "Enable automatic recall", "default": "true", "choices": ["true", "false"]},
    {"key": "auto_capture", "description": "Enable automatic turn capture", "default": "true", "choices": ["true", "false"]},
    {"key": "top_k", "description": "Recall result limit", "default": "10"},
    {"key": "min_score", "description": "Normal recall min score", "default": "0.4"},
    {"key": "llm_api_key", "description": "LLM API key", "secret": True, "required": False, "env_var": "MEMORY_LLM_API_KEY"},
    {"key": "embedder_api_key", "description": "Embedder API key", "secret": True, "required": False, "env_var": "MEMORY_EMBEDDER_API_KEY"}
]
```

高级字段通过手动编辑 `$HERMES_HOME/hy_memory.json` 或后续 `cli.py config` 子命令管理。

## 8. Provider 生命周期细化

### 8.1 `is_available()`

职责：便宜、无网络、无长期副作用。

计划逻辑：

1. 尝试 `import hy_memory` 或 `from hy_memory import HyMemoryClient`。
2. 若 import 失败，返回 `False`，并在 debug log 中提示 `pip install hy-memory`。
3. 不检查 API key 是否存在，因为用户可能使用本地 Ollama 或 profile env 还未加载；也不初始化 `HyMemoryClient`。
4. 不启动 server、不创建数据库、不访问网络。

### 8.2 `initialize(session_id, **kwargs)`

职责：绑定 Hermes runtime 上下文，加载配置，准备 lazy adapter。

计划逻辑：

1. 保存 `hermes_home`，默认使用 `hermes_constants.get_hermes_home()`。
2. 保存当前 `session_id`。
3. 读取 `$HERMES_HOME/hy_memory.json`，合并默认配置。
4. 解析 `user_id`：优先 `kwargs["user_id"]` 或 `kwargs["user_id_alt"]`，否则配置中的 `user_id`，否则 `hermes_default`。
5. 解析 `agent_id`：配置中的 `agent_id`，将 `{identity}` 替换为 `kwargs["agent_identity"]`，否则 `hermes`。
6. 解析 data dirs：默认 `$HERMES_HOME/hy_memory`，并为 vector/cache/history/graph 补齐显式路径，避免落到 `~/.hy_memory`。
7. 根据 `agent_context` 设置 `_write_enabled = agent_context not in {"cron", "flush", "subagent"}`。
8. 创建 `HyMemoryClientAdapter`，但可以 lazy-init 真正的 `HyMemoryClient`。若配置 `warm_start=true`，可后台预热。
9. 设置 `_active = True`，若后续 client 初始化失败，工具返回错误且自动 recall/capture 降级为空。

### 8.3 `system_prompt_block()`

返回静态提示，不注入召回内容。建议内容：

```text
# HY Memory
Active. User: <user_id>. Agent: <agent_id>. Mode: <mode>.
Use hy_memory_search, hy_memory_add, hy_memory_get, hy_memory_update, hy_memory_delete, hy_memory_list, and hy_memory_status for explicit memory operations.
For deletion or update, search first and use exact memory_id; do not fabricate ids.
```

如果 provider 未 active，则返回空字符串。

### 8.4 `queue_prefetch()` 与 `prefetch()`

Hermes 文档明确 `prefetch()` 应该快，因此采用 Mem0/Supermemory 类似的后台预取：

- `queue_prefetch(query, session_id="")`：启动或替换后台线程，调用 `adapter.search(query, user_ids=[active_user], agent_ids=[active_agent optional], limit=top_k, min_score=min_score)`，格式化结果后写入 `_prefetch_result`。
- `prefetch(query, session_id="")`：最多 join 很短时间，例如 0.2 到 1 秒，然后返回 `_prefetch_result` 并清空；如果无结果或出错返回空字符串。
- 第一轮没有预取结果是可接受的；需要第一轮强召回时可后续增加 `blocking_first_prefetch` 配置，但不进入 MVP 默认。
- 召回内容不要自带 `<memory-context>`，由 Hermes `MemoryManager.build_memory_context_block()` 统一包裹。

格式建议：

```markdown
## HY Memory
The following are retrieved long-term memories for user `<user_id>`. Use silently when relevant.
- [profile] [score 82%] content ... (id: ...)
- [normal] [score 76%] content ... (id: ...)
```

对 evolution chain：如果 HY Memory 返回 `evolution_chain`，按 oldest -> latest 展开，沿用 OpenClaw 的可读格式，但限制总字符数，避免污染上下文。

### 8.5 `sync_turn()`

自动捕获策略：

1. 若 `_active`、`auto_capture`、`_write_enabled` 不全为真，直接返回。
2. 从 `messages` 中优先抽取最新 user/assistant turn；若没有 `messages`，用 `user_content` 与 `assistant_content` 构造二元 messages。
3. 清理文本：移除 `<memory-context>...</memory-context>`、`<hy-memory-context>...</hy-memory-context>`、OpenClaw 的 `<relevant-memories>...</relevant-memories>`、工具调用噪声、空白与过短 trivial 内容。
4. 对太短消息或纯确认类消息跳过，借鉴 Supermemory 的 trivial regex。
5. 在后台线程调用 `client.add(messages, user_id=..., agent_id=..., session_id=current_session, metadata={"source":"hermes", "type":"conversation_turn", ...})`。
6. 若已有写线程仍在运行，先短 join 或将任务放入小队列；MVP 可采用单线程串行，避免同一个 `HyMemoryClient` 并发写。

### 8.6 `on_memory_write()`

用于镜像 Hermes 内置 memory tool 的显式写入。

- 只处理 `action == "add"` 且 content 非空。
- `target` 写入 metadata：`{"source":"hermes_memory_tool", "target": target, "type":"explicit_memory"}`。
- `replace/remove` 暂不自动映射到 update/delete，因为 Hermes 内置 memory 的文本没有 HY Memory `memory_id`，盲目删除风险高。可在工具层提供显式 `hy_memory_update/delete`。

### 8.7 `on_session_switch()`

- 更新 `_session_id`。
- 对 `reset=True` 清理 prefetch 缓存与捕获缓冲。
- 对 `/resume`、`/branch`、context compression 保留 user/agent scope，仅切换 session scope。
- 记录 `parent_session_id` 到后续写入 metadata，便于追踪 lineage。

### 8.8 `shutdown()`

- join prefetch/write/warmup 线程，超时建议 5 秒。
- 调用 `HyMemoryClient.close()`。
- 清空 adapter 引用，避免后续 stale client。

## 9. SDK Adapter 设计

`client_adapter.py` 负责隔离 HY Memory SDK 的导入、初始化、调用和结果归一化，Provider 不直接散落调用 SDK。

### 9.1 类接口

```python
class HyMemoryClientAdapter:
    def __init__(self, config: HyMemoryResolvedConfig, logger=None): ...
    def is_ready(self) -> bool: ...
    def get_client(self): ...  # lazy, thread-safe
    def add(self, data, *, user_id, agent_id, session_id, metadata=None, memory_at=None) -> dict: ...
    def search(self, query, *, user_ids, agent_ids=None, session_ids=None, limit=10, min_score=0.4, profile_limit=5, profile_min_score=0.4, reader="") -> dict: ...
    def get(self, memory_id: str) -> dict | None: ...
    def update(self, memory_id: str, content: str) -> dict: ...
    def delete(self, memory_id: str) -> dict: ...
    def delete_all(self, *, user_id: str, agent_ids=None, session_ids=None) -> dict: ...
    def list_memories(self, *, user_id: str, agent_id=None, limit=100, offset=0, order="desc") -> dict: ...
    def status(self) -> dict: ...
    def close(self) -> None: ...
```

### 9.2 初始化配置

`HyMemoryClient` 默认会从 env 和 `~/.hy_memory` 推导路径，因此 adapter 必须显式覆盖路径，避免 profile 泄漏：

- `config.vector_store.persist_directory = <data_dir>/data/vector_db`
- `config.cache.db_path = <data_dir>/data/cache.db`
- `config.history.db_path = <data_dir>/data/history.db`
- `config.graph_store.db_path = <data_dir>/data/kuzu_db`
- `config.vector_store.collection_name = configured collection`
- `mode = configured mode`

优先使用 `MemoryConfig.from_dict()` 然后显式 patch nested path 字段。不要在全局长期修改 `os.environ`；如必须临时设置 `MEMORY_DATA_DIR`，必须在 lock 内保存并恢复，防止影响其他插件或 Hermes 自身。

### 9.3 搜索归一化

实现 Python 版 OpenClaw `normalizeSearchMemories`：

```python
def normalize_search_memories(raw):
    if isinstance(raw, list): return raw
    if isinstance(raw, dict):
        out = []
        for key in ("profile", "proactive", "normal"):
            if isinstance(raw.get(key), list): out.extend(raw[key])
        for key, value in raw.items():
            if key not in {"profile", "proactive", "normal"} and isinstance(value, list): out.extend(value)
        return out
    return []
```

保留每条 memory 的 `memory_id`、`content`、`score`、`layer`、`tags`、`memory_at`、`gmt_created`、`evolution_chain`、`source_raw_memory_id`、`speculate` 等字段，但工具输出默认截断内容并隐藏过长 metadata。

## 10. 工具设计

工具都返回 JSON 字符串；错误通过 `tool_error()`。工具名全部使用 `hy_memory_` 前缀。

### 10.1 `hy_memory_search`

用途：语义搜索 HY Memory。参数：

- `query` string required。
- `limit` integer optional，默认配置 `top_k`，范围 1..50。
- `user_id` string optional，默认 active user。
- `agent_id` string optional，默认 active agent；传空可跨 agent 搜索。
- `session_id` string optional，可限定 session。
- `min_score` number optional。
- `include_raw` boolean optional，默认 false；true 时返回原始字段。

返回：`{"results": [{"id", "content", "score", "layer", "memory_at", "tags"}], "count": N}`。

### 10.2 `hy_memory_add`

用途：写入显式记忆或 messages。参数：

- `content` string optional。
- `messages` array optional，每项 `{role, content}`。
- `metadata` object optional。
- `user_id`、`agent_id`、`session_id` optional。
- `memory_at` string optional，ISO 时间。

校验：`content` 与 `messages` 至少一个；metadata 非 dict 则丢弃或报错。返回 HY Memory 的 `success`、`memory_id`、`request_id`、`elapsed_ms`。

### 10.3 `hy_memory_get`

参数：`memory_id` required。返回单条 memory 的精简字段和 raw 可选字段。

### 10.4 `hy_memory_update`

参数：`memory_id` required、`content` required。

安全说明沿用 OpenClaw：只用于更新已有 memory；不要用 update 写新事实；memory_id 必须来自 search/list/get，不得猜测。失败时返回 HY Memory error message。

### 10.5 `hy_memory_delete`

参数：

- `memory_id` optional。
- `all` boolean optional。
- `confirm` boolean optional。
- `user_id` optional，仅 `all=true` 时使用。
- `agent_id`、`session_id` optional，用于缩小批量删除范围。

安全策略：

- 删除单条必须提供 `memory_id`。
- `all=true` 必须 `confirm=true`。
- 若用户要求删除局部信息，工具说明要求先 search，再决定 update 剩余完整内容或 delete 整条。

### 10.6 `hy_memory_list`

参数：`user_id` optional、`agent_id` optional、`limit` default 50、`offset` default 0、`order` enum `desc|asc`。返回 active memories 列表。

### 10.7 `hy_memory_status`

用途：不做昂贵网络健康检查时返回本地配置/可用性；可选 `deep` 参数用于调用 SDK metrics 或一次轻量 backend check。

返回建议：

```json
{
  "configured": true,
  "client_initialized": true,
  "mode": "pro",
  "user_id": "...",
  "agent_id": "...",
  "data_dir": "...",
  "vector_store": "chroma",
  "auto_recall": true,
  "auto_capture": true
}
```

## 11. CLI 规划

`cli.py` 不是 MVP 硬依赖，但建议实现，方便用户自检。Hermes 只为 active provider 注册 CLI。

建议命令：

```bash
hermes hy_memory status [--deep]
hermes hy_memory search "query" [--limit 10]
hermes hy_memory add "text"
hermes hy_memory list [--limit 50]
hermes hy_memory inspect <memory_id>
hermes hy_memory delete <memory_id>
hermes hy_memory reset --yes
```

CLI 复用 provider 的 config loader 与 adapter，不复制逻辑。`reset` 必须有 `--yes`，并默认只删当前配置 user 范围。

## 12. 安装与启用规划

### 12.1 开发安装

推荐在开发阶段使用 symlink：

```bash
mkdir -p ~/.hermes/plugins
ln -sfn /home/xu/project/tools/hermes-hy-memory ~/.hermes/plugins/hy_memory
hermes memory setup hy_memory
```

如果 `hermes memory setup hy_memory` 不可用，可用交互式：

```bash
hermes memory setup
```

或手动设置 config：

```yaml
memory:
  provider: hy_memory
```

启用或修改 provider 后需要新开会话，必要时 reload/reset/restart Hermes Agent。

### 12.2 生产安装

后续可以提供 `scripts/install_dev.sh` 或 `scripts/install_plugin.py`：

1. 检查当前目录包含 `plugin.yaml` 与 `__init__.py`。
2. 创建 `$HERMES_HOME/plugins/hy_memory` symlink 或复制。
3. 提示运行 `hermes memory setup hy_memory`。
4. 提示用户 reload/reset/restart Hermes Agent。

不在安装脚本中自动写入 API key。

## 13. 实施阶段

### Phase 0：仓库骨架与入口验证

交付：`plugin.yaml`、`__init__.py`、最小 provider 类、发现测试。

任务：

1. 创建 `plugin.yaml`。
2. 创建 `__init__.py`，包含 `HyMemoryProvider(MemoryProvider)` 与 `register(ctx): ctx.register_memory_provider(HyMemoryProvider())`。
3. `name` 返回 `hy_memory`。
4. `is_available()` 只检查 `hy_memory` import。
5. `get_tool_schemas()` 暂返回空或最小 `hy_memory_status`。
6. 写 `tests/test_discovery.py`，用临时 `$HERMES_HOME/plugins/hy_memory` 验证 `discover_memory_providers()` 与 `load_memory_provider("hy_memory")`。

验证：

```bash
python -m py_compile __init__.py
pytest -q tests/test_discovery.py
```

### Phase 1：配置层

交付：`config.py`、配置 schema、保存与加载单测。

任务：

1. 定义默认配置与 `HyMemoryResolvedConfig` dataclass。
2. 实现 `_as_bool`、`_as_int`、`_as_float`、路径展开、`{identity}` 替换。
3. 实现 `load_hy_memory_config(hermes_home, runtime_kwargs)`。
4. 实现 `save_config(values, hermes_home)`，只保存非 secret。
5. 实现 `get_config_schema()`。
6. 保证缺失 JSON、JSON 损坏、字段类型错误时安全 fallback。

验证：

```bash
pytest -q tests/test_config.py
```

### Phase 2：SDK adapter

交付：`client_adapter.py`，可 mock 的 `HyMemoryClient` 封装。

任务：

1. Lazy import `hy_memory` 与 `MemoryConfig`。
2. 实现 lazy client 初始化，threading.Lock 保护。
3. 显式设置 `$HERMES_HOME/hy_memory` 下的数据路径。
4. 实现 add/search/get/update/delete/delete_all/list/status/close。
5. 实现 search normalization。
6. 初始化失败时保存 last_error，工具可返回可读错误。

验证：

```bash
pytest -q tests/test_client_adapter.py
```

### Phase 3：显式工具

交付：`tool_handlers.py` 与 provider 的 `get_tool_schemas()`/`handle_tool_call()`。

任务：

1. 定义 `hy_memory_search` schema 与 handler。
2. 定义 `hy_memory_add` schema 与 handler。
3. 定义 `hy_memory_get` schema 与 handler。
4. 定义 `hy_memory_update` schema 与 handler，加入 search-first 安全文案。
5. 定义 `hy_memory_delete` schema 与 handler，加入 `confirm=true` 批量删除门禁。
6. 定义 `hy_memory_list` 与 `hy_memory_status`。
7. 所有工具输出 JSON；错误用 `tool_error`。
8. 对 limit、min_score、metadata、messages 做输入校验。

验证：

```bash
pytest -q tests/test_tools.py
```

### Phase 4：自动召回与自动捕获

交付：`formatting.py`、`capture.py` 与 provider lifecycle 完整实现。

任务：

1. 实现 memory context 格式化，包含 layer、score、时间、id、evolution chain。
2. 实现内容截断策略：单条 memory 与总 context 都限制长度，例如单条 500 字，总计 4000 字，可配置。
3. 实现 `queue_prefetch()` 后台线程，调用 adapter search。
4. 实现 `prefetch()` 快速返回缓存结果。
5. 实现 `sync_turn()` 后台写入。
6. 实现清洗函数，剥离 `<memory-context>`、`<hy-memory-context>`、`<relevant-memories>`。
7. 实现 `on_memory_write()` 对显式 add 的镜像。
8. 实现 `on_session_switch()` 与 `shutdown()`。

验证：

```bash
pytest -q tests/test_prefetch_capture.py tests/test_formatting.py
```

### Phase 5：CLI、README 与 smoke test

交付：`cli.py`、`README.md`、`scripts/smoke_hy_memory.py`。

任务：

1. 实现 `hermes hy_memory status/search/add/list/inspect/delete/reset`。
2. 写 README：安装、配置、启用、重启提示、常见 env、Chroma 默认、高级 backend extras、故障排查。
3. 写 smoke 脚本，真实环境变量存在时执行 add -> search -> get -> delete；缺 key 时跳过并说明。
4. 确认 README 不包含任何真实 key，仅使用占位符。

验证：

```bash
python -m py_compile cli.py
python scripts/smoke_hy_memory.py --skip-if-unconfigured
```

### Phase 6：端到端 Hermes 验证

交付：实际在临时 HERMES_HOME 或默认 profile 中确认 discovery/setup/provider 工具可用。

建议先用临时 profile 或临时 HERMES_HOME 做无副作用检查：

```bash
TMP_HOME=$(mktemp -d)
mkdir -p "$TMP_HOME/plugins"
ln -s /home/xu/project/tools/hermes-hy-memory "$TMP_HOME/plugins/hy_memory"
HERMES_HOME="$TMP_HOME" PYTHONPATH=/home/xu/.hermes/hermes-agent python - <<'PY'
from plugins.memory import discover_memory_providers, load_memory_provider
print(discover_memory_providers())
p = load_memory_provider('hy_memory')
print(type(p).__name__, p.name, bool(p.get_tool_schemas()))
PY
```

如果真实 API/env 已配置，再在默认 profile 中做一次实际 add/search/delete smoke，并在完成后删除测试 memory。

## 14. 测试矩阵

| 测试层 | 用例 | 是否需要真实 HY Memory/LLM | 目标 |
|---|---|---:|---|
| py_compile | 所有插件 Python 文件 | 否 | 语法正确 |
| discovery | 临时 `$HERMES_HOME/plugins/hy_memory` | 否 | Hermes 能发现并加载 provider |
| config | JSON 缺失/损坏/类型错误/模板替换/路径解析 | 否 | 配置稳定 |
| schema | tool schema 名称、必填项、安全描述 | 否 | 工具契约稳定 |
| adapter mock | mock `HyMemoryClient` add/search/get/update/delete/list | 否 | SDK 调用参数正确 |
| normalization | array、profile/proactive/normal dict、异常结构 | 否 | Search 输出稳定 |
| tools mock | 参数缺失、limit clamp、delete confirm、update safety | 否 | 工具错误与输出正确 |
| capture | 清洗 memory context、trivial message 跳过、metadata | 否 | 不污染记忆 |
| lifecycle | session switch、shutdown join、write disabled contexts | 否 | Hermes lifecycle 正确 |
| smoke | add/search/get/delete 闭环 | 是，可跳过 | 真实集成可靠 |

## 15. 风险与缓解

### R1：`hy-memory` 依赖较重，可能影响 Hermes runtime

缓解：MVP 只声明 `hy-memory` core，不默认 extras；client lazy-init；初始化失败不让 Hermes 启动崩溃；如果发生不可接受的 dependency conflict，再增加 `transport="http"` 后备方案，复用 OpenClaw 的 HTTP endpoint contract 和 server 管理思路。

### R2：直接 SDK 初始化可能慢

缓解：`is_available()` 不初始化；`initialize()` 只加载配置；`HyMemoryClient` lazy-init 或后台 warmup；工具调用时若未 ready 返回清晰错误或等待一次初始化。

### R3：搜索必须传 `user_ids`

缓解：Provider 初始化时解析 active user；所有 search 工具和 prefetch 默认传 `[active_user]`；允许工具显式覆盖 `user_id`，但不会默认跨用户搜索。

### R4：自动写入可能把 recall context 写回 HY Memory

缓解：清洗函数剥离 Hermes `<memory-context>`、OpenClaw `<relevant-memories>` 与 provider 自身 `<hy-memory-context>`；捕获前做长度和 trivial 过滤；metadata 标记 source。

### R5：update/delete 误操作

缓解：工具描述强制 search-first；`hy_memory_delete(all=true)` 必须 `confirm=true`；不通过 `on_memory_write(remove/replace)` 自动映射删除；批量 reset 仅 CLI `--yes`。

### R6：多 profile / gateway 用户隔离错误

缓解：数据目录使用 `$HERMES_HOME`；user scope 优先 runtime `user_id`；agent scope 使用 `{identity}`；session scope 使用 Hermes session id；测试覆盖 user/agent/session 解析。

### R7：日志泄漏 key 或过多 memory 内容

缓解：日志不打印 env secret；status 输出隐藏 key；debug 中 memory 内容截断；README 用占位符；测试检查常见 secret 字段不进入 status JSON。

## 16. 回滚策略

- 若插件启用后异常，可将 `config.yaml` 的 `memory.provider` 设为空字符串或通过 `hermes memory setup` 选回 Built-in only。
- 如果使用 symlink 安装，删除 `$HERMES_HOME/plugins/hy_memory` 即可移除插件。
- 插件数据默认在 `$HERMES_HOME/hy_memory/`，删除该目录即可清理本地 HY Memory 数据；删除前必须确认用户不需要保留记忆。
- 若真实 smoke 写入测试 memory，测试结束必须通过 `hy_memory_delete` 删除测试 memory。

## 17. 完成定义

MVP 完成需满足：

1. `plan.md` 中的 Phase 0 到 Phase 4 完成，Phase 5 README 至少包含安装/配置/验证/重启提示。
2. 临时 `$HERMES_HOME` 下 `discover_memory_providers()` 能发现 `hy_memory`，`load_memory_provider("hy_memory")` 能返回 provider 实例。
3. `get_tool_schemas()` 返回所有计划工具，且工具名全部为 `hy_memory_*`。
4. 无真实 API key 时，单元测试全部通过，provider 初始化不崩溃，工具返回可读配置错误。
5. 有真实 API/env 时，smoke test 完成 add -> search -> get -> delete 闭环，并报告真实返回值。
6. 自动 capture 不写入 `<memory-context>` 或 `<relevant-memories>` 注入内容。
7. `shutdown()` 能关闭 `HyMemoryClient` 并 join 后台线程。
8. `git status --short --ignored` 仍显示 `reference/` 为 ignored，不把外部快照提交进仓库。

## 18. 后续增强候选，不进入 MVP 默认

1. `transport="http"`：复用 OpenClaw 的 server endpoint contract，在 SDK direct 失败或需要依赖隔离时启用。
2. 动态 extras 安装：setup 时根据 vector store 选择安装 `hy-memory[qdrant]` 或 `hy-memory[faiss]`。
3. 更完整的交互式 setup wizard：复用 OpenClaw LLM/embedder/vector provider 选择逻辑，但改写为 Hermes CLI。
4. `on_pre_compress()`：在上下文压缩前对即将丢弃的消息做一次 HY Memory 写入或 summary。
5. `on_delegation()`：将父 agent 的 subagent 任务与结果作为观察写入，但必须避免保存临时任务进度。
6. 导入/导出工具：用于从 built-in `MEMORY.md`/`USER.md` 迁移到 HY Memory，需单独做安全规划。
7. 性能指标：暴露 `get_metrics(minutes)` 的 CLI 与 tool，只在用户需要时开启。
