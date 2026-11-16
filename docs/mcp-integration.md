# MCP integration

Install Writune from PyPI into a dedicated environment, then run `writune init`. MCP registration is part of the installed package:

```sh
writune mcp register --client codex --install-skill
writune doctor
```

Select `codex`, `claude-code`, or `opencode`; register each client separately. Use the same `--home /absolute/config/directory` for initialization, registration, and diagnostics when choosing a custom configuration home. The saved command uses the current environment's absolute Python path with `-m writune serve --home ...`. Windows uses `pythonw.exe`. Provider credentials stay in Writune's private `.env`, outside the MCP configuration.

## Inspecting and merging configuration

```sh
writune mcp show --client codex
writune mcp register --client codex --config /absolute/client/config.toml
writune skill install --client codex
```

`mcp show` prints a copyable entry without writing to a file. `mcp register` merges only Writune's entry, preserves unrelated fields and Codex TOML comments, and backs up existing configuration to OS temporary storage. Repeating the same registration is idempotent. Inspect a conflicting Writune entry before using `--replace`. Customized skill files are retained rather than overwritten. For fully manual editing and copying, use the [manual guide](../MANUAL_INSTALL.md).

| Client | Default user configuration | Entry |
| --- | --- | --- |
| Codex | `$CODEX_HOME/config.toml`, otherwise `~/.codex/config.toml` | `mcp_servers.writune` |
| Claude Code | `~/.claude.json` | `mcpServers.writune` |
| OpenCode V2 | `$XDG_CONFIG_HOME/opencode/opencode.json`, otherwise `~/.config/opencode/opencode.json` | `mcp.servers.writune` |

OpenCode V2 uses a local command array and `disabled: false`; legacy direct `mcp.writune` entries require migration. The CLI does not rewrite JSONC: edit an existing `opencode.jsonc` manually or explicitly provide a valid JSON configuration path. See the [OpenCode MCP guide](https://opencode.ai/docs/mcp-servers/) and [Claude Code MCP guide](https://code.claude.com/docs/en/mcp). Reconnect the client after registration and confirm its own tool discovery. A CLI stdio check does not establish discovery within a running client.

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
