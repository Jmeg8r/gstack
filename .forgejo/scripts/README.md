# Fork-only scripts

These live under `.forgejo/` so they never conflict with upstream on a sync. Note that gstack's egress-receipt scanner skips dot-directories and does not read them.

## fork-sync

Syncs the `jfcadm/gstack` fork with upstream `garrytan/gstack` up to the merge decision, and no further. Every problem stops the run **before anything is pushed**; re-running resumes from the first stage that hasn't passed.

```bash
.forgejo/scripts/fork-sync --until checks   # dry run: through the build checks, nothing pushed
.forgejo/scripts/fork-sync                  # full run, ends with a forge PR
```

Run it from the fork checkout, which needs remotes `origin` (the forge) and `github` (upstream). A full run takes about an hour: the free suite, standalone reruns in three worktrees, and Codex. When an agent launches it, wrap it so a turn boundary can't kill it:

```bash
bin/gstack-detach --label fork-sync -- .forgejo/scripts/fork-sync
```

### Stages

| Stage | Stops when |
|---|---|
| `preflight` | Forge `main` and the GitHub mirror's `main` differ, or the forge already has the sync branch. Exits 0 with "nothing to sync" when upstream is already in forge `main`. |
| `hooks-backup` | Never. It copies the global `core.hooksPath` directory and records sha256 per file. |
| `merge` | `git merge --no-ff` of upstream into `upgrade/sync-v<VERSION>` conflicts, or the worktree has uncommitted changes. |
| `fork-delta` | A file the fork changed now matches upstream although upstream never took the fork's version (a dropped fork change). |
| `checks` | `bun install`, `build:cso`, `gen:skill-docs` (or it leaves drift), `typecheck`, or `typecheck:test` (new diagnostics) fails. |
| `suite` | A test fails on the merge in every run but never on pure upstream or on the pre-sync fork (`fork-caused`). Pre-existing, flaky and upstream failures are reported only. |
| `codex` | The Codex review gate is FAIL (P0/P1, or the review didn't run). PASS and UNVERIFIED continue, and the verdict is posted verbatim. |
| `hooks-verify` | The global hooks directory changed during the run. It is never restored automatically; the report gives the restore command. |
| `pr` | The live checkout changed, forge `main` moved, the mirror differs, or the push or `forge-pr create` fails. |

### Resuming after a stop

The report (`report.md` in the run directory, also printed) says what to fix and where. Fix it in the merge worktree (`<run dir>/merge`), commit, and run the same command again. Every stage from `merge` on records the merge HEAD it passed at, so a new commit re-runs those stages.

- **Conflicts:** resolve per hunk (`git checkout -m <file>`, then edit the markers), never with a whole-file `--ours` or `--theirs`. Then commit the merge.
- **An intended drop** (upstream replaced the fork's change): re-run with `--accept-dropped <path>`; the report lists it as dropped on purpose.

### Abandoning a run

A run pins the upstream and forge SHAs it started with, and resuming keeps them. To start over on a newer upstream instead, remove the run's worktrees, its local branch and its directory:

```bash
git worktree remove --force <run dir>/merge   # and <run dir>/upstream, <run dir>/baseline if present
git branch -D upgrade/sync-v<VERSION>
rm -r <run dir>
```

### Options

| Option | Default |
|---|---|
| `--until merge\|fork-delta\|checks\|suite\|codex\|pr` | `pr` |
| `--reruns N` (extra standalone reruns where the merge fails more than upstream) | 3 |
| `--run-dir PATH` | `~/.gstack-dev/fork-sync/v<upstream VERSION>`, or the one unfinished run if there is one |
| `--mirror URL` (or `FORK_SYNC_MIRROR`) | `https://github.com/Jmeg8r/gstack.git` |
| `--accept-dropped PATH` (repeatable) | none |
| `GSTACK_SKILL_ROOT` (where the Codex probe and validator live) | `~/.claude/skills/gstack` |

### Exit codes

`0` done through `--until` (or nothing to sync) · `2` usage or environment error · `3` another run holds the lock (or a stale lock: remove it once no run is active) · `10` stopped on a problem, with nothing pushed.

### What it never does

It never merges, pushes `main`, force-pushes, bypasses the forge ai-review gate, or changes `~/.git-hooks`, `~/.claude/settings.json`, or the live checkout's branch or working tree. The forge token is read by `forge-pr`'s own reader and never appears in argv or logs.

### Tests

```bash
python3 -m unittest discover -s .forgejo/scripts -p 'test_*.py' -v
```
