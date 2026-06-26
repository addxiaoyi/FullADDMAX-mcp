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

### ⚡ 一键配置（推荐 / Recommended）

安装 server 时会自动附带 `fulladdmax-install` 命令。它能自动检测本机已装的 IDE 并写入正确配置。

```bash
pip install -e .

# 1) 看本机有哪些 IDE 被识别
fulladdmax-install --list

# 2) 装到 Claude Desktop + Cursor + Codex（自动跳过未装的）
fulladdmax-install \
  --base-url https://api.openai.com/v1 \
  --api-key sk-... \
  --model gpt-4o-mini

# 3) 只装到指定 IDE
fulladdmax-install --ide claude --api-key sk-...

# 4) HTTP 模式（指向已启动的 HTTP server）
fulladdmax-install --ide cursor --url http://127.0.0.1:8000/mcp

# 5) 预览不写文件
fulladdmax-install --ide cursor --api-key sk-... --dry-run

# 6) 卸载
fulladdmax-install --ide cursor --uninstall
```

支持的 IDE：`claude`、`cursor`、`trae`、`continue`、`codex`（逗号分隔多选）。参数 `--api-key` 也可省略，靠环境变量 `FULLADDMAX_API_KEY` 提供。卸载时 `--api-key` 等可省略。

输出示例：

```
  claude   installed  C:\Users\l\AppData\Roaming\Claude\claude_desktop_config.json (container_key=mcpServers)
  cursor   installed  C:\Users\l\.cursor\mcp.json                                  (container_key=mcpServers)
  codex    skipped    C:\Users\l\.codex\config.toml                               (no change)
```

---

### 手动配置（可选） / Manual config (optional)

> 所有命令都假设你把 `BASE_URL` / `API_KEY` / `MODEL` 替换成你自己的值；`fulladdmax` 是配置文件里这台 server 的显示名（可改成 `My-Agents`、`maxmcp` 等）。

---

### Claude Desktop

配置文件路径：

| OS | 路径 |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

**macOS / Linux（bash / zsh）：**

```bash
CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
[ -f "$CONFIG" ] || CONFIG="$HOME/.config/Claude/claude_desktop_config.json"
mkdir -p "$(dirname "$CONFIG")"

# 用 jq 合并到已有配置，没有就新建
TMP=$(mktemp)
jq '.mcpServers += {
  "fulladdmax": {
    "command": "fulladdmax-mcp",
    "env": {
      "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
      "FULLADDMAX_API_KEY":  "sk-...",
      "FULLADDMAX_MODEL":    "gpt-4o-mini"
    }
  }
}' "$CONFIG" -o "$TMP" 2>/dev/null || cat > "$TMP" <<'JSON'
{
  "mcpServers": {
    "fulladdmax": {
      "command": "fulladdmax-mcp",
      "env": {
        "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
        "FULLADDMAX_API_KEY":  "sk-...",
        "FULLADDMAX_MODEL":    "gpt-4o-mini"
      }
    }
  }
}
JSON
mv "$TMP" "$CONFIG"
echo "✅ Claude Desktop config written to $CONFIG"
open -a "Claude"   # macOS：自动重启 Claude
```

**Windows（PowerShell 5+）：**

```powershell
$config = "$env:APPDATA\Claude\claude_desktop_config.json"
New-Item -ItemType Directory -Force -Path (Split-Path $config) | Out-Null

if (Test-Path $config) {
  $cfg = Get-Content $config -Raw | ConvertFrom-Json
} else {
  $cfg = [pscustomobject]@{ mcpServers = [pscustomobject]@{} }
}

$cfg.mcpServers | Add-Member -NotePropertyName fulladdmax -NotePropertyValue ([pscustomobject]@{
  command = "fulladdmax-mcp"
  env     = [pscustomobject]@{
    FULLADDMAX_BASE_URL = "https://api.openai.com/v1"
    FULLADDMAX_API_KEY  = "sk-..."
    FULLADDMAX_MODEL    = "gpt-4o-mini"
  }
}) -Force

$cfg | ConvertTo-Json -Depth 10 | Set-Content $config -Encoding UTF8
Write-Host "✅ Claude Desktop config written to $config"
# Start-Process "Claude"   # 如果想自动重启
```

