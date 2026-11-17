"""User configuration and secret handling for Writune.

Configuration is read only from the selected Writune home directory.  In
particular, this module never implicitly reads a project-local ``.env`` file.
The precedence order is built-in defaults, ``config.json``, the selected
home's ``.env``, process environment variables, and explicit ``load`` keyword
overrides.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from dotenv import dotenv_values
from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_home() -> Path:
    configured = os.environ.get("WRITUNE_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path(user_config_dir("writune"))


def _default_data_dir() -> Path:
    return Path(user_data_dir("writune"))


def _to_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if result <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return result


class ModelConfig(BaseModel):
    """Overrides for one model served by a provider."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, hide_input_in_errors=True)

    capabilities: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    cache: dict[str, Any] = Field(default_factory=dict)
    tokenizer: str | None = None
    prices: dict[str, Any] = Field(default_factory=dict)
    price: dict[str, Any] | float | None = None
    custom_templates: dict[str, str | dict[str, str]] = Field(default_factory=dict)
    connect_timeout: float | None = Field(default=None, gt=0)
    first_token_timeout: float | None = Field(default=None, gt=0)
    inactivity_timeout: float | None = Field(default=None, gt=0)
    total_timeout: float | None = Field(default=None, gt=0)
    timeouts: dict[str, float] = Field(default_factory=dict)

    @field_validator("timeouts")
    @classmethod
    def validate_timeouts(cls, value: dict[str, float]) -> dict[str, float]:
        return {name: _to_float(timeout, f"timeouts.{name}") for name, timeout in value.items()}


class ProviderConfig(BaseModel):
    """Configuration for one Responses API compatible provider."""

    model_config = ConfigDict(extra="allow", validate_assignment=True, hide_input_in_errors=True)

    base_url: str = ""
    adapter: str = "responses"
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    max_concurrency: int = Field(default=4, ge=1)
    model_concurrency: dict[str, int] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    cache: dict[str, Any] = Field(default_factory=dict)
    tokenizer: str | None = None
    prices: dict[str, Any] = Field(default_factory=dict)
    price: dict[str, Any] | float | None = None
    custom_templates: dict[str, str | dict[str, str]] = Field(default_factory=dict)

    # Named timeout fields keep the common case easy to configure. ``timeouts``
    # remains available for provider-specific timeout dimensions.
    connect_timeout: float | None = Field(default=None, gt=0)
    first_token_timeout: float | None = Field(default=None, gt=0)
    inactivity_timeout: float | None = Field(default=None, gt=0)
    total_timeout: float | None = Field(default=None, gt=0)
    timeouts: dict[str, float] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http or https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain URL credentials; store credentials in api_key")
        if parsed.fragment:
            raise ValueError("base_url must not contain a URL fragment")
        for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
            normalized_name = re.sub(r"[^a-z0-9]", "", name.casefold())
            if (
                normalized_name in {"key", "apikey", "auth", "authorization", "token", "accesstoken", "refreshtoken", "secret", "clientsecret", "password", "passwd", "credential", "credentials", "sig", "signature"}
                or normalized_name.endswith(("key", "token", "secret", "password", "passwd", "credential", "signature"))
                or normalized_name.startswith("auth")
            ):
                raise ValueError("base_url query parameters must not contain credentials; store credentials in api_key")
        return value

    @field_validator("model_concurrency")
    @classmethod
    def validate_model_concurrency(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not model or limit < 1 for model, limit in value.items()):
            raise ValueError("model_concurrency values must be positive integers")
        return value

    @field_validator("models")
    @classmethod
    def validate_models(cls, value: dict[str, ModelConfig]) -> dict[str, ModelConfig]:
        if any(not model.strip() for model in value):
            raise ValueError("models must use non-empty model names")
        return value

    @field_validator("timeouts")
    @classmethod
    def validate_timeouts(cls, value: dict[str, float]) -> dict[str, float]:
        return {name: _to_float(timeout, f"timeouts.{name}") for name, timeout in value.items()}

    def resolve_model(self, model: str) -> "ProviderConfig":
        """Return a provider snapshot with the named model's overrides applied.

        Unspecified model values inherit provider defaults. Nested capability,
        parameter, cache, price, and timeout maps merge so one model can change
        a single entry without discarding its provider's other settings.
        """

        model_name = model.strip() if isinstance(model, str) else ""
        model_config = self.models.get(model_name)
        resolved = self.model_dump(mode="python")
        if model_config is not None:
            overrides = model_config.model_dump(mode="python", exclude_unset=True)
            for field_name, value in overrides.items():
                if field_name in {"capabilities", "parameters", "cache", "prices", "price", "custom_templates", "timeouts"}:
                    current = resolved.get(field_name)
                    if isinstance(current, dict) and isinstance(value, dict):
                        resolved[field_name] = _deep_merge(current, value)
                        continue
                resolved[field_name] = value
            for timeout_name,timeout_value in overrides.get('timeouts',{}).items():
                canonical='first_token_timeout' if timeout_name=='first_text_timeout' else timeout_name
                if canonical in {'connect_timeout','first_token_timeout','inactivity_timeout','total_timeout'} and canonical not in overrides:
                    resolved[canonical]=timeout_value
        return ProviderConfig.model_validate(resolved)


