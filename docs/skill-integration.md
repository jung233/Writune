# Skill installation

The skill lives in `.agents/skills/writune-polish/` and includes a `SKILL.md` plus four focused references. The repository location is directly discoverable by Codex and is a documented compatibility source for OpenCode. Claude Code expects a different directory.

## Codex

For use in this repository, leave the folder at `.agents/skills/writune-polish`. For a user-level install, copy the entire folder to `~/.agents/skills/writune-polish`:

```sh
mkdir -p ~/.agents/skills
cp -R .agents/skills/writune-polish ~/.agents/skills/
```

The skill is eligible for automatic selection and can be requested as `$writune-polish`. Codex skill locations are documented in the [Codex skills guide](https://developers.openai.com/codex/skills/).

## Claude Code

Project scope:

```sh
mkdir -p .claude/skills
cp -R .agents/skills/writune-polish .claude/skills/
```

User scope uses `~/.claude/skills/writune-polish` instead. Claude Code can select skills automatically or invoke one with `/writune-polish`. See the [Claude Code skills guide](https://code.claude.com/docs/en/skills).

## OpenCode

OpenCode can discover the repository's existing `.agents/skills/writune-polish` as a project compatibility skill. Alternatively, copy it to `.opencode/skills/writune-polish`. A user-level OpenCode install can use `~/.config/opencode/skills/writune-polish`; OpenCode also searches global compatibility locations including `~/.agents/skills`. See the [OpenCode skills guide](https://opencode.ai/docs/skills).

## Registering MCP is separate

Installing the skill supplies writing workflow guidance only. It does not install Writune, start the MCP server, or register the server in a client. Configure the MCP server separately using [mcp-integration.md](mcp-integration.md) and confirm that its tools are exposed before expecting the skill workflow to run.
