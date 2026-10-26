---
change: 2026-10-26-fork-sync
status: accepted
track: T1
author: james
accepted_by: james
linear: pending
source: conversation
---
# Intent: Script the gstack fork sync up to the merge decision

## Problem

"Script the fork sync end to end up to my merge decision, following this session's steps: hooks backup, scratch-worktree --no-ff merge, fork-delta check, suite plus paired standalone comparison, Codex review, forge PR with the report."

Upstream garrytan/gstack ships about 8 releases a day (205 in the 30 days to 2026-10-26). `/gstack-upgrade` only fast-forwards from `origin`, which is the forge copy `jfcadm/gstack`, so it can never bring in upstream changes. The fork has to be synced by hand. Each of the two hand syncs so far (forge PR #16 to v1.91.179.0, forge PR #17 to v1.91.226.0) took about an hour of agent work, and they repeat the same steps every time. The third time is the trigger to automate.

## Proposed outcome

One command runs a sync from the live checkout's repo and either:

- **stops before anything reaches the forge**, leaving the scratch worktree and a report naming each merge conflict, each fork-caused test failure, or a Codex FAIL, so that James or an agent can fix it in that worktree and re-run to resume; or
- **opens a forge PR** whose body is the run's report, with the Codex verdict posted verbatim as a PR comment, and stops there for James's merge decision.

Steps, in the order the manual syncs ran them:

1. Pre-flight: fetch `origin` and `github`; refuse unless the forge `main` and the GitHub mirror `main` are the same commit (a forge push force-mirrors to GitHub, lessons L45).
2. Back up `~/.git-hooks` and record checksums.
3. Create a scratch worktree on `upgrade/sync-v<upstream VERSION>` off `origin/main` and merge `github/main` with `--no-ff`.
4. Fork-delta check: the fork's own changes against upstream still apply after the merge.
5. `bun install`, `bun run build:cso`, `bun run gen:skill-docs` (must leave no drift), `bun run typecheck`, `bun run typecheck:test`.
6. Full free suite (`bun run test`) on the merge. Each file with a failure is then rerun on its own in the merge worktree and in a pure `github/main` worktree. A failure counts against the fork only if it happens in the merge worktree and not upstream, and it still does after reruns (flake filter).
7. Codex review of the fork's delta (`--base github/main`) through the installed `/codex` review path, with the verdict from `lib/outside-review-result.ts`.
8. Re-check `~/.git-hooks` against the backup; a mismatch stops the run.
9. Push the branch to `origin`, open the forge PR with the report, and post the Codex output verbatim with an explicit verdict.

## Affected users and systems

- James, as the operator who merges.
- The fork checkout `~/Projects/gstack` (also `/Volumes/Projects/Repositories/gstack`), which is the live gstack install through `~/.claude/skills/gstack`. The script must never change what that checkout has checked out.
- The forge `jfcadm/gstack` and its push mirror `Jmeg8r/gstack` (sync_on_commit).
- The Codex CLI (paid model calls), and `~/.git-hooks` (read and backed up only).

## Constraints

- Stops before pushing on: merge conflicts, generated-doc drift, a typecheck failure, any fork-caused test failure, a Codex FAIL (P0/P1 or a failed run), or a `~/.git-hooks` mismatch. PASS and UNVERIFIED Codex verdicts both proceed to the PR, with the verdict posted verbatim.
- Never merges, never pushes `main`, never force-pushes, and never bypasses the forge ai-review gate.
- Never modifies `~/.git-hooks`, `~/.claude/settings.json`, or the live checkout's branch or working tree.
- Lives in a fork-only path that cannot conflict with upstream on a future sync. `.forgejo/` holds the fork's other forge tooling. Note that the egress-receipt scanner skips dot-directories, so it will not read the script.
- The forge token stays out of argv and logs (read the way `forge-pr` reads it).
- Re-running after a fix resumes from the existing scratch worktree; it does not start a second merge.

## Out of scope

- Resolving conflicts or fixing regressions automatically.
- Scheduling (cron or a launchd job). The script is run on demand.
- Merging the PR, fast-forwarding the live checkout, or re-running `./setup` after the merge.
- The pre-existing `bin/blast-radius.py` P2 and the egress-scanner finding on `bin/review-pr.sh`.

## Success signals

- A dry-run mode against the live fork completes pre-flight, merge, fork-delta check and build checks in a scratch worktree, without pushing, and prints the report.
- Injecting a conflicting change in a test repo makes the script exit non-zero before any `git push` (the remote's refs are unchanged, checked with `git ls-remote`), with the conflicting path named in the report.
- A file that fails on the merge worktree and not on pure upstream is listed as fork-caused; one that fails on both is not.
- `~/.git-hooks` checksums match before and after a full run.
- A full run on the next upstream release opens a forge PR whose body is the report, with one verbatim Codex comment, and the live checkout's `git status` and branch are unchanged.

## Open questions

- Linear: the gstack fork's Linear project and issue are not set up in this session (`linear: pending`).
- How many standalone reruns the flake filter needs. The manual syncs used one paired run plus three reruns on the files that differed.
