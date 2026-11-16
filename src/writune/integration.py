"""Package-native MCP client registration and Writune skill installation."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import MutableMapping
from enum import Enum
from pathlib import Path
from typing import Any

import tomlkit
import typer

from writune.config import Settings
from writune.files.atomic import atomic_write
from writune.runtime import windowless_python


class Client(str, Enum):
    """Supported MCP client configuration formats."""

    CODEX = "codex"
    CLAUDE_CODE = "claude-code"
    OPENCODE = "opencode"


SERVER_NAME = "writune"
mcp_app = typer.Typer(name="mcp", no_args_is_help=True, help="Register Writune with MCP clients")
skill_app = typer.Typer(name="skill", no_args_is_help=True, help="Install the Writune polish skill")


def _client_name(client: str | Client) -> str:
    value = client.value if isinstance(client, Client) else client
    if value not in {item.value for item in Client}:
        choices = ", ".join(item.value for item in Client)
        raise ValueError(f"Unsupported client {value!r}; choose one of: {choices}")
    return value


def default_config(client: str | Client) -> Path:
    """Return the platform default configuration path for an MCP client."""

    client = _client_name(client)
    if client == Client.CODEX.value:
        root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        return (root / "config.toml").resolve()
    if client == Client.CLAUDE_CODE.value:
        return (Path.home() / ".claude.json").resolve()

    config_root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser()
    json_path = config_root / "opencode" / "opencode.json"
    jsonc_path = json_path.with_suffix(".jsonc")
    if jsonc_path.exists() and not json_path.exists():
        return jsonc_path.resolve()
    return json_path.resolve()


def _resolved_home(home: str | Path | None) -> Path:
    return Settings.load(home).home_dir.expanduser().resolve()


def _command_and_args(
    home: Path,
    executable: str | Path | None = None,
) -> tuple[str, list[str]]:
    python = Path(windowless_python(executable)).expanduser().resolve()
    return str(python), ["-m", "writune", "serve", "--home", str(home)]


def _entry_for(
    client: str,
    home: Path,
    executable: str | Path | None = None,
) -> dict[str, Any]:
    command, args = _command_and_args(home, executable)
    if client == Client.OPENCODE.value:
        return {"type": "local", "command": [command, *args], "disabled": False}
    entry: dict[str, Any] = {"command": command, "args": args}
    if client == Client.CODEX.value:
        entry.update({"enabled": True, "startup_timeout_sec": 20, "tool_timeout_sec": 60})
    else:
        entry["type"] = "stdio"
    return entry


def render_snippet(
    client: str | Client,
    home: str | Path | None = None,
    executable: str | Path | None = None,
) -> str:
    """Render a complete, copyable client configuration snippet."""

    client = _client_name(client)
    entry = _entry_for(client, _resolved_home(home), executable)
    if client == Client.CODEX.value:
        document = tomlkit.document()
        servers = tomlkit.table()
        toml_entry = tomlkit.table()
        for key, value in entry.items():
            toml_entry[key] = value
        servers[SERVER_NAME] = toml_entry
        document["mcp_servers"] = servers
        return tomlkit.dumps(document)
    if client == Client.OPENCODE.value:
        return json.dumps({"mcp": {"servers": {SERVER_NAME: entry}}}, ensure_ascii=False, indent=2) + "\n"
    return json.dumps({"mcpServers": {SERVER_NAME: entry}}, ensure_ascii=False, indent=2) + "\n"


def _is_mapping(value: Any) -> bool:
    return isinstance(value, MutableMapping)


def _read_client_config(
    client: str,
    text: str,
    config: Path,
) -> tuple[Any, MutableMapping[str, Any], MutableMapping[str, Any] | None]:
    if client == Client.CODEX.value:
        try:
            document = tomlkit.parse(text)
        except Exception as exc:
            raise ValueError(f"Invalid TOML in client configuration {config}: {exc}") from exc
        if not _is_mapping(document):
            raise ValueError("Codex configuration must be a TOML table")
        if "mcp_servers" not in document:
            document["mcp_servers"] = tomlkit.table()
        servers = document["mcp_servers"]
        if not _is_mapping(servers):
            raise ValueError("Codex mcp_servers must be a TOML table")
        return document, servers, None

    if client == Client.OPENCODE.value and config.suffix.casefold() == ".jsonc":
        raise ValueError(
            f"OpenCode JSONC configuration is not supported safely: {config}; "
            "use a JSON config file or edit the JSONC file manually"
        )
    try:
        document = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in client configuration {config}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("Client configuration must be a JSON object")
    if client == Client.OPENCODE.value:
        if "mcp" not in document:
            document["mcp"] = {}
        mcp = document["mcp"]
        if not isinstance(mcp, dict):
            raise ValueError("OpenCode mcp must be a JSON object")
        legacy = mcp.get(SERVER_NAME) if SERVER_NAME in mcp else None
        if SERVER_NAME in mcp and not isinstance(legacy, dict):
            raise ValueError("Legacy OpenCode mcp.writune entry must be a JSON object")
        if "servers" not in mcp:
            mcp["servers"] = {}
        servers = mcp["servers"]
        if not isinstance(servers, dict):
            raise ValueError("OpenCode mcp.servers must be a JSON object")
        return document, servers, legacy

    if "mcpServers" not in document:
        document["mcpServers"] = {}
    servers = document["mcpServers"]
    if not isinstance(servers, dict):
        raise ValueError("Client mcpServers must be a JSON object")
    return document, servers, None


def _entry_matches(client: str, previous: MutableMapping[str, Any], desired: dict[str, Any]) -> bool:
    if client == Client.OPENCODE.value:
        return previous.get("type") == desired["type"] and previous.get("command") == desired["command"]
    return previous.get("command") == desired["command"] and previous.get("args") == desired["args"]


def _dump_client_config(client: str, document: Any) -> bytes:
    if client == Client.CODEX.value:
        return tomlkit.dumps(document).encode("utf-8")
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _current_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def register(
    client: str | Client,
    config: str | Path | None = None,
    executable: str | Path | None = None,
    home: str | Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Register the current Writune Python in a client config."""

    client = _client_name(client)
    config_path = Path(config).expanduser().resolve() if config is not None else default_config(client)
    if client == Client.OPENCODE.value and config_path.suffix.casefold() == ".jsonc":
        raise ValueError(
            f"OpenCode JSONC configuration is not supported safely: {config_path}; "
            "use a JSON config file or edit the JSONC file manually"
        )

    settings_home = _resolved_home(home)
    desired = _entry_for(client, settings_home, executable)
    original = _current_bytes(config_path)
    text = original.decode("utf-8-sig") if original is not None else ""
    document, servers, legacy = _read_client_config(client, text, config_path)

    if legacy is not None and not replace:
        raise ValueError(
            "Legacy OpenCode mcp.writune entry exists; use --replace to move it to mcp.servers.writune"
        )
    current_exists = SERVER_NAME in servers
    previous = servers.get(SERVER_NAME) if current_exists else legacy
    if current_exists and not _is_mapping(previous):
        raise ValueError(f"Existing {SERVER_NAME} entry must be a configuration object")
    if current_exists and previous is not None and not _entry_matches(client, previous, desired) and not replace:
        raise ValueError(
            "A different writune entry exists; inspect it and use --replace to update only that entry"
        )
    if previous is None:
        entry = tomlkit.table() if client == Client.CODEX.value else {}
    else:
        entry = previous

    if client == Client.OPENCODE.value:
        entry["type"] = desired["type"]
        entry["command"] = desired["command"]
        entry.pop("enabled", None)
        entry["disabled"] = False
        if legacy is not None:
            document["mcp"].pop(SERVER_NAME, None)
    else:
        entry["command"] = desired["command"]
        entry["args"] = desired["args"]
        if client == Client.CODEX.value:
            entry["enabled"] = True
            entry.setdefault("startup_timeout_sec", 20)
            entry.setdefault("tool_timeout_sec", 60)
        else:
            entry["type"] = "stdio"
    servers[SERVER_NAME] = entry
    output = _dump_client_config(client, document)
    if original == output:
        return {"client": client, "config_path": str(config_path), "changed": False, "backup_path": None}

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if _current_bytes(config_path) != original:
        raise RuntimeError("Client configuration changed during registration; rerun after inspecting the change")

    backup_path: Path | None = None
    if original is not None:
        backup_path = Path(tempfile.mkdtemp(prefix="writune-mcp-backup-")) / config_path.name
        atomic_write(backup_path, original, overwrite=False)

    try:
        if original is None:
            atomic_write(config_path, output, overwrite=False)
        else:
            if _current_bytes(config_path) != original:
                raise RuntimeError(
                    "Client configuration changed during registration; rerun after inspecting the change"
                )
            atomic_write(config_path, output, overwrite=True)
    except FileExistsError as exc:
        raise RuntimeError(
            "Client configuration appeared during registration; rerun after inspecting the change"
        ) from exc

    return {
        "client": client,
        "config_path": str(config_path),
        "changed": True,
        "backup_path": str(backup_path) if backup_path else None,
    }


