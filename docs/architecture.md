# Architecture

Writune keeps the writing agent, the polishing provider, and file delivery as separate responsibilities:

```text
Codex / Claude Code / OpenCode
              |
       Writune MCP (stdio)
              |
      Shared Service layer
        /             \\
 SQLite task store   Workspace/file operations
        |
 Detached, lease-owned worker
        |
 Responses API provider
```

## Request lifecycle

The MCP server and CLI call the same synchronous `Service` methods. A submit call validates its arguments, snapshots source text into the private task directory, commits a queued row to SQLite, and reserves or wakes a worker. It does not wait for model inference. The returned task ID can be checked later with `writune_status` and `writune_result`.

SQLite uses WAL mode. Queue claims, attempt records, cancellation flags, and worker leases are coordinated with transactions. A worker runs separately from the MCP entrypoint, maintains its lease, applies the configured concurrency limits, and exits after the queue has been idle for the configured interval. On an expired lease, the service can requeue work; if an upstream request may have been in flight, the task records possible duplicate billing.

The data directory contains `writune.sqlite3`, queue state and performance history. Per-task files (`original.txt`, `result.txt`, `result.json`) live in `Settings.task_dir`, a user- and database-scoped directory under the operating system's temporary directory. This separation applies even when `data_dir` points into a project. Default applied copies and apply records also use temporary storage. The OS can reclaim those files, and Writune's TTL cleanup removes expired terminal tasks. Missing task files produce `TASK_FILES_EXPIRED` without issuing another provider request. Upgrade copies retain historical originals.

## Background updates

The embedded coordinator fetches stable release metadata from PyPI and downloads a compatible wheel from `files.pythonhosted.org` over HTTPS. It verifies the PyPI SHA-256, the embedded file manifest, and compatibility before validating imports in temporary storage. Registered workers drain active work, and files are then replaced in the original package directory. Package writes roll back on failure, and the installed wheel metadata and RECORD are refreshed. Neither Git nor pip is invoked at runtime.

Installation-wide call locks and worker locks are stored under `.writune-runtime/updates`. Worker bootstrap acquires the import fence before loading updateable modules. The MCP service proxy detects changed Python sources and executes subsequent service methods in a fresh interpreter through JSON over private pipes, while the original stdio transport remains connected. Dependency or entry-point changes require a full installation; changed MCP schemas require reconnection. See [configuration](configuration.md).

Package-native MCP registration and Skill installation are handled in `writune.integration`. Build preparation copies the canonical repository skill and installation documents into package resources and then computes their update manifest. Before testing or building a release, run `python scripts/build_update_manifest.py` and include the generated resources and manifest. Wheels include the complete CLI setup workflow without requiring a checkout.

## Polishing pipeline

Each task uses a stable prompt prefix for global instructions, language, scene, style, and shared context; the paragraph is variable input. A context ID is content-addressed, so changing the context produces a different ID. Markdown batch segmentation preserves input order and separators, and fenced blocks remain intact as one unit.

Protected spans are masked before the provider call and restored before output validation. The deterministic validator checks for empty, obviously truncated, abnormally short/long, or prefaced output, unresolved protected markers, and changes to protected values or references. These safeguards are not semantic fact checking.

The provider adapter streams Responses API output using Server-Sent Events and monitors first nonempty text, inactivity, total time, and token-rate conditions. Token-rate checks use a configured tokenizer when available and otherwise use an explicit lexical estimate.

## Failure and file delivery

Attempts share one configured retry budget. A configured secondary model is preferred for the retry. If no usable polished result is produced, Writune writes the original snapshot as the result and reports `original_fallback`; this is completed delivery, not successful polishing.

File apply operations accept TXT, Markdown, LaTeX, and DOCX. Text replacements preserve detected encoding and the target's dominant newline convention. Writes are staged and validated before publication; the default applied copy goes beside the temporary task result, while an explicit `output_path` selects the final deliverable. Apply manifests remain in temporary storage even for explicit final outputs. DOCX replacement requires the marker to be the only text in one supported paragraph.

On Windows, MCP registration selects the environment's `pythonw.exe` with `-m writune serve`. Worker startup also selects `pythonw.exe`, uses `CREATE_NO_WINDOW`, a hidden startup configuration and a new process group, and redirects all standard streams. Neither path invokes a shell.

See [configuration](configuration.md), [MCP integration](mcp-integration.md), and [security](security.md) for operational details.
