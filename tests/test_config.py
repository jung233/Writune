import json

import pytest
from pydantic import SecretStr, ValidationError

from writune.config import ProviderConfig, Settings


def test_load_precedence_and_explicit_home(tmp_path, monkeypatch):
    home = tmp_path / "selected-home"
    home.mkdir()
    (home / "config.json").write_text(
        json.dumps({"max_concurrency": 2, "zh_primary": "json/model", "providers": {"one": {"base_url": "https://json.example/v1"}}}),
        encoding="utf-8",
    )
    (home / ".env").write_text(
        "WRITUNE_MAX_CONCURRENCY=3\nWRITUNE_PROVIDER_ONE_API_KEY=dotenv-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WRITUNE_HOME", str(tmp_path / "ignored-home"))
    monkeypatch.setenv("WRITUNE_MAX_CONCURRENCY", "4")

    settings = Settings.load(home, max_concurrency=5)

    assert settings.home_dir == home
    assert settings.max_concurrency == 5
    assert settings.zh_primary == "json/model"
    assert settings.providers["one"].base_url == "https://json.example/v1"
    assert settings.providers["one"].api_key.get_secret_value() == "dotenv-secret"


def test_save_keeps_credentials_in_home_env_and_redacts_json(tmp_path):
    secret = "a-secret-value"
    settings = Settings(
        data_dir=tmp_path / "data",
        providers={
            "my-provider": ProviderConfig(
                base_url="https://api.example/v1",
                api_key=SecretStr(secret),
                capabilities={"instructions": True},
            )
        },
        zh_primary="my-provider/model-x",
    )

    config_path = settings.save(tmp_path / "config-home")
    config_text = config_path.read_text(encoding="utf-8")
    env_text = (tmp_path / "config-home" / ".env").read_text(encoding="utf-8")

    assert secret not in config_text
    assert secret not in settings.model_dump_json()
    assert "WRITUNE_PROVIDER_MY-PROVIDER_API_KEY" in env_text
    assert secret in env_text

    loaded = Settings.load(tmp_path / "config-home")
    assert loaded.providers["my-provider"].api_key.get_secret_value() == secret
    assert loaded.zh_primary == "my-provider/model-x"


def test_provider_config_validates_url_and_concurrency():
    with pytest.raises(ValidationError):
        ProviderConfig(base_url="file:///tmp/key")
    with pytest.raises(ValidationError):
        ProviderConfig(base_url="https://api.example", max_concurrency=0)


def test_provider_model_overrides_merge_with_provider_defaults_and_round_trip(tmp_path):
    provider = ProviderConfig(
        base_url="https://api.example/v1",
        capabilities={"instructions": True, "temperature": False, "cache": {"prompt_cache_key": True}},
        parameters={"temperature": 0.2, "max_output_tokens": 512},
        cache={"enabled": True, "retention": "1h"},
        tokenizer="cl100k_base",
        connect_timeout=8,
        timeouts={"first_token_timeout": 20, "total_timeout": 90},
        prices={"input_per_million": 1.0},
        custom_templates={"shared": "provider text", "nested": {"zh": "provider text"}},
        models={
            "model-a": {
                "capabilities": {"temperature": True},
                "parameters": {"temperature": 0.7},
                "cache": {"retention": "24h"},
                "tokenizer": "p50k_base",
                "connect_timeout": 2,
                "timeouts": {"first_token_timeout": 5},
                "prices": {"output_per_million": 2.0},
                "custom_templates": {"model-only": "model text", "nested": {"en": "model text"}},
            }
        },
    )

    resolved = provider.resolve_model("model-a")

    assert resolved.capabilities == {
        "instructions": True,
        "temperature": True,
        "cache": {"prompt_cache_key": True},
    }
    assert resolved.parameters == {"temperature": 0.7, "max_output_tokens": 512}
    assert resolved.cache == {"enabled": True, "retention": "24h"}
    assert resolved.tokenizer == "p50k_base"
    assert resolved.connect_timeout == 2
    assert resolved.timeouts == {"first_token_timeout": 5, "total_timeout": 90}
    assert resolved.prices == {"input_per_million": 1.0, "output_per_million": 2.0}
    assert resolved.custom_templates == {
        "shared": "provider text",
        "nested": {"zh": "provider text", "en": "model text"},
        "model-only": "model text",
    }
    assert provider.capabilities["temperature"] is False
    assert provider.resolve_model("other").tokenizer == "cl100k_base"

    home = tmp_path / "config-home"
    Settings(data_dir=tmp_path / "data", providers={"primary": provider}).save(home)
    loaded = Settings.load(home).providers["primary"]
    assert loaded.resolve_model("model-a").timeouts == resolved.timeouts
    assert loaded.resolve_model("model-a").tokenizer == "p50k_base"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@api.example/v1",
        "https://api.example/v1?api_key=secret",
        "https://api.example/v1?X-Auth-Token=secret",
    ],
)
def test_provider_url_rejects_embedded_credentials(url):
    with pytest.raises(ValidationError) as exc_info:
        ProviderConfig(base_url=url)
    assert url not in str(exc_info.value)


def test_provider_url_allows_non_secret_query_parameters():
    provider = ProviderConfig(base_url="https://api.example/v1?api-version=2025-01-01")

    assert provider.base_url == "https://api.example/v1?api-version=2025-01-01"


def test_provider_names_must_not_collide_in_env_variables():
    with pytest.raises(ValidationError, match="provider names"):
        Settings(providers={"OpenAI": ProviderConfig(base_url="https://api.example/v1")})


def test_custom_templates_load_save_and_json_environment(tmp_path, monkeypatch):
    templates = {"my_style": "Preserve a concise, direct tone."}
    monkeypatch.setenv("WRITUNE_CUSTOM_TEMPLATES", json.dumps(templates))

    settings = Settings.load(tmp_path / "config-home")
    assert settings.custom_templates == templates

    monkeypatch.delenv("WRITUNE_CUSTOM_TEMPLATES")
    settings.save()
    loaded = Settings.load(tmp_path / "config-home")

    assert loaded.custom_templates == templates


def test_paths_resolve_from_home_and_remain_stable_after_cwd_change(tmp_path, monkeypatch):
    home = tmp_path / "config-home"
    home.mkdir()
    (home / "config.json").write_text(
        json.dumps({"data_dir": "../state", "allowed_workspaces": ["../projects", "work"]}),
        encoding="utf-8",
    )

    settings = Settings.load(home)
    expected_home = home.resolve()
    expected_data = (expected_home / "../state").resolve()
    expected_roots = [(expected_home / "../projects").resolve(), (expected_home / "work").resolve()]

    assert settings.home_dir == expected_home
    assert settings.data_dir == expected_data
    assert settings.allowed_workspaces == expected_roots
    assert all(path.is_absolute() for path in [settings.home_dir, settings.data_dir, *settings.allowed_workspaces])

    other_cwd = tmp_path / "different-working-directory"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    assert settings.home_dir == expected_home
    assert settings.data_dir == expected_data
    assert settings.allowed_workspaces == expected_roots


def test_default_model_names_are_not_hardcoded(tmp_path):
    settings = Settings.load(tmp_path / "new-home")

    assert (settings.zh_primary, settings.zh_secondary, settings.en_primary, settings.en_secondary) == ("", "", "", "")
    assert settings.db_path == settings.data_dir / "writune.sqlite3"