> **HTTP 模式**：如果 server 是远程启动的（`fulladdmax-mcp --transport streamable-http`），把上面 JSON 里的 `command`+`env` 换成 `"url": "http://host:port/mcp"`，并且该远程 server 必须先用 `configure_llm` tool 配过凭据。

---

### Cursor

配置文件路径：

| OS | 路径 |
|----|------|
| 全平台 | `~/.cursor/mcp.json` |

**macOS / Linux：**

```bash
CONFIG="$HOME/.cursor/mcp.json"
mkdir -p "$(dirname "$CONFIG")"

cat > "$CONFIG" <<'JSON'
{
  "mcpServers": {
    "fulladdmax": {
      "command": "fulladdmax-mcp",
      "env": {
        "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
        "FULLADDMAX_API_KEY":  "sk-...",
        "FULLADDMAX_MODEL":    "gpt-4o-mini"
      }
    }
  }
}
JSON
echo "✅ Cursor MCP config written to $CONFIG"
```

**Windows（PowerShell）：**

```powershell
$config = "$env:USERPROFILE\.cursor\mcp.json"
New-Item -ItemType Directory -Force -Path (Split-Path $config) | Out-Null
@'
{
  "mcpServers": {
    "fulladdmax": {
      "command": "fulladdmax-mcp",
      "env": {
        "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
        "FULLADDMAX_API_KEY":  "sk-...",
        "FULLADDMAX_MODEL":    "gpt-4o-mini"
      }
    }
  }
}
'@ | Set-Content $config -Encoding UTF8
Write-Host "✅ Cursor MCP config written to $config"
```

也可以用 Cursor 内置的 CLI（v0.40+）：

```bash
# 添加 stdio 模式
cursor --add-mcp '{
  "name": "fulladdmax",
  "command": "fulladdmax-mcp",
  "env": {
    "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
    "FULLADDMAX_API_KEY":  "sk-...",
    "FULLADDMAX_MODEL":    "gpt-4o-mini"
  }
}'

# 或者添加 HTTP 模式（需要先启动 server）
cursor --add-mcp '{"name":"fulladdmax","url":"http://127.0.0.1:8000/mcp"}'
```

---

### Trae

Trae 把 MCP 配置放在 `mcp.json` 里（同 Cursor 格式），路径：

| OS | 路径 |
|----|------|
| 全平台 | `~/.trae/mcp.json`（部分版本在 `~/Library/Application Support/Trae/User/mcp.json`） |

**macOS / Linux：**

```bash
CONFIG="$HOME/.trae/mcp.json"
[ -f "$CONFIG" ] || CONFIG="$HOME/Library/Application Support/Trae/User/mcp.json"
mkdir -p "$(dirname "$CONFIG")"

cat > "$CONFIG" <<'JSON'
{
  "mcpServers": {
    "fulladdmax": {
      "command": "fulladdmax-mcp",
      "env": {
        "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
        "FULLADDMAX_API_KEY":  "sk-...",
        "FULLADDMAX_MODEL":    "gpt-4o-mini"
      }
    }
  }
}
JSON
echo "✅ Trae MCP config written to $CONFIG"
```

**Windows（PowerShell）：**

```powershell
$config = "$env:USERPROFILE\.trae\mcp.json"
New-Item -ItemType Directory -Force -Path (Split-Path $config) | Out-Null
@'
{
  "mcpServers": {
    "fulladdmax": {
      "command": "fulladdmax-mcp",
      "env": {
        "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
        "FULLADDMAX_API_KEY":  "sk-...",
        "FULLADDMAX_MODEL":    "gpt-4o-mini"
      }
    }
  }
}
'@ | Set-Content $config -Encoding UTF8
Write-Host "✅ Trae MCP config written to $config"
```

