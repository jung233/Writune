# Writune 全手动安装指南

[返回 README](README.md) · [English](MANUAL_INSTALL.md) · [自动 Prompt](INSTALL_PROMPT.md)

本指南逐项创建环境、填写配置、编辑 MCP 条目并复制 Skill。不要运行项目安装脚本，也不要用 `writune init` 或 `writune mcp register` 代写配置。如果只想通过命令完成配置，请使用 README 中的 PyPI + CLI 流程。

## 1. 创建专用环境并安装包

需要 Python 3.11+。以下 `YOU`、路径、URL 和模型 ID 均为示例，执行前请替换为实际值。请选择一个全新的环境路径，不要覆盖已有环境。命令直接使用新环境的解释器，无需激活，不永久修改 PATH。

Windows PowerShell：

```powershell
python -I -m venv C:/Users/YOU/Tools/writune-env
& C:/Users/YOU/Tools/writune-env/Scripts/python.exe -I -m pip --isolated install --upgrade "writune>=0.1.2"
```

macOS/Linux（macOS 的用户路径通常为 `/Users/YOU`）：

```sh
python3 -I -m venv /home/YOU/tools/writune-env
/home/YOU/tools/writune-env/bin/python -I -m pip --isolated install --upgrade 'writune>=0.1.2'
```

## 2. 手工创建 Writune 配置

使用文件管理器创建一个独立的配置目录，例如 Windows 的 `C:/Users/YOU/.config/writune` 或 macOS/Linux 的 `/home/YOU/.config/writune`。下方选择的目录必须与 MCP 条目中的 `--home` 一致。

在该目录创建 UTF-8 的 `config.json`：

```json
{
  "providers": {"primary": {"base_url": "https://provider.example/v1"}},
  "zh_primary": "primary/ACTUAL_CHINESE_MODEL_ID",
  "en_primary": "primary/ACTUAL_ENGLISH_MODEL_ID",
  "zh_secondary": "",
  "en_secondary": "",
  "allowed_workspaces": ["C:/Users/YOU/Documents/drafts"],
  "auto_update": true,
  "update_check_seconds": 21600,
  "default_style": "natural",
  "max_concurrency": 4,
  "connect_timeout": 8,
  "first_token_timeout": 70,
  "inactivity_timeout": 20,
  "total_timeout": 420,
  "min_tps": 6,
  "retry_count": 1,
  "task_ttl_hours": 24
}
```

将 URL 替换为上游提供的 Responses API Base URL，并保留实际端口和 `/v1` 等路径。将模型 ID 替换为该服务实际支持的名称；`primary/` 左侧为本地 Provider 名，右侧为完整模型 ID。没有备用模型时，保留空字符串。将 `allowed_workspaces` 改为允许 Writune 读写的实际绝对路径；macOS/Linux 使用相应的 `/Users/...` 或 `/home/...` 路径。

同目录另建 `.env`，只存放密钥：

```dotenv
WRITUNE_PROVIDER_PRIMARY_API_KEY="YOUR_PRIVATE_KEY"
```

不要将密钥写入 MCP 配置，也不要放入源码仓库。中英文模型可以相同；独立备用 Provider、多模型参数和超时覆盖见[配置说明](docs/configuration.md)。SQLite 数据目录默认采用平台用户数据目录，原文快照和润色结果默认保存在系统临时目录，不必在项目中创建任务文件夹。

## 3. 手工编辑客户端 MCP 配置

先备份已有客户端配置，然后仅合并 `writune` 条目，保留其他设置。以下为 Windows 示例。在 macOS/Linux 上，将解释器改为 `/home/YOU/tools/writune-env/bin/python`，配置目录改为 `/home/YOU/.config/writune`；其余参数相同。Windows 使用环境中的 `pythonw.exe`，以避免后台弹出控制台。

### Codex

编辑 `~/.codex/config.toml`。若设置了 `CODEX_HOME`，则编辑该目录下的 `config.toml`：

```toml
[mcp_servers.writune]
command = "C:/Users/YOU/Tools/writune-env/Scripts/pythonw.exe"
args = ["-m", "writune", "serve", "--home", "C:/Users/YOU/.config/writune"]
enabled = true
startup_timeout_sec = 20
tool_timeout_sec = 60
```

### Claude Code

编辑 `~/.claude.json`，把以下 `writune` 对象合并进已有 `mcpServers`：

```json
{
  "mcpServers": {
    "writune": {
      "type": "stdio",
      "command": "C:/Users/YOU/Tools/writune-env/Scripts/pythonw.exe",
      "args": ["-m", "writune", "serve", "--home", "C:/Users/YOU/.config/writune"]
    }
  }
}
```

### OpenCode

编辑 `~/.config/opencode/opencode.json`，或编辑已有的 `opencode.jsonc`。如果设置了 `XDG_CONFIG_HOME`，则使用该目录下的 `opencode/opencode.json`。将条目放在 OpenCode V2 的 `mcp.servers.writune` 下：

```json
{
  "mcp": {
    "servers": {
      "writune": {
        "type": "local",
        "command": [
          "C:/Users/YOU/Tools/writune-env/Scripts/pythonw.exe",
          "-m",
          "writune",
          "serve",
          "--home",
          "C:/Users/YOU/.config/writune"
        ],
        "disabled": false
      }
    }
  }
}
```

## 4. 手工复制配套 Skill

完整 Skill 已包含在 pip 安装的包中，目录为：

- Windows：`C:/Users/YOU/Tools/writune-env/Lib/site-packages/writune/resources/skills/writune-polish/`
- macOS/Linux：`/home/YOU/tools/writune-env/lib/python3.11/site-packages/writune/resources/skills/writune-polish/`，把 `python3.11` 换成实际解释器版本。

使用文件管理器或原生复制命令，将整个 `writune-polish` 文件夹（包括 `SKILL.md`、`references` 和 `agents`）复制到以下目标路径：

| 客户端 | 用户级 Skill 目标目录 |
| --- | --- |
| Codex | `~/.agents/skills/writune-polish` |
| Claude Code | `~/.claude/skills/writune-polish` |
| OpenCode | `~/.config/opencode/skills/writune-polish`，或 `$XDG_CONFIG_HOME/opencode/skills/writune-polish` |

如果目标已有定制内容，先比较并自行合并。Skill 提供写作指导，MCP 条目提供工具连接，两者都需要正确配置。

## 5. 验收与后续升级

配置完成后可用 CLI 检查，不会代写上述配置文件：

```powershell
& C:/Users/YOU/Tools/writune-env/Scripts/python.exe -m writune doctor --home C:/Users/YOU/.config/writune
& C:/Users/YOU/Tools/writune-env/Scripts/python.exe -m writune test-provider --home C:/Users/YOU/.config/writune
```

macOS/Linux 使用各自环境中的 Python 路径和配置目录，以相同参数运行。`doctor` 包含真实 stdio 启动检查；`test-provider` 会发送短模型请求并产生使用量。随后重连客户端 MCP，确认工具列表中出现 `writune_submit`、`writune_status` 和 `writune_result`。

包内自动更新只读取 PyPI 正式稳定版本，通过哈希和兼容性校验后在原目录更新，当前请求先完成。`writune update-status` 显示状态。若提示 `manual_install_required`，在同一环境的 Python 后运行 `-m pip install --upgrade writune`，然后重连客户端。自定义 Skill 的独立副本需另行比较更新。要禁用自动更新，在 `config.json` 中设置 `"auto_update": false`。
