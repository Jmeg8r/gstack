---
change: 2026-11-08-cso-symlink-alias
status: accepted
track: T3
intent: ./intent.md
spec: ./spec.md
linear: pending
---
# Plan: /cso audits repositories that contain tracked symlinks

## Context
`gstack-cso start` refuses any repo with a tracked symlink (`UNSAFE_PATH: Symlink or special
source file`), even under `--paths`. That blocks /cso on claudeclaw and studydeck
(`AGENTS.md -> CLAUDE.md`, a deliberate anti-drift symlink in claudeclaw, #699) and blocked
ClaudeClaw ASTGL-74's security pass. Accepted intent and spec (fork `jfcadm/gstack`,
`docs/changes/2026-11-08-cso-symlink-alias/`): record each symlink as a never-followed
manifest entry, either an alias (an in-repo regular file, coverage complete) or unaudited
(coverage partial), with `--paths` honoured, diff mode hashing `120000` link text, and race
re-checks. Worktree `/Volumes/Projects/Repositories/.worktrees/gstack-cso-symlink-alias`,
branch `fix/cso-symlink-alias` off `origin/main` `f0a566a26`. The installed
`~/.claude/skills/gstack` is not touched.

## Files that change
| File | Change |
|---|---|
| `docs/changes/2026-11-08-cso-symlink-alias/plan.md` | this plan, committed first |
| `test/cso-snapshot-symlink.test.ts` (new) | all R1–R9 cases, fixture style of `test/cso-snapshot-identity.test.ts`; race cases via `spyOn(fs,'lstatSync')` like `test/cso-snapshot-disappearance-race.test.ts` |
| `lib/cso/snapshot.ts` | `rejectSpecialFiles` skips symlinks; new `classifySymlink`; symlink branch in the capture loop (before the directory and `--paths` branches); symlink branch in `assertEntriesStable`; `gitModes`/`gitHashes` for links |
| `docs/troubleshooting.md` | `#cso-capacity`: alias and unaudited behaviour, F4 limit; drop "Symlinks must be removed" |

Nothing in `cli.ts`, `contracts.ts`, `verification.ts` (F1).

## Order of work
Each step → verify, then commit (conventional, `-F` message file, `git branch --show-current` first).
0. **Baseline:** `bun install --frozen-lockfile`; `bun test test/cso-snapshot-*.test.ts test/cso-*coverage*.test.ts` (whichever exist) on the untouched branch → record pass counts. Commit plan.md (`docs(plan):`).
1. **Tests first (red):** write `test/cso-snapshot-symlink.test.ts`:
   - alias `AGENTS.md -> CLAUDE.md`, nested alias `docs/x.md -> ../README.md` → `excluded: symlink alias of <handle>`, handle equals `snapshotPathHandle(snapshotPathId(repo,target))`;
   - unaudited, one test each: absolute, `../outside` escape, dangling, directory, chain (link → link), crossing a symlinked dir, not in inventory (ignored target), non-UTF-8 link text → `withheld: symlink not followed (<reason>)`;
   - disclosure: plant a marker in an outside file and in the link text; grep `snapshot.json`, `readable/`, `snapshot/` → 0 hits; positive control finds it in a control file;
   - coverage: drive the same path `cli.ts` uses. `snapshotCoverage` is not exported, so assert the manifest shape that function keys on (`excluded:` prefix vs other `not-read` entries); plus one end-to-end through the CLI `start` path if an existing test shows how, else a recorded gap;
   - `--paths` excludes a symlink → `OUTSIDE_AUDIT_PATHS`; symlink under `node_modules/` → dependency exclusion; no throw;
   - diff (`base` arg): unchanged symlink not in `changedPaths`; retargeted in; regular file replaced by symlink in;
   - race: retarget on the 2nd `lstat` of the link; swap link→file → `SNAPSHOT_RACE`;
   - FIFO still rejected with the existing text (already covered; assert again beside a symlink);
   - `assertSnapshot` passes and no symlink exists under `snapshot/` or `readable/` for every fixture.
   `skipIf(win32)` for symlink tests. → run: new file red for the expected reasons (the current code throws `UNSAFE_PATH`), baseline suites still green. Commit `test:`.
2. **Implement** in `snapshot.ts`, smallest edits in existing style:
   - `rejectSpecialFiles`: `if (!stat.isFile() && !stat.isSymbolicLink()) special.push(path);`
   - `classifySymlink(repo, path, inventory: Set<string>, objectFormat)`: `readlinkSync(full,{encoding:'buffer'})`; `linkHash = createHash(objectFormat).update(\`blob ${len}\0\`).update(buf)`; decode with fatal `TextDecoder`; reject absolute / `posix.normalize(posix.join(posix.dirname(path), text))` escaping or empty; require `inventory.has(target)`, `!noReadPath(target)`, `containedFile(repo,target)` ok, `lstat(target).isFile()`; map each failure to the spec's reason string; discard text.
   - capture loop: on `source.isSymbolicLink()` (after `lstat`, before `isDirectory`): out of scope or dependency → existing transformation; else alias or unaudited entry; always `gitModes.set(path,'120000')`, `gitHashes.set(path,linkHash)`, `symlinks.set(path,{dev,ino,linkHash})`; `continue`.
   - `assertEntriesStable`: before the regular-file branch, if `symlinks.has(e.path)` → `lstat` is a symlink with same `dev`/`ino` and the re-read link hash is equal, else `SNAPSHOT_RACE`.
   → new file green; baseline suites same counts. Commit `fix:`.
