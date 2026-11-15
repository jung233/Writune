# Selective editorial method

Use this method to prepare a polishing request and to review its result. The runtime prompt contains the corresponding silent editorial workflow; do not append this entire reference to every task.

## Scope and routing

Language polishing is a local revision of an established draft. Diagnose expression problems before choosing changes. If the user requests review only, report problems with source passages and do not submit for polishing. If an argument needs new evidence or a section needs reorganization, handle that in the main agent's drafting task when authorized, then polish the settled draft. Writune does not expose Sepia's write/review/refactor/recreate operations as API modes.

Choose a supported scene and style using the document's purpose. Include the section in shared context (especially Abstract, Methods, Results, or Discussion), and use available context fields for audience, terminology, and supplied author or venue samples. Use only supplied material within the task's scope; no venue research is required for routine polishing. Without examples, use the scene baseline. Context can clarify a reference; it must not become extra facts in the paragraph.

## Diagnose, revise, verify

1. Identify concrete problems: a referent that is hard to resolve, an obscured relationship between existing statements, an overloaded sentence, redundant exposition, register mismatch, or repeated framing. For several paragraphs, check what each contributes and whether adjacent paragraphs connect. Useful topic sentences and required summaries can stay. Do not infer AI authorship from wording.
2. Address continuity and sentence construction before polishing individual words. Keep the argument, paragraph order, headings, lists, and procedural sequence. Prefer precise substitutions and removal of empty wording. Preserve information-bearing repetition, including technical terms; synonym rotation can reduce precision.
3. Review rhythm only where there is enough running prose. Change an awkward run of similar sentences only when splitting or combining improves reading. A one-line answer, list, or table needs no rhythm treatment. Neither average sentence length nor punctuation frequency is a target. Ordinary, effective sentences need no change.
4. Compare the revision with the source. Remove an addition if it contributes neither meaning nor necessary grammar. Restore the original wording if it was already correct, clear, and shorter. Preserve grammatical repairs. Check numbers, citations, identifiers, qualification, quotation text, and the author's reported experiences. Do not introduce new recommendations, conclusions, admissions, or limitations.

The polishing model performs its diagnosis silently so the result remains prose only. The main agent can inspect source and result files without reproducing the entire passage in chat. If a revision changes meaning, do not apply it; retain the original and report the discrepancy. Do not silently rewrite the returned passage or resubmit unchanged text repeatedly. Deterministic validation is useful but cannot certify semantic fidelity.

## Language and genre calibration

Academic text keeps disciplinary structure, measured claims, and appropriate formality. Results describe observations; Methods preserve reproducible detail. Removing a qualification because it sounds hesitant changes the science. Do not add unsupported caution to sound rigorous.

Chinese prose keeps its source variant, regional usage, and venue conventions. Simplify padded verbs such as “进行讨论” when “讨论” retains the meaning. Remove connective stacking only where the relationship remains clear. An isolated “此外”, “然而”, or “不是……而是……” is legitimate. English vocabulary lists do not translate into Chinese prohibitions. Formal writing does not need conversational particles, and numbers or punctuation must not be changed to imitate a supposed human distribution.

Do not apply fiction techniques such as adding twists, chronology shifts, invented details, or unresolved subplots to factual writing. Do not apply every stylistic rule to every paragraph, insert mistakes, or promise AI-detector outcomes.

## Source and adaptation

Adapted from [Nanako0129/sepia](https://github.com/Nanako0129/sepia), version 0.13.0, inspected 2026-11-15 at commit `0eb11d6c64c962fcc35f596c167c6e38045d6cb8`:

- [Canonical skill](https://github.com/Nanako0129/sepia/blob/0eb11d6c64c962fcc35f596c167c6e38045d6cb8/skills/sepia/SKILL.md): operation boundaries, calibration, and author/source preservation.
- [Professional pass](https://github.com/Nanako0129/sepia/blob/0eb11d6c64c962fcc35f596c167c6e38045d6cb8/skills/sepia/references/professional-pass.md): genre fit, relevance, and information density.
- [Discourse pass](https://github.com/Nanako0129/sepia/blob/0eb11d6c64c962fcc35f596c167c6e38045d6cb8/skills/sepia/references/discourse-pass.md): attention to paragraph contributions; Writune excludes its narrative rearrangement prescriptions.
- [Style pass](https://github.com/Nanako0129/sepia/blob/0eb11d6c64c962fcc35f596c167c6e38045d6cb8/skills/sepia/references/style-pass.md): selective editing, rhythm, deletion/reversion checks, and overcorrection safeguards.
- [Chinese calibration](https://github.com/Nanako0129/sepia/blob/0eb11d6c64c962fcc35f596c167c6e38045d6cb8/skills/sepia/references/languages/zh.md): language-specific judgment rather than literal transfer of English rules.

These are adapted editorial heuristics, not a validated improvement metric. Sepia distinguishes detection studies from tested interventions; its reported detection statistics do not establish that this adaptation improves writing or passes detectors. Writune does not adopt model fingerprint tables, numerical style quotas, or the full upstream routing system. See [Sepia's MIT notice](sepia-license.md).
