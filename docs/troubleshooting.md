# Troubleshooting

## The Writune tools are missing from the client

Check that the MCP server is configured with the correct absolute `writune` executable path and the `serve` argument. Confirm that the relevant environment contains the package and dependencies, then restart or reload the client's MCP connection. Check the client's MCP status view or tool list. Installing `writune-polish` alone does not register the MCP server.

## Initialization or provider configuration fails

Run `writune config show` to inspect redacted values and `writune doctor` for local checks. Writune reads `config.json` and `.env` from its user config directory; it does not implicitly read a project-local `.env`. Set `WRITUNE_HOME` if the CLI and MCP server should use a different configuration directory.

Model names must use `provider/model`, and the provider portion must match a key in `providers`. Check that the key is in the Writune home's `.env`, that the base URL is absolute HTTP(S), and that the server can reach the provider. Use `writune doctor --probe` or `writune test-provider` only when a network request is acceptable.

## A file path is rejected

Add the intended directory to `allowed_workspaces`. Relative paths resolve from Writune's task directory; use an absolute path for a normal project file. Paths containing `..` or resolving through a symlink outside an allowed root are rejected by design.

## Apply reports a missing or repeated placeholder

Use a unique marker of the form `{{WRITUNE:RESULTS_001}}`. For TXT, Markdown, and LaTeX it must occur exactly once. For DOCX it must be the only text in a supported paragraph. Check that `placeholder_id` is the identifier alone, such as `RESULTS_001`, not the full marker. Keep the source file; apply defaults to a copy in the temporary task directory. Specify `output_path` for a final document, and explicitly enable overwrite to replace an existing destination.

## Temporary files expired

`TASK_FILES_EXPIRED` means the OS or a cleanup operation removed a task's temporary files. Submit the source again to create a new task. Retain final documents at an explicit `output_path`.

## Windows console flashes

Use the installed environment's `pythonw.exe` as the MCP command with `-m writune serve --home <CONFIG_HOME>`. `scripts/register_mcp.py` selects this entry automatically on Windows. Register the command directly; a PowerShell, cmd, `start` or terminal wrapper is unnecessary. Worker startup is windowless as well. Harness-side diagnostics must use hidden process creation and redirected streams.

## The result path is inaccessible

Task paths belong to the MCP server's filesystem. Use `writune_export` to copy a completed result into an allowed workspace that the client can access. If there is no shared path, use opt-in inline result text only when needed and permitted.

## ETA is unavailable or a task used the original

Cold-start estimates can be `null` because Writune has no successful history for that provider/model/language/scene. Estimates are not deadlines. Check the result's `outcome`, `error`, and `possible_duplicate_billing`; `original_fallback` means the original was delivered, not that the text was polished. Check an existing task ID before resubmitting after a disconnect.

## MCP Tasks are not listed

Writune uses its own submit/status/result/cancel tools. It does not advertise `io.modelcontextprotocol/tasks`; use `writune_status` with the Writune task or batch ID.