也可以在 Trae UI 里：设置 → MCP → 添加 → 粘贴上面的 JSON。

---

### Continue.dev

配置文件：`~/.continue/config.json`（v0.9+ 也支持 `~/.continue/config.yaml`）。

**JSON 写法：**

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "name": "fulladdmax",
        "command": "fulladdmax-mcp",
        "env": {
          "FULLADDMAX_BASE_URL": "https://api.openai.com/v1",
          "FULLADDMAX_API_KEY":  "sk-...",
          "FULLADDMAX_MODEL":    "gpt-4o-mini"
        }
      }
    ]
  }
}
```

**YAML 写法（v0.9+）：**

```yaml
mcpServers:
  - name: fulladdmax
    command: fulladdmax-mcp
    env:
      FULLADDMAX_BASE_URL: https://api.openai.com/v1
      FULLADDMAX_API_KEY: sk-...
      FULLADDMAX_MODEL: gpt-4o-mini
```

**macOS / Linux 一键写入：**

```bash
CONFIG="$HOME/.continue/config.yaml"
mkdir -p "$(dirname "$CONFIG")"

cat > "$CONFIG" <<'YAML'
mcpServers:
  - name: fulladdmax
    command: fulladdmax-mcp
    env:
      FULLADDMAX_BASE_URL: https://api.openai.com/v1
      FULLADDMAX_API_KEY: sk-...
      FULLADDMAX_MODEL: gpt-4o-mini
YAML
echo "✅ Continue config written to $CONFIG"
```

---

### Codex CLI

Codex 配置文件：`~/.codex/config.toml`（TOML 格式）。

```toml
[[mcp_servers]]
name = "fulladdmax"
command = "fulladdmax-mcp"
env = { FULLADDMAX_BASE_URL = "https://api.openai.com/v1", FULLADDMAX_API_KEY = "sk-...", FULLADDMAX_MODEL = "gpt-4o-mini" }
```

**macOS / Linux 一键写入：**

```bash
CONFIG="$HOME/.codex/config.toml"
mkdir -p "$(dirname "$CONFIG")"

# 追加（不覆盖）
cat >> "$CONFIG" <<'TOML'

[[mcp_servers]]
name = "fulladdmax"
command = "fulladdmax-mcp"
env = { FULLADDMAX_BASE_URL = "https://api.openai.com/v1", FULLADDMAX_API_KEY = "sk-...", FULLADDMAX_MODEL = "gpt-4o-mini" }
TOML
echo "✅ Codex config appended to $CONFIG"
```

**Windows（PowerShell）：**

```powershell
$config = "$env:USERPROFILE\.codex\config.toml"
New-Item -ItemType Directory -Force -Path (Split-Path $config) | Out-Null
Add-Content -Path $config -Value @'

[[mcp_servers]]
name = "fulladdmax"
command = "fulladdmax-mcp"
env = { FULLADDMAX_BASE_URL = "https://api.openai.com/v1", FULLADDMAX_API_KEY = "sk-...", FULLADDMAX_MODEL = "gpt-4o-mini" }
'@
Write-Host "✅ Codex config appended to $config"
```

---

### 验证安装 / Verify the install

配完之后，**重启 IDE**，然后让模型（或者你自己）调 `ping`：

> "请调用 fulladdmax 的 ping tool"

模型应该返回：

```
FullADDMAX-mcp v0.2.0 OK
base_url  : https://api.openai.com/v1
model     : gpt-4o-mini
api_key   : sk-...****    (前 4 位 + ****，完整 key 不会泄露)
timeout   : 60.0s
retries   : 2
```

如果 ping 返回 `api_key: (unset)`，说明 env 没传进去（HTTP 模式正常现象，stdio 模式请检查配置文件里的 `env` 块）。

---

## 🧰 自定义工具 / Function Calling

`fulladdmax-mcp` 的 agent 可以在工作流中途调用**你注册的 Python 工具**。LLM 通过 OpenAI 兼容的 `tools`/`tool_calls` 协议发起调用，框架负责调度、错误捕获、终止。

### 1. 注册一个工具

```python
# your_app.py  —  在 fulladdmax-mcp 启动前执行
import fulladdmax_mcp.tools as ft

