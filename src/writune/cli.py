"""Typer command-line interface backed by the shared Writune service."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import typer
from pydantic import SecretStr, ValidationError

from writune.config import ProviderConfig, Settings
from writune.integration import mcp_app, skill_app
from writune.service import Service, split_model

app = typer.Typer(name="writune", no_args_is_help=True, help="Asynchronous AI writing polisher")
config_app = typer.Typer(name="config", no_args_is_help=True, help="Inspect or update Writune settings")
app.add_typer(config_app, name="config")
app.add_typer(mcp_app, name="mcp")
app.add_typer(skill_app, name="skill")


@app.callback(invoke_without_command=True)
def main(version: bool = typer.Option(False, "--version", is_eager=True)) -> None:
    """Configure and operate the installed Writune package."""
    if version:
        from writune import __version__
        typer.echo(__version__)
        raise typer.Exit()


def _settings(home: Path | None) -> Settings:
    return Settings.load(home)


def _service(home: Path | None, *, start_worker: bool = True) -> Service:
    from writune.updates import UpdatingService
    return UpdatingService(_settings(home), start_worker=start_worker)


def _model_ref(provider_name: str, model_name: str) -> str:
    if not model_name:
        return ""
    return model_name if "/" in model_name else f"{provider_name}/{model_name}"


def _url_with_port(value: str, port: int | None) -> str:
    url = value.strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("provider URL must be an absolute http or https URL")
    if port is None:
        return url.rstrip("/")
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), parts.query, parts.fragment))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"could not read JSON config file: {exc}") from exc
    if not isinstance(value, dict):
        raise typer.BadParameter("config file must contain a JSON object")
    return value


def _merge_dicts(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Merge nested config objects so partial CLI config files keep defaults."""

    merged = dict(base)
    for key, value in update.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(current, value)
        else:
            merged[key] = value
    return merged


