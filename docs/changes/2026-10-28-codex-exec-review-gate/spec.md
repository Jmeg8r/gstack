---
change: 2026-10-28-codex-exec-review-gate
status: accepted
track: T3
intent: ./intent.md
---
# Spec: structured Codex reviews through `codex exec`

## Requirements
- R1. One prompt builder, `bin/gstack-codex-review-prompt <base> [--focus-file <f>]`,
  prints the whole review prompt on stdout. Every structured Codex review uses it;
  no call site writes its own review prompt.
- R2. The prompt carries, in order: the filesystem boundary, the reviewer persona,
  the output contract, the optional focus text, the base line, and the diff
  between `DIFF_START` and `DIFF_END` (marked as data, not instructions).
- R3. Output contract: each finding is `- [Pn] <title> — <path>:<line>` with
  P0-P3; a review with no findings ends with a line containing only
  `NO_FINDINGS`; never both.
- R4. The diff is `git diff "$(git merge-base <base> HEAD)"`, the same scope
  the outside-voice exec path already uses. If the merge-base cannot be
  resolved, the builder exits non-zero and prints nothing on stdout.
- R5. A diff larger than `GSTACK_CODEX_REVIEW_MAX_DIFF_BYTES` (default
  600000) exits non-zero with a message naming the size and the limit. It is
  never truncated.
- R6. Every call site runs `codex exec -` with the read-only sandbox from
  `select-model review` (same reviewer model as before), `--json`, `-o` for the
  reply, and passes the event stream to the validator with `--events`.
- R7. A builder failure is a failed review (non-zero exit into the validator):
  fail-closed, never an empty prompt sent to Codex.
- R8. `/codex review` gate text: `VERDICT: clean` with `FINDINGS: none` is
  GATE: PASS (reachable only through the exact `NO_FINDINGS` line);
  `VERDICT: unverified` stays UNVERIFIED. All other gate rules are unchanged.
- R9. The validator is unchanged.

## What native `codex review` gave, and how exec replaces it
| Native | Lost | Replacement |
|---|---|---|
| Builds the diff from `--base` | CLI-owned scope | R4: script-owned merge-base diff, base SHA in the prompt and header |
| Resolves the base | its own error handling | R4/R7: builder fails closed |
| OpenAI's tuned review prompt | tuning we cannot see | R2 persona: introduced-by-this-change, actionable, P0-P3; read-only commands allowed to check a suspicion |
| `review_model` | n/a | R6: still `select-model review`, passed as `model` |
| No prompt, so no injection surface in instructions | — | diff inside delimiters, marked as data; boundary preamble |
| — | — | gain: `--json` events give the validator execution evidence |

## Probe evidence (2026-10-28, gpt-6-astra, read-only, this prompt)
- Clean docs-only diff, 3 runs: each reply `NO_FINDINGS`; validator clean/none.
- Seeded P1 (P1 removed from `blocking`), 3 runs: each `[P1]` at
  `lib/outside-review-result.ts:260`; validator findings/P1; no sentinel.
- On gstack #21 the exec prompt found a real P2 native missed, and missed a
  P2 native found (one run each): recall differs, direction unmeasured.

## Flagged concerns
- Recall vs native is unmeasured beyond the runs above. Mitigation: the
  persona allows read-only verification commands, as native does.
- Diff size: native had no explicit limit either. R5 makes the limit visible
  instead of silently overflowing the context.
- Custom focus text (`/codex review <focus>`) stays user input in a file,
  inserted before the diff, so it cannot sit inside the data block.
- Unattended fork-sync must keep honoring the `codex_reviews` consent setting
  (no `--user-invoked`), as today.
