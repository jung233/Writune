# Writune Fully Manual Installation

[Back to README](README.en.md) · [中文](MANUAL_INSTALL.zh-CN.md) · [Automatic Prompt](INSTALL_PROMPT.md)

This guide creates the environment, settings, MCP entries, and skill files one step at a time. It uses no project installation scripts and does not call `writune init` or `writune mcp register` to write configuration. For CLI-assisted setup, follow the PyPI route in the README.

## 1. Create an environment and install the package

Use Python 3.11+. Replace `YOU`, paths, URLs, and model IDs with actual values before running the commands. Choose a new environment directory and keep any existing environment intact. The commands invoke the new interpreter directly, so you do not need to activate the environment or make permanent PATH changes.

Windows PowerShell:

```powershell
python -I -m venv C:/Users/YOU/Tools/writune-env
& C:/Users/YOU/Tools/writune-env/Scripts/python.exe -I -m pip --isolated install --upgrade "writune>=0.1.2"
```

macOS/Linux (on macOS, user paths usually begin with `/Users/YOU`):

```sh
python3 -I -m venv /home/YOU/tools/writune-env
/home/YOU/tools/writune-env/bin/python -I -m pip --isolated install --upgrade 'writune>=0.1.2'
```

## 2. Write Writune configuration files

Create a private configuration directory using a file manager—for example, `C:/Users/YOU/.config/writune` on Windows or `/home/YOU/.config/writune` on Linux. Use this same directory for the `--home` argument in the MCP entry.

Create a UTF-8 `config.json`:

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

Replace the URL with the provider's Responses API base URL, including its actual port and any `/v1` path. Replace model IDs with names supported by that provider. In a `primary/model-id` reference, the left side identifies the local provider and the right side preserves the complete upstream model ID. Leave unused fallback slots empty. Replace `allowed_workspaces` with the actual absolute directories Writune may read and write; use `/Users/...` or `/home/...` paths on macOS/Linux.

Create a separate `.env` in the same directory for credentials:

```dotenv
WRITUNE_PROVIDER_PRIMARY_API_KEY="YOUR_PRIVATE_KEY"
```

Do not store the key in MCP client configuration or in source control. Chinese and English may share a model. See [configuration](docs/configuration.md) for independent fallback providers and model-specific settings. SQLite uses the platform user data directory by default; task snapshots and results use OS temporary storage.

## 3. Edit client MCP configuration

Back up the existing client file, then merge only the Writune entry while preserving the other settings. The examples below are for Windows. On macOS/Linux, replace the interpreter with `/home/YOU/tools/writune-env/bin/python` and the configuration home with `/home/YOU/.config/writune`. Keep the other arguments. On Windows, use the environment's `pythonw.exe` to run without a console window.

### Codex

Edit `~/.codex/config.toml`, or `config.toml` under `CODEX_HOME` when that variable is set:

```toml
[mcp_servers.writune]
command = "C:/Users/YOU/Tools/writune-env/Scripts/pythonw.exe"
args = ["-m", "writune", "serve", "--home", "C:/Users/YOU/.config/writune"]
enabled = true
startup_timeout_sec = 20
tool_timeout_sec = 60
```

### Claude Code

Edit `~/.claude.json` and merge the Writune object into the existing `mcpServers`:

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

Edit `~/.config/opencode/opencode.json`, or an existing `opencode.jsonc`. With `XDG_CONFIG_HOME`, use its `opencode/opencode.json` location. For OpenCode V2, place the entry under `mcp.servers.writune`:

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

## 4. Copy the complete bundled skill

The pip package includes the skill at:

- Windows: `C:/Users/YOU/Tools/writune-env/Lib/site-packages/writune/resources/skills/writune-polish/`.
- macOS/Linux: `/home/YOU/tools/writune-env/lib/python3.11/site-packages/writune/resources/skills/writune-polish/`; replace `python3.11` with the actual interpreter version.

Use a file manager or native copy command to copy the entire `writune-polish` folder, including `SKILL.md`, `references`, and `agents`, to the selected client's destination:

| Client | User skill directory |
| --- | --- |
| Codex | `~/.agents/skills/writune-polish` |
| Claude Code | `~/.claude/skills/writune-polish` |
| OpenCode | `~/.config/opencode/skills/writune-polish`, or `$XDG_CONFIG_HOME/opencode/skills/writune-polish` |

If an existing skill has been customized, compare and merge it yourself. The skill provides writing instructions; the MCP entry connects tools. Configure both.

## 5. Verify and maintain the installation

After editing the files, use the CLI for verification:

```powershell
& C:/Users/YOU/Tools/writune-env/Scripts/python.exe -m writune doctor --home C:/Users/YOU/.config/writune
& C:/Users/YOU/Tools/writune-env/Scripts/python.exe -m writune test-provider --home C:/Users/YOU/.config/writune
```

On macOS/Linux, use your environment's Python and configuration directory with the same arguments. `doctor` includes a real stdio startup check. `test-provider` sends a short model request and consumes provider usage. Reconnect the client MCP, then confirm that `writune_submit`, `writune_status`, and `writune_result` are listed.

The embedded updater checks for stable releases published on PyPI, verifies hashes and compatibility, finishes active work, and updates the original directory. Inspect `writune update-status`. If it reports `manual_install_required`, use the same interpreter with `-m pip install --upgrade writune`, then reconnect the client. When updating separately installed customized skill copies, compare them. Disable automatic updates by setting `"auto_update": false` in `config.json`.
