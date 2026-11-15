# MCP integration

Writune exposes a local stdio MCP server. Start it with `writune serve`; use the absolute executable path in a client's configuration when the executable is not on that client's PATH.

## Codex

Codex reads MCP server configuration from `~/.codex/config.toml` by default. Add a stdio server entry and replace the executable path with the path in the Writune installation:

```toml
[mcp_servers.writune]
command = "C:/path/Writune/.writune-venv/Scripts/pythonw.exe"
args = ["-m", "writune", "serve"]
startup_timeout_sec = 20
tool_timeout_sec = 60
```

On Linux, for example:

```toml
[mcp_servers.writune]
command = "/home/you/Writune/.writune-venv/bin/writune"
args = ["serve"]
startup_timeout_sec = 20
tool_timeout_sec = 60
```

The absolute path is illustrative. Never put the provider API key in this file; Writune loads it from its own configuration directory. Restart/reload the client MCP connection after editing the config. See the [Codex MCP guide](https://developers.openai.com/codex/mcp/) for current configuration options.

`scripts/register_mcp.py --client codex --executable <WRITUNE_EXECUTABLE> --home <CONFIG_HOME>` merges this entry automatically. On Windows the script converts the environment's `writune.exe` path to `pythonw.exe` with `-m writune serve`; no shell or visible console is involved. Use `--client claude-code` to update `~/.claude.json` instead, and `--install-skill` to install the full companion skill. Existing unrelated settings are retained, backups use OS temporary storage, and `--replace` explicitly updates a conflicting Writune entry. The complete harness workflow is in [INSTALL_PROMPT.md](../INSTALL_PROMPT.md).

## Claude Code

Install the local stdio server with the executable from the Writune environment:

```sh
claude mcp add --scope user writune -- /absolute/path/to/writune serve
```

For project scope, use `--scope project` instead of `--scope user`. On Windows, use the environment's `pythonw.exe` directly with arguments `-m writune serve --home <CONFIG_HOME>`; the registration script handles this automatically. Check `claude mcp list` or `/mcp` in a Claude Code session. See the [Claude Code MCP guide](https://code.claude.com/docs/en/mcp).

## OpenCode

Add a local server entry to `opencode.json` or `opencode.jsonc`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "writune": {
        "type": "local",
        "command": ["/absolute/path/to/writune", "serve"]
      }
    }
  }
}
```

On Windows, use a command array such as `["C:/path/Writune/.writune-venv/Scripts/pythonw.exe", "-m", "writune", "serve"]` to avoid a console window. See the [OpenCode MCP servers guide](https://opencode.ai/docs/mcp-servers) for current local-server settings.

## Writune tools

| Tool | Purpose |
| --- | --- |
| `writune_create_context` | Create or update a content-addressed writing context |
| `writune_submit` | Submit text or an authorized source path |
| `writune_submit_batch` | Submit item lists, source paths, or a Markdown file for segmentation |
| `writune_status` | Inspect one task or a batch |
| `writune_result` | Read task outcome and result/manifest paths; inline text is opt-in |
| `writune_apply` | Insert a result into one placeholder in a supported file |
| `writune_export` | Copy a completed result to a destination inside an allowed workspace |
| `writune_merge_batch` | Merge completed results in source order, retaining automatic Markdown separators and BOM |
| `writune_cancel` | Request task cancellation |
| `writune_health` | Report initialization, worker, queue, storage, and provider configuration state |

Task states are `queued`, `running`, `completed`, `failed`, and `cancelled`. Result outcomes are `polished`, `original_fallback`, and `none`. In particular, `completed` with `original_fallback` means that original text was safely delivered after polishing attempts failed.

## Async compatibility

Writune's `task_id`, `batch_id`, submit/status/result/cancel tools are application-level Writune operations. They are not the MCP Tasks extension `io.modelcontextprotocol/tasks`. The current Python SDK migration guide says its previous experimental Tasks runtime was removed and the reintroduced extension is not implemented by that SDK. Writune pins `mcp>=1.20,<2` and keeps its own polling-compatible tools.

References: [official SDK migration guide](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md) and [SDK roadmap](https://github.com/modelcontextprotocol/python-sdk/blob/main/ROADMAP.md). This build was tested with SDK 1.30.0; it does not import the older experimental Tasks runtime. The FastMCP adapter in `mcp_server.create_server` keeps protocol integration separate from the shared task service.

If the MCP host is remote or cannot see the server's filesystem, a returned result path is not automatically transferable. `writune_export` writes to a Writune-authorized destination; it helps only if that destination is shared with the client. Inline result text is an opt-in fallback and can reduce the token-saving benefit.

Without a shared path, explicitly use `writune_result(task_id=..., inline_text=true)` only when content transfer is needed. A server path is not a download URL. Writune does not automatically send contents, create a public download server or add a separate Base64 data outlet.

`writune_submit_batch` accepts `max_concurrency`; omitted item fields inherit batch defaults. `writune_health(probe=true)` explicitly tests provider availability and may incur usage. `create_server(tasks_adapter=...)` accepts a future `TaskExtensionAdapter`; no native Tasks support is advertised without an explicitly installed compatible adapter.

## Tool-list verification

After client setup, inspect the client's MCP server status and available Writune tools. This repository's structural skill tests do not test real Codex, Claude Code, or OpenCode discovery.
