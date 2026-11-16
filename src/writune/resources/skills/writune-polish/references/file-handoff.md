# File handoff and apply

## Submit through an authorized path

Writune reads source files only from its configured allowed workspaces. Save draft inputs there before submitting. At submission, Writune snapshots the input into its task directory, so later edits to the source file do not silently change the queued task.

For a single task, provide exactly one of `text` or `source_path`. Prefer `source_path` for paragraphs already in a file. For batches, use the documented `markdown_path`, `source_paths`, or `items` input mode exposed by the active tool schema. Do not assume the agent's local path is visible to a remote MCP host.

The worker writes `result.txt` and a `result.json` manifest in the Writune task directory. A result response normally returns paths and task metadata, not the full text. Avoid `inline_text=true` unless the user explicitly needs the contents in chat or the file is not otherwise accessible.

## Insert into a document

Use a unique placeholder such as `{{WRITUNE:RESULTS_001}}` in the target file. Once the task is complete, call `writune_apply` with its `task_id`, the target path, and placeholder ID. If no output path is supplied, the applied copy stays in Writune's system temporary task directory. Use an explicit output_path for the final document; keep previews and intermediate results in OS temporary storage. Existing files are not overwritten unless the caller explicitly enables overwrite.

If the placeholder is missing or matches more than once, stop and correct the target rather than guessing. Check the generated file for the replaced marker, surrounding content, encoding, and format. Do not feed the full result back to the main model simply to rewrite it into the destination.

## DOCX scope

Use an exclusive paragraph placeholder for DOCX, with the marker occupying its own paragraph. The implementation can inspect supported document-body, table, header, and footer paragraphs, but it rejects placeholders in unsupported package parts, paragraphs with non-text content, and markers mixed with other paragraph text. Check the applied document for paragraph content and surrounding formatting. If the placeholder shape is outside the implementation's supported scope, leave the source intact and use a supported text/Markdown handoff or ask the user how to proceed.

## Remote or non-shared filesystems

When the client and Writune do not share a filesystem, a Writune-local `result_path` may not be readable by the client. Use the controlled `writune_export` operation to copy a completed result into an allowed workspace path; this works across a remote boundary only when that path is shared or otherwise accessible to the client. If no usable shared path exists, request inline text only when needed and permitted by the user. Inline transfer returns the polished text through the client and can reduce the token-saving benefit. Never claim that a remote path is usable until the client can access it.

## Retention

Task files are stored under Writune's configured data directory and are subject to the configured task TTL and cleanup command. Keep the original draft and the destination until you have checked the final document. Do not delete task files needed for recovery or audit.
