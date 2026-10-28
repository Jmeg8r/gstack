---
change: 2026-10-28-codex-exec-review-gate
status: accepted
track: T3
intent: ./intent.md
linear: pending
---
# Plan: structured Codex reviews through `codex exec`

## Files that change
- New `bin/gstack-codex-review-prompt` and `test/codex-review-prompt.test.ts`.
- `scripts/resolvers/outside-voice.ts`, `scripts/resolvers/outside-voice-steps.ts`
  and the review/ship adversarial sections they render.
- `codex/sections/review-mode.md.tmpl` and its generated `review-mode.md`,
  `codex/SKILL.md.tmpl` (filesystem-boundary note).
- Tests that pin the native command: `codex-web-search-flag`, `codex-hardening`,
  `skill-validation`, `codex-invocation-sites`, `outside-voice-*` as they fail.
- `docs/troubleshooting.md` (outside-review section).
- sovereign-forge `bin/gstack-fork-sync.sh` and its tests (separate PR).

## Order of work
0. gstack #21 "zero" P2, test-first → verify: 22/22, suites match main. Done (0989537).
1. Probe gate → verify: 3/3 clean runs `NO_FINDINGS`, 3/3 seeded runs `[P1]`. Done.
2. Prompt builder, test-first → verify: tests for R2-R5 and R7 fail, then pass.
3. Outside-voice resolver → verify: rendered structured block runs the builder
   and `codex exec`, passes `--events`; related tests updated and green.
4. /codex review template and gate text (R8) → verify: `bun run gen:skill-docs`
   (or the repo's generator) is clean; no template still calls `codex review --base`.
5. Validator untouched → verify: `git diff origin/main -- lib/outside-review-result.ts` empty.
6. sovereign-forge fork-sync → verify: its self-tests, plus a dry run of the Codex step.
7. Whole-change checks → verify: typecheck, related suites vs main baseline,
   `snyk test`, a live /codex review of this branch reaching PASS or a tagged result.
8. Adversarial review (T3), forge PRs, Codex review through the new path posted
   verbatim with explicit PASS/FAIL.

## Risks
- Recall differs from native (spec, flagged concerns).
- Generated skill docs drift from templates; regenerate and diff.
- Tests that encode native-only rationale need rewriting, not deleting.

## Proof
- `bun test test/codex-review-prompt.test.ts` and the related suites.
- `bun run typecheck`.
- Probe results above, and the live review in step 7.

## Options not taken
- `-c developer_instructions` on native review: ignored (probe).
- Letting Codex run `git diff` itself: scope drift.
- Relaxing the validator to accept clean prose: ruled out by James.
