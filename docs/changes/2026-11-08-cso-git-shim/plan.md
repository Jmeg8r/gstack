---
change: 2026-11-08-cso-git-shim
status: accepted
track: T3
intent: ./intent.md
spec: ./spec.md
linear: pending
---
# Plan: the CSO helper always runs the Git it resolved

## Context
See intent.md: Bun's `realpathSync('/usr/bin/git')` sometimes returns another hard-link
name of the xcode-select shim (`/usr/bin/python3`), so the helper spawns the wrong tool,
or real Git without hardening. Worktree
`/Volumes/Projects/Repositories/.worktrees/gstack-cso-git-shim`, branch `fix/cso-git-shim`
off `origin/main` `f0a566a26`. The installed `~/.claude/skills/gstack` is not touched.

## Files that change
| File | Change |
|---|---|
| `test/cso-executable-hardlink.test.ts` (new) | R1–R3 cases; committed first and failing |
| `lib/cso/process.ts` | `canonicalExecutable`; used in `executable()` and `hardenGit()` |
| `.github/workflows/free-tests.yml` | add the new test to `macos-named-regressions`, the only CI job where the darwin stress case runs (Linux realpath keeps the opened name, so the other cases pass there even on the old code) |
| `docs/changes/2026-11-08-cso-git-shim/*` | these artifacts |

## Order of work
1. Commit the failing test.
   - **Stress (darwin, skipped when `/usr/bin/git` is absent or has `nlink` 1):** 20,000
     calls of `executable('git')` return one value; the shim inode under `/usr/bin` gives
     `/usr/bin/git`.
   - **Hard links:** a temp dir holds an executable `tool` and a hard link `alias`.
     `canonicalExecutable` of each returns its own path, over 5,000 alternating calls.
   - **Symlinks:** relative chain, absolute target, symlinked parent dir and a loop,
     compared with Node's lexical expectations; the loop throws.
   - **hardenGit:** `git()` against a temp repo 2,000 times in a row never yields
     `unsupported invocation` (runs on any platform with a trusted Git).
2. Implement `canonicalExecutable` and switch both call sites. The test passes.
3. VERIFY (below).

## Risks
- The walk could differ from `realpathSync` on an odd layout (for example `/usr/bin`
  itself as a symlink, or firmlinks). Mitigation: firmlinks are not symlinks to `lstat`,
  and the symlink tests cover symlinked parents. The CSO suites exercise the Docker
  multicall (`cso-docker-argv0`).
- Windows is untouched by construction (R4). Not testable on this Mac; the code path is
  a branch on `process.platform`.

## Proof
- `bun test test/cso-executable-hardlink.test.ts` fails on `f0a566a26` (stress case) and
  passes on the fix.
- Scratch stress script: 5,000 helper `git(repo, ['rev-parse','--show-object-format'])`
  calls at 300-way concurrency under `childEnvironment` → 0 failures (before: about 2%).
- CSO suites with a real-path `TMPDIR` (`TMPDIR=$(realpath "$TMPDIR")`):
  `bun test test/cso-*.test.ts` → no `unsupported invocation` failures. Any other failure
  is listed with its cause; `cso-cache` 'recovers dead immutable leases' is known and
  out of scope.
- `bun run typecheck` (if present), `snyk test`, the forge gate, then the Codex review.

## Options not taken
- **`xcrun --find git` at setup.** Picks Xcode's real Git and bypasses the shim, but
  leaves `realpathSync` exposed for every other multiply-linked trusted binary, and it
  adds a setup-time dependency on xcrun.
- **Prefer `/opt/homebrew/bin/git`.** Changes the trusted directory order (a security
  decision) to dodge a canonicalization bug.
- **Pass `DEVELOPER_DIR`/`TMPDIR` to the child.** Disproved: environment has no effect.
- **Skip canonicalization.** `cso-docker-argv0` depends on resolving OrbStack's
  `docker -> docker-tools` symlink, and the hardening check compares canonical paths.

## VERIFY results (2026-11-08, rebased onto `c87512939`)
- `test/cso-executable-hardlink.test.ts`: with `canonicalExecutable` temporarily set to
  the old `realpathSync`, 3 of 4 fail (the stress case returned `/usr/bin/python3`; 47 of
  2,000 helper `git()` calls failed with `unsupported invocation`). With the fix: 4 of 4
  pass, and 15 of 15 together with `cso-git-hardening`.
- Stress, 5,000 helper `git()` calls at 300-way concurrency under `childEnvironment`:
  **340 failures on `f0a566a26`, 0 on the fix** (before and after the rebase).
- All CSO suites with a real-path `TMPDIR`, rebuilt binary: 848 pass, 35 skip, 3 fail,
  **0 `unsupported invocation`**. None of the three is caused by this change:
  - `cso-cache` 'recovers dead immutable leases': known, out of scope.
  - `cso-file-ceiling` `afterAll` hook timeout (the rm of the 5,000-file fixture runs past
    5 s): it also fails with the old `process.ts` swapped back in.
  - `cso-requalification` 'the committed catalog is not older than its trigger inputs'
    (`preparationSha256`): it already fails on `main` at `c87512939`, because PR 25 edited
    `process.ts`, which is in the image helpers' import closure. This change moves that
    hash again. Requalifying the runtimes is a separate promotion run, not done here.
- `bun run typecheck` is clean. `typecheck:test`: "819 known diagnostics, none new".
  `snyk test`: 140 dependencies, 0 issues.
- Adversarial review (fresh-context correctness panel): no correctness bug that the
  current callers can reach. The two low-severity test findings (no CI job ran the darwin
  case; a tight default timeout) are fixed by the `free-tests.yml` row and explicit test
  timeouts.

## Deviations
- The branch was cut from `f0a566a26` and rebased onto `c87512939` (PR 25) before the
  push. The only overlap was PR 25's one-line `HASH_KEYS` addition in `process.ts`, which
  rebased without conflict.
- `.github/workflows/free-tests.yml` joined the change after the adversarial review
  (recorded in Files that change).
