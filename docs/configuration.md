# Configuration

## Background updates

Writune checks for updates over HTTPS after a CLI/MCP call, then every six hours while the MCP server or worker remains running. The updater is embedded in the Python package and uses the standard library HTTP client; users do not need Git, a Git checkout, pip, or an external updater to receive compatible code updates. It supports both wheel installations and editable source installations. Checking runs in a detached, windowless process. Set `auto_update: false` in `config.json` or `WRITUNE_AUTO_UPDATE=false` to disable it. `update_check_seconds` (environment: `WRITUNE_UPDATE_CHECK_SECONDS`) controls the interval, with a minimum of 60 seconds.

The updater fetches the configured Writune repository's latest revision metadata and ZIP archive through HTTPS, validates the embedded SHA-256 file manifest, checks dependencies and imports, and replaces package files in the **original installation directory**. Candidate validation uses an OS temporary directory, automatically removed afterwards; no second installation is retained. Runtime locks and status live under `.writune-runtime/updates` inside the original project/package directory. Local package modifications, failed downloads/checksums/imports, or changed dependencies prevent automatic application. A file-write failure restores files already changed. Dependency, Python-requirement, or entry-point changes require a normal package installation. Distribution metadata and separately installed skills are maintained by that installation process.

Workers stop claiming tasks and finish active requests before an update. They resume the durable queue in a fresh process after the package changes. Short service calls wait during this switch. If draining exceeds 90 seconds, the update is deferred and normal work resumes. An existing MCP connection stays open and routes subsequent service calls through a fresh interpreter using the same package directory. New MCP tools or changed tool schemas require reconnecting the client. The package directory must be writable by the running user.

Run `writune update-status` to inspect the last result without network access. Common statuses include `updated`, `up_to_date`, `local_changes`, `manual_install_required`, and `check_failed`. Failed checks leave active requests running and retry after the configured interval. Configuration, credentials, task snapshots, and queue history stay in their existing locations.

## Model-specific settings

Providers can define `models`, keyed by the exact upstream model ID. A model entry can override `capabilities`, `parameters`, `cache`, `tokenizer`, `timeouts` and `prices`. These maps merge with provider defaults. For example:

```json
{"models":{"reasoning-model":{"capabilities":{"temperature":false,"reasoning":true},"parameters":{"reasoning":{"effort":"low"}},"timeouts":{"first_token_timeout":40},"tokenizer":"cl100k_base"}}}
```

Credentials remain in the independent `.env`; models cannot replace provider credentials. Tokenizer names use an optional installed tiktoken encoding; otherwise TPS is explicitly estimated. Cumulative text is counted so SSE boundaries do not inflate tokens.

Task precedence is explicit parameters, named `profiles`, language defaults (including zh/en primary/fallback), then global defaults. `default_primary_model`/`default_secondary_model` are optional global references. `language_defaults` accepts per-language style/options/defaults. `custom_templates` accepts flat strings or nested `styles`/`scenes` maps. Explicit priority `0`, empty local context and empty secondary model stay explicit. Options merge defaults, Profile and explicit keys.

## Batch, encoding, health and accounting

`writune_submit_batch(max_concurrency=1)` imposes a durable batch cap under global/provider/model limits. CLI: `writune submit-batch --markdown-path /allowed/draft.md --max-concurrency 1`. Saturated providers/models do not fill every global slot with waiting requests.

UTF BOM sources are detected automatically. Use `source_encoding="gbk"` (CLI `--source-encoding gbk`) for non-BOM legacy source files. Original bytes remain in `original.source`. For legacy target documents, `writune_apply(target_encoding="gbk")` retains surrounding bytes. Automatic Markdown batches retain encoding/BOM/CRLF/separators via `writune_merge_batch` or `writune merge-batch`; explicit `separator` selects a custom joining format.

Stats use the most recent 50 attempts per provider/model/language/scene; `Service.stats(recent_window=...)` changes the bounded window. Costs are null unless explicit rates and complete reported usage exist. Optional provider `prices[model]` or model `prices` use `input_per_million`, `output_per_million`, `cached_input_per_million` and `currency`. No public price is invented.

`writune_health(probe=true)` makes a short real provider request and may incur usage. Default `probe=false` performs no model call and reports `not_probed`.

Provider `adapter` accepts `responses`, `openai_responses`, or `openai_compatible_responses`; all explicitly use Responses streaming through the official OpenAI SDK. An unsupported adapter is rejected, and the protocol never silently switches to Chat Completions.