def _init_settings(
    *,
    home: Path | None,
    config_file: Path | None,
    non_interactive: bool,
    primary_url: str | None,
    primary_port: int | None,
    api_key: str | None,
    backup_url: str | None,
    backup_port: int | None,
    backup_api_key: str | None,
    zh_primary_model: str | None,
    zh_fallback_model: str | None,
    en_primary_model: str | None,
    en_fallback_model: str | None,
    default_style: str | None,
    max_concurrency: int | None,
    connect_timeout: float | None,
    first_token_timeout: float | None,
    min_tps: float | None,
    inactivity_timeout: float | None,
    total_timeout: float | None,
    retry_count: int | None,
    data_dir: Path | None,
    task_ttl_hours: float | None,
    allowed_workspaces: list[Path] | None,
) -> Settings:
    existing = Settings.load(home)
    payload: dict[str, Any] = existing.model_dump(mode="python")
    if config_file is not None:
        payload = _merge_dicts(payload, _read_json(config_file))
        # Preserve Settings.load's environment-over-JSON secret precedence even
        # when the explicit input JSON is outside Writune's config directory.
        providers = dict(payload.get("providers") or {})
        for provider_name, configured in existing.providers.items():
            secret = configured.api_key.get_secret_value()
            if not secret:
                continue
            current = providers.get(provider_name)
            current_data = current.model_dump(mode="python") if isinstance(current, ProviderConfig) else dict(current or {})
            current_data["api_key"] = SecretStr(secret)
            providers[provider_name] = current_data
        payload["providers"] = providers

    explicit = {
        "default_style": default_style,
        "max_concurrency": max_concurrency,
        "connect_timeout": connect_timeout,
        "first_token_timeout": first_token_timeout,
        "min_tps": min_tps,
        "inactivity_timeout": inactivity_timeout,
        "total_timeout": total_timeout,
        "retry_count": retry_count,
        "data_dir": data_dir,
        "task_ttl_hours": task_ttl_hours,
        "allowed_workspaces": allowed_workspaces,
    }
    payload.update({key: value for key, value in explicit.items() if value is not None})

    has_cli_provider_fields = any(
        value is not None
        for value in (primary_url, primary_port, api_key, backup_url, backup_port, backup_api_key)
    )
    if non_interactive:
        if has_cli_provider_fields:
            current_primary = payload.get("providers", {}).get("primary", {})
            if isinstance(current_primary, ProviderConfig):
                current_primary = current_primary.model_dump(mode="python")
            current_primary = dict(current_primary or {})
            resolved_primary_url = primary_url or current_primary.get("base_url", "")
            resolved_primary_key = api_key or current_primary.get("api_key", "")
            if isinstance(resolved_primary_key, SecretStr):
                resolved_primary_key = resolved_primary_key.get_secret_value()
            if not (resolved_primary_url and resolved_primary_key):
                raise typer.BadParameter(
                    "a primary URL and API key are required; use --primary-url/--api-key or provide them via config/.env"
                )
            current_backup = payload.get("providers", {}).get("backup", {})
            if isinstance(current_backup, ProviderConfig):
                current_backup = current_backup.model_dump(mode="python")
            resolved_backup_url = backup_url
            if resolved_backup_url is None and (backup_port is not None or backup_api_key is not None):
                resolved_backup_url = dict(current_backup or {}).get("base_url")
            _merge_provider_options(
                payload,
                resolved_primary_url,
                primary_port,
                resolved_primary_key,
                resolved_backup_url,
                backup_port,
                backup_api_key,
            )
        for field, value in (
            ("zh_primary", _model_ref("primary", zh_primary_model) if zh_primary_model else None),
            ("zh_secondary", _model_ref("backup", zh_fallback_model) if zh_fallback_model is not None else None),
            ("en_primary", _model_ref("primary", en_primary_model) if en_primary_model else None),
            ("en_secondary", _model_ref("backup", en_fallback_model) if en_fallback_model is not None else None),
        ):
            if value is not None:
                payload[field] = value
        settings = Settings(**payload)
        _require_initialized(settings)
        settings.save(home)
        return settings

    if config_file is None:
        primary_url = primary_url or typer.prompt("Primary upstream URL", default="http://127.0.0.1/v1")
        parsed = urlsplit(primary_url)
        default_primary_port = parsed.port or (443 if parsed.scheme == "https" else 8317)
        primary_port = primary_port if primary_port is not None else typer.prompt(
            "Primary upstream port", default=default_primary_port, type=int
        )
        api_key = api_key if api_key is not None else typer.prompt("Primary API key", hide_input=True)
        backup_url = backup_url if backup_url is not None else typer.prompt(
            "Backup upstream URL (leave blank to disable)", default=""
        )
        if backup_url:
            backup_parts = urlsplit(backup_url)
            backup_port = backup_port if backup_port is not None else typer.prompt(
                "Backup upstream port", default=backup_parts.port or (443 if backup_parts.scheme == "https" else 8318), type=int
            )
            backup_api_key = backup_api_key if backup_api_key is not None else typer.prompt(
                "Backup API key (blank to reuse primary key)", hide_input=True, default=""
            )
        zh_primary_model = zh_primary_model or typer.prompt("Chinese primary model")
        zh_fallback_model = zh_fallback_model if zh_fallback_model is not None else typer.prompt(
            "Chinese fallback model (blank to disable)", default=""
        )
        en_primary_model = en_primary_model or typer.prompt("English primary model")
        en_fallback_model = en_fallback_model if en_fallback_model is not None else typer.prompt(
            "English fallback model (blank to disable)", default=""
        )
        default_style = default_style or typer.prompt("Default polishing style", default="natural")
        max_concurrency = max_concurrency if max_concurrency is not None else typer.prompt("Max concurrency", default=4, type=int)
        connect_timeout = connect_timeout if connect_timeout is not None else typer.prompt("Connect timeout (seconds)", default=8.0, type=float)
        first_token_timeout = first_token_timeout if first_token_timeout is not None else typer.prompt("First text token timeout (seconds)", default=25.0, type=float)
        min_tps = min_tps if min_tps is not None else typer.prompt("Minimum TPS", default=6.0, type=float)
        inactivity_timeout = inactivity_timeout if inactivity_timeout is not None else typer.prompt("Generation inactivity timeout (seconds)", default=20.0, type=float)
        total_timeout = total_timeout if total_timeout is not None else typer.prompt("Total request timeout (seconds)", default=120.0, type=float)
        retry_count = retry_count if retry_count is not None else typer.prompt("Retry count", default=1, type=int)
        data_dir = data_dir or Path(typer.prompt("Queue and statistics directory (task files use OS temp)", default=str(existing.data_dir)))
        task_ttl_hours = task_ttl_hours if task_ttl_hours is not None else typer.prompt("Task file retention (hours)", default=24.0, type=float)
        if allowed_workspaces is None:
            workspace_text = typer.prompt(
                "Allowed workspace directories (separate multiple paths with semicolons)",
                default=";".join(str(path) for path in existing.allowed_workspaces),
            )
            allowed_workspaces = [Path(part.strip()) for part in workspace_text.split(";") if part.strip()]


    if has_cli_provider_fields or config_file is None:
        if not primary_url or not api_key:
            raise typer.BadParameter("a primary URL and API key are required")
        _merge_provider_options(payload, primary_url, primary_port, api_key, backup_url, backup_port, backup_api_key)
    for field, value in (
        ("zh_primary", _model_ref("primary", zh_primary_model or "")),
        ("zh_secondary", _model_ref("backup", zh_fallback_model or "")),
        ("en_primary", _model_ref("primary", en_primary_model or "")),
        ("en_secondary", _model_ref("backup", en_fallback_model or "")),
    ):
        if value:
            payload[field] = value
    for key, value in (
        ("default_style", default_style),
        ("max_concurrency", max_concurrency),
        ("connect_timeout", connect_timeout),
        ("first_token_timeout", first_token_timeout),
        ("min_tps", min_tps),
        ("inactivity_timeout", inactivity_timeout),
        ("total_timeout", total_timeout),
        ("retry_count", retry_count),
        ("data_dir", data_dir),
        ("task_ttl_hours", task_ttl_hours),
        ("allowed_workspaces", allowed_workspaces),
    ):
        if value is not None:
            payload[key] = value
    settings = Settings(**payload)
    _require_initialized(settings)
    settings.save(home)
    return settings