@ft.register_tool
async def get_weather(city: str) -> str:
    """Look up current weather for a city."""
    return f"Weather in {city}: 72°F, sunny"

@ft.register_tool
async def search_docs(query: str, top_k: int = 5) -> str:
    """Search the local documentation index."""
    return f"<{top_k} results for '{query}'>"
```

或者用 `register_tool(fn, name=..., description=..., parameters=...)` 显式提供 OpenAI 格式的 JSON Schema。

### 2. 把工具暴露给 agent

每个工作流 tool 都接 `tools: list[str] | None` 参数：

| 值 | 含义 |
|----|------|
| `None`（默认）| 所有已注册工具（自动排除 orchestrator 自身） |
| `["get_weather"]` | 仅白名单里的工具 |
| `[]` | 关闭 function-calling（保持纯对话模式） |

> MCP 调用时这个参数名是 `tools`，类型是 `string[]`（每个元素是工具名）。

### 3. 在 MCP 客户端调用

让模型（Cursor / Claude Desktop / Trae）：

> "用 fulladdmax 的 `orchestrator_run` 写一个旅游规划，要求 `tools=["get_weather"]`"

模型会：
1. 调用 `orchestrator_run(task="...", tools=["get_weather"])` 
2. Planner 拆 3 个子任务
3. 每个 worker 看到 tool 列表后可以调 `get_weather(city=...)` 拿真实数据
4. Synthesizer 汇总成最终答案

### 4. 自递归保护

为了防止 LLM 在 worker 里调到 `orchestrator_run` 再次进入工作流（造成无限递归），框架默认排除这 6 个工具名：

```
ping, configure_llm, orchestrator_run, parallel_agents_run,
map_reduce_run, swarm_run
```

这个白名单在 `fulladdmax_mcp.tools.DEFAULT_EXCLUDE` 里。

### 5. 在 MCP 客户端里查看当前可用工具

调用 `list_agent_tools`：

```
- get_weather — Look up current weather for a city.
- search_docs — Search the local documentation index.

OpenAI specs (excluded: ...):
```json
[
  {"type": "function", "function": {"name": "get_weather", "description": "...", "parameters": {...}}},
  ...
]
```
```

### 6. 协议细节

`LLMClient.chat_with_tools` 的执行流程：

```
loop (max 6 steps):
  1. POST /v1/chat/completions with messages + tools + tool_choice=auto
  2. 如果 response.message.tool_calls 非空：
     a. 把 assistant message（带 tool_calls）追加到对话
     b. 逐个调 executor(call)
     c. 把结果作为 role=tool 消息追加到对话
  3. 如果 response.message 没有 tool_calls（或达到 max_steps）→ 退出
