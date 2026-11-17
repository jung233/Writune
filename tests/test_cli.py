from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from writune.cli import app
from writune.config import ProviderConfig, Settings


runner = CliRunner()


def test_version_is_available_without_a_subcommand():
    from writune import __version__
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == __version__


def test_noninteractive_init_accepts_environment_secret_without_a_key_argument(tmp_path, monkeypatch):
    monkeypatch.setenv("WRITUNE_PROVIDER_PRIMARY_API_KEY", "private-environment-key")
    home = tmp_path / "config"
    result = runner.invoke(app, [
        "init", "--non-interactive", "--home", str(home),
        "--primary-url", "https://provider.example/v1",
        "--zh-primary-model", "zh-model", "--en-primary-model", "en-model",
    ])
    assert result.exit_code == 0, result.output
    assert "private-environment-key" not in result.output
    assert Settings.load(home).providers["primary"].api_key.get_secret_value() == "private-environment-key"
    assert "private-environment-key" not in (home / "config.json").read_text()


def test_update_command_reports_a_full_install_requirement(monkeypatch):
    from writune import updates
    monkeypatch.setattr(updates, "check_and_update", lambda root, interval: {
        "status": "manual_install_required", "available_version": "0.2.0",
    })
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert json.loads(result.output)["status"] == "manual_install_required"


