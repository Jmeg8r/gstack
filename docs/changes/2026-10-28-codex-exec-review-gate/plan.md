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

## Deviations
- The builder also takes `--commit <sha>` and `--uncommitted` (R1 named only
  `<base>`), so `/codex review` keeps its three scopes on one path. Tests cover both.
- `/codex review` had two paths (native scoped review, and `codex exec` with a
  hand-written prompt for custom focus). They are now one: the builder with an
  optional `--focus-file`. Tests that counted two dispatch blocks or two
  `--start codex-review` tokens now count one.
- R7 is implemented as `exit 1` before Codex runs (the block stops with
  "Not run: the review prompt could not be built"), not as a non-zero exit fed
  to the validator. Nothing is sent, so nothing is validated.
- `CODEX_REVIEW_MODEL_CONFIG_FLAG` (`-c review_model=...`) is removed: no
  template uses it once native review is gone. `select-model review` still
  reads `review_model` from config, and exec receives the result as `model`.
- The builder sets `BASH_COMPAT=50` (its prompt heredoc is over 512 bytes; the
  heredoc pipe-deadlock guard requires it).
- Tests that encoded native-only rationale were rewritten for exec: skill
  validation (scope), codex-hardening (sandbox pin, gate text), web-search flag
  (model), skill isolation (boundary now lives in the builder), outside-voice
  invocation (argv, sandbox), binding drift, model-role and effort counts.
  Size pins for the review skill were lowered and the copilot/factory codex
  pins deleted; both skills shrank.
- Adversarial review (T3, 2026-10-28) found that an empty diff built a valid
  prompt, and a model asked for NO_FINDINGS would pass it (P1). The builder now
  exits 1 on an empty diff. Also from that review: base mode includes untracked
  files (R4 said `git diff <merge-base>`, which skips them, while the docs said
  working tree included), untracked symlinks are shown as their target, and
  `--no-ext-diff --no-textconv` keep user diff config out of the patch. The
  builder lists untracked files from the top level. Each fix has a test that
  failed first.