```

错误处理：executor 抛任何异常都会被捕获并以 `"ERROR: ExceptionName: msg"` 形式反馈给 LLM（不打断整个工作流）。

---

## 🛠️ 工具列表 / Tool Reference

| Tool | 用途 |
|------|------|
| `ping` | 健康检查，返回版本和当前 LLM 配置（key 脱敏） |
| `configure_llm` | 配置 OpenAI 兼容的 base_url / api_key / model |
| `configure_context_store` | 切到 memory / sqlite 后端 + 设 TTL |
| `list_sessions` | 列出 store 里所有 session + Markdown 表格 + JSON |
| `get_session` | 读一个 session 的完整 payload（JSON） |
| `delete_session` | 删一个 session（级联删所有 key） |
| `purge_expired_sessions` | GC 过期的 session（按 last_access + TTL） |
| `list_agent_tools` | 列出当前已注册给 agent 调用的工具 + OpenAI specs JSON |
| `unregister_agent_tool` | 取消注册某个 agent 工具 |
| `obsidian_list_notes` | 列出 Obsidian vault 里的所有 .md 笔记 |
| `obsidian_read_note` | 读一个 .md 笔记，返回 frontmatter + body |
| `obsidian_search_notes` | 关键字搜索笔记（case-insensitive 默认） |
| `obsidian_write_note` | 创建/覆盖一个 .md 笔记（带 frontmatter） |
| `obsidian_append_note` | 追加内容到 .md 笔记（保留 frontmatter） |
| `register_swarm_agent` | 注册自定义 Swarm agent profile（name / system / description） |
| `unregister_swarm_agent` | 取消注册某个 Swarm agent |
| `list_swarm_agents` | 列出当前所有 Swarm agent + JSON |
| `orchestrator_run` | Orchestrator-Workers：planner 拆任务 → N 个 worker 并行 → synthesizer 汇总 |
| `parallel_agents_run` | 并行子代理：最多 10 个并发，每个失败单独记录不中断整体 |
| `map_reduce_run` | Map-Reduce：map 阶段并行分片，reduce 阶段合并 |
| `swarm_run` | Swarm：内置 researcher / coder / critic / writer 4 个 agent，支持 JSON 交接 + 自定义 profile |

> `orchestrator_run` / `parallel_agents_run` / `map_reduce_run` / `swarm_run` 都接 `tools: list[str] | None` 参数（默认 `None` = 用全部已注册工具，`[]` = 关闭 function-calling）。
> 这 4 个工作流都接 `session_id: str = ""` 参数（默认 `""` = 创建新 session，传值 = 绑定到已有 session，跨请求持久化）。
> `swarm_run` 还接 `agents_json: str` 参数（默认 `""` = 用模块级 registry，JSON 数组 = 一次性覆盖本次调用的 agent 集）。
> 所有 `obsidian_*` tool 都接 `vault_path: str` 参数（vault 根目录的绝对路径），同一个 server 可以在一个 session 内服务多个 vault。

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
- [x] Function calling / agent-callable tools（v0.3.0）
- [x] Obsidian vault 双向读写集成（v0.3.0）
- [x] 自定义 Swarm agent profile 注册 API（v0.3.0）
- [x] 持久化 context（SQLite + Memory，v0.4.0）
- [ ] Token 用量统计 & 成本控制
- [ ] 限流令牌桶（避免打爆 LLM 限流）

---

## 🗒️ Obsidian 集成 / Vault Integration

5 个 `obsidian_*` tool 提供对 [Obsidian](https://obsidian.md/) vault 的双向读写。`vault_path` 是 vault 根目录的绝对路径，作为每个 tool 的参数传入 — 同一个 server 可以在一个 session 内服务多个 vault。

| Tool | 行为 |
|------|------|
| `obsidian_list_notes(vault_path, folder="", limit=500)` | 列出 vault（或子目录）下所有 `.md` 笔记 |
| `obsidian_read_note(vault_path, path)` | 读一个笔记，返回 frontmatter + body |
| `obsidian_search_notes(vault_path, keyword, folder="", case_sensitive=False, limit=50)` | 关键字搜索（默认 case-insensitive），返回 path + snippet |
| `obsidian_write_note(vault_path, path, body, frontmatter_json="", overwrite=False)` | 创建/覆盖笔记（frontmatter 通过 JSON 字符串传） |
| `obsidian_append_note(vault_path, path, content)` | 追加内容（保留 frontmatter） |

### Frontmatter 支持

手写的 YAML 解析器（**零依赖**），支持 Obsidian 里常见的所有 frontmatter 用法：

- 标量（字符串 / 数字 / 布尔 / null）
- 块列表 `- a / - b` 和流列表 `[a, b, "c d"]`
- 嵌套映射（2 空格缩进）
- 块标量 `|`（多行字符串）
- 单/双引号字符串
- 注释（`#` 整行和行内）
- Unicode（中文等）

