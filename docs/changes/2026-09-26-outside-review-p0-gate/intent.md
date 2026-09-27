---
change: 2026-09-26-outside-review-p0-gate
status: draft
track: T3
linear: pending
---
# Intent: structured outside-review gate blocks P0 and stops trusting clean prose

## What James asked for (2026-09-26)
`validateOutsideReview(text, 'structured')` in `lib/outside-review-result.ts`:

1. The gate verdict only failed on P1, so a `[P0]` or native `P0:` finding
   PASSED. /codex review treats P0 as blocking; this gate must too.
2. The no-findings check phrase-matched prose ("no actionable bugs/issues/...").
   Codex's clean wording varies run to run ("No actionable defects", "no
   actionable issues", "No actionable regressions" all seen 2026-09-26), so this
   is fragile. Decide between the prose match and the explicit NO_FINDINGS
   sentinel; stay fail-closed either way.

## Why it matters
The structured pass is the P1 gate on large diffs in /review and /ship. A false
PASS on a P0 lets the most severe class of finding through the only gate that
reads it.

## Affected systems
- `lib/outside-review-result.ts` (validator)
- `scripts/resolvers/review.ts`, `scripts/resolvers/outside-voice.ts`,
  `claude-code/SKILL.md.tmpl` (prompt and gate wording)
- Generated `review/` and `ship/` section docs
