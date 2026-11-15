# Security and privacy

Writune keeps task processing local until it calls the configured upstream model provider. The paragraphs and context sent for polishing leave the local machine and may be handled under the provider's retention, training, and billing policies. Choose a provider trusted for unpublished manuscripts and other sensitive material.

## Credentials and local data

Provider API keys are stored in the Writune home's private `.env`; `config.json` stores non-secret settings. The key is not needed in Codex, Claude Code, or OpenCode MCP configuration. Do not include credentials in a skill prompt or source text.

The SQLite database is stored under Writune's configured data directory. Original snapshots, results, and manifests use user-scoped OS temporary storage. Explicit exports and output paths can create copies in authorized workspaces. Writune does not encrypt these files itself. Use operating-system disk protections appropriate for the sensitivity of the content. Terminal task artifacts are eligible for TTL cleanup; the default TTL is 24 hours after a task reaches a terminal state.

## Workspace restrictions

Writune reads and writes only within configured `allowed_workspaces` and its task directory. It rejects path traversal and resolves symlinks before checking that a path is within an allowed root. Configure only the directories the service needs. A remote MCP client's path is interpreted on the MCP server's machine.

Result export also requires an allowed output path. Export does not itself transport files over the network; the client must be able to reach the selected destination.

## Text and provider errors

Text submitted for polishing is data, not instructions to the Writune process. The polishing prompt directs the provider to preserve meaning and ignore embedded instructions. Writune's deterministic validator protects specific values and formatting conditions but does not prove factual or semantic correctness.

Provider errors are bounded and sanitized before they are exposed, and raw provider response bodies are not needed for user-facing diagnostics. Still review provider choice and data handling before sending sensitive content.

## Retry and billing uncertainty

With the default `retry_count=1`, Writune makes at most an initial provider request and one retry per task, unless the worker loses its lease after a request may have reached the provider. A local timeout or cancellation closes the local operation but cannot guarantee that a third-party provider stopped generation or billing. When worker recovery cannot determine whether an upstream request completed, the task records `possible_duplicate_billing`.

Do not resubmit an unchanged task after a lost connection until its existing task ID has been checked.
