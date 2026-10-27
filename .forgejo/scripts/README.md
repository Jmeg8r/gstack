# Fork-only scripts

These live under `.forgejo/` so they never conflict with upstream on a sync. Note that gstack's egress-receipt scanner skips dot-directories and does not read them.

## fork-sync

Syncs the `jfcadm/gstack` fork with upstream `garrytan/gstack` up to the merge decision, and no further. Every problem stops the run **before anything is pushed**; re-running resumes from the first stage that hasn't passed. A resumed suite stage first moves the earlier attempt's CI plans, results and shard logs to `attempts/<n>/` in the run directory.

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
| `suite` | The free suite runs through the runner's own CI mode: `--ci-plan`, then `--ci-run` per shard (the tree-mutating shard alone, last), then `--ci-verify`; a pass clears the stage. Otherwise: missing, duplicate or foreign results stop; a shard with no usable coverage (incomplete summary, timeout, attribution outside its plan) is environmental only if the same shard has no usable coverage on pure upstream too, or it stops; failures no named test explains stop unless `--accept-unaccounted` (agent-host home writes excepted); and every failing file plus a focused set (test files the fork changed or that name a fork-changed file) is rerun alone on the merge, upstream and the pre-sync fork (a file that hangs past the per-file timeout counts as failing), where a `fork-caused` result stops the run. |
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
| `--shards N` (concurrent shards in the runner's CI plan) | 6 |
| `--reruns N` (extra standalone reruns where the merge fails more than upstream) | 3 |
| `--run-dir PATH` | `~/.gstack-dev/fork-sync/v<upstream VERSION>-<upstream sha>`, or the one unfinished run if there is one. A finished run is never resumed. |
| `--mirror URL` (or `FORK_SYNC_MIRROR`) | `https://github.com/Jmeg8r/gstack.git` |
| `--accept-dropped PATH` (repeatable) | none |
| `--accept-unaccounted` (continue past suite failures no named test accounts for, each listed in the report) | off |
| `GSTACK_SKILL_ROOT` (where the Codex probe and validator live) | `~/.claude/skills/gstack` |
| `FORK_SYNC_EXTERNAL_HOME_WRITERS` (colon-separated globs of real-home paths agent hosts write while the suite runs; containment failures touching only these are reported, not blocking) | Claude Code's `.claude.json` backups, session files and `~/.claude/state/*`; the Codex app's SQLite journal, shell snapshots and locks |

### Exit codes

`0` done through `--until` (or nothing to sync) · `2` usage or environment error · `3` another run holds the lock (or a stale lock: remove it once no run is active) · `10` stopped on a problem, with nothing pushed · `130` interrupted (Ctrl-C or SIGTERM) after stopping every process group it started; re-run to resume.

### What it never does

It never merges, pushes `main`, force-pushes, bypasses the forge ai-review gate, or changes `~/.git-hooks`, `~/.claude/settings.json`, or the live checkout's branch or working tree. The forge token is read by `forge-pr`'s own reader and never appears in argv or logs.

### Tests

```bash
python3 -m unittest discover -s .forgejo/scripts -p 'test_*.py' -v
```
