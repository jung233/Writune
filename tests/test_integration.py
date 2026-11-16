from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomlkit
from typer.testing import CliRunner

import writune.integration as integration
from writune.integration import install_skill, register, render_snippet, skill_source


def test_codex_registration_preserves_comments_and_is_idempotent(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    monkeypatch.setattr(integration, "windowless_python", lambda executable=None: str(python))
    config = tmp_path / "config.toml"
    original = (
        '# existing settings\nmodel = "kept-model"\n'
        '[mcp_servers.other]\ncommand = "other-tool" # keep this comment\n'
    )
    config.write_text(original, encoding="utf-8")

    first = register("codex", config=config, home=tmp_path / "writune-home")
    assert Path(first["backup_path"]).read_text(encoding="utf-8") == original
    saved_text = config.read_text(encoding="utf-8")
    assert "# existing settings" in saved_text
    assert "# keep this comment" in saved_text
    document = tomlkit.parse(saved_text)
    assert document["model"] == "kept-model"
    assert document["mcp_servers"]["other"]["command"] == "other-tool"
    entry = document["mcp_servers"]["writune"]
    assert entry["command"] == str(python.resolve())
    assert entry["args"][:3] == ["-m", "writune", "serve"]
    assert entry["enabled"] is True
    assert entry["startup_timeout_sec"] == 20
    assert entry["tool_timeout_sec"] == 60

    after_first = config.read_bytes()
    second = register("codex", config=config, home=tmp_path / "writune-home")
    assert second["changed"] is False
    assert config.read_bytes() == after_first


def test_json_registration_preserves_settings_and_refuses_conflicts(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    monkeypatch.setattr(
        integration,
        "windowless_python",
        lambda executable=None: str(executable or python),
    )
    config = tmp_path / "claude.json"
    config.write_text(
        json.dumps(
            {
                "projects": {"project": {"keep": True}},
                "mcpServers": {"other": {"command": "other-tool"}},
            }
        ),
        encoding="utf-8",
    )
    register("claude-code", config=config, home=tmp_path / "writune-home")
    document = json.loads(config.read_text(encoding="utf-8"))
    assert document["projects"]["project"]["keep"] is True
    assert document["mcpServers"]["other"]["command"] == "other-tool"
    assert document["mcpServers"]["writune"]["type"] == "stdio"

    before = config.read_bytes()
    other_python = tmp_path / "different-python.exe"
    with pytest.raises(ValueError, match="different writune entry"):
        register(
            "claude-code",
            config=config,
            executable=other_python,
            home=tmp_path / "writune-home",
        )
    assert config.read_bytes() == before

    replaced = register(
        "claude-code",
        config=config,
        executable=other_python,
        home=tmp_path / "writune-home",
        replace=True,
    )
    assert Path(replaced["backup_path"]).read_bytes() == before
    updated = json.loads(config.read_text(encoding="utf-8"))
    assert updated["mcpServers"]["writune"]["command"] == str(other_python.resolve())


def test_opencode_uses_v2_schema_and_migrates_legacy_entry_only_with_replace(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    monkeypatch.setattr(integration, "windowless_python", lambda executable=None: str(python))
    config = tmp_path / "opencode.json"
    config.write_text(
        json.dumps({"setting": "keep", "mcp": {"other": {"enabled": True}, "servers": {}}}),
        encoding="utf-8",
    )
    register("opencode", config=config, home=tmp_path / "writune-home")
    document = json.loads(config.read_text(encoding="utf-8"))
    assert document["setting"] == "keep"
    assert document["mcp"]["other"] == {"enabled": True}
    entry = document["mcp"]["servers"]["writune"]
    assert entry == {
        "type": "local",
        "command": [
            str(python.resolve()),
            "-m",
            "writune",
            "serve",
            "--home",
            str((tmp_path / "writune-home").resolve()),
        ],
        "disabled": False,
    }

    legacy_config = tmp_path / "legacy-opencode.json"
    legacy = {
        "mcp": {
            "other": {"keep": 1},
            "writune": {"type": "local", "command": ["old-python", "-m", "writune"], "enabled": True},
        }
    }
    legacy_config.write_text(json.dumps(legacy), encoding="utf-8")
    with pytest.raises(ValueError, match="Legacy OpenCode"):
        register("opencode", config=legacy_config, home=tmp_path / "writune-home")
    assert json.loads(legacy_config.read_text(encoding="utf-8")) == legacy

    register("opencode", config=legacy_config, home=tmp_path / "writune-home", replace=True)
    migrated = json.loads(legacy_config.read_text(encoding="utf-8"))
    assert migrated["mcp"]["other"] == {"keep": 1}
    assert "writune" not in migrated["mcp"]
    assert migrated["mcp"]["servers"]["writune"]["disabled"] is False
    assert "enabled" not in migrated["mcp"]["servers"]["writune"]


def test_opencode_default_detects_jsonc_and_refuses_to_overwrite_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    jsonc = tmp_path / "xdg" / "opencode" / "opencode.jsonc"
    jsonc.parent.mkdir(parents=True)
    jsonc.write_text('{\n  // keep this comment\n  "mcp": {}\n}\n', encoding="utf-8")

    assert integration.default_config("opencode") == jsonc.resolve()
    with pytest.raises(ValueError, match="JSONC configuration is not supported safely"):
        register("opencode", home=tmp_path / "writune-home")
    assert "keep this comment" in jsonc.read_text(encoding="utf-8")


def test_cli_show_prints_copyable_snippet_and_register_writes_only_explicit_config(tmp_path):
    runner = CliRunner()
    home = tmp_path / "writune-home"
    shown = runner.invoke(
        integration.mcp_app,
        ["show", "--client", "opencode", "--home", str(home)],
    )
    assert shown.exit_code == 0, shown.output
    snippet = json.loads(shown.output)
    assert snippet["mcp"]["servers"]["writune"]["type"] == "local"
    assert "-m" in snippet["mcp"]["servers"]["writune"]["command"]
    assert not home.exists()

    config = tmp_path / "client" / "opencode.json"
    registered = runner.invoke(
        integration.mcp_app,
        [
            "register",
            "--client",
            "opencode",
            "--home",
            str(home),
            "--config",
            str(config),
        ],
    )
    assert registered.exit_code == 0, registered.output
    result = json.loads(registered.output)
    assert result["changed"] is True
    assert json.loads(config.read_text(encoding="utf-8"))["mcp"]["servers"]["writune"]["disabled"] is False
    assert not home.exists()


def test_skill_source_prefers_installed_package_resource(tmp_path, monkeypatch):
    package = tmp_path / "site-packages" / "writune"
    module = package / "integration.py"
    packaged_skill = package / "resources" / "skills" / "writune-polish"
    packaged_skill.mkdir(parents=True)
    (packaged_skill / "SKILL.md").write_text("package resource", encoding="utf-8")
    project_skill = tmp_path / ".agents" / "skills" / "writune-polish"
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text("editable resource", encoding="utf-8")
    monkeypatch.setattr(integration, "__file__", str(module))

    assert skill_source() == packaged_skill.resolve()


def test_skill_install_checks_all_customizations_before_copying_and_installs_tree(tmp_path):
    source = tmp_path / "source"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text("main", encoding="utf-8")
    (source / "references" / "guide.md").write_text("guide", encoding="utf-8")
    destination = tmp_path / "custom" / "writune-polish"
    destination.mkdir(parents=True)
    (destination / "references").mkdir()
    (destination / "references" / "guide.md").write_text("custom", encoding="utf-8")

    with pytest.raises(ValueError, match="Existing customized skill retained"):
        install_skill("opencode", source=source, destination=destination)
    assert not (destination / "SKILL.md").exists()

    (destination / "references" / "guide.md").write_text("guide", encoding="utf-8")
    installed = install_skill("opencode", source=source, destination=destination)
    assert installed == destination.resolve()
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "main"
    assert (destination / "references" / "guide.md").read_text(encoding="utf-8") == "guide"

    with pytest.raises(ValueError, match="trees must be separate"):
        install_skill("opencode", source=source, destination=source / "installed")


def test_render_snippet_matches_client_schema_and_home_is_absolute(tmp_path):
    home = tmp_path / "runtime-home"
    toml_snippet = tomlkit.parse(render_snippet("codex", home=home))
    assert toml_snippet["mcp_servers"]["writune"]["args"][-1] == str(home.resolve())
    claude_snippet = json.loads(render_snippet("claude-code", home=home))
    assert claude_snippet["mcpServers"]["writune"]["type"] == "stdio"
    opencode_snippet = json.loads(render_snippet("opencode", home=home))
    assert opencode_snippet["mcp"]["servers"]["writune"]["disabled"] is False


def test_registration_preserves_a_concurrent_edit(tmp_path, monkeypatch):
    config = tmp_path / "client.json"
    config.write_text('{"keep": "original"}', encoding="utf-8")
    original_read = integration._current_bytes
    reads = 0

    def concurrent_read(path):
        nonlocal reads
        reads += 1
        if reads == 2:
            path.write_text('{"keep": "concurrent edit"}', encoding="utf-8")
        return original_read(path)

    monkeypatch.setattr(integration, "_current_bytes", concurrent_read)
    with pytest.raises(RuntimeError, match="changed during registration"):
        register("claude-code", config=config, home=tmp_path / "home")
    assert json.loads(config.read_text())["keep"] == "concurrent edit"
