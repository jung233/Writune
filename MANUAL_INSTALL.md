<div align="center">

# 🛠️ Writune Manual Installation Guide

**Step-by-step manual setup for isolated Python environments, Provider initialization, and MCP client registration.**

[⬅️ Back to README](README.en.md) &nbsp;·&nbsp; [🇨🇳 中文手动安装指南](MANUAL_INSTALL.zh-CN.md) &nbsp;·&nbsp; [⚡ One-Step Harness Install](INSTALL_PROMPT.md)

</div>

---

> [!TIP]
> **Prefer an automated installation?**
> Give **[INSTALL_PROMPT.md](INSTALL_PROMPT.md)** to your coding agent (Codex, Claude Code, or OpenCode) and ask it to run the installation task automatically. Use this manual guide only if you want full terminal control over each step.

---

## 1️⃣ Create the Isolated Environment

Obtain the source, enter the Writune directory, and use **Python 3.11+**.

### Windows PowerShell

```powershell
.\scripts\install.ps1
# Or include development & test dependencies:
.\scripts\install.ps1 -WithDev
```


### macOS / Linux

```bash
bash scripts/install.sh
# Or include development & test dependencies:
bash scripts/install.sh --dev
```


### Environment Guarantees & Custom Flags

- **Dedicated `.writune-venv`:** Packages are installed into `.writune-venv` and marked with Writune ownership. Existing Python installations, Conda environments, project virtualenvs, user `site-packages`, system `PATH`, shell profiles, and Windows registry entries remain untouched.
- **Unowned Directory Protection:** If the selected target directory already contains an unowned environment, the script refuses to overwrite it and prompts for a different path.
- **Custom Paths:** Pass `-VenvPath` and `-PythonExecutable` in PowerShell, or `--venv` and `--python` in Bash.
- **Managed Python via `uv`:** When `uv` is available, add `-UseUv` (PowerShell) or `--uv` (Bash) to provision Writune's private managed Python runtime.

---

## 2️⃣ Configure Provider, Models & Authorized Workspaces

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

### What Interactive Setup Configures

1. **Upstream Responses API:** Base URL, port, and hidden API Key input (credentials are saved separately to `.env` in your config directory and are never mixed into `config.json`).
2. **Language-Specific Polishing Models:** Model references use the `provider/model` format (retaining the full upstream model ID to the right of the first slash) for Chinese and English primary and fallback slots.
   - 💡 *Recommended human-sounding models:* **DeepSeek V4.1**, **Gemini Flash / Gemini 4 series**, etc.
3. **Workspaces & Runtime Governance:** Authorized workspace directories (`allowed_workspaces`), default style, concurrency limits, connection/first-token/inactivity/total timeouts, minimum TPS (`min_tps`), retry count, and temporary task file retention (`task_ttl_hours`).

> For scripted or automated initialization, use `init --non-interactive --config-file <path>`. See [Configuration](docs/configuration.md) for full JSON templates and model-level overrides.

---

## 3️⃣ Register MCP & Companion Skill Automatically

The registration script merges the Writune entry, preserves unrelated client settings and Codex TOML comments, and saves a pre-edit backup in OS temporary storage.

### Windows PowerShell (Codex)

```powershell
.\.writune-venv\Scripts\python.exe scripts/register_mcp.py --client codex --executable "$PWD/.writune-venv/Scripts/writune.exe" --home $writuneConfig --install-skill
```

### macOS / Linux (Codex)

```bash
.writune-venv/bin/python scripts/register_mcp.py --client codex --executable "$PWD/.writune-venv/bin/writune" --home "$writune_config" --install-skill
```

### Client Options & Paths

- **Claude Code:** Change `--client codex` to `--client claude-code`.
- **Conflict Resolution:** If a different Writune entry already exists, inspect its path and pass `--replace` to update it.
- **Custom Config File:** Pass `--config <path>` to target a non-default client configuration file.
- **Windowless Execution on Windows:** Registration automatically binds `pythonw.exe` so that both the MCP server and the background worker run without opening a console window.

| Client | User MCP Configuration | User Skill Location |
| :--- | :--- | :--- |
| **Codex** | `~/.codex/config.toml` (honors `CODEX_HOME`) | `~/.agents/skills/writune-polish` |
| **Claude Code** | `mcpServers.writune` in `~/.claude.json` | `~/.claude/skills/writune-polish` |
| **OpenCode** | `mcp.servers.writune` in `opencode.json` / `opencode.jsonc` | `.agents/skills/writune-polish` |

After registration, reload your agent's MCP configuration and verify tool discovery. For manual JSON/TOML snippets and OpenCode setup, see **[MCP Integration](docs/mcp-integration.md)**.
