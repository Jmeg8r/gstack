---
change: 2026-11-08-cso-git-fail-closed
status: draft
track: T3
risk: high
risk_reason: hardenGit decides whether Git runs against an audited repository with fsmonitor, hooks and config includes disabled; a silent fallback runs Git, or a lookalike, without those protections
author: claude
linear: pending
source: concern C1 in docs/changes/2026-11-08-cso-git-shim/spec.md (jfcadm/gstack PR 26)
---
# Intent: CSO refuses a Git-named child it cannot harden

## Problem
`hardenGit()` in `lib/cso/process.ts` runs on every `runProcess` call. When the file's
basename is `git` or `git.exe`, it checks the file against the trusted Git from
`executable('git')`, then adds the hardening flags: `core.fsmonitor=false`,
`core.hooksPath=/dev/null`, `include.path=/dev/null`, the attribute and exclude nulls,
`--work-tree`, and the config-identity binding.

Two branches skip all of that and return the arguments unchanged:
- `executable('git')` throws (no trusted Git can be resolved);
- the file does not canonicalize to the trusted Git.

So a Git-named child that is not the trusted Git still spawns, without hardening. Until
PR 26, Bun's `realpathSync` race reached the second branch for real `/usr/bin/git`
about 1% of the time. PR 26 makes the comparison deterministic but keeps the silent
fallback (its concern C1). Any future caller that passes a different Git, or a stale
path, gets unhardened Git and no error.

## Proposed outcome
- A `runProcess` call whose file is named `git` or `git.exe` (any case) and is not the
  trusted Git throws `CsoError('UNSAFE_PATH')`, and nothing is spawned.
- When the trusted Git cannot be resolved, the same call throws the resolver's
  `CsoError` (`TOOL_UNAVAILABLE`) and nothing is spawned.
- A path that resolves to the trusted Git (including a symlink to it) is hardened as
  today.
- A regression test shows an untrusted Git-named binary being refused. It fails on the
  current code.

## Caller survey (2026-11-08)
Every `runProcess` call in `lib/cso`, `scripts` and `test`, by the file it spawns:

| Caller | File | Effect of the change |
|---|---|---|
| `lib/cso/process.ts` `git()` | `executable('git')` | Matches trusted Git; unchanged |
| `lib/cso/snapshot.ts:694` check-ignore | `executable('git')` | Matches; unchanged |
| `lib/cso/docker.ts` `runDocker` | `executable('docker')` | Not Git-named; unchanged |
| `lib/cso/witness.ts:713` | Bun or the compiled helper | Not Git-named; unchanged |
| `test/cso-git-hardening.test.ts` (3 calls) | `executable('git')` | Matches; unchanged |
| `test/cso-docker-argv0.test.ts` (2) | temp `docker` multicall | Not Git-named |
| `test/cso-file-ceiling`, `cso-snapshot-state`, `cso-json-redaction` | `/bin/sh` | Not Git-named |
| `test/cso-docker-mounts.test.ts` | spies `runProcess` itself | Never reaches `hardenGit` |

No caller spawns a non-trusted Git through `runProcess`, on purpose or otherwise. The
repo's fake-`git` test doubles (`gstack-ff-update`, `brain-sync`, `redact-prepush-emails`,
`script-invocation`, `approval-classes`, `ship-hook-*`, `setup-ps1`,
`env-host-fence-conformance`) are PATH shims for shell scripts. They never reach
`lib/cso`, and `executable()` ignores PATH. CSO's own fixtures call `/usr/bin/git` through
`spawnSync`, not `runProcess`. So no test double needs an exemption, and the change
needs no opt-out flag.

## Affected users and systems
- `lib/cso/process.ts` (`hardenGit`) and one new test file.
- `gstack-cso` users: no change when Git resolves normally. A host with no trusted Git
  already fails at `executable('git')` before any Git call.

## Constraints
- No opt-out or exemption parameter. A fail-closed gate with a bypass is not fail-closed.
- Windows keeps the `realpathSync`-based `canonicalExecutable` and the bound `git.exe`
  check from PR 26. Only the two fallback returns change.
- Stacked on PR 26 (`fix/cso-git-shim`), which edits the same line. Rebase onto `main`
  once PR 26 merges.
