# FullADDMAX-mcp · 中文文档

> **多 agent MCP 服务器**：4 大工具 / 31 个 op，LLM 自动识别（Claude / Cursor / Ollama / DeepSeek / Qwen / GLM 等 11+ 厂商），零配置离线 stub 模式，一行命令生成 SVG 面板。v0.6.0

[English README](README.md) · [完整 API 参考](README.md#api) · [CHANGELOG](CHANGELOG.md)

![GitHub release](https://img.shields.io/github/v/release/addxiaoyi/FullADDMAX-mcp)
![GitHub stars](https://img.shields.io/github/stars/addxiaoyi/FullADDMAX-mcp)
![GitHub last commit](https://img.shields.io/github/last-commit/addxiaoyi/FullADDMAX-mcp)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 🚀 5 行快速开始

```bash
# 1. 装（国内推荐清华 pip 镜像）
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fulladdmax-mcp

# 2. 启动 MCP server（无 LLM 配置也能跑 - 走 offline stub 模式）
fulladdmax-mcp

# 3. 在另一个终端，看一眼面板
fulladdmax-mcp panel --out docs/panel.svg

# 4. 配置 LLM（任选一家，复制粘贴即可）
echo 'DEEPSEEK_API_KEY=sk-你的-key' >> .env

# 5. 重启 server，所有 agent op 自动接 DeepSeek
fulladdmax-mcp
```

**不需要：**
- ❌ 复杂的 YAML 配置
- ❌ 安装 Docker / K8s
- ❌ 注册任何云服务
- ❌ 联网（除非要调真 LLM）

> 📋 **需要 MCP JSON 配置?** 跳到 [docs/mcp-configs.md](docs/mcp-configs.md) — 6 大中国 LLM × 7 个主机的复制即用片段,或者 [examples/mcp-json/](examples/mcp-json/) 下 43 个独立 JSON 文件直接 `cp` 到主机配置目录。

---

## 🇨🇳 中国 LLM 一键配置

服务器会自动识别下面 6 个中国 LLM 厂商，**优先级 = 显式 FULLADDMAX_* > OPENAI_* > 下面任一 > Ollama/vLLM**。直接 `export` 对应 env var 即可：

| 厂商 | env var | base URL | 默认模型 | 适用场景 |
|------|---------|----------|---------|---------|
| **DeepSeek 深度求索** | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` | `deepseek-chat` | 性价比之王，推理强 |
| **通义千问 Qwen** | `DASHSCOPE_API_KEY` 或 `QWEN_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | 阿里云生态，中文强 |
| **智谱 GLM** | `ZHIPUAI_API_KEY` 或 `GLM_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` | 清华系，工具调用好 |
| **豆包 Doubao** | `ARK_API_KEY` 或 `DOUBAO_API_KEY` | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-pro-32k` | 字节系，价格低 |
| **Kimi 月之暗面** | `MOONSHOT_API_KEY` 或 `KIMI_API_KEY` | `https://api.moonshot.cn/v1` | `moonshot-v1-128k` | 长文本，128k context |
| **Yi 零一万物** | `YI_API_KEY` 或 `LINGYIWANWU_API_KEY` | `https://api.lingyiwanwu.com/v1` | `yi-large` | 性价比 + 多语言 |

**示例 - DeepSeek：**

```bash
export DEEPSEEK_API_KEY="sk-你的-key"
export DEEPSEEK_MODEL="deepseek-reasoner"  # 可选：用 R1 推理
fulladdmax-mcp
```

**示例 - Qwen：**

```bash
export DASHSCOPE_API_KEY="sk-你的-key"
export QWEN_MODEL="qwen-coder-plus"  # 代码任务用 coder 版
fulladdmax-mcp
```

**示例 - GLM 工具调用**（GLM 工具调用兼容性最好）：

```bash
export ZHIPUAI_API_KEY="你的-key"
export GLM_MODEL="glm-4-plus"
fulladdmax-mcp
```

所有 11 个 env var 别名都支持：`DEEPSEEK_API_KEY` 和 `QWEN_*` 是用户友好别名，`DASHSCOPE_*` 和 `ZHIPUAI_*` 是官方名。设置任一即可。

---

## 🎯 国产生态 MCP host 集成

| MCP host | 集成难度 | 配置方式 |
|----------|---------|---------|
| **Cherry Studio** | ⭐ 最简单 | 图形界面配 base_url + api_key，详见 [docs/integrations/cherry-studio.md](docs/integrations/cherry-studio.md) |
| **ChatGPT Box / Immersive** | ⭐ 简单 | Chrome 扩展设置页填 base_url + api_key |
| **Dify** | ⭐⭐ 中等 | 在 Dify 工作流加 MCP 节点，填 fulladdmax-mcp 命令路径 |
| **FastGPT** | ⭐⭐ 中等 | OneAPI / OpenAI 兼容代理层 |
| **NextChat / OneAPI** | ⭐⭐ 中等 | 当作 OpenAI 客户端配 base_url + api_key |
| **Trae** | ⭐ 最简单 | `~/.trae-cn/mcp.json` 加一段即可 |

**Cherry Studio 完整示例**（最受欢迎）：

```json
{
  "mcpServers": {
    "fulladdmax": {
      "command": "fulladdmax-mcp",
      "env": {
        "DEEPSEEK_API_KEY": "sk-你的-key",
        "DEEPSEEK_MODEL": "deepseek-chat"
      }
    }
  }
}
```

粘贴到 Cherry Studio → 设置 → MCP 服务器 → 添加。重启客户端即可看到 4 个新工具。

---

## 🤖 4 个 mega tool 一览

| mega tool | ops | 作用 |
|-----------|-----|------|
| **admin** | 9 | ping, configure_llm, server_info, 等等 |
| **agent** | 7 | orchestrator / parallel_agents / map_reduce / swarm / hive_run / delegate / auto_workflow |
| **config** | 10 | server 配置管理 |
| **knowledge** | 5 | 知识库管理 |

**agent tool 7 个 op 全部支持 offline stub 模式**（无 LLM 也能跑，返 deterministic Markdown 框架）。

---

## 🇨🇳 错误信息本地化

服务器**错误信息**支持中英双语切换（在 v0.6.0+）。把 `FULLADDMAX_LANG=zh-CN` 加到 `.env`：

```bash
# .env
FULLADDMAX_LANG=zh-CN
DEEPSEEK_API_KEY=sk-你的-key
```

错误信息样例对比：

| 场景 | English (default) | 中文 (`FULLADDMAX_LANG=zh-CN`) |
|------|-------------------|-------------------------------|
| 缺必填字段 | `ERROR: bad_param: missing required field 'task'` | `ERROR: 缺少必填字段 'task'` |
| hive waves 超限 | `ERROR: waves must be 1..20, got 25` | `ERROR: waves 必须在 1..20 之间, 当前 25` |
| 频率限制 | `ERROR: rate limit exceeded for session 'abc'; retry in 30s` | `ERROR: 会话 'abc' 触发频率限制; 请 30s 后重试` |
| LLM 未配置 | `ERROR: LLM not configured. Call configure_llm() or set FULLADDMAX_API_KEY.` | `ERROR: LLM 未配置。请调用 configure_llm() 或设置 FULLADDMAX_API_KEY 环境变量。` |

**注意**：i18n **只翻译错误信息**，面板标签 / log format / tool schema 保持英文（这是故意的，避免和 MCP host 的 UI 冲突）。详见 [src/fulladdmax_mcp/i18n.py](src/fulladdmax_mcp/i18n.py)。

---

## 🇨🇳 工具调用兼容 (中国 LLM)

中国 LLM 厂商（GLM / 豆包 / Qwen）的 tool_calls 响应格式与 OpenAI 略有差异。**已自动归一化**：

| 变体 | 出现于 | 自动处理 |
|------|--------|---------|
| `arguments` 为 dict 而非 JSON string | GLM-4 | 自动 `json.dumps` |
| `id` 字段名为 `tool_call_id` | 豆包某些版本 | 自动 rename |
| `type` 字段缺失或叫 `kind` | 豆包 alt | 默认 `"function"` |
| 顶层 `name` + `parameters` | 豆包 alt | 自动归位到 `function.{name, arguments}` |
| `arguments` 为空 / `None` / `{}` | 所有厂商 | 默认 `"{}"` |

无需配置，**所有消费 `tool_calls` 的代码都受益**（orchestrator / parallel_agents / map_reduce / swarm / hive_run）。

---



1. **递归深度** - agent ops 内部 stateful 但无硬编码深度限制
2. **波浪数** - `hive_run(waves=N)` 走 LLM 前先检查 N ≤ 20
3. **错误隔离** - 单个 sub-agent 失败不影响其他

---

## 🔧 故障排除（中文）

### Q1: 启动报错 `No LLM configured`

**正常情况**。这表示你走的是 offline stub 模式。3 种解决方式：

```bash
# 方式 1: 配置真 LLM
export DEEPSEEK_API_KEY="sk-..."

# 方式 2: 显式开启 stub
export FULLADDMAX_AGENT_OFFLINE=1
fulladdmax-mcp

# 方式 3: 用本地 Ollama
ollama serve &
export OLLAMA_HOST=http://localhost:11434
export OLLAMA_MODEL=qwen2.5-coder:7b
fulladdmax-mcp
```

### Q2: 调 GLM / 豆包工具调用失败

GLM 和豆包对 `tool_calls` 字段的格式有微小差异。代码已经做了 fallback：如果第一次解析失败，自动重试一次不带 tool_calls。

如仍失败：在 `.env` 加：
```bash
FULLADDMAX_LOG_LEVEL=DEBUG
FULLADDMAX_LOG_FILE=./logs/debug.log
```
重启后看 `debug.log` 里 LLM 返回的原始 JSON。

### Q3: 网络问题连不上 LLM

```bash
# 检查是否能直连 DeepSeek
curl -I https://api.deepseek.com

# 如果不行，配代理
export HTTPS_PROXY=http://127.0.0.1:7890
fulladdmax-mcp
```

### Q4: PyPI 安装慢

```bash
# 临时用清华镜像
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fulladdmax-mcp

# 永久配置
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q5: GitHub 拉代码慢

```bash
# 用 ghproxy 镜像
git clone https://ghproxy.com/https://github.com/addxiaoyi/FullADDMAX-mcp.git

# 或用 fastgit
git clone https://hub.fastgit.xyz/addxiaoyi/FullADDMAX-mcp.git
```

---

## 📊 面板 / Dashboard

```bash
# 生成 SVG 面板（一行命令）
fulladdmax-mcp panel --out docs/panel.svg

# JSON 格式 + 写盘 + 自动轮转 (生产推荐)
fulladdmax-mcp --log-format json --log-file /var/log/fulladdmax-mcp.log
```

面板包含 3 张核心卡：**Server** · **LLM** · **Agent Tools**。
智能三态：LLM api_key 字段会自动识别
- 真 key → 显示 `sk-x****`
- host 继承 (Claude Desktop 等) → 显示 `inherited from Claude Desktop`
- 完全没配 → 显示 `(off-the-shelf)` 灰标签

---

## 📋 全部 env var 速查

```bash
# === 🇨🇳 中国 LLM（任选一）===
export DEEPSEEK_API_KEY=...           # DeepSeek
export DASHSCOPE_API_KEY=...          # 通义千问
export ZHIPUAI_API_KEY=...            # 智谱 GLM
export ARK_API_KEY=...                # 豆包
export MOONSHOT_API_KEY=...           # Kimi
export YI_API_KEY=...                 # Yi 零一万物

# === 通用 LLM（最高优先级）===
export FULLADDMAX_BASE_URL=...
export FULLADDMAX_API_KEY=...
export FULLADDMAX_MODEL=...

# === 本地 LLM ===
export OLLAMA_HOST=http://localhost:11434
export OLLAMA_MODEL=qwen2.5-coder:7b

# === 行为开关 ===
export FULLADDMAX_AGENT_OFFLINE=1     # 强制走 stub
export HTTPS_PROXY=http://127.0.0.1:7890  # 代理

# === 日志（5 维）===
export FULLADDMAX_LOG_LEVEL=DEBUG
export FULLADDMAX_LOG_FORMAT=json
export FULLADDMAX_LOG_FILE=./logs/app.log
export FULLADDMAX_LOG_ROTATE_MAX_BYTES=10485760
export FULLADDMAX_LOG_ROTATE_BACKUPS=5
```

完整 list 见 [.env.example](.env.example)（36 个 var，全部注释，方便复制）。

---

## 🤝 贡献

欢迎 PR！建议优先：

1. **加中国 LLM 厂商** (在 `src/fulladdmax_mcp/env_autodetect.py` 加 1 行到 `_CN_LLM_PROVIDERS` 表)
2. **加中国 MCP host 文档** (在 `docs/integrations/` 加 md)
3. **修国内网络特殊问题** (在 `scripts/` 加 issue reproduction)

中文 issue 直接写中文即可，作者双语回复。

---

## 📜 License

MIT — 商用 / 私用 / 修改 / 分发都允许。唯一保留：版权声明。

---

## 🔗 相关资源

- [MCP 协议规范](https://modelcontextprotocol.io/)
- [Anthropic Claude API](https://docs.anthropic.com/)
- [DeepSeek Platform](https://platform.deepseek.com/)
- [阿里云百炼](https://bailian.console.aliyun.com/)
- [智谱 AI 开放平台](https://open.bigmodel.cn/)
- [火山方舟](https://www.volcengine.com/product/doubao)
- [Moonshot Kimi](https://platform.moonshot.cn/)
- [零一万物](https://platform.lingyiwanwu.com/)
- [Cherry Studio](https://cherry-ai.com/)
- [awesome-mcp-zh](https://github.com/awesome-mcp/awesome-mcp-zh)
