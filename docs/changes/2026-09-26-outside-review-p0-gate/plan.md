---
change: 2026-09-26-outside-review-p0-gate
status: draft
track: T3
intent: ./intent.md
linear: pending
---
# Plan: structured outside-review gate

## Files that change
- `test/outside-voice-invocation.test.ts`: new failing cases (committed first)
- `lib/outside-review-result.ts`: tag parsing, P0 block, unknown-tag and sentinel rules
- `scripts/resolvers/review.ts`, `scripts/resolvers/outside-voice.ts`,
  `claude-code/SKILL.md.tmpl`: wording
- `review/sections/adversarial.md`, `ship/sections/adversarial.md`: regenerated
- `test/fixtures/golden/{codex,factory}-ship-SKILL.md`: re-rendered

## Order of work
1. Failing tests: P0 fails; NO_FINDINGS line passes; tag beats sentinel;
   untagged prose / inline sentinel / unknown tags are not completed.
2. Validator fix.
3. Prompt wording, then `bun run gen:skill-docs`.
4. Adversarial review (pstack correctness seat) of the validator; close the
   unrecognized-severity class it finds, test-first.
5. Update the codex/factory ship goldens (intended wording only).

## Risks
- Clean native Codex runs degrade from `clean` to `unavailable` (see spec).
- Prompted providers that print `NO_FINDINGS` inline in a sentence now read as
  missing coverage; the prompts now ask for a standalone line.

## Proof
- `bun test test/outside-voice-invocation.test.ts`: new cases fail before, pass after
- `bun run test` failure set diffed against a clean origin/main worktree

## Options not taken
- Widen the prose regex: still fragile, still false-completes beside untagged defects.
- Port PR 9's rollout reader here: duplicates unmerged code; do it after PR 9 lands.
