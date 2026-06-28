# 复制即用 · FullADDMAX-mcp 配置片段

下面 6 个中国 LLM 服务商的 MCP JSON 配置,选中后**整段**复制到对应 MCP 主机即可。
每个片段都已填好 `base_url` 和默认模型,你只需要:

1. 把 `sk-...` 替换成你自己的 API Key
2. 粘到对应主机的 MCP 配置文件里
3. 重启主机(部分主机热重载)

> 通用规则:`FULLADDMAX_LANG=zh-CN` 会把所有错误信息翻成中文。

---

## 📋 6 大中国 LLM 一览

| 编号 | 服务商 | 厂商 | 默认模型 | 备注 |
|---|---|---|---|---|
| A | **DeepSeek** | 深度求索 | `deepseek-chat` | 性价比最高,中文强 |
| B | **Qwen** | 阿里通义 | `qwen-plus` | 长文本/代码好 |
| C | **GLM** | 智谱 | `glm-4-plus` | 多模态、工具调用稳 |
| D | **Doubao** | 字节火山方舟 | `doubao-pro-32k` | 国内延迟低 |
| E | **Kimi** | 月之暗面 | `moonshot-v1-128k` | 长上下文王(128k) |
| F | **Yi** | 零一万物 | `yi-large` | 双语均衡 |

---

## 🖥️ 主机配置位置速查

| 主机 | 配置文件路径 | 重启方式 |
|---|---|---|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)<br>`%APPDATA%\Claude\claude_desktop_config.json` (Windows) | 退出重开 |
| Cursor | `~/.cursor/mcp.json` | 设置面板 → MCP → 刷新 |
| Cline (VSCode) | VSCode 设置 → Cline → MCP Servers → 右上 ⚙️ | 自动 |
| Trae IDE | 设置 → MCP → 右上 `+` | 自动 |
| Cherry Studio | 设置 → MCP 服务器 → 手动添加 | 自动 |
| ChatGPT Box | 偏好设置 → MCP → 填 JSON | 重启插件 |
| NextChat | 配置 → MCP 服务器 | 自动 |
| Dify | 工具 → 自定义 → MCP 兼容服务 | 重启工作流 |

---

## 🍒 A. Cherry Studio (国内最常见)

设置 → MCP 服务器 → 添加 → 粘贴下面任一段:

<details>
<summary><b>A1. DeepSeek</b></summary>

```json
{
  "mcpServers": {
    "fulladdmax-deepseek": {
      "command": "uvx",
      "args": ["fulladdmax-mcp"],
      "env": {
        "DEEPSEEK_API_KEY": "sk-你的-deepseek-key",
        "DEEPSEEK_MODEL": "deepseek-chat",
        "FULLADDMAX_LANG": "zh-CN"
      },
      "description": "FullADDMAX-mcp · DeepSeek 深度求索"
    }
  }
}
```

</details>

<details>
<summary><b>A2. 通义千问 Qwen (DashScope)</b></summary>

```json
{
  "mcpServers": {
    "fulladdmax-qwen": {
      "command": "uvx",
      "args": ["fulladdmax-mcp"],
      "env": {
        "DASHSCOPE_API_KEY": "sk-你的-阿里云-key",
        "QWEN_MODEL": "qwen-plus",
        "FULLADDMAX_LANG": "zh-CN"
      },
      "description": "FullADDMAX-mcp · 阿里通义千问"
    }
  }
}
```

</details>

<details>
<summary><b>A3. 智谱 GLM</b></summary>

```json
{
  "mcpServers": {
    "fulladdmax-glm": {
      "command": "uvx",
      "args": ["fulladdmax-mcp"],
      "env": {
        "ZHIPUAI_API_KEY": "你的智谱-key",
        "GLM_MODEL": "glm-4-plus",
        "FULLADDMAX_LANG": "zh-CN"
      },
      "description": "FullADDMAX-mcp · 智谱 GLM"
    }
  }
}
```

</details>

<details>
<summary><b>A4. 豆包 Doubao (火山方舟)</b></summary>

```json
{
  "mcpServers": {
    "fulladdmax-doubao": {
      "command": "uvx",
      "args": ["fulladdmax-mcp"],
      "env": {
        "ARK_API_KEY": "你的火山方舟-key",
        "ARK_MODEL": "doubao-pro-32k",
        "FULLADDMAX_LANG": "zh-CN"
      },
      "description": "FullADDMAX-mcp · 字节豆包"
    }
  }
}
```

</details>

<details>
<summary><b>A5. Kimi (月之暗面)</b></summary>

```json
{
  "mcpServers": {
    "fulladdmax-kimi": {
      "command": "uvx",
      "args": ["fulladdmax-mcp"],
      "env": {
        "MOONSHOT_API_KEY": "sk-你的-kimi-key",
        "MOONSHOT_MODEL": "moonshot-v1-128k",
        "FULLADDMAX_LANG": "zh-CN"
      },
      "description": "FullADDMAX-mcp · 月之暗面 Kimi"
    }
  }
}
```

