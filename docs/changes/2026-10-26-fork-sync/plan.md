---
change: 2026-10-26-fork-sync
status: draft
track: T1
intent: ./intent.md
linear: pending
---
# Plan: Script the gstack fork sync up to the merge decision

One Python 3 script (stdlib only, the same as `forge-pr`) runs the sync as numbered stages. It records progress in a run directory, so a re-run after a fix resumes at the first stage that hasn't passed. It never touches the live checkout's branch or working tree; all git work happens in worktrees under the run directory.

## Files that change

| File | What |
|---|---|
| `.forgejo/scripts/fork-sync` | New. Executable Python 3 (`#!/usr/bin/env python3`), stdlib only. CLI and stages. |
| `.forgejo/scripts/fork_sync_lib.py` | New. Pure logic with no subprocess calls, so it can be unit-tested: free-runner log parsing, failure classification, fork-delta comparison, hooks manifest diff, report rendering, stage state. |
| `.forgejo/scripts/test_fork_sync.py` | New. `unittest` suite: unit tests on the lib, plus integration tests that build throwaway git repos (bare `origin`, bare `github`, a clone) for the conflict-stops-before-push case and resume. |
| `.forgejo/scripts/README.md` | New. Usage, stages, stop conditions, exit codes, and how to resume. |
| `docs/changes/2026-10-26-fork-sync/plan.md` | This file; `## Deviations` added if the build departs from it. |

Nothing under upstream's paths changes, so future syncs cannot conflict on this work. `.forgejo/` is skipped by the egress scanner (dot-directory); the intent records that.

## Design

**CLI.** `fork-sync [--repo PATH] [--until STAGE] [--reruns N] [--run-dir PATH]`

- `--repo` defaults to the git toplevel of the current directory, which must have remotes `origin` (forge) and `github` (upstream).
- `--until merge|checks|suite|codex|pr` (default `pr`). `--until checks` is the dry run: pre-flight through build checks, with no suite, Codex or push.
- `--reruns` defaults to 3.
- `--run-dir` defaults to `~/.gstack-dev/fork-sync/v<upstream VERSION>/`.

**Exit codes.** 0 means the PR was opened or the `--until` stage passed. 10 means stopped on a problem (the report says what; the worktree is kept). 2 means usage or environment error. 3 means another run holds the lock.

**Run directory.** It holds `state.json` (stage → passed, plus the SHA it passed at), `report.md`, `logs/`, `hooks-backup/` with `hooks.sha256`, and the worktrees `merge/` (branch `upgrade/sync-v<VER>`), `upstream/` (detached at `github/main`) and `baseline/` (detached at the pre-sync `origin/main`). A `lock` file uses `O_EXCL` and holds the PID; a lock left by a dead PID is reported, never stolen silently.

**Stages,** each idempotent, recorded in `state.json` when it passes:

1. `preflight`: `git fetch origin` and `git fetch github main`. Stop unless forge `main` equals the GitHub mirror's `main` (`git ls-remote`; the mirror URL is read from `--mirror` or the `FORK_SYNC_MIRROR` env var, default `https://github.com/Jmeg8r/gstack.git`). Stop if `github/main` is already an ancestor of `origin/main` ("nothing to sync"). Record the live checkout's HEAD, branch and the sha256 of `git status --porcelain` for the final check.
2. `hooks-backup`: resolve `git config --global --get core.hooksPath`; if it's set, copy that directory to `hooks-backup/` and write per-file sha256. If it's unset, record "none" and skip the hooks checks.
3. `merge`: create the `merge/` worktree off `origin/main` on `upgrade/sync-v<VER>`, or reuse it if it exists. Then `git merge --no-ff --no-edit github/main`. On conflicts, list the paths (`--diff-filter=U`) in the report and stop with code 10. On resume, the stage passes once `MERGE_HEAD` is absent, the tree is clean, and `github/main` is an ancestor of HEAD; a merge someone else finished by hand counts.
4. `fork-delta`: take the fork's pre-sync delta, the files that differ between `merge-base(origin/main, github/main)` and `origin/main`. Every one of them must still differ from `github/main` after the merge; a fork file that now matches upstream is a silently dropped fork change. Also report the upstream commits since the base that touch fork-delta files (`git log base..github/main -- <files>`) as "review these". A dropped file stops the run.
5. `checks`: in `merge/`, run `bun install`, `bun run build:cso` and `bun run gen:skill-docs`, then require `git status --porcelain --untracked-files=no` to be empty (no generated drift). Then `bun run typecheck`, and `bun run typecheck:test` with output containing `none new`. Any failure stops the run.
6. `suite`: run `bun run test --verbose` in `merge/` (logged). Parse `  ✗ <file> — <test>` and `  ✗ <file> (crashed: …)` lines into the set of files with failures. A clean suite skips to Codex. Otherwise create `upstream/` and `baseline/`, each with `bun install` and `build:cso`, and run every failing file alone (`bun test <file>`, 600 s timeout) in all three worktrees. Then, for files where `merge/` has failures that `upstream/` doesn't, rerun `--reruns` more times in each worktree. A test name, normalized (timings and `/private/...` paths stripped), is classified:
   - **fork-caused**: fails in every `merge/` run and in no `upstream/` run, and in no `baseline/` run. This blocks.
   - **pre-existing on fork**: same, but it also fails in `baseline/`. Reported, not blocking. This is what keeps the `review-pr.sh` egress-scanner finding from blocking every run.
   - **flaky**: fails in some `merge/` runs but not all, or in some `upstream/` runs. Reported, not blocking.
   - **upstream/environmental**: fails in `upstream/` every time. Reported as a count.
   - The hooks check runs after this stage, because the suite is what wrote to `~/.git-hooks` before #3353.
