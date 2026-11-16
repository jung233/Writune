# Writune workflows

For complete Markdown drafts, submit `markdown_path` once with a suitable batch `max_concurrency`. Once completed, call `writune_merge_batch(batch_id, output_path)` to restore order, separators and BOM directly to a file. Do not join already segmented Markdown with extra blank lines or ask the main model to reproduce all polished paragraphs. For individual placeholders continue using `writune_apply`.

Use the exposed Writune MCP tools for all Writune operations. The skill directory does not establish a connection to the server. The current Writune interface is based on its own submit/status/result tools; these task IDs are Writune IDs, not the MCP Tasks protocol.

## One paragraph

1. Finish the paragraph and choose its language, writing scene, and style. Review [style-profiles.md](style-profiles.md) when those choices are not obvious.
2. If the document has useful shared context, create it once with `writune_create_context` and reuse the returned `context_id` for paragraphs in that document section. Include only context that is needed to preserve meaning; do not send an entire manuscript when a short summary will do.
3. Save the draft as UTF-8 text under a workspace allowed by Writune. Submit with `writune_submit` using `source_path`, or use `text` for short content when no shared file path is available. Supply exactly one of `text` and `source_path`.
4. Record the returned `task_id`. Continue independent work while it runs. Use `writune_status` when progress affects the next action.
5. When complete, use `writune_result` to get the outcome and paths. Prefer `writune_apply` or `writune_export` when the result must be inserted or copied into an allowed workspace. Keep `inline_text` off unless the user needs the text in chat or paths are not shared.
6. Check the destination document after applying the result. Keep the task's original file until the destination has been checked.

Typical submission fields are `source_path`, `language` (`en` or `zh`), `scene`, `style`, optional `context_id`, and optional primary/secondary model overrides. Only pass fields accepted by the tool schema shown in the current MCP session.

## Markdown or many paragraphs

For a Markdown document, save the draft first and submit its path with `writune_submit_batch(markdown_path=...)`. Writune segments the Markdown into ordered items; for a fixed set of separate paragraph files use `source_paths`, or use the exposed `items` mode if each item needs its own fields. Reuse a shared context and style where appropriate. The result includes a `batch_id`, ordered `task_ids`, an `accepted_count`, and an estimate when available.

The configured concurrency limit caps simultaneous upstream calls. A request to process 20 paragraphs does not mean 20 calls will run at once. Use the returned task order to place results in the original order. Check the batch with `writune_status(batch_id=...)`; wait until each needed task is terminal before inserting output. Do not resubmit the same unchanged batch just because work is still queued.

Example shape (replace paths with files in the user's authorized workspace):

```text
Draft: drafts/results.md
Submit: writune_submit_batch(markdown_path="drafts/results.md",
                             language="en", scene="sci_paper",
                             style="academic", context_id="ctx_...")
Keep batch_id and task_ids; continue another section while the batch runs.
```

## Context reuse

Use context for constraints that apply across a section: terminology, audience, scientific scope, and writing goal. Do not store new facts or conclusions in context unless they are present in the user's source material. Context is versioned by content; after changing it, use the newly returned ID for new tasks instead of assuming old tasks were updated.

For technical prose, use `topic` and `context_summary` to identify the discipline, objects, and relevant methods from the supplied source. Include enough context to distinguish the intended operation and the field's conventional verb-object expressions; a list of technical nouns alone may not resolve the meaning. Put only exact terms that must remain unchanged in `terminology`, since these terms are protected during polishing. Describe usage preferences and conventional collocations in `context_summary` or `constraints` instead of protecting an entire awkward phrase.

When a paragraph depends on nearby text, supply the relevant neighboring sentences through `local_context` if the exposed submission schema supports it. Identify their relationship to the paragraph and provide only what resolves referents or technical meaning. Do not put changing paragraph-specific context into a shared context reused across unrelated passages. Missing or ambiguous context is not permission to infer an unstated experimental step or strengthen a finding.

See [file-handoff.md](file-handoff.md) for shared versus remote paths and [error-handling.md](error-handling.md) before retrying a failed task.