不支持的部分：复杂的 YAML 1.2 特性（多重引用、自定义 tag）— 这些 frontmatter 解析会报 `VaultError`。

### 路径安全

- 绝对路径（`/etc/passwd`、`C:\Windows`）— 拒绝
- 路径穿越（`../foo`、`foo/../../bar`）— 拒绝
- 文件大小限制 5 MB

### 用法示例

**MCP 客户端调用（Cursor / Claude Desktop / Trae）：**

> "用 fulladdmax 的 `obsidian_search_notes` 找 `D:\MyVault` 里所有提到 'FullADDMAX' 的笔记"

> "用 `obsidian_read_note` 读 `D:\MyVault\Projects\roadmap.md` 完整内容"

> "把今天的工作日志追加到 `D:\MyVault\Daily\2026-06-26.md`"

**Agent 自动使用（function calling）：**

`obsidian_*` tool 同时也注册到了 agent 工具注册表，所以 worker 在 `orchestrator_run(tools=["obsidian_search_notes", "obsidian_read_note"])` 中可以**自动**调用它们来检索笔记，然后基于笔记内容生成答案。

例如：让 worker 写一份竞品分析报告，框架会让 worker：
1. `obsidian_search_notes("竞品 X")` 找出相关笔记
2. `obsidian_read_note(...)` 读每篇笔记
3. 把内容整合到最终答案里

### 完整示例

```python
from fulladdmax_mcp.obsidian import (
    list_notes_tool, read_note_tool, search_notes_tool,
    append_note_tool, write_note_tool,
)

vault = "D:/MyVault"

# 列出所有笔记
print(list_notes_tool(vault))

# 搜索
print(search_notes_tool(vault, "FullADDMAX"))

# 读
print(read_note_tool(vault, "Projects/roadmap.md"))

# 写（带 frontmatter）
print(write_note_tool(
    vault, "Daily/2026-06-26.md",
    body="今天开始 obsidian 集成",
    frontmatter_json='{"tags": ["work"], "status": "draft"}',
))

# 追加
print(append_note_tool(
    vault, "Daily/2026-06-26.md",
    "## 增量笔记\n- 跑通了 list / read / search / write / append 5 个 tool",
))
```

输出示例：

```
Found 12 note(s):
- Daily/2026-06-25.md
- Daily/2026-06-26.md
- Projects/roadmap.md
- ...

Found 3 match(es) for 'FullADDMAX':
- **Projects/roadmap.md** — …# 项目路线图  ## 进行中 - FullADDMAX 集成 …
- **Daily/2026-06-25.md** — …## 笔记  - FullADDMAX v0.3.0 已发布 …

# Projects/roadmap.md

## Frontmatter
​```yaml
status: active
tags: [mcp, agent]
​```

## Body
# 项目路线图
...
```

---

## 🐝 自定义 Swarm Agent / Dynamic Agent Profiles

Swarm 内置 4 个 agent profile（`researcher` / `coder` / `critic` / `writer`）。`register_swarm_agent` 让 MCP 客户端（或 Python 脚本）**动态注册/覆盖**任意数量的 agent。注册后所有后续 `swarm_run` 都看得到，跨请求持久化（只要 server 还活着）。

### 三种方式提供 agent

| 方式 | 用法 | 持久化 |
|------|------|--------|
| **内置**（默认） | 4 个 built-in 自动 seed，零配置 | 是 |
| **`register_swarm_agent`** | 动态注册 / 覆盖；server 启动后任意时间调用 | 是（直到 unregister） |
| **`swarm_run(agents_json=...)`** | 一次性传 JSON 数组，只对本次调用生效 | 否 |

### MCP 客户端用法

**1. 列出当前 agent：**

> "调 fulladdmax 的 `list_swarm_agents`"

返回 Markdown 报告 + JSON 块（机器可读）：

```
Registered swarm agents (4):
- **coder** — Implements and reviews code; explains trade-offs.
- **critic** — Stress-tests the proposal and surfaces risks.
- **researcher** — Gathers information, surfaces options, proposes hypotheses.
- **writer** — Synthesizes the final user-facing response.
```

