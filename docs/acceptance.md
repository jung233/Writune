# Verification guide

Run checks from the repository root after installing development dependencies with `scripts/install.ps1 -WithDev` on Windows or `bash scripts/install.sh --dev` on macOS/Linux.

## Automated checks

```powershell
.\.writune-venv\Scripts\python.exe -m pytest -q --tb=short
.\.writune-venv\Scripts\python.exe -m build
.\.writune-venv\Scripts\python.exe scripts/verify_package.py
```

On macOS/Linux, replace the interpreter path with `.writune-venv/bin/python`. Package verification requires a fresh wheel and source distribution in `dist/`; remove stale distributions before building a different version.

The suite covers Responses streaming, timeouts, retries, cancellation, queue concurrency, worker crash recovery, protected text, file encoding, atomic apply, DOCX structure, temporary storage, client registration, and MCP stdio workflows. Platform-specific tests may skip when their required filesystem features are unavailable; review the reported skip reasons.

Package verification installs the wheel into an isolated temporary target, checks imports and CLI startup, and checks that installation resources are present and private local data is absent.

## Optional live checks

Set `WRITUNE_HOME` to a private test configuration with valid provider credentials and an authorized scratch workspace. These commands call the configured upstream provider:

```powershell
.\.writune-venv\Scripts\python.exe scripts/live_check.py
.\.writune-venv\Scripts\python.exe scripts/live_mcp_check.py
```

Each script creates a unique subdirectory in the first authorized workspace for its synthetic drafts and reports. Inspect and remove that directory after verification. Credentials stay in the private Writune configuration. Keep generated results, logs, task identifiers, local paths, and provider traces out of source control.

## Commit and release checks

Review `git status --short --untracked-files=all` and the staged diff before committing. Local configuration, credentials, databases, logs, environments, and build outputs are ignored. Ignore rules do not remove files that are already tracked, so review the staged file list as well.

Run automated checks on each supported platform before claiming platform compatibility. Live upstream success verifies the configured connection and delivery workflow; it does not establish semantic correctness of polished prose.
