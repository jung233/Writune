# Skill installation

The complete `writune-polish` skill, including references and agent metadata, is bundled in the Python package. No checkout is required:

```sh
writune skill install --client codex
```

Select `codex`, `claude-code`, or `opencode`. `writune mcp register --client codex --install-skill` combines registration with the same skill installer. Use `--destination /absolute/path/writune-polish` to select an exact destination directory.

| Client | Default user destination |
| --- | --- |
| Codex | `~/.agents/skills/writune-polish` |
| Claude Code | `~/.claude/skills/writune-polish` |
| OpenCode | `~/.config/opencode/skills/writune-polish`, respecting `XDG_CONFIG_HOME` |

The installer checks the entire destination before copying and will not overwrite customized files. Inspect conflicts and merge changes manually. Package updates refresh bundled resources and preserve separately installed customized skills. To install manually, copy the entire `writune/resources/skills/writune-polish` directory from site-packages, as described in the [manual guide](../MANUAL_INSTALL.md).

The canonical development source remains `.agents/skills/writune-polish`; `scripts/build_update_manifest.py` synchronizes it into the package resources. Installing a skill supplies writing instructions but does not establish a tool connection. Register MCP separately and confirm that the client exposes Writune's tools.