Writune keeps its settings outside the project by default. `Settings.load()` uses the platform's user configuration directory for `config.json` and `.env`; `WRITUNE_HOME` selects a different configuration directory. The task database uses `data_dir`, whose default is the platform's user data directory. Original snapshots, polishing outputs and task manifests always use `Settings.task_dir` below `tempfile.gettempdir()`, independently of `data_dir`. Default apply copies and apply records use temporary storage too. An explicit `output_path` selects a final document destination.

The source precedence is: built-in defaults, `config.json`, the selected Writune home's `.env`, `WRITUNE_*` process environment variables, then explicit CLI/API overrides. Writune does not implicitly load a project-local `.env`.

## Initialize

Run `writune init` for interactive setup. It records the provider URL, API credential, language model defaults, allowed workspaces, and other settings. For scripted setup, use non-interactive mode with a JSON file for provider/model settings and supply credentials through `WRITUNE_PROVIDER_<NAME>_API_KEY` (for example, `WRITUNE_PROVIDER_PRIMARY_API_KEY`) in the process environment. Writune writes those credentials to the selected home's `.env` when saving. A key passed through a command-line flag may appear in shell history or process arguments. Do not put an upstream API key in a client MCP configuration file. Consult `writune init --help` for the installed version's exact options.

Use `writune config show` to inspect redacted configuration and `writune doctor` to check local setup. `writune doctor --probe` optionally checks provider availability; it can make network requests. `writune test-provider` performs a provider generation request and may incur upstream usage.

## Provider and model configuration

Model references use `provider/model`, where the provider name is a key under `providers`. A supported JSON shape is:

```json
{
  "providers": {
    "default": {
      "base_url": "https://api.example.com/v1",
      "max_concurrency": 4,
      "capabilities": {},
      "parameters": {},
      "cache": {}
    }
  },
  "zh_primary": "default/chinese-model",
  "zh_secondary": "default/chinese-fallback",
  "en_primary": "default/english-model",
  "en_secondary": "default/english-fallback",
  "allowed_workspaces": ["C:/Users/you/Documents/drafts"],
  "default_scene": "general_prose",
  "default_style": "natural",
  "max_concurrency": 4,
  "max_queue_size": 200,
  "retry_count": 1
}
```

The API key belongs in the Writune home's `.env`, not `config.json`:

```dotenv
WRITUNE_PROVIDER_DEFAULT_API_KEY="replace-with-your-key"
```

For a single provider, the compatibility variables `WRITUNE_PROVIDER_URL` and `WRITUNE_API_KEY` are also accepted. For multiple providers, use names such as `WRITUNE_PROVIDER_OPENAI_URL` and `WRITUNE_PROVIDER_OPENAI_API_KEY`, or configure providers in JSON. API keys are stored separately from the non-secret JSON settings.

## Important settings

| Setting | Default | Purpose |
| --- | ---: | --- |
| `data_dir` | platform user data directory | SQLite queue, state and performance history |
| `Settings.task_dir` | computed under OS temporary storage | Original snapshots, results and temporary task files; scoped by user and database |
| `allowed_workspaces` | empty | Additional directories Writune can read/write |
| `zh_primary`, `en_primary` | empty | Required language-specific model refs |
| `zh_secondary`, `en_secondary` | empty | Optional retry model refs |
| `max_concurrency` | 4 | Global active task limit |
| `max_queue_size` | 200 | Maximum queued task count |
| `retry_count` | 1 | Retries after the first request; default maximum is two attempts |
| `worker_idle_seconds` | 30 | Idle time before worker exit |
| `lease_seconds` | 15 | Worker/task lease interval |
| `task_ttl_hours` | 24 | Retention age before terminal task cleanup |
| `connect_timeout` | 8s | Provider connection timeout |
| `first_token_timeout` | 25s | Wait for first nonempty text |
| `inactivity_timeout` | 20s | Maximum wait without generation progress |
| `total_timeout` | 120s | Maximum duration for one provider attempt |
| `min_tps` | 6 | Minimum generation rate threshold after warm-up |

Provider-level settings can override concurrency, model concurrency, capability flags, request parameters, cache options, tokenizers, and timeouts. A provider base URL must be an absolute HTTP or HTTPS URL.

## Workspaces and paths

Configure every user directory Writune should read or write in `allowed_workspaces`. The private task directory is also authorized. Paths outside these roots are rejected; relative paths resolve from the task directory, so use an absolute path when referring to an ordinary project file.