def _merge_provider_options(
    payload: dict[str, Any],
    primary_url: str,
    primary_port: int | None,
    api_key: str,
    backup_url: str | None,
    backup_port: int | None,
    backup_api_key: str | None,
) -> None:
    providers = dict(payload.get("providers") or {})

    def provider_values(name: str) -> dict[str, Any]:
        current = providers.get(name)
        if isinstance(current, ProviderConfig):
            return current.model_dump(mode="python")
        if isinstance(current, dict):
            return dict(current)
        return {}

    primary_values = provider_values("primary")
    primary_values.update(base_url=_url_with_port(primary_url, primary_port), api_key=SecretStr(api_key))
    providers["primary"] = ProviderConfig(**primary_values)
    if backup_url:
        backup_values = provider_values("backup")
        backup_key: str | SecretStr | None
        if backup_api_key is not None:
            backup_key = backup_api_key or api_key
        else:
            backup_key = backup_values.get("api_key") or api_key
        backup_values.update(
            base_url=_url_with_port(backup_url, backup_port),
            api_key=SecretStr(backup_key) if isinstance(backup_key, str) else backup_key,
        )
        providers["backup"] = ProviderConfig(**backup_values)
    elif backup_url == "" and "backup" in providers:
        providers.pop("backup")
    payload["providers"] = providers


def _require_initialized(settings: Settings) -> None:
    missing = []
    if not settings.zh_primary:
        missing.append("Chinese primary model")
    if not settings.en_primary:
        missing.append("English primary model")
    for label, model_ref in (
        ("Chinese primary", settings.zh_primary),
        ("Chinese fallback", settings.zh_secondary),
        ("English primary", settings.en_primary),
        ("English fallback", settings.en_secondary),
    ):
        if not model_ref:
            continue
        try:
            provider_name, _ = split_model(model_ref)
            provider = settings.providers.get(provider_name)
        except ValueError:
            provider = None
            provider_name = "unknown"
        if not provider or not provider.base_url:
            missing.append(f"{label} model provider URL ({provider_name})")
        elif not provider.api_key.get_secret_value():
            missing.append(f"{label} model provider API key ({provider_name})")
    if missing:
        raise typer.BadParameter("missing required initialization values: " + ", ".join(missing))


