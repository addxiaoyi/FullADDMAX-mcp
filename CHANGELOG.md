# Changelog

All notable changes to FullADDMAX-mcp are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

> 📌 **当前最新版本: v0.6.0** — 4 个 mega tool · 31 个 op · 中国 LLM 全自动识别 · 中英双语错误信息
>
> 📖 完整文档:[English README](README.md) · [中文 README](README.zh-CN.md) · [MCP JSON 配置](docs/mcp-configs.md)

---

## [Unreleased]

### Planned
- (暂无 — 等待用户反馈)

---

## [0.6.0] — 2026-06-28

### ⚠️ Breaking Changes

- **Mega tool 整合** — 28 个独立 tool 全部重命名/合并为 4 个 mega tool:
  - `admin`(9 op) · `agent`(7 op) · `config`(10 op) · `knowledge`(5 op)
  - 业务参数统一打包成 `params_json` JSON 字符串
  - 详见 [README 迁移指南](README.md#-迁移指南--migration-guide-v05--v06)
- **pyproject.toml version bump** — `0.1.0` → `0.6.0`

### ✨ Added — China MCP 优化 (本次主要工作)

- **🇨🇳 6 大中国 LLM 自动识别** — `env_autodetect.py` 新增
  - DeepSeek(深度求索) — `DEEPSEEK_*` → `https://api.deepseek.com/v1`
  - 通义千问 Qwen(阿里 DashScope)— `DASHSCOPE_*` / `QWEN_*`
  - 智谱 GLM — `ZHIPUAI_*` / `GLM_*` → `https://open.bigmodel.cn/api/paas/v4`
  - 豆包 Doubao(字节火山方舟)— `ARK_*` / `DOUBAO_*` → `https://ark.cn-beijing.volces.com/api/v3`
  - Kimi(月之暗面 Moonshot)— `MOONSHOT_*` / `KIMI_*` → `https://api.moonshot.cn/v1`
  - 零一万物 Yi(01.AI)— `YI_*` / `LINGYIWANWU_*` → `https://api.lingyiwanwu.com/v1`
- **🛠 工具调用格式归一化** — `llm.py` 新增 `_normalize_tool_calls()`
  - 自动处理 GLM/豆包/Qwen 的 4 类差异:`arguments` 为 dict、`tool_call_id` 别名、缺 `type` 字段、参数在顶层
- **📡 流式 SSE 支持** — `LLMClient.chat_stream()` 新增 async generator
  - OpenAI 标准 SSE 协议(`data: {...}` / `data: [DONE]`)
  - 兼容 vLLM / LM Studio / Qwen DashScope / GLM 等不同 chunk 格式
- **🌐 错误信息本地化 (i18n)** — 新建 `src/fulladdmax_mcp/i18n.py`
  - 36 个错误键,完整英/中双语对照
  - 通过 `FULLADDMAX_LANG=zh-CN` 切换
  - 默认 `en`,无效值静默回退
- **🧪 中国 MCP 主机注入测试** — `scripts/test_chinese_mcp_hosts.py`
  - Cherry Studio / ChatGPT Box / Dify / NextChat 四种环境注入模式
- **📋 复制即用 MCP JSON 配置**
  - `docs/mcp-configs.md` — 6 LLM × 7 主机的 Markdown 片段
  - `examples/mcp-json/` — 43 个独立 JSON 文件
  - `scripts/generate_mcp_json_examples.py` — 自动生成器
  - `scripts/test_mcp_json_examples.py` — 校验器

### 📝 Documentation

- **中文 README** — 新建 `README.zh-CN.md`(5 行快速开始 / 中国 LLM 配置 / i18n 说明)
- **`.env.example` 扩充** — 新增 6 个中国 LLM 段 + `FULLADDMAX_LANG` 配置说明
- **CHANGELOG** — 新建本文件

### 🛡 Misc

- **`.gitignore` 补全** — 新增 14 条规则: `.ruff_cache/` / `.mypy_cache/` / `.hypothesis/` / `.claude/` / `.envrc` / `coverage.xml` 等

### 📊 Stats
- 修改文件:9
- 新增文件:51(3 docs + 43 JSON + 4 scripts + 1 i18n)
- 测试:366 个全通过
- 新增 i18n 键:36(18 zh-CN + 18 en)

---

## [0.5.0] — 2026-06-20

### ✨ Added

- **Token 用量统计** — 全自动记录每次 LLM 调用的 prompt/completion token + 成本估算
  - `usage_store.py` + SQLite 后端
  - 7 个新 MCP tool(`get_usage_stats` / `list_usage_records` / `reset_usage_stats` / `configure_pricing_override` 等)
- **限流令牌桶** — `rate_limit.py` 双层设计
  - Global RPM/TPM + per-session RPM/TPM
  - 4 个新 MCP tool(`configure_rate_limit` / `reset_rate_limit` / `get_rate_limit_status` 等)
- **可配置 logging** — `logging_config.py`
  - 5 个维度:level / format / file / rotation / max_bytes
  - 支持 CLI flag 与 env var 双重配置
- **零配置离线 stub** — `FULLADDMAX_AGENT_OFFLINE=1` 时 7 个 agent op 全走 deterministic Markdown 框架

---

## [0.4.0] — 2026-06-10

### ✨ Added

- **持久化 context** — `context.py` + `context_store.py`
  - Memory + SQLite 双后端
  - 跨调用可传递 state
- **4 个新 MCP tool**:`list_sessions` / `get_session` / `delete_session` / `purge_expired_sessions`

---

## [0.3.0] — 2026-05-30

### ✨ Added

- **Function calling / agent-callable tools** — 7 个 agent workflow 全部支持工具调用
  - `chat_with_tools()` 循环调度
- **Obsidian vault 双向读写** — `obsidian.py` 集成
  - 5 个新 MCP tool(`obsidian_list_notes` / `read` / `search` / `write` / `append`)
- **自定义 Swarm agent profile** — 注册 API
  - 3 个新 MCP tool(`register_swarm_agent` / `unregister_swarm_agent` / `list_swarm_agents`)
- **一键 SVG 面板** — `fulladdmax-mcp panel` 命令
  - 3 张核心卡:Server / LLM / Agent Tools
  - 单文件 SVG,可直接 `git commit`

---

## [0.2.0] — 2026-05-15

### ✨ Added

- **HTTP / Streamable-HTTP transport** — 除 stdio 外的可选传输层
- **`fulladdmax-install` CLI** — 一键把 server 写到 Claude Desktop / Cursor / Cline 配置

---

## [0.1.0] — 2026-05-01

### ✨ Initial Release

- MCP stdio server,暴露 7 个核心 agent tool
  - `orchestrator_run` · `parallel_agents_run` · `map_reduce_run` · `swarm_run` · `auto_workflow` · `delegate` · `hive_run`
- LLM autodetect:`FULLADDMAX_*` → `OPENAI_*` → 宿主注入 → 本地
- OpenAI 兼容端点(`/v1/chat/completions`),不绑定 provider
- 基础会话管理 + JSON 参数解析

---

## 📐 版本约定

- **Major** (1.0) — API 稳定,生产可用
- **Minor** (0.x) — 新功能(可含 breaking change,标 ⚠️)
- **Patch** (0.0.x) — Bug 修复 / 文档 / 性能

## 🤝 贡献

欢迎 PR / Issue!所有 breaking change 都会在 README 顶部和本文件标 ⚠️。

## 📄 许可证

[MIT](LICENSE) © addxiaoyi
