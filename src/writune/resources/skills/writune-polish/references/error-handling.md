# Errors, cancellation, and fallback

## Retry budget

Read `retry_count` from configuration when the policy matters. It means retries after the first request: the default `1` allows at most two upstream attempts total. When a secondary model is configured, Writune uses it for the retry; without one, it can retry the primary. Non-retryable errors may stop sooner. Do not submit another task to get around the configured attempt budget.

A first-text-token timeout is a failed attempt, not a successful empty result. Output validation failures also consume the same finite attempt budget. Partial output from a failed attempt is not the final result. A network failure can leave uncertainty about whether the upstream provider completed or billed the request; local cancellation does not guarantee the provider stopped processing.

## Original fallback

Streaming timeout means no token-bearing text or reasoning output for 120 seconds by default, including the initial wait; metadata and heartbeats do not reset it. Active streaming has no total-duration or low-TPS cutoff. Provider/model idle overrides may be configured.

`REPETITIVE_OUTPUT` stops a generation when sustained repeated recent output is detected and output reaches 20 times input tokens. `OUTPUT_LIMIT_EXCEEDED` stops at 100 times input regardless of repetition; observed reasoning tokens count when exposed by the stream. Stream-time counts are estimates; a configured tokenizer improves accuracy, and reported usage replaces estimates when available. Hidden reasoning that the provider does not stream cannot be monitored. These guards do not automatically retry. Result metadata exposes `review_required`, `partial_output_path`, and, if available, `partial_reasoning_path`. The normal result remains the original fallback. The main model must inspect retained output against the source before using any part; never apply partial output automatically.

`writune_result` returns structured metadata with no duplicate MCP text content block. By default it omits the `text` field entirely; keep `inline_text=false` and use files. Request `inline_text=true` only when actually needed to avoid filling the main model's context.

After all permitted attempts fail, Writune can complete delivery with `outcome=original_fallback`. This means the preserved source was written as the task result; it does not mean the paragraph was polished. Use that original result and continue the larger document task. Report the number of fallback paragraphs and concise error codes. Keep user-facing explanations short and never expose API keys, raw prompts, or sensitive provider request data.

If a status response reports `completed`, inspect `outcome` or the result's `polished` and `fallback_used` fields before describing it. Use the task's error summary to explain what failed.

## MCP unavailable

If Writune tools are missing, disconnected, or fail before returning a task ID:

1. Do not claim a task was submitted or polished.
2. Preserve the original draft and continue independent work where possible.
3. If the user still needs the document now, use the available authorized editing workflow, clearly treating it as a separate manual edit rather than a Writune result.
4. Report that Writune could not be reached and identify the last confirmed step. Do not repeatedly retry an unavailable server.

If a task ID was returned before the connection failed, reconnect and query that same ID before considering a resubmission. This avoids duplicate work and possible duplicate provider charges.

## Cancellation and recovery

Use `writune_cancel` only when cancellation is requested or the task is no longer needed. Check the returned status; a running request may already have reached the provider. After an interrupted connection, query the task before resubmitting. Report `possible_duplicate_billing` when the result or status exposes it.

Writune's deterministic validator checks specific output invariants; it is not a semantic fact checker. Review high-impact numbers, references, terminology, and claims against the original before delivery.
