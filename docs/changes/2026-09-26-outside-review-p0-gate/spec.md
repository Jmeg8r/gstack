---
change: 2026-09-26-outside-review-p0-gate
status: draft
track: T3
intent: ./intent.md
---
# Spec: structured gate contract

## Requirements
- R1. Any severity tag `[P0]`–`[P3]` (bracketed anywhere, or native `Pn:` at a
  line start after markdown stripping) completes the review.
- R2. Gate is `fail` when any tag is P0 or P1, else `pass`.
- R3. A tag outside P0–P3 (`[P4]`–`[P9]`) is `completed: false`
  (unknown severity), even beside known tags.
- R4. With no tags, the review completes (`pass`) only when a line is exactly
  `NO_FINDINGS` (optionally bulleted or wrapped in matching `*`, `_`, or
  backtick emphasis). All other output is `completed: false`.
- R5. Tags outrank a contradictory `NO_FINDINGS` line.
- R6. Prompts that can be steered ask for `[P0]`–`[P3]` or a line containing
  only `NO_FINDINGS`.

## Design decision: sentinel only, prose dropped
Prose matching fails in both directions: it misses new clean wordings, and it
completes a review whose only defect is untagged ("no bugs in the parser, but
the cache drops writes"). Requiring the sentinel is deterministic and
fail-closed.

## Flagged concerns
- **Native `codex review --base` cannot be prompted.** It never prints
  NO_FINDINGS, so a clean native run now reports `outside_status: unavailable`
  (GATE: MISSING COVERAGE, informational) instead of `clean`. No blocking
  finding is lost, because findings are always tagged. Follow-up: read Codex's
  structured `ReviewOutputEvent` verdict from the session rollout, the way
  jfcadm/gstack PR 9 (`lib/codex-review-gate.ts`) does for /codex review, once
  that PR merges.
- The sentinel check runs on raw text because `plainReview` strips `_`,
  which would erase the sentinel itself.
- R7 (from the adversarial review): P0/P1 tags are checked first, so a
  blocker beside an unknown tag still fails. Any other `P<n>` token the tag
  parser did not recognize (numbered list, table, lowercase, `P10`) makes the
  review `completed: false`. Cost: prose such as "p99 latency" reads as missing
  coverage. That is fail-closed noise, not a false pass.

## Adversarial review (pstack correctness seat, 2026-09-26)
- A differential fuzz (59,049 inputs) against origin/main found no new false
  PASS. The unrecognized-severity class above was pre-existing and is closed here.
- Runtime callers read only `.completed`; the GATE verdict comes from the skill
  prose. So `completed` is the live risk surface.
- Open, pre-existing, not changed here: a `NO_FINDINGS` line next to an untagged
  defect still completes. Closing it would mean rejecting any sentinel response
  that has other content, which is a contract decision for James.