</details>

<details>
<summary><b>A6. 零一万物 Yi</b></summary>

```json
{
  "mcpServers": {
    "fulladdmax-yi": {
      "command": "uvx",
      "args": ["fulladdmax-mcp"],
      "env": {
        "YI_API_KEY": "你的零一万物-key",
        "YI_MODEL": "yi-large",
        "FULLADDMAX_LANG": "zh-CN"
      },
      "description": "FullADDMAX-mcp · 零一万物 Yi"
    }
  }
}
```

</details>

---

## 💬 B. ChatGPT Box

偏好设置 → MCP → 直接粘贴(整段覆盖原文件):

<details>
<summary><b>B1. DeepSeek</b></summary>

```json
{
  "mcpServers": {
    "fulladdmax": {
      "command": "uvx",
      "args": ["fulladdmax-mcp"],
      "env": {
        "DEEPSEEK_API_KEY": "sk-你的-deepseek-key",
        "DEEPSEEK_MODEL": "deepseek-chat",
        "FULLADDMAX_LANG": "zh-CN"
      }
    }
  }
}
```

</details>

<details>
<summary><b>B2. Qwen / B3. GLM / B4. Doubao / B5. Kimi / B6. Yi</b></summary>

把上面的 `DEEPSEEK_API_KEY` 整段替换成对应变量即可,变量名见顶部表格。

</details>

---

## 🤖 C. Claude Desktop / Cursor / Cline / Trae

配置文件 `mcp.json` / `claude_desktop_config.json`,用同样格式:

```json
{
  "mcpServers": {
    "fulladdmax": {
      "command": "uvx",
      "args": ["fulladdmax-mcp"],
      "env": {
        "DEEPSEEK_API_KEY": "sk-你的-key"
      }
    }
  }
}
```

> **多 Key 并存**(例如同时用 DeepSeek 做主模型、GLM 做图像):
>
> ```json
> {
>   "mcpServers": {
>     "fulladdmax-deepseek": {
>       "command": "uvx",
>       "args": ["fulladdmax-mcp"],
>       "env": {
>         "DEEPSEEK_API_KEY": "sk-ds",
>         "DEEPSEEK_MODEL": "deepseek-chat"
>       }
>     },
>     "fulladdmax-glm": {
>       "command": "uvx",
>       "args": ["fulladdmax-mcp"],
>       "env": {
>         "ZHIPUAI_API_KEY": "glm",
>         "GLM_MODEL": "glm-4v-plus"
>       }
>     }
>   }
> }
> ```

---

## 🛠️ D. Dify (工作流节点)

工具 → 自定义 → MCP 兼容服务 → 命令行模式:

```bash
# 完整启动命令(给 Dify 填到「启动命令」里)
uvx fulladdmax-mcp
```

环境变量单独填(每个 key 一行):

```
DEEPSEEK_API_KEY=sk-你的-deepseek-key
DEEPSEEK_MODEL=deepseek-chat
FULLADDMAX_LANG=zh-CN
```

---

## 💭 E. NextChat

配置 → MCP 服务器 → 命令行模式:

```
uvx fulladdmax-mcp
```

环境变量(单独区域):

```
DEEPSEEK_API_KEY=sk-你的-deepseek-key
DEEPSEEK_MODEL=deepseek-chat
FULLADDMAX_LANG=zh-CN
```

---

## 🔄 同时使用多个 Key?优先级

服务器启动时按下面顺序找第一个非空 key:

1. `FULLADDMAX_*` (最高)
2. 主机注入(Claude/Cursor)
3. `OPENAI_*`
4. 中国厂商专用(`DEEPSEEK_` / `QWEN_` / `GLM_` / `ARK_` / `MOONSHOT_` / `YI_`)
5. 本地(Ollama / vLLM / LM Studio)

如果你只想要一个厂商生效,**只填它那组变量**就行,其它不用动。

---

## 🧪 验证是否生效

在任何主机里,叫出 `admin(operation="ping")` 工具,看返回里有没有这一行:

```
LLM: configured (source=DEEPSEEK_API_KEY, base_url=https://api.deepseek.com/v1, model=deepseek-chat)
```

看到 `configured` 就 OK。返回 `not configured` 请检查 Key 是否漏了 `sk-` 前缀,或 Key 是否过期。

---

## 🆘 故障排查

| 现象 | 原因 | 修法 |
|---|---|---|
| 主机看不到 fulladdmax 工具 | JSON 缩进错 | 复制后用 JSON 在线校验 |
| `LLM not configured` | Key 没注入 | 检查 `env` 字段,不要写到 `args` |
| 工具调用格式异常 | 国产模型小差异 | 升级到 v0.6.0+,自动 normalize |
| 错误信息是英文 | 没设语言 | 加 `FULLADDMAX_LANG=zh-CN` |
| `MCP server disconnected` | `uvx` 没装 | 改用 `pip install fulladdmax-mcp` + `"command": "fulladdmax-mcp"` |
