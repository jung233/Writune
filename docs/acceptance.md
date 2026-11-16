# Verification guide

Create a dedicated environment with `python -m venv .writune-venv`. Install development dependencies using its Python interpreter with `-m pip install -e ".[dev]"`, then run checks from the repository root.

## Automated checks

```powershell
.\.writune-venv\Scripts\python.exe scripts/build_update_manifest.py
.\.writune-venv\Scripts\python.exe -m pytest -q --tb=short
.\.writune-venv\Scripts\python.exe -m build
.\.writune-venv\Scripts\python.exe scripts/verify_package.py
```

On macOS/Linux, replace the interpreter path with `.writune-venv/bin/python`. Package verification selects the current project version from `dist/` and automatically removes its temporary verification directory.

The suite covers Responses streaming, timeouts, retries, cancellation, queue concurrency, worker crash recovery, protected text, file encoding, atomic apply, DOCX structure, temporary storage, client registration, and MCP stdio workflows. Platform-specific tests may be skipped when the filesystem features they require are unavailable; review the reported skip reasons.

Package verification installs the wheel into an isolated temporary target, checks imports and CLI startup, and verifies that installation resources are present and private local data is absent.

## Optional live checks

Set `WRITUNE_HOME` to a private test configuration with valid provider credentials and an authorized scratch workspace. These commands call the configured upstream provider:

```powershell
.\.writune-venv\Scripts\python.exe scripts/live_check.py
.\.writune-venv\Scripts\python.exe scripts/live_mcp_check.py
```

Each script creates a unique subdirectory in the first authorized workspace for its synthetic drafts and reports. Inspect and remove that directory after verification. Credentials remain in the private Writune configuration. Keep generated results, logs, task identifiers, local paths, and provider traces out of source control.

## Commit and release checks

Review `git status --short --untracked-files=all` and the staged diff before committing. Local configuration, credentials, databases, logs, environments, and build outputs are ignored. Ignore rules do not remove already tracked files, so review the staged file list as well.

Run automated checks on each supported platform before claiming platform compatibility. Live upstream success verifies the configured connection and delivery workflow; it does not establish the semantic correctness of polished prose.