@app.command()
def init(
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Do not prompt; use a JSON config or options."),
    home: Path | None = typer.Option(None, "--home", "--config-dir", help="Writune configuration directory."),
    config_file: Path | None = typer.Option(None, "--config-file", help="Read settings from a JSON file."),
    primary_url: str | None = typer.Option(None, "--primary-url", help="Primary Responses API base URL."),
    primary_port: int | None = typer.Option(None, "--primary-port", min=1, max=65535),
    api_key: str | None = typer.Option(None, "--api-key", help="Primary provider API key."),
    backup_url: str | None = typer.Option(None, "--backup-url", help="Optional fallback provider base URL."),
    backup_port: int | None = typer.Option(None, "--backup-port", min=1, max=65535),
    backup_api_key: str | None = typer.Option(None, "--backup-api-key", help="Optional fallback provider API key."),
    zh_primary_model: str | None = typer.Option(None, "--zh-primary-model"),
    zh_fallback_model: str | None = typer.Option(None, "--zh-fallback-model"),
    en_primary_model: str | None = typer.Option(None, "--en-primary-model"),
    en_fallback_model: str | None = typer.Option(None, "--en-fallback-model"),
    default_style: str | None = typer.Option(None, "--default-style"),
    max_concurrency: int | None = typer.Option(None, "--max-concurrency", min=1),
    connect_timeout: float | None = typer.Option(None, "--connect-timeout", min=0),
    first_token_timeout: float | None = typer.Option(None, "--first-token-timeout", min=0),
    min_tps: float | None = typer.Option(None, "--min-tps", min=0),
    inactivity_timeout: float | None = typer.Option(None, "--inactivity-timeout", min=0),
    total_timeout: float | None = typer.Option(None, "--total-timeout", min=0),
    retry_count: int | None = typer.Option(None, "--retry-count", min=0),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    task_ttl_hours: float | None = typer.Option(None, "--task-ttl-hours", min=0),
    allowed_workspaces: list[Path] | None = typer.Option(None, "--allowed-workspace", help="Repeat for each authorized workspace."),
) -> None:
    """Create the user configuration interactively or from explicit values."""

    try:
        settings = _init_settings(
            home=home,
            config_file=config_file,
            non_interactive=non_interactive,
            primary_url=primary_url,
            primary_port=primary_port,
            api_key=api_key,
            backup_url=backup_url,
            backup_port=backup_port,
            backup_api_key=backup_api_key,
            zh_primary_model=zh_primary_model,
            zh_fallback_model=zh_fallback_model,
            en_primary_model=en_primary_model,
            en_fallback_model=en_fallback_model,
            default_style=default_style,
            max_concurrency=max_concurrency,
            connect_timeout=connect_timeout,
            first_token_timeout=first_token_timeout,
            min_tps=min_tps,
            inactivity_timeout=inactivity_timeout,
            total_timeout=total_timeout,
            retry_count=retry_count,
            data_dir=data_dir,
            task_ttl_hours=task_ttl_hours,
            allowed_workspaces=allowed_workspaces,
        )
    except (ValueError, ValidationError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Writune configuration saved.")
    typer.echo(f"Config: {settings.config_path}")
    primary_names = list(
        dict.fromkeys(
            split_model(model_ref)[0]
            for model_ref in (settings.zh_primary, settings.en_primary)
            if model_ref
        )
    )
    for provider_name in primary_names:
        provider = settings.providers.get(provider_name)
        typer.echo(f"Provider {provider_name}: {provider.base_url if provider else 'not configured'}")
    typer.echo(f"Chinese models: {settings.zh_primary} / {settings.zh_secondary or 'disabled'}")
    typer.echo(f"English models: {settings.en_primary} / {settings.en_secondary or 'disabled'}")


@config_app.command("show")
def config_show(home: Path | None = typer.Option(None, "--home", "--config-dir")) -> None:
    """Print settings as JSON with credentials masked."""

    settings = _settings(home)
    typer.echo(json.dumps(settings.redacted_dict(), ensure_ascii=False, indent=2, default=str))


def _parse_config_value(key: str, raw: str) -> Any:
    if key.rsplit(".", 1)[-1] == "api_key":
        return SecretStr(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Setting name or dotted path such as providers.primary.base_url."),
    value: str | None = typer.Argument(None, help="Value; API keys are prompted without echo when omitted."),
    home: Path | None = typer.Option(None, "--home", "--config-dir"),
) -> None:
    """Update one setting and persist provider credentials in the private .env file."""

    settings = _settings(home)
    if key.split(".", 1)[0] not in Settings.model_fields:
        raise typer.BadParameter(f"unknown setting: {key.split('.', 1)[0]}")
    if value is None:
        value = typer.prompt(f"Value for {key}", hide_input=key.rsplit(".", 1)[-1] == "api_key")
    data = settings.model_dump(mode="python")
    parts = key.split(".")
    if not parts or any(not part for part in parts):
        raise typer.BadParameter("setting path must not contain empty components")
    target: dict[str, Any] = data
    for part in parts[:-1]:
        current = target.get(part)
        if current is None:
            current = {}
            target[part] = current
        if not isinstance(current, dict):
            raise typer.BadParameter(f"{part} is not a nested setting")
        target = current
    target[parts[-1]] = _parse_config_value(key, value)
    try:
        updated = Settings(**data)
    except ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    updated.save(settings.home_dir)
    typer.echo(f"Updated {key}; saved {updated.config_path}")


def _check_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".writune-doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _provider_probe(settings: Settings) -> tuple[bool, str]:
    """Perform a short Responses API request using a configured primary model."""

    model_ref = settings.en_primary or settings.zh_primary
    if not model_ref:
        return False, "no primary model is configured"
    try:
        provider_name, model_name = split_model(model_ref)
        provider = settings.providers[provider_name]
        from writune.polishing.prompt import Prompt
        from writune.providers.responses import ResponsesProvider

        async def generate() -> Any:
            return await ResponsesProvider(provider=provider, settings=settings).generate(
                model_name,
                Prompt(prefix="Return only the requested test phrase.", input="Reply with exactly: Writune provider connection OK"),
                options={"max_output_tokens": 32},
            )

        result = asyncio.run(generate())
        if not result.text.strip():
            return False, "provider returned empty output"
        return True, f"generation succeeded ({len(result.text)} output characters)"
    except Exception as exc:  # errors are already sanitized by the provider adapter
        return False, f"{type(exc).__name__}: {exc}"


def _mcp_startup_probe(settings: Settings) -> tuple[bool, str]:
    """Initialize the real stdio entry point without making a provider request."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def check() -> int:
        from writune.runtime import windowless_python
        parameters=StdioServerParameters(command=windowless_python(),args=['-m','writune','serve','--home',str(settings.home_dir)],env=os.environ.copy())
        with open(os.devnull,'w') as errlog:
            async with asyncio.timeout(15):
                async with stdio_client(parameters,errlog=errlog) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        discovered=await session.list_tools()
                        names={tool.name for tool in discovered.tools}
                        if not {'writune_submit','writune_status','writune_result','writune_apply','writune_cancel'}.issubset(names):
                            raise ValueError('Required tools are missing')
                        return len(names)
    try:
        return True,f'real stdio initialized; {asyncio.run(check())} tools discovered'
    except Exception:
        return False,'stdio initialization or required tool discovery failed'


@app.command()
def doctor(
    probe: bool = typer.Option(False, "--probe", help="Make a real, short Responses API request."),
    home: Path | None = typer.Option(None, "--home", "--config-dir"),
) -> None:
    """Check Python, dependencies, settings, credentials, storage, provider, and skill."""

    checks: list[tuple[str, bool, str]] = []
    checks.append(("python", sys.version_info >= (3, 11), sys.version.split()[0]))
    for package in ("mcp", "typer", "httpx", "pydantic", "pydantic-settings", "python-docx", "python-dotenv", "openai", "tomlkit", "packaging"):
        try:
            version = importlib.metadata.version(package)
            checks.append((f"dependency:{package}", True, version))
        except importlib.metadata.PackageNotFoundError:
            checks.append((f"dependency:{package}", False, "not installed"))
    try:
        settings = _settings(home)
        config_ok = settings.config_path.exists()
        checks.append(("config", config_ok, str(settings.config_path)))
        initialized = bool(settings.en_primary and settings.zh_primary)
        checks.append(("models", initialized, "configured" if initialized else "primary models are missing"))
        configured = bool(settings.providers and all(p.api_key.get_secret_value() for p in settings.providers.values()))
        checks.append(("credentials", configured, "configured" if configured else "one or more provider keys are missing"))
        storage_ok = _check_writable(settings.data_dir)
        db_ok = False
        db_detail = str(settings.db_path)
        if storage_ok:
            try:
                service = Service(settings, start_worker=False)
                with service.db.connect() as connection:
                    connection.execute("SELECT 1").fetchone()
                db_ok = True
            except Exception as exc:
                db_detail = f"{settings.db_path}: {type(exc).__name__}: {exc}"
        checks.append(("storage", storage_ok and db_ok, db_detail if storage_ok else f"not writable: {settings.data_dir}"))
        checks.append(("task_directory", _check_writable(settings.task_dir), str(settings.task_dir)))
        for provider_name, provider in settings.providers.items():
            configured_provider = bool(provider.base_url and provider.api_key.get_secret_value())
            checks.append((f"provider:{provider_name}", configured_provider, "URL and key configured" if configured_provider else "URL or key missing"))
        checks.append(("mcp_sdk", importlib.metadata.version("mcp").startswith("1."), f"mcp {importlib.metadata.version('mcp')}"))
        mcp_ok,mcp_detail=_mcp_startup_probe(settings)
        checks.append(('mcp_startup',mcp_ok,mcp_detail))
        checks.append(("skill", _skill_installed(), "writune-polish skill found" if _skill_installed() else "skill file not found in project/user skill directories"))
        if probe:
            ok, detail = _provider_probe(settings)
            checks.append(("provider_probe", ok, detail))
    except Exception as exc:
        checks.append(("config", False, f"{type(exc).__name__}: {exc}"))
    for name, ok, detail in checks:
        typer.echo(f"{'OK' if ok else 'FAIL'}  {name}: {detail}")
    if any(not ok for _, ok, _ in checks):
        raise typer.Exit(code=1)


def _skill_installed() -> bool:
    candidates = [
        Path.cwd() / ".agents" / "skills" / "writune-polish" / "SKILL.md",
        Path.home() / ".agents" / "skills" / "writune-polish" / "SKILL.md",
        Path.home() / ".codex" / "skills" / "writune-polish" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "writune-polish" / "SKILL.md",
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "opencode" / "skills" / "writune-polish" / "SKILL.md",
    ]
    return any(path.is_file() for path in candidates)


@app.command()
def test_provider(
    model: str | None = typer.Option(None, "--model", help="provider/model; defaults to configured English primary."),
    home: Path | None = typer.Option(None, "--home", "--config-dir"),
) -> None:
    """Make an actual short generation request to confirm provider connectivity."""

    settings = _settings(home)
    if model:
        settings.en_primary = model
    ok, detail = _provider_probe(settings)
    typer.echo(("Provider test passed: " if ok else "Provider test failed: ") + detail)
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def serve(home: Path | None = typer.Option(None, "--home", "--config-dir")) -> None:
    """Run the Writune MCP stdio server."""

    from writune.mcp_server import run

    run(home)


@app.command()
def submit(
    text: str | None = typer.Option(None, "--text"),
    source_path: Path | None = typer.Option(None, "--source-path"),
    source_encoding: str | None = typer.Option(None,'--source-encoding'),
    language: str | None = typer.Option(None, "--language"),
    scene: str | None = typer.Option(None, "--scene"),
    context_id: str | None = typer.Option(None, "--context-id"),
    primary_model: str | None = typer.Option(None, "--primary-model"),
    secondary_model: str | None = typer.Option(None, "--secondary-model"),
    style: str | None = typer.Option(None, "--style"),
    priority: int | None = typer.Option(None, "--priority"),
    profile: str | None = typer.Option(None,'--profile'),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key"),
    home: Path | None = typer.Option(None, "--home", "--config-dir"),
) -> None:
    """Submit one paragraph from text or an authorized source file."""

    result = _service(home).submit(
        text=text,
        source_path=str(source_path) if source_path else None,
        source_encoding=source_encoding,
        language=language,
        scene=scene,
        context_id=context_id,
        primary_model=primary_model,
        secondary_model=secondary_model,
        style=style,
        priority=priority,
        profile=profile,
        idempotency_key=idempotency_key,
    )
    _echo_json(result)


@app.command()
def status(
    task_id: str | None = typer.Option(None, "--task-id"),
    batch_id: str | None = typer.Option(None, "--batch-id"),
    home: Path | None = typer.Option(None, "--home", "--config-dir"),
) -> None:
    """Show task or batch progress."""

    _echo_json(_service(home).status(task_id=task_id, batch_id=batch_id))


@app.command('submit-batch')
def submit_batch(
    markdown_path: Path | None = typer.Option(None,'--markdown-path'),
    items_file: Path | None = typer.Option(None,'--items-file',help='UTF-8 JSON array of paragraph objects'),
    source_paths: list[Path] | None = typer.Option(None,'--source-path'),
    language: str | None = typer.Option(None,'--language'),
    scene: str | None = typer.Option(None,'--scene'),
    context_id: str | None = typer.Option(None,'--context-id'),
    style: str | None = typer.Option(None,'--style'),
    max_concurrency: int | None = typer.Option(None,'--max-concurrency',min=1),
    idempotency_key: str | None = typer.Option(None,'--idempotency-key'),
    home: Path | None = typer.Option(None,'--home','--config-dir'),
) -> None:
    service=_service(home)
    items=json.loads(service.workspace.check(items_file).read_text(encoding='utf-8-sig')) if items_file else None
    _echo_json(service.submit_batch(items=items,markdown_path=markdown_path,source_paths=source_paths or None,language=language,scene=scene,context_id=context_id,style=style,max_concurrency=max_concurrency,idempotency_key=idempotency_key))


@app.command('merge-batch')
def merge_batch(batch_id: str, output_path: Path, separator: str | None = typer.Option(None,'--separator'),overwrite: bool = typer.Option(False,'--overwrite'),home: Path | None = typer.Option(None,'--home','--config-dir')) -> None:
    _echo_json(_service(home,start_worker=False).merge_batch(batch_id,output_path,separator,overwrite))


@app.command()
def result(
    task_id: str = typer.Argument(...),
    inline_text: bool = typer.Option(False, "--inline-text", help="Include full result text in output."),
    home: Path | None = typer.Option(None, "--home", "--config-dir"),
) -> None:
    """Show result paths, outcome, and optional inline text."""

    _echo_json(_service(home).result(task_id, inline_text=inline_text))


@app.command()
def apply(
    task_id: str = typer.Option(..., "--task-id"),
    target_path: Path = typer.Option(..., "--target-path"),
    placeholder_id: str = typer.Option(..., "--placeholder-id"),
    output_path: Path | None = typer.Option(None, "--output-path"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    home: Path | None = typer.Option(None, "--home", "--config-dir"),
) -> None:
    """Apply a result to a controlled document path."""

    _echo_json(
        _service(home).apply(
            task_id=task_id,
            target_path=str(target_path),
            placeholder_id=placeholder_id,
            output_path=str(output_path) if output_path else None,
            overwrite=overwrite,
        )
    )


@app.command()
def cancel(task_id: str = typer.Argument(...), home: Path | None = typer.Option(None, "--home", "--config-dir")) -> None:
    """Cancel a queued or running task."""

    _echo_json(_service(home).cancel(task_id))


@app.command()
def stats(home: Path | None = typer.Option(None, "--home", "--config-dir")) -> None:
    """Display actual historical performance observations."""

    _echo_json(_service(home, start_worker=False).stats())


@app.command()
def worker(home: Path | None = typer.Option(None, "--home", "--config-dir")) -> None:
    """Run a worker process in the foreground."""

    from writune.tasks.worker import Worker

    asyncio.run(Worker(_settings(home)).run())


@app.command('update')
def update_command() -> None:
    """Check PyPI now and apply a compatible stable release in the original directory."""
    from writune.updates import check_and_update, source_root
    result = check_and_update(source_root(), interval=0)
    _echo_json(result)
    if result['status'] in {'check_failed', 'manual_install_required', 'local_changes'}:
        raise typer.Exit(code=1)


@app.command('update-status')
def update_status_command() -> None:
    """Show the last background update outcome without making a network request."""
    from writune.updates import update_status
    _echo_json(update_status())


@app.command()
def cleanup(home: Path | None = typer.Option(None, "--home", "--config-dir"), all_history: bool = typer.Option(False, "--all", help="Remove all terminal task history and unused contexts; keep active tasks.")) -> None:
    """Remove expired task files and records, or all terminal history."""

    _echo_json(_service(home, start_worker=False).cleanup(all_history=all_history))


def _echo_json(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))