**2. 注册一个自定义 agent：**

> "用 `register_swarm_agent` 注册 `legal`，system 是 'You are a legal reviewer. Be precise about liability. Always reply with JSON {next, message}.'"

→ `"registered: legal (total: 5 agent(s))"`

**3. 用自定义 agent 跑 swarm：**

> "用 `swarm_run` 跑 '撰写一份产品发布合规检查报告'，initial_agent=researcher，handoff 给 legal，再 handoff 给 writer"

模型可能会这样交接：

```
researcher  → legal       "Here is the product spec, please review for legal issues."
legal       → writer      "Compliance check: no major issues, see notes."
writer      → DONE        "Final report: ..."
```

**4. 一次性传入（不污染 registry）：**

> "用 `swarm_run` 跑 '分析竞品'，initial_agent=analyst，agents_json 是 '[{...}, {...}]'"

JSON 格式：

```json
[
  {"name": "analyst", "system": "You are a market analyst. Reply with JSON.", "description": "Market research."},
  {"name": "strategist", "system": "You are a strategist. Reply with JSON.", "description": "Strategic synthesis."}
]
```

→ 本次调用只看到这两个 agent；registry 不动。

### Python 用法

```python
from fulladdmax_mcp import swarm

# 注册一个（覆盖默认 researcher）
swarm.register_swarm_agent(
    name="researcher",
    system=(
        "You are a senior research analyst. Cross-check claims across "
        "multiple sources. Always reply with JSON {next, message}."
    ),
    description="Senior cross-validated researcher.",
    overwrite=True,
)

# 看下注册表
for a in swarm.list_swarm_agents():
    print(f"  {a.name}: {a.description}")

# 跑一次
import asyncio
out = asyncio.run(swarm.run("researcher", "What is the current state of MCP?"))
print(out)
```

### 安全保证

- `register` 用 `RLock` 保护，多线程并发安全
- 重复名默认报错（`SwarmAgentAlreadyExistsError`），必须 `overwrite=True` 才覆盖
- 空 name / 空 system prompt 被拒绝
- Built-in 可被删除（`unregister_swarm_agent("writer")`）— 除非重启进程，不会自动恢复
- `swarm_run` 在 `initial_agent` 不在 agent 集时立即抛 `EmptyInputError`，不会在 LLM 调用后才报错

---

## 💾 持久化 Context / Persistent Session State

工作流（orchestrator / parallel / map_reduce / swarm）在执行过程中会往一个**模块级** context store 写中间结果（planner 的子任务、worker 输出、handoff 链等），后续步骤读它做汇总。**默认**是 in-process memory，进程重启就丢。**持久化后端**用 SQLite — 数据落到单文件，跨进程 / 跨重启都还在。

### 5 个新 MCP Tool

| Tool | 行为 |
|------|------|
| `configure_context_store(backend, sqlite_path, ttl_seconds)` | 切到 `memory` 或 `sqlite`，设 TTL（默认 7 天） |
| `list_sessions()` | 列出所有 session：Markdown 表格 + JSON 块 |
| `get_session(session_id)` | 读一个 session 完整 payload（JSON） |
| `delete_session(session_id)` | 删一个 session（级联删所有 key） |
| `purge_expired_sessions(ttl_seconds=0)` | GC：删掉 `last_access` 早于 ttl 的所有 session（默认用 store 的 TTL） |

外加 4 个工作流 tool 都接 `session_id: str = ""` 参数（默认创建新 session，传值 = 绑定到已有 session，跨请求持久化）。

### 两种后端

| Backend | 持久化 | 跨进程 | 适用场景 |
|---------|--------|--------|---------|
| `MemoryContextStore`（默认） | ❌ | ❌ | 单进程、单元测试、临时跑 |
| `SqliteContextStore` | ✅ | ✅ | 长跑 server、跨重启、想看历史 session |

### 客户端使用