def skill_source() -> Path:
    """Find the package-bundled skill, with a development-tree fallback."""

    package_source = Path(__file__).resolve().parent / "resources" / "skills" / "writune-polish"
    project_source = Path(__file__).resolve().parents[2] / ".agents" / "skills" / "writune-polish"
    for candidate in (package_source, project_source):
        if candidate.is_dir():
            return candidate.resolve()
    raise FileNotFoundError(
        f"Writune polish skill source was not found; searched: {package_source}, {project_source}"
    )


def default_skill_destination(client: str | Client) -> Path:
    """Return the skills directory where this client discovers installed skills."""

    client = _client_name(client)
    if client == Client.CODEX.value:
        return (Path.home() / ".agents" / "skills").resolve()
    if client == Client.CLAUDE_CODE.value:
        return (Path.home() / ".claude" / "skills").resolve()
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser()
    return (config_root / "opencode" / "skills").resolve()


def install_skill(
    client: str | Client,
    source: str | Path | None = None,
    destination: str | Path | None = None,
) -> Path:
    """Install a full skill directory without replacing customized files."""

    client = _client_name(client)
    source_path = Path(source).expanduser().resolve() if source is not None else skill_source()
    if not source_path.is_dir():
        raise FileNotFoundError(f"Skill source directory does not exist: {source_path}")
    target = (
        Path(destination).expanduser().resolve()
        if destination is not None
        else default_skill_destination(client) / "writune-polish"
    )
    try:
        target.relative_to(source_path)
        overlaps_source = True
    except ValueError:
        try:
            source_path.relative_to(target)
            overlaps_source = True
        except ValueError:
            overlaps_source = False
    if overlaps_source:
        raise ValueError("Skill source and install destination trees must be separate")

    source_files = [path for path in source_path.rglob("*") if path.is_file()]
    if not source_files:
        raise ValueError(f"Skill source directory contains no files: {source_path}")
    if target.exists() and not target.is_dir():
        raise ValueError(f"Skill install destination is not a directory: {target}")

    for source_file in source_files:
        installed_file = target / source_file.relative_to(source_path)
        ancestor = installed_file.parent
        while ancestor != target.parent:
            if ancestor.exists() and not ancestor.is_dir():
                raise ValueError(f"Skill install path is not a directory: {ancestor}")
            if ancestor == target:
                break
            ancestor = ancestor.parent
        if installed_file.exists():
            if not installed_file.is_file():
                raise ValueError(f"Skill install path is not a file: {installed_file}")
            if installed_file.read_bytes() != source_file.read_bytes():
                raise ValueError(f"Existing customized skill retained: {installed_file}")

    shutil.copytree(source_path, target, dirs_exist_ok=True)
    return target


