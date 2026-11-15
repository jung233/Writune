<div align="center">

# 🛠️ Writune 手动安装与配置指南

**分步完成隔离 Python 环境创建、Provider 与模型初始化，以及客户端 MCP 与 Skill 注册。**

[⬅️ 返回主 README](README.md) &nbsp;·&nbsp; [🇺🇸 English Manual Install Guide](MANUAL_INSTALL.md) &nbsp;·&nbsp; [⚡ 一键自动安装 Prompt](INSTALL_PROMPT.md)

</div>

---

> [!TIP]
> **更推荐自动安装：**
> 直接将 **[INSTALL_PROMPT.md](INSTALL_PROMPT.md)** 交给你的编码助手（Codex、Claude Code 或 OpenCode）即可全自动完成安装与验收。仅当你需要手动控制终端中的每一步命令时，参考本文档。

---

## 1️⃣ 创建隔离虚拟环境

获取源码并进入 Writune 目录，使用 **Python 3.11+**。

### Windows PowerShell

```powershell
.\scripts\install.ps1
# 如需包含开发与测试依赖：
.\scripts\install.ps1 -WithDev
```

### macOS / Linux

```bash
bash scripts/install.sh
# 如需包含开发与测试依赖：
bash scripts/install.sh --dev
```

### 环境安全保障与自定义参数

- **专用 `.writune-venv` 隔离环境：** 所有依赖包均安装到新建的 `.writune-venv` 中，并写入 Writune 归属标识。现有的 Python、Conda、项目虚拟环境、用户 `site-packages`、系统 `PATH`、shell profile 与 Windows 注册表项均保持原样。
- **未归属目录保护：** 若目标路径已存在非 Writune 创建的其他环境，安装脚本会拒绝修改并保护原有文件，同时提示选择新路径。
- **自定义路径：** 在 PowerShell 中可通过 `-VenvPath`、`-PythonExecutable`，或在 Bash 中通过 `--venv`、`--python` 指定路径。
- **使用 `uv` 托管私有 Python：** 若系统缺少合适版本的 Python，或已安装 `uv`，可追加 `-UseUv`（PowerShell）或 `--uv`（Bash），在安装目录内配置私有 managed Python。

---

## 2️⃣ 初始化 Provider、模型与授权工作区

### Windows PowerShell

```powershell
$writuneConfig = Join-Path $env:USERPROFILE '.config\writune'
.\.writune-venv\Scripts\python.exe -m writune init --home $writuneConfig
.\.writune-venv\Scripts\python.exe -m writune doctor --home $writuneConfig
```

### macOS / Linux

```bash
writune_config="$HOME/.config/writune"
.writune-venv/bin/python -m writune init --home "$writune_config"
.writune-venv/bin/python -m writune doctor --home "$writune_config"
```

### 交互式初始化配置项

1. **Responses API 连接信息：** 上游服务地址、端口，以及以隐藏输入方式填写的 API Key（凭据单独保存在配置目录下的 `.env` 中，绝不混入 `config.json`）。
2. **中英文首选模型与备用模型：** 采用 `provider/model` 格式（第一个斜杠右侧保留上游的完整模型 ID），中英文可设为同一模型，也可分别指定。
   - 💡 *推荐选用“说人话”、文风自然的低成本高性价比模型：* 如 **DeepSeek V4.1**、**Gemini Flash / Gemini 4 系列** 等。
3. **授权工作区与运行策略：** 授权读写目录（`allowed_workspaces`）、默认风格、并发上限、连接/首字/停顿/总超时、最低生成速率（`min_tps`）、重试次数及临时任务保留时长（`task_ttl_hours`）。

> 自动化或脚本安装可使用 `init --non-interactive --config-file <path>`。完整的 JSON 配置结构及逐模型覆盖参数，详见 [配置说明](docs/configuration.md)。

---

## 3️⃣ 自动注册客户端 MCP 与配套 Skill

注册脚本会安全地合并 Writune 条目，保留客户端的其他配置以及 Codex 的 TOML 注释，并将修改前的备份保存到系统临时目录。

### Windows PowerShell（接入 Codex）

```powershell
.\.writune-venv\Scripts\python.exe scripts/register_mcp.py --client codex --executable "$PWD/.writune-venv/Scripts/writune.exe" --home $writuneConfig --install-skill
```

### macOS / Linux（接入 Codex）

```bash
.writune-venv/bin/python scripts/register_mcp.py --client codex --executable "$PWD/.writune-venv/bin/writune" --home "$writune_config" --install-skill
```

### 客户端参数与路径说明

- **接入 Claude Code：** 将 `--client codex` 改为 `--client claude-code`。
- **更新已有冲突条目：** 若已存在指向不同路径的 Writune 条目，核实后追加 `--replace` 以更新。
- **自定义客户端配置文件位置：** 追加 `--config <path>`。
- **Windows 无窗口运行：** 注册脚本在 Windows 下会自动选用虚拟环境中的 `pythonw.exe` 启动 MCP；后台 Worker 也同样以无窗口方式启动，不会弹出黑色控制台窗口。

| 客户端 | 用户级 MCP 配置文件 | 用户级 Skill 安装位置 |
| :--- | :--- | :--- |
| **Codex** | `~/.codex/config.toml`（支持 `CODEX_HOME`） | `~/.agents/skills/writune-polish` |
| **Claude Code** | `~/.claude.json` 中的 `mcpServers.writune` | `~/.claude/skills/writune-polish` |
| **OpenCode** | `opencode.json` / `opencode.jsonc` 中的 `mcp.servers.writune` | `.agents/skills/writune-polish` |

注册完成后，让编码助手刷新 MCP 配置并确认工具可被发现即可。手动配置示例及 OpenCode 接入方式详见 **[MCP 集成说明](docs/mcp-integration.md)**。