**1. 切到 SQLite：**

> "调 `configure_context_store(backend='sqlite', sqlite_path='/tmp/fam-ctx.db')`"

→ `"Configured SqliteContextStore at /tmp/fam-ctx.db (ttl=604800.0s)"`

**2. 跑一个工作流并指定 session：**

> "用 `orchestrator_run(task='分析 Q4 销售数据', session_id='quarterly-2026q4')`"

session 里会自动写：
- `task`、`subtasks`（planner 拆出来的）
- `worker_results`（每个 worker 的输出）
- `final`（synthesizer 的最终答案）

**3. 任何时候（甚至 server 重启后）查 session：**

> "调 `get_session('quarterly-2026q4')`"

```json
{
  "task": "分析 Q4 销售数据",
  "subtasks": ["提取关键指标", "对比 Q3", "生成图表"],
  "worker_results": [
    "Q4 营收 ¥1234 万，同比 +12%",
    "Q3 对比：Q4 比 Q3 高 8%",
    "图表代码：import matplotlib..."
  ],
  "final": "Q4 销售分析报告：..."
}
```

**4. 跨请求续传：**

> "昨天那个 `quarterly-2026q4` session 里的 final 字段你再展开下"

agent 调 `get_session('quarterly-2026q4')` 拿到上次的结果，在新请求里继续工作。

**5. 周期 GC：**

> "调 `purge_expired_sessions()` 删掉 30 天没动过的 session"

→ `"purged: 7 session(s)"`

### Python 脚本用法

```python
import asyncio
from fulladdmax_mcp import context as ctx
from fulladdmax_mcp import orchestrator
from fulladdmax_mcp.context_store import SqliteContextStore

# 切到 SQLite (跨重启都还在)
ctx.use_sqlite_store("/tmp/fam-ctx.db", ttl_seconds=30 * 86400)

# 直接读写
ctx.use_sqlite_store  # ...
sid = ctx.new_session()
ctx.put("user", "alice")
ctx.put("step", 1)
print(ctx.snapshot())  # {'user': 'alice', 'step': 1}

# 跑工作流，自动写到当前 session
out = asyncio.run(orchestrator.run("分析 Q4 数据", num_workers=2))

# 直接读 store API
store = ctx.store()  # SqliteContextStore
print(store.list_sessions())
print(store.snapshot(sid))

# 重启后: 同一条 SQL 文件重新打开，数据还在
```

### 数据模型

每个 session 是 sqlite 里的一行 `sessions(session_id, created_at, last_access)`，key/value 是 `entries(session_id, key, value)` — `value` 是 JSON 字符串。WAL 模式 + foreign key cascade — 删 session 自动删 entries。

### 重要保证

- **配置切换 close 上一个 store**（避免 SQLite 文件句柄泄漏）
- **TTL 检查只看 `last_access`** — 每次 `put` / `merge` 自动 bump；`get` 默认不 bump（开关 `touch_on_read=True`）
- **线程安全** — MemoryContextStore 用 `RLock`，SqliteContextStore 用 `check_same_thread=False` + 进程内 RLock
- **JSON 兼容** — 任意 JSON-serialisable 值（str / int / list / dict / bool / None）。非 JSON 值用 `put(key, value, default=str)` 自动转字符串
- **跨进程** — 同 SQL 文件，多进程安全（SQLite 内置锁）。**不要**多个进程同时改同一个 session

### 用 session_id 跑工作流的详细语义

| `session_id` 值 | 行为 |
|------|------|
| `""`（默认） | 创建新 session，session id 在 MCP tool 输出里可看（context 模块当前绑定） |
| `"quarterly-2026q4"` | bind 到已有 session；**不存在则自动创建**（bind 是 idempotent） |
| `""` 但 bind() 已经在外部调用 | 用外部 bind 的 session |

`bind()` 是 idempotent — 给个不存在的 id 它会创建，给个已有的就接上。这让客户端不用先调 `create_session` 之类的预热接口。

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