7. `codex`: in `merge/`, run the installed `/codex` review path exactly as `~/.claude/skills/gstack/codex/sections/review-mode.md` does (`gstack-review-log --start`, then `gstack-codex-probe select-model review` and `run-with-timeout ... 330 codex review --base github/main` with the read-only sandbox config). The verdict comes from `bun lib/outside-review-result.ts --verdict --exit <code> --stderr <file> structured <out>`. Exit 0 or 4 (clean or unverified) passes. Exit 3 (findings, P0/P1) or 1 (unavailable) stops. Log the result with `gstack-review-log --finish`. The raw output is saved for the PR comment.
8. `hooks-verify`: re-hash the hooks dir against `hooks.sha256`. Any difference stops the run, shows the diff, and gives the restore command. It never restores automatically.
9. `pr`: `git push -u origin upgrade/sync-v<VER>` (never `--force`, never `main`). Then `forge-pr --repo <owner/name> create --base main --head <branch> --title "chore(upstream): sync fork to garrytan/gstack v<VER>" --body-file report.md`. Post the Codex comment (verbatim output in a `codex-review` fence plus an explicit `GATE: PASS|UNVERIFIED`) through `forge-pr`'s `ForgeAPI`, loaded the way this session did, so the token stays in `forge-pr`'s own reader. Afterwards, check that the mirror's `main` is unchanged and that the live checkout's HEAD, branch and status hash match the values recorded at pre-flight.

**Report** (`report.md`, also the PR body): versions and SHAs, the merge result, the fork-delta table and "review these" commits, build check results, suite classification counts plus a fork-caused/pre-existing/flaky table, the Codex verdict line, the hooks check, and the forge ai-review size-limit note. It ends with the post-merge steps (discard the `name:` lines, fast-forward the live checkout, `./setup --prefix`).

## Order of work

1. `fork_sync_lib.py` with its unit tests first (test-first: classification, log parsing, delta, hooks diff, state).
2. `fork-sync` stages 1 to 5, plus the integration tests: conflict stops before push, with the remote unchanged by `git ls-remote`; resume after a hand-resolved merge; a dropped fork file stops the run.
3. Stages 6 to 9.
4. README.
5. Dry run against the live fork: `--until checks`.
6. Commit per logical unit, push `feat/fork-sync`, open the forge PR, run the Codex review.

## Risks

- **The free suite still writes outside the hermetic home** (it did before #3353). Mitigated by the hooks backup and verify stage, which stops without restoring, so James sees it.
- **Free-runner output format drift** upstream breaks the parser. Mitigated: the parser fails closed. If the runner's summary reports failures but no `✗` lines parse, the stage stops with "could not attribute failures" instead of reporting clean.
- **Standalone reruns are slow:** about 25 to 45 minutes for 90 files across three worktrees. Accepted; documented in the README. The script is long-running, so the README says to launch it under `bin/gstack-detach` when an agent runs it.
- **The Codex review path changes upstream.** The script reads `review-mode.md` only for documentation and calls the probe binaries directly. A changed CLI fails at the probe step with its own error, not silently.
- **No egress receipts** for the push, PR and comment. This is fork-operator tooling outside gstack's shipped surface, and the intent records that the scanner doesn't read `.forgejo/`.

## Proof

- `python3 -m unittest discover -s .forgejo/scripts -p 'test_*.py' -v` → all pass. This includes `test_conflict_stops_before_push` (exit 10, conflicting path in `report.md`, `git ls-remote origin` unchanged) and `test_dropped_fork_file_stops`.
- Positive control: deleting the conflict check in `stage_merge` makes `test_conflict_stops_before_push` fail.
- `.forgejo/scripts/fork-sync --until checks` against the live fork (upstream at 1.91.248.0 or later) exits 0 and prints a `report.md` with the merge, delta and build results. `git -C /Volumes/Projects/Repositories/gstack status --porcelain` and its branch are unchanged afterwards, and forge `main` and the mirror are unchanged (`git ls-remote`).
- `~/.git-hooks` sha256 matches before and after the dry run.
- Codex review of `feat/fork-sync` (`--base origin/main`) is posted on its forge PR.

## Options not taken

- **A gstack skill instead of a script.** A skill would make an agent re-derive the steps every time, the cost being automated away, and would live in upstream-owned paths.
- **Bash.** The classification and report logic needs real data structures and unit tests; Python matches `forge-pr`.
- **Opening a draft PR on problems.** James chose stop-before-push.
- **Auto-restoring `~/.git-hooks`.** Restoring hides that something wrote there; stopping surfaces it.
- **Scheduling it (launchd).** Out of scope per the intent.
