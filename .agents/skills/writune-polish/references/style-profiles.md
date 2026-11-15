# Language, scene, and style

Choose language, writing scene, and tone from the user's request and the document's purpose. Do not infer a desired style solely from a file extension or the word “paper.”

Use the [selective editorial method](sepia-method.md) to diagnose concrete expression problems before revision. Match supplied author/venue samples when available, preserve effective wording, and check for filler introduced by the edit. Naturalness does not require casual language, fixed sentence lengths, or changing every sentence.

## Scenes

Use the closest configured scene:

| Scene | Typical material |
| --- | --- |
| `sci_paper` | English scientific manuscripts, including Results, Discussion, Methods, and Abstract |
| `chinese_journal` | Chinese journal prose |
| `school_essay` | Course essays |
| `academic_assignment` | Academic assignments |
| `business_report` | Business reports |
| `technical_report` | Technical reports |
| `user_manual` | User-facing instructions and manuals |
| `general_prose` | Other prose |

If a scene is not accepted by the exposed tool, use a supported configured scene or leave the choice to the configured default; do not invent a scene name.

## Styles

Use a supported style that matches the request, such as `natural`, `concise`, `formal`, `academic`, `explanatory`, or `literary`. The tool's current schema and Writune configuration are authoritative if a requested option is unavailable.

## Preserve meaning

For every language and scene, preserve all reported values, units, p/q values, confidence intervals, effect estimates, gene and protein names, dataset identifiers, citations, formulas, groups, direction of findings, and the original degree of uncertainty. Do not strengthen association into causation, add unsupported mechanisms, or change a hypothesis into a confirmed mechanism. For Results, keep the prose centered on observations and avoid adding interpretation. Discussion may express interpretation only as the source supports it. Methods must retain procedural detail; Abstracts must not gain results absent from the source.

For Chinese journal prose, use clear, disciplined Chinese, keep terminology consistent, avoid formulaic transitions, and preserve the author's claims and qualifications. More broadly, favor natural, appropriately formal academic Chinese. Remove repetitive transitions, empty emphasis, and template-like summaries where that improves flow. Do not apply a phrase blacklist or make wording changes that alter emphasis or evidence.

For course essays, assignments, reports, and manuals, follow the user's requested register and audience. For a school essay, preserve the writer's ideas and voice. Do not turn a student's argument into a different argument or add unsupported evidence.

Writune's deterministic output checks can catch only the specific constraints implemented by the validator. A passing result does not prove factual accuracy, preserve every nuance, or replace review against the source.