def _cli_fail(exc: Exception) -> None:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1)


@mcp_app.command("register")
def mcp_register(
    client: Client = typer.Option(..., "--client", help="MCP client: codex, claude-code, or opencode"),
    home: Path | None = typer.Option(None, "--home", help="Writune home directory"),
    config: Path | None = typer.Option(None, "--config", help="Client config path (defaults to the platform path)"),
    replace: bool = typer.Option(False, "--replace", help="Replace only an existing conflicting Writune entry"),
    install: bool = typer.Option(False, "--install-skill", help="Install the writune-polish skill after registration"),
) -> None:
    """Register Writune's MCP server in a client configuration."""

    try:
        result = register(client, config=config, home=home, replace=replace)
        if install:
            result["skill_path"] = str(install_skill(client))
    except (OSError, RuntimeError, ValueError) as exc:
        _cli_fail(exc)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@mcp_app.command("show")
def mcp_show(
    client: Client = typer.Option(..., "--client", help="MCP client: codex, claude-code, or opencode"),
    home: Path | None = typer.Option(None, "--home", help="Writune home directory"),
) -> None:
    """Print a copyable client configuration snippet without writing files."""

    try:
        typer.echo(render_snippet(client, home=home), nl=False)
    except (OSError, RuntimeError, ValueError) as exc:
        _cli_fail(exc)


@skill_app.command("install")
def skill_install(
    client: Client = typer.Option(..., "--client", help="MCP client: codex, claude-code, or opencode"),
    destination: Path | None = typer.Option(
        None,
        "--destination",
        help="Exact destination directory for the writune-polish skill",
    ),
) -> None:
    """Install the Writune polish skill for a client."""

    try:
        typer.echo(str(install_skill(client, destination=destination)))
    except (OSError, RuntimeError, ValueError) as exc:
        _cli_fail(exc)