class Settings(BaseSettings):
    """Validated Writune settings loaded from a user-owned config directory."""

    model_config = SettingsConfigDict(
        env_prefix="WRITUNE_",
        env_file=None,
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
        validate_assignment=True,
        hide_input_in_errors=True,
    )

    home_dir: Path = Field(default_factory=_default_home)
    data_dir: Path = Field(default_factory=_default_data_dir)
    allowed_workspaces: list[Path] = Field(default_factory=list)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    # Model names are intentionally empty until configured by the user.
    zh_primary: str = ""
    zh_secondary: str = ""
    en_primary: str = ""
    en_secondary: str = ""
    default_primary_model: str = ""
    default_secondary_model: str = ""
    language_defaults: dict[str, dict[str, Any]] = Field(default_factory=dict)

    max_concurrency: int = Field(default=4, ge=1)
    max_queue_size: int = Field(default=200, ge=1)
    retry_count: int = Field(default=1, ge=0)
    worker_idle_seconds: float = Field(default=30, gt=0)
    auto_update: bool = True
    update_check_seconds: float = Field(default=21600, ge=60)
    lease_seconds: float = Field(default=15, gt=0)
    task_ttl_hours: float = Field(default=24, gt=0)
    default_scene: str = "general_prose"
    default_style: str = "natural"
    profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)
    custom_templates: dict[str, str | dict[str, str]] = Field(default_factory=dict)

    connect_timeout: float = Field(default=8, gt=0)
    first_token_timeout: float = Field(default=120, gt=0)
    inactivity_timeout: float = Field(default=120, gt=0)
    total_timeout: float = Field(default=420, gt=0)
    min_tps: float = Field(default=6, gt=0)
    tps_window: float = Field(default=10, gt=0)
    tps_warmup_tokens: int = Field(default=30, ge=0)
    tps_warmup_seconds: float = Field(default=10, gt=0)
    consecutive_slow_windows: int = Field(default=2, ge=1)

    @field_validator("home_dir", mode="before")
    @classmethod
    def expand_home_dir(cls, value: Any) -> Path:
        return Path(value).expanduser()

    @field_validator("data_dir", mode="before")
    @classmethod
    def expand_data_dir(cls, value: Any) -> Path:
        return Path(value).expanduser()

    @field_validator("allowed_workspaces", mode="before")
    @classmethod
    def expand_allowed_workspaces(cls, value: Any) -> Any:
        if isinstance(value, (str, Path)):
            value = [value]
        if value is None:
            return []
        return [Path(path).expanduser() for path in value]

    @field_validator("providers")
    @classmethod
    def validate_provider_names(cls, value: dict[str, ProviderConfig]) -> dict[str, ProviderConfig]:
        # These names are embedded in .env variable names. Lowercase ASCII
        # slugs make that mapping reversible on case-insensitive platforms and
        # avoid collisions caused by punctuation sanitization.
        pattern = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
        invalid = [name for name in value if not pattern.fullmatch(name)]
        if invalid:
            raise ValueError("provider names must be lowercase ASCII slugs using letters, digits, '-' or '_'")
        env_names = [_provider_api_key_env_name(name) for name in value]
        if len(env_names) != len(set(env_names)):
            raise ValueError("provider names collide when encoded as .env variable names")
        return value

    @model_validator(mode="after")
    def normalize_paths(self) -> "Settings":
        """Resolve configured paths once so worker cwd changes cannot affect them."""

        home = self.home_dir.expanduser().resolve()

        def resolve_from_home(path: Path) -> Path:
            expanded = path.expanduser()
            if not expanded.is_absolute():
                expanded = home / expanded
            return expanded.resolve()

        object.__setattr__(self, "home_dir", home)
        object.__setattr__(self, "data_dir", resolve_from_home(self.data_dir))
        object.__setattr__(
            self,
            "allowed_workspaces",
            [resolve_from_home(path) for path in self.allowed_workspaces],
        )
        return self

    @property
    def config_path(self) -> Path:
        return self.home_dir / "config.json"

    @property
    def env_path(self) -> Path:
        return self.home_dir / ".env"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "writune.sqlite3"

    @property
    def task_dir(self) -> Path:
        """Intermediate text and results always belong in OS-managed scratch storage."""
        from writune.files.temporary import task_directory
        return task_directory(self.db_path)

    @classmethod
    def load(cls, home: str | Path | None = None, **overrides: Any) -> "Settings":
        """Load settings with explicit, deterministic source precedence.

        ``home`` takes precedence over ``WRITUNE_HOME``.  If omitted, the
        environment override is honored, followed by the platform's user
        configuration directory.
        """

        home_path = Path(home).expanduser() if home is not None else _default_home()
        config_path = home_path / "config.json"
        env_path = home_path / ".env"

        data: dict[str, Any] = {}
        if config_path.exists():
            try:
                loaded = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Could not read Writune configuration at {config_path}") from exc
            if not isinstance(loaded, dict):
                raise ValueError(f"Writune configuration at {config_path} must contain a JSON object")
            data = loaded

        dotenv_env = {
            str(key).upper(): str(value)
            for key, value in dotenv_values(env_path).items()
            if key is not None and value is not None
        } if env_path.exists() else {}
        process_env = {
            str(key).upper(): str(value)
            for key, value in os.environ.items()
            if str(key).upper().startswith("WRITUNE_")
        }
        dotenv_env.update(process_env)

        data = _deep_merge(data, _environment_to_settings(dotenv_env))
        if overrides:
            data = _deep_merge(data, overrides)
        # The selected home is explicit and must remain the source of the
        # loaded configuration even if an older config file contains a home.
        data["home_dir"] = home_path

        settings = cls(_env_file=None, **data)
        return settings

    def save(self, home: str | Path | None = None) -> Path:
        """Persist non-secret configuration to JSON and API keys to ``.env``.

        Returns the path to ``config.json``. Provider keys are never written
        into that JSON file.
        """

        if home is not None:
            self.home_dir = Path(home).expanduser()
        self.home_dir.mkdir(parents=True, exist_ok=True)

        payload = self.model_dump(mode="python")
        provider_payload: dict[str, Any] = {}
        secret_values: dict[str, str] = {}
        for name, provider in self.providers.items():
            provider_data = provider.model_dump(mode="python", exclude={"api_key"})
            provider_payload[name] = provider_data
            secret = provider.api_key.get_secret_value()
            if secret:
                secret_values[_provider_api_key_env_name(name)] = secret
        payload["providers"] = provider_payload
        json_text = json.dumps(_json_compatible(payload), ensure_ascii=False, indent=2) + "\n"

        # Keep only Writune credentials from the previous file. Configuration
        # values belong in config.json; retaining old .env overrides would
        # silently make a later load disagree with the just-saved settings.
        old_env = dotenv_values(self.env_path) if self.env_path.exists() else {}
        unrelated_env = {
            str(key): str(value)
            for key, value in old_env.items()
            if key and value is not None and not str(key).upper().startswith("WRITUNE_")
        }
        env_values = {**unrelated_env, **secret_values}
        env_lines = [f"{key}={_dotenv_quote(value)}" for key, value in sorted(env_values.items())]
        env_text = "\n".join(env_lines) + ("\n" if env_lines else "")

        _atomic_write(self.config_path, json_text, mode=0o600)
        _atomic_write(self.env_path, env_text, mode=0o600)
        return self.config_path

    def redacted_dict(self) -> dict[str, Any]:
        """Return a JSON-ready settings representation with secrets masked."""

        return self.model_dump(mode="json")