def test_init_non_interactive_saves_credentials_separately_and_show_redacts(tmp_path):
    home = tmp_path / "config"
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    result = runner.invoke(
        app,
        [
            "init",
            "--non-interactive",
            "--home",
            str(home),
            "--primary-url",
            "http://127.0.0.1/v1",
            "--primary-port",
            "8317",
            "--api-key",
            "private-api-key",
            "--zh-primary-model",
            "zh-polisher",
            "--en-primary-model",
            "en-polisher",
            "--data-dir",
            str(data),
            "--allowed-workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "private-api-key" not in result.output
    assert "primary/zh-polisher" in result.output
    assert "http://127.0.0.1:8317/v1" in result.output
    config_text = (home / "config.json").read_text(encoding="utf-8")
    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "private-api-key" not in config_text
    assert "private-api-key" in env_text
    assert Settings.load(home).providers["primary"].api_key.get_secret_value() == "private-api-key"

    shown = runner.invoke(app, ["config", "show", "--home", str(home)])
    assert shown.exit_code == 0, shown.output
    assert "private-api-key" not in shown.output
    assert "**********" in shown.output


def test_init_interactive_asks_for_setup_values_and_keeps_key_hidden(tmp_path):
    home = tmp_path / "config"
    data = tmp_path / "tasks"
    workspace = tmp_path / "workspace"
    inputs = [
        "http://127.0.0.1/v1",
        "8317",
        "hidden-key",
        "",  # no alternate upstream
        "zh-model",
        "",  # no Chinese fallback
        "en-model",
        "",  # no English fallback
        "natural",
        "4",
        "8",
        "25",
        "20",
        "1",
        str(data),
        "24",
        str(workspace),
    ]
    result = runner.invoke(app, ["init", "--home", str(home)], input="\n".join(inputs) + "\n")
    assert result.exit_code == 0, result.output
    assert "Primary API key" in result.output
    assert "hidden-key" not in result.output
    saved = Settings.load(home)
    assert saved.providers["primary"].api_key.get_secret_value() == "hidden-key"
    assert saved.data_dir == data
    assert saved.allowed_workspaces == [workspace]


def test_init_config_file_deep_merges_nested_settings_before_cli_overrides(tmp_path):
    config_file = tmp_path / "partial-config.json"
    config_file.write_text(
        json.dumps(
            {
                "providers": {
                    "primary": {
                        "capabilities": {"instructions": True},
                        "cache": {"enabled": True},
                    },
                    "backup": {
                        "base_url": "https://backup.example/v1",
                        "api_key": "backup-test-key",
                        "capabilities": {"reasoning": True},
                    },
                },
                "profiles": {"paper": {"scene": "sci_paper"}},
                "zh_primary": "primary/zh-v1",
                "zh_secondary": "backup/zh-v2",
                "en_primary": "primary/en-v1",
                "en_secondary": "backup/en-v2",
                "max_concurrency": 3,
            }
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    result = runner.invoke(
        app,
        [
            "init",
            "--non-interactive",
            "--home",
            str(home),
            "--config-file",
            str(config_file),
            "--primary-url",
            "https://api.example/v1",
            "--api-key",
            "explicit-secret",
            "--default-style",
            "concise",
        ],
    )
    assert result.exit_code == 0, result.output
    saved = Settings.load(home)
    assert saved.providers["primary"].base_url == "https://api.example/v1"
    assert saved.providers["primary"].api_key.get_secret_value() == "explicit-secret"
    assert saved.providers["primary"].capabilities == {"instructions": True}
    assert saved.providers["primary"].cache == {"enabled": True}
    assert saved.providers["backup"].base_url == "https://backup.example/v1"
    assert saved.providers["backup"].capabilities == {"reasoning": True}
    assert saved.profiles == {"paper": {"scene": "sci_paper"}}
    assert saved.max_concurrency == 3
    assert saved.default_style == "concise"
    assert saved.zh_secondary == "backup/zh-v2"
    assert saved.en_secondary == "backup/en-v2"
    assert "explicit-secret" not in saved.config_path.read_text(encoding="utf-8")


def test_init_config_file_keeps_environment_provider_key(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "providers": {"primary": {"base_url": "https://api.example/v1"}},
                "zh_primary": "primary/zh-model",
                "en_primary": "primary/en-model",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WRITUNE_PROVIDER_PRIMARY_API_KEY", "environment-secret")
    home = tmp_path / "home"
    result = runner.invoke(
        app,
        ["init", "--non-interactive", "--home", str(home), "--config-file", str(config_file)],
    )
    assert result.exit_code == 0, result.output
    saved = Settings.load(home)
    assert saved.providers["primary"].api_key.get_secret_value() == "environment-secret"
    assert "environment-secret" not in saved.config_path.read_text(encoding="utf-8")


def test_init_accepts_custom_provider_name_from_config_file(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "providers": {
                    "magpie": {
                        "base_url": "https://magpie.example/v1",
                        "api_key": "config-key",
                    }
                },
                "zh_primary": "magpie/zh-model",
                "en_primary": "magpie/en-model",
            }
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    result = runner.invoke(
        app,
        ["init", "--non-interactive", "--home", str(home), "--config-file", str(config_file)],
    )
    assert result.exit_code == 0, result.output
    assert "Provider magpie: https://magpie.example/v1" in result.output
    saved = Settings.load(home)
    assert saved.providers["magpie"].api_key.get_secret_value() == "config-key"


def test_config_set_rejects_unknown_top_level_setting(tmp_path):
    home = tmp_path / "home"
    Settings(data_dir=tmp_path / "data").save(home)
    result = runner.invoke(app, ["config", "set", "unknown_setting", "value", "--home", str(home)])
    assert result.exit_code == 2
    assert "unknown setting" in result.output
    assert "unknown_setting" not in Settings.load(home).model_dump(mode="json")


def test_config_set_updates_model_and_secret_without_echoing_secret(tmp_path):
    home = tmp_path / "config"
    Settings(
        data_dir=tmp_path / "data",
        providers={"primary": ProviderConfig(base_url="https://api.example/v1", api_key="old-secret")},
        zh_primary="primary/zh-v1",
        en_primary="primary/en-v1",
    ).save(home)

    changed_model = runner.invoke(app, ["config", "set", "zh_primary", "primary/zh-v2", "--home", str(home)])
    assert changed_model.exit_code == 0, changed_model.output
    changed_key = runner.invoke(
        app,
        ["config", "set", "providers.primary.api_key", "new-secret", "--home", str(home)],
    )
    assert changed_key.exit_code == 0, changed_key.output
    assert "new-secret" not in changed_key.output
    saved = Settings.load(home)
    assert saved.zh_primary == "primary/zh-v2"
    assert saved.providers["primary"].api_key.get_secret_value() == "new-secret"
    assert "new-secret" not in saved.config_path.read_text(encoding="utf-8")


def test_doctor_checks_runtime_config_storage_sdk_and_skill(tmp_path, monkeypatch):
    home = tmp_path / "config"
    Settings(
        data_dir=tmp_path / "data",
        allowed_workspaces=[tmp_path],
        providers={"primary": ProviderConfig(base_url="https://api.example/v1", api_key="test-key")},
        zh_primary="primary/zh-model",
        en_primary="primary/en-model",
    ).save(home)
    skill = tmp_path / ".agents" / "skills" / "writune-polish" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: writune-polish\n---\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "--home", str(home)])
    assert result.exit_code == 0, result.output
    for expected in ("python:", "dependency:mcp:", "config:", "models:", "credentials:", "storage:", "mcp_sdk:", "mcp_startup:", "skill:"):
        assert expected in result.output
    assert 'real stdio initialized; 10 tools discovered' in result.output
    assert "test-key" not in result.output


def test_test_provider_calls_generation_path_and_does_not_print_key(tmp_path, monkeypatch):
    home = tmp_path / "config"
    settings = Settings(
        data_dir=tmp_path / "data",
        providers={"primary": ProviderConfig(base_url="https://api.example/v1", api_key="private-key")},
        zh_primary="primary/zh-model",
        en_primary="primary/en-model",
    )
    settings.save(home)
    calls = []

    class FakeProvider:
        def __init__(self, provider, settings):
            assert provider.api_key.get_secret_value() == "private-key"

        async def generate(self, model, prompt, options=None):
            calls.append((model, prompt.input, options))
            return SimpleNamespace(text="Writune provider connection OK")

    monkeypatch.setattr("writune.providers.responses.ResponsesProvider", FakeProvider)
    result = runner.invoke(app, ["test-provider", "--home", str(home)])
    assert result.exit_code == 0, result.output
    assert "Provider test passed" in result.output
    assert "private-key" not in result.output
    assert calls == [("en-model", "Reply with exactly: Writune provider connection OK", {"max_output_tokens": 32})]
