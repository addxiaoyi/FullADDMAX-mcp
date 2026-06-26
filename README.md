# FullADDMAX-mcp

> 多 Agent 编排 MCP Server / Multi-agent orchestration MCP server

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)]()
[![MCP](https://img.shields.io/badge/MCP-stdio-green.svg)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)]()

FullADDMAX-mcp 把单个 AI Agent 变成一支团队。它通过 [Model Context Protocol](https://modelcontextprotocol.io/)（stdio）暴露 4 种经过实战打磨的多 Agent 工作流，让 Claude Desktop、Cursor、Trae、Continue.dev 等任何 MCP 客户端都能直接调度子代理、做并行研究、流水线处理、和 Agent 协作。

FullADDMAX-mcp turns a single AI agent into a team. It exposes four battle-tested multi-agent workflows over the [Model Context Protocol](https://modelcontextprotocol.io/) (stdio), so any MCP client (Claude Desktop, Cursor, Trae, Continue.dev, ...) can dispatch sub-agents, run parallel research, shard processing, and collaborate with handoffs out of the box.

---

## ✨ 特性 / Features

- **4 种编排模式 / 4 workflows** — Orchestrator-Workers、Parallel Fan-Out、Map-Reduce、Swarm Handoffs
- **MCP stdio 协议 / MCP stdio transport** — 直接被 Claude Desktop / Cursor / Trae 加载
- **OpenAI 兼容 LLM / OpenAI-compatible LLM** — 支持 OpenAI、OpenRouter、DeepSeek、Qwen、本地 Ollama、vLLM、LM Studio...
- **共享会话 Context / Shared session context** — 工具调用之间可传递状态
- **超时 + 重试 + 并发限流 / Timeout + retry + bounded concurrency** — 防止 LLM 限流和卡死
- **零外部依赖运行**（除 `mcp` 和 `httpx`）/ No extra runtime deps beyond `mcp` and `httpx`
- **完整测试 + 4 个可跑示例 / Full test suite + 4 runnable examples**

---

## 📦 安装 / Installation

```bash
# 克隆
git clone https://github.com/addxiaoyi/FullADDMAX-mcp.git
cd FullADDMAX-mcp

# 用 pip（推荐开发模式）
pip install -e ".[dev]"

# 或用 uv
uv pip install -e ".[dev]"
```

可选依赖 `dev` 包含 `pytest` / `pytest-asyncio` / `respx` / `ruff`，运行测试和 lint 需要。

---

## 🔧 配置 LLM / Configure LLM

支持任何 OpenAI 兼容的 `/v1/chat/completions` 端点。

### 方式 A：环境变量（推荐给服务化部署）/ Environment variables (recommended for servers)

| 变量 | 默认 | 说明 |
|------|------|------|
| `FULLADDMAX_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容 base URL |
| `FULLADDMAX_API_KEY` | _(空)_ | API key |
| `FULLADDMAX_MODEL` | `gpt-4o-mini` | 模型名 |
| `FULLADDMAX_TEMPERATURE` | `0.7` | 采样温度 (0-2) |
| `FULLADDMAX_MAX_TOKENS` | `2048` | 单次响应最大 token |
| `FULLADDMAX_TIMEOUT` | `60` | 单次请求超时（秒）|
| `FULLADDMAX_MAX_RETRIES` | `2` | 5xx/网络错误重试次数 |

也支持 `OPENAI_API_KEY` 作为 `FULLADDMAX_API_KEY` 的兜底。

### 方式 B：运行时通过 MCP tool 配 / Configure at runtime via the MCP tool

调用一次 `configure_llm(base_url, api_key, model, ...)`，之后所有工作流都会用这个配置。

---

## 🧩 客户端集成 / Client Integration

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）或 `%APPDATA%\Claude\claude_desktop_config.json`（Windows）：

```json
{
  "mcpServers": {
    "fulladdmax": {
      "command": "fulladdmax-mcp",
      "env": {
        "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
        "FULLADDMAX_API_KEY": "sk-...",
        "FULLADDMAX_MODEL": "gpt-4o-mini"
      }
    }
  }
}
```

### Cursor

`Settings → MCP → Add new global MCP server`，command 填 `fulladdmax-mcp`，env 同上。

### Trae

`设置 → MCP → 添加`，同上。

---

## 🛠️ 工具列表 / Tool Reference

| Tool | 用途 |
|------|------|
| `ping` | 健康检查，返回版本和当前 LLM 配置（key 脱敏） |
| `configure_llm` | 配置 OpenAI 兼容的 base_url / api_key / model |
| `orchestrator_run` | Orchestrator-Workers：planner 拆任务 → N 个 worker 并行 → synthesizer 汇总 |
| `parallel_agents_run` | 并行子代理：最多 10 个并发，每个失败单独记录不中断整体 |
| `map_reduce_run` | Map-Reduce：map 阶段并行分片，reduce 阶段合并 |
| `swarm_run` | Swarm：内置 researcher / coder / critic / writer 4 个 agent，强制 JSON 交接 |

### `orchestrator_run(task, num_workers=3, timeout=300)`

1. Planner agent 把 `task` 拆成 `num_workers`（1-10）个独立子任务（JSON 数组）
2. Worker agent 并行执行每个子任务
3. Synthesizer agent 汇总所有结果

### `parallel_agents_run(tasks, max_concurrent=10, timeout=300)`

`tasks` 是 1-10 个独立 prompt 字符串列表，并发执行；输出为 Markdown 报告，每个任务一个 `## Task #N` 小节。

### `map_reduce_run(items, map_prompt="", reduce_prompt="", max_concurrent=10, timeout=600)`

- `map_prompt` 含占位符 `{item}` → 填入每个 item
- `reduce_prompt` 含占位符 `{results}` → 填入合并后的 map 输出
- 留空使用通用模板

### `swarm_run(initial_agent, task, max_handoffs=8, timeout=300)`

- `initial_agent` ∈ {`researcher`, `coder`, `critic`, `writer`}
- 每个 agent 必须以 JSON `{"next": <name|DONE>, "message": <string>}` 回复
- 达到 `max_handoffs` 或 `next="DONE"` 时结束

---

## 🌐 传输协议 / Transports

FullADDMAX-mcp 通过 CLI 切换 transport。

### stdio（默认）— Claude Desktop / Cursor / Trae

```bash
fulladdmax-mcp                       # 等价于 --transport stdio
```

stdio 模式直接被 MCP 客户端作为子进程拉起，通过 stdin/stdout 走 JSON-RPC。配置 `mcpServers` 时 `command` 填 `fulladdmax-mcp`，`env` 写 LLM 凭据（见下）。

### Streamable-HTTP（推荐给 HTTP 客户端 / 远程部署 / 多客户端共享）

`streamable-http` 是 MCP 1.x 推荐的生产级 HTTP transport（POST 请求携带 JSON-RPC，GET 用于 SSE 流式响应）。

```bash
# 启动 HTTP server（默认 127.0.0.1:8000，mount path /mcp）
fulladdmax-mcp --transport streamable-http

# 自定义 host/port
fulladdmax-mcp --transport http --host 0.0.0.0 --port 9000

# 自定义 mount path
fulladdmax-mcp --transport streamable-http --mount-path /fulladdmax
```

服务起来后，HTTP 客户端连接：

```
http://127.0.0.1:8000/mcp
```

也支持 `python -m fulladdmax_mcp --transport streamable-http` 用同一套参数。

#### 用 `curl` 自测

```bash
# 1) 初始化 session
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'

# 2) 调用 ping tool（带上一步返回的 Mcp-Session-Id）
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <session-id>" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ping","arguments":{}}}'
```

#### Claude Desktop 通过 HTTP 接入

```json
{
  "mcpServers": {
    "fulladdmax-http": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

> 注意：HTTP 模式下 LLM 凭据不能再通过 `env` 字段注入（该 server 进程不读 env），请改用 `configure_llm` tool 在运行时配置。

### SSE（旧客户端兼容）

```bash
fulladdmax-mcp --transport sse --port 8000
```

SSE 保留给旧版 MCP 客户端。新部署请用 `streamable-http`。

### Transport 对照 / Transport comparison

| Transport | 用途 | 配置字段 |
|-----------|------|---------|
| `stdio`（默认） | 本地 MCP 客户端（Claude Desktop / Cursor / Trae） | `command` + `env` |
| `streamable-http` | 远程 / 多客户端共享 / 反向代理 | `url` |
| `sse` | 旧客户端兼容 | `url` |

### 安全提示 / Security notes

- 默认只绑定 `127.0.0.1`，只接受本机连接；FastMCP 内置 DNS-rebinding 保护。
- 暴露到公网时务必加反向代理（nginx / Caddy）+ TLS，并在前面挂认证层。
- HTTP 模式下 server 进程内不读 `FULLADDMAX_API_KEY` env（凭据通过 `configure_llm` 配置），但请求日志可能暴露任务文本，注意日志脱敏。

---

## 🚀 快速开始 / Quickstart

启动 MCP server：

```bash
fulladdmax-mcp
```

在 Claude Desktop 中配置好后，模型可以直接调用：

> **请用 orchestrator_run 把"为一个 todo app 设计 REST API"拆成 3 个子任务并行执行**

模型会自动调 `configure_llm`（如果还没配过），然后调 `orchestrator_run`，最后把结果呈现给你。

### 本地跑示例 / Run examples directly

```bash
export FULLADDMAX_BASE_URL=https://api.openai.com/v1
export FULLADDMAX_API_KEY=sk-...
export FULLADDMAX_MODEL=gpt-4o-mini

python examples/orchestrator_demo.py
python examples/parallel_demo.py
python examples/mapreduce_demo.py
python examples/swarm_demo.py
```

---

## 🧪 测试与开发 / Development

```bash
# 跑测试（用 respx mock httpx，不需要真实 LLM key）
pytest -q

# 代码风格
ruff check src tests

# 启动 MCP Inspector 调试
mcp dev src/fulladdmax_mcp/server.py
```

---

## 🗺️ 路线图 / Roadmap

- [x] HTTP / Streamable-HTTP transport（v0.2.0）
- [ ] 自定义 Swarm agent profile 注册 API
- [ ] 工具调用 (function calling) 支持，让 worker 真正能用 MCP 工具
- [ ] 持久化 context（Redis / SQLite 后端）
- [ ] Token 用量统计 & 成本控制
- [ ] 限流令牌桶（避免打爆 LLM 限流）

---

## 🤝 与其他项目的对比 / Comparison

| 项目 | 定位 | 差异 |
|------|------|------|
| [lastmile-ai/mcp-agent](https://github.com/lastmile-ai/mcp-agent) | 通用 MCP Agent 框架 | FullADDMAX-mcp 只做编排，更轻量、零状态、纯 tool |
| [Ask149/orchestrator](https://github.com/Ask149/orchestrator) | 多代理并行 CLI | FullADDMAX-mcp 是 MCP server 形式，被任意 LLM 客户端加载 |
| [task-orchestrator](https://github.com/) (a29601) | 持久工作流图 | FullADDMAX-mcp 走 OpenAI 兼容 LLM，无需专用后端 |
| PraisonAI / BeeAI / fast-agent | 完整 Agent 平台 | FullADDMAX-mcp 是单文件可装的 MCP server |

---

## 📄 许可证 / License

MIT © addxiaoyi