_SETTING_ENV_NAMES = {
    "AUTO_UPDATE": "auto_update",
    "UPDATE_CHECK_SECONDS": "update_check_seconds",
    "DATA_DIR": "data_dir",
    "ALLOWED_WORKSPACES": "allowed_workspaces",
    "PROVIDERS": "providers",
    "ZH_PRIMARY": "zh_primary",
    "ZH_SECONDARY": "zh_secondary",
    "EN_PRIMARY": "en_primary",
    "EN_SECONDARY": "en_secondary",
    "DEFAULT_PRIMARY_MODEL": "default_primary_model",
    "DEFAULT_SECONDARY_MODEL": "default_secondary_model",
    "LANGUAGE_DEFAULTS": "language_defaults",
    "MAX_CONCURRENCY": "max_concurrency",
    "MAX_QUEUE_SIZE": "max_queue_size",
    "RETRY_COUNT": "retry_count",
    "WORKER_IDLE_SECONDS": "worker_idle_seconds",
    "LEASE_SECONDS": "lease_seconds",
    "TASK_TTL_HOURS": "task_ttl_hours",
    "DEFAULT_SCENE": "default_scene",
    "DEFAULT_STYLE": "default_style",
    "PROFILES": "profiles",
    "CUSTOM_TEMPLATES": "custom_templates",
    "CONNECT_TIMEOUT": "connect_timeout",
    "FIRST_TOKEN_TIMEOUT": "first_token_timeout",
    "INACTIVITY_TIMEOUT": "inactivity_timeout",
    "TOTAL_TIMEOUT": "total_timeout",
    "MIN_TPS": "min_tps",
    "TPS_WINDOW": "tps_window",
    "TPS_WARMUP_TOKENS": "tps_warmup_tokens",
    "TPS_WARMUP_SECONDS": "tps_warmup_seconds",
    "CONSECUTIVE_SLOW_WINDOWS": "consecutive_slow_windows",
}