3. **Mutation check** (throwaway copies, never committed): (a) skip the `containedFile` check, (b) drop `'120000'`, (c) remove the symlink stability branch, (d) accept absolute targets → each turns ≥1 new test red. Record in plan.md Deviations/Verify.
4. **Docs (R10)** in `docs/troubleshooting.md#cso-capacity`. Commit `docs:`.
5. **VERIFY:** `bun test` for all CSO suites that run without Docker; `bun run typecheck` (or the repo's equivalent, from `package.json`); `security-review` on the diff; `bun run build:cso` in the worktree, then the worktree launcher `start --diff --base origin/main` on (i) this fork and (ii) the ClaudeClaw ASTGL-74 worktree → run created, `inspect` shows the symlink classified, coverage as R5 predicts; `finish` both runs. If the launcher refuses outside the installed distribution, record it and defer the live check to deployment.

## Risks
- **Weakening a security tool.** Mitigated by never-follow (only `lstat`/`readlink`), containment through the existing `containedFile`, race re-check, mutation-checked tests, and the self-audit.
- **Diff-mode false changes** if the hash doesn't match git's: test (unchanged symlink not changed) proves equality with `git ls-tree`.
- **Upstream merge conflicts** (F1): edits confined to 4 places in one file.
- **Platform:** macOS and Linux symlink semantics only; Windows tests skip as elsewhere.
- **Build/install side effects:** `build:cso` writes `bin/` in the worktree only; check `git status` after and ensure build outputs are ignored, else do not commit them.

## Proof (becomes VERIFY)
- New test file: all cases pass; the 4 mutants each fail ≥1 case.
- Existing CSO suites: same pass counts as baseline step 0.
- Typecheck clean. `security-review`: findings recorded.
- Live: both runs created and classified as predicted; reports finished.
- `git diff --stat origin/main...HEAD`: only the 4 files above plus the change folder.

## Options not taken
- Removing the symlinks in claudeclaw and studydeck (James chose the tool fix).
- Following in-repo symlinks to copy their target (a second copy, and a following path).
- Coverage-code changes in `cli.ts` (the `excluded:` prefix already gives the decided coverage).
- Rebuilding symlinks in the execution copy for comprehensive mode (F4, separate change).

## Baseline (step 0, 2026-11-08, `f0a566a26` + change docs)
- `bun test test/cso-snapshot-*.test.ts` → 100 pass, 0 fail across 3 files, **only with
  `TMPDIR` set to its real path** (`/private/var/folders/...`). With the macOS default
  (`/var/folders/...`, under the `/var -> /private/var` symlink) 85 of 100 fail with
  `Private state has a symlink or non-directory ancestor`: the private-state guard
  refusing a symlinked ancestor, as designed. Every test run in this change uses the real path.

## Deviations
- **Three existing tests encoded the removed rule and were rewritten, not left unchanged**
  (intent success signal "existing suites pass unchanged"). Each now asserts its stated
  purpose under the new behaviour:
  - `test/cso-snapshot-state.test.ts` "rejects symlinks and hard links as execution inputs"
    → "never follows a symlink into an execution input, and rejects hard links": the
    `/etc/passwd` link is `withheld (absolute)`, absent from both copies; the hard link is
    still refused. The original `expect(...).rejects` calls were not awaited, so the
    hard-link half never actually ran; both are awaited now.
  - same file, "rejects broken untracked symlinks instead of silently omitting them" →
    "records a broken untracked symlink explicitly…": `withheld (missing target)`.
  - `test/cso-ordinary-repos.test.ts` negative control: a symlink to an outside directory
    is a withheld gap (coverage partial), never the nested-repository boundary exclusion,
    and the outside content reaches no run file. CLI-level; runs only with built binaries.
- **One extra unaudited reason**, `target is not a regular file` (an ignored FIFO, socket
  or device as the target), not in the spec's list.
- **Pre-existing CLI-suite failures.** `cso-cli`, `cso-ordinary-repos`, `cso-cache`,
  `cso-eval`, `cso-git-hardening` and `cso-lease-identity` fail 33 tests identically on
  untouched `origin/main` and on this branch (they need built binaries); no difference in
  the failing set. They are rerun after `build:cso` at VERIFY.
- **Race tests moved below the repository root after the first mutation pass** (commit
  `6cded4521`). With the symlink stability check disabled, both race tests still passed:
  their link was in the root, and `guard()`'s repository-root identity check (the root
  directory and its Git marker, by mtime/ctime) caught the unlink first. The raced link is
  now `docs/guide.md`, and the assertion requires the `SNAPSHOT_RACE` message to name it.

## Mutation check (step 3)
Each mutant applied to the committed `snapshot.ts`, `test/cso-snapshot-symlink.test.ts` run,
then restored with `git restore`:

| Mutant | Result |
|---|---|
| skip `containedFile` on the alias target | 2 fail (chained symlink, crossing a symlinked directory) |
| record `100644` instead of `120000` | 1 fail (diff mode) |
| stability check never throws | 2 fail (retarget, swap), after the race tests moved below the root |
| accept absolute targets | 1 fail (absolute target) |
