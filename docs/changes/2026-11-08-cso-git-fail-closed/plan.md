---
change: 2026-11-08-cso-git-fail-closed
status: accepted
track: T3
intent: ./intent.md
spec: ./spec.md
linear: pending
---
# Plan: CSO refuses a Git-named child it cannot harden

## Context
See intent.md and spec.md. Worktree
`/Volumes/Projects/Repositories/.worktrees/gstack-cso-git-fail-closed`, branch
`fix/cso-git-fail-closed`, stacked on `fix/cso-git-shim` `90edb015c` (PR 26, open,
CI green). PR 26 introduces `canonicalExecutable` and edits the same line. Once PR 26
merges, this branch is rebased onto `origin/main` before its own merge.

## Files that change
| File | Change |
|---|---|
| `test/cso-git-fail-closed.test.ts` (new) | R1–R3 cases; committed first and failing |
| `lib/cso/process.ts` | `hardenGit`: the two `return { args }` fallbacks become throws |
| `docs/changes/2026-11-08-cso-git-fail-closed/*` | these artifacts |

## Order of work
1. Commit the failing test (`test(cso): …`). Locally, 4 of its 5 cases fail now.
2. Change `hardenGit` per the spec (`fix(cso): …`). All 5 pass.
3. VERIFY (below), then record the results in this plan.
4. `forge-pr create`, base `fix/cso-git-shim` while PR 26 is open (so the diff shows only
   this change), retargeted to `main` after PR 26 merges. Run `/codex review` and post its
   output verbatim with PASS/FAIL as a PR comment.

## Risks
- A caller not found by the survey spawns another Git through `runProcess`. Mitigation:
  the survey covered every `runProcess` reference in `lib`, `scripts`, `test` and `bin`.
  The full CSO suites run in VERIFY, and a missed caller would fail loudly
  (`UNSAFE_PATH`), not silently.
- Windows `git.exe` from a different directory is now refused (spec C2). The Windows
  CI job covers it.

## Proof
- `bun test test/cso-git-fail-closed.test.ts` fails on `90edb015c` (4 fail, 1 pass) and
  passes after the fix (5 pass).
- `TMPDIR=$(realpath "$TMPDIR") bun test test/cso-*.test.ts`: no new failures compared
  with the same run on `90edb015c`. Each failure is listed with its cause. Known
  pre-existing: `cso-cache` 'recovers dead immutable leases'.
- `bun run typecheck` if present, `snyk test`, the forge gate, then the Codex review.

## Options not taken
- **An opt-out parameter for test doubles.** The survey found no caller that needs one,
  and a bypass would undo the fail-closed property.
- **Harden by canonical path instead of basename** (spec C1). Stricter, but it changes
  which children are hardened. That is a separate change.
- **Keep the fallback and log a warning.** Still runs unhardened Git. Rejected.