_INT_FIELDS = {
    "max_concurrency",
    "max_queue_size",
    "retry_count",
    "tps_warmup_tokens",
    "consecutive_slow_windows",
}
_FLOAT_FIELDS = {
    "worker_idle_seconds",
    "lease_seconds",
    "task_ttl_hours",
    "connect_timeout",
    "first_token_timeout",
    "inactivity_timeout",
    "total_timeout",
    "min_tps",
    "tps_window",
    "tps_warmup_seconds",
}
_JSON_FIELDS = {
    "providers",
    "models",
    "model_concurrency",
    "capabilities",
    "parameters",
    "cache",
    "prices",
    "price",
    "custom_templates",
    "language_defaults",
    "timeouts",
    "profiles",
    "custom_templates",
}
_PROVIDER_SUFFIXES = {
    "BASE_URL": "base_url",
    "URL": "base_url",
    "API_KEY": "api_key",
    "MAX_CONCURRENCY": "max_concurrency",
    "MODEL_CONCURRENCY": "model_concurrency",
    "MODELS": "models",
    "CAPABILITIES": "capabilities",
    "PARAMETERS": "parameters",
    "CACHE": "cache",
    "TOKENIZER": "tokenizer",
    "PRICES": "prices",
    "PRICE": "price",
    "CUSTOM_TEMPLATES": "custom_templates",
    "CONNECT_TIMEOUT": "connect_timeout",
    "FIRST_TOKEN_TIMEOUT": "first_token_timeout",
    "INACTIVITY_TIMEOUT": "inactivity_timeout",
    "TOTAL_TIMEOUT": "total_timeout",
    "TIMEOUTS": "timeouts",
}


def _environment_to_settings(env: dict[str, str]) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    for name, field_name in _SETTING_ENV_NAMES.items():
        key = f"WRITUNE_{name}"
        if key in env:
            settings[field_name] = _parse_env_value(field_name, env[key])

    providers: dict[str, dict[str, Any]] = {}
    if "providers" in settings:
        raw_providers = settings.pop("providers")
        if isinstance(raw_providers, dict):
            providers = {str(name): dict(config) for name, config in raw_providers.items() if isinstance(config, dict)}
        else:
            settings["providers"] = raw_providers

    # Backward-compatible single-provider variables documented for initial
    # setup. They are represented internally by a provider named "default".
    if "WRITUNE_PROVIDER_URL" in env or "WRITUNE_API_KEY" in env:
        default_provider = providers.setdefault("default", {})
        if "WRITUNE_PROVIDER_URL" in env:
            default_provider["base_url"] = env["WRITUNE_PROVIDER_URL"]
        if "WRITUNE_API_KEY" in env:
            default_provider["api_key"] = env["WRITUNE_API_KEY"]

    nested_prefix = "WRITUNE_PROVIDERS__"
    for key, value in env.items():
        if key.startswith(nested_prefix):
            parts = key[len(nested_prefix):].split("__")
            if len(parts) >= 2:
                provider_name = parts[0].lower()
                field_name = "_".join(parts[1:]).lower()
                if field_name in {"base_url", "url", "api_key", "max_concurrency", "model_concurrency", "models", "capabilities", "parameters", "cache", "tokenizer", "prices", "price", "custom_templates", "connect_timeout", "first_token_timeout", "inactivity_timeout", "total_timeout", "timeouts"}:
                    normalized_field = "base_url" if field_name == "url" else field_name
                    providers.setdefault(provider_name, {})[normalized_field] = _parse_env_value(normalized_field, value)

    # Friendly scalar names for individual providers, e.g.
    # WRITUNE_PROVIDER_OPENAI_API_KEY and WRITUNE_PROVIDER_OPENAI_URL.
    provider_suffix_pattern = "|".join(
        sorted((re.escape(suffix) for suffix in _PROVIDER_SUFFIXES), key=len, reverse=True)
    )
    for key, value in env.items():
        match = re.fullmatch(rf"WRITUNE_PROVIDER_(.+)_({provider_suffix_pattern})", key)
        if not match:
            continue
        provider_name, suffix = match.groups()
        field_name = _PROVIDER_SUFFIXES.get(suffix)
        if field_name is not None:
            configured_name = next(
                (
                    name
                    for name in providers
                    if _provider_api_key_env_name(name) == key
                    or name.casefold() == provider_name.casefold()
                ),
                provider_name.lower(),
            )
            providers.setdefault(configured_name, {})[field_name] = _parse_env_value(field_name, value)

    if providers:
        settings["providers"] = _deep_merge(settings.get("providers", {}), providers)
    return settings


def _parse_env_value(field_name: str, value: str) -> Any:
    if field_name == "allowed_workspaces":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            separator = ";" if ";" in value else os.pathsep
            parsed = [part.strip() for part in value.split(separator) if part.strip()]
        return parsed
    if field_name in _JSON_FIELDS:
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Environment value for {field_name} must be valid JSON") from exc
    if field_name in _INT_FIELDS:
        return int(value)
    if field_name in _FLOAT_FIELDS:
        return float(value)
    return value


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in update.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = value
    return result


def _provider_api_key_env_name(provider_name: str) -> str:
    return f"WRITUNE_PROVIDER_{provider_name.upper()}_API_KEY"


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


def _dotenv_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def _atomic_write(path: Path, contents: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary_name, mode)
        except OSError:
            # Windows does not expose POSIX mode bits in the same way.
            pass
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
