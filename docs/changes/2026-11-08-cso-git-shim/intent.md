---
change: 2026-11-08-cso-git-shim
status: draft
track: T3
risk: high
risk_reason: the CSO helper's executable resolution decides which binary runs against an audited repository and whether Git hardening applies; a wrong answer runs the wrong tool or unhardened Git
author: claude
accepted_by: pending
linear: pending
source: side session from the jfcadm/gstack PR 25 VERIFY (2026-11-08)
---
# Intent: the CSO helper always runs the Git it resolved

## Problem
About 2% of the CSO helper's Git calls on James's Mac Studio fail with
`CsoError: Could not read bounded Git metadata: <op> exited 2 (unsupported invocation)`.
The captured stderr is `unknown option --no-optional-locks` and a usage message from
Xcode's `python3`. The flake hits random tests across `test/cso-*.test.ts`, which makes
the CSO suites unreliable as merge evidence. Live `gstack-cso` runs are probably hit too.
It was found while verifying PR 25 (`fix/cso-symlink-alias`) and predates that change.

## Root cause (proven, 2026-11-08)
- `/usr/bin/git` is the xcode-select shim, one of 78 hard links to a single inode
  (`git`, `python3`, `make`, `clang`, ...). The shim picks the tool from its own
  executable path, not from argv[0]: run as `/usr/bin/git` with argv0 `python3`, it
  still runs Git.
- `executable()` in `lib/cso/process.ts` returns `realpathSync(p)`. Under Bun 1.4.4 on
  macOS, `realpathSync` asks the kernel for the file's path. A file with several hard
  links has one vnode, and the kernel returns whichever name it has cached. In 20,000
  calls, `realpathSync('/usr/bin/git')` returned `/usr/bin/python3` 262 times (1.3%).
  Node 24's JavaScript `realpathSync` returned `/usr/bin/git` every time.
- So `executable('git')` sometimes returns `/usr/bin/python3`, and the helper spawns
  python3 with Git's arguments. One Git call resolves the path twice (in `git()` and in
  `hardenGit()`), which gives the ~2% failure rate.
- Environment is not the cause. With 300 concurrent spawns ×3 of `/usr/bin/git`, there
  were 0 misfires under the sanitized child env, with `TMPDIR`, with `DEVELOPER_DIR`,
  with the real `HOME`, and under the full environment, in both Node and Bun.
- The same race has a security side. When `git()` gets `/usr/bin/git` but
  `hardenGit()`'s own `realpathSync(file)` returns another name, the comparison with the
  trusted Git fails. `hardenGit` then returns the arguments **unhardened** (no
  `core.fsmonitor=false`, `core.hooksPath=/dev/null`, `include.path=/dev/null` or
  config-identity binding), and real Git runs against the audited repository.

## Proposed outcome
- `executable()` resolves symlinks one path component at a time (`lstat` + `readlink`)
  and never asks the kernel for a hard-linked file's name. `/usr/bin/git` stays
  `/usr/bin/git`. A symlink such as `/opt/homebrew/bin/git -> ../Cellar/git/2.54.0/bin/git`,
  or OrbStack's `docker -> docker-tools`, still resolves to its target.
- `hardenGit()` compares with the same function, so its trusted-Git check is
  deterministic.
- A regression test fails on the current code under Bun on macOS and passes after the fix.

## Affected users and systems
- `lib/cso/process.ts` (`executable`, `hardenGit`) and a new test.
- Every `gstack-cso` user on macOS whose first trusted Git is the xcode-select shim. That
  is the default when Xcode or the Command Line Tools are installed and `/usr/local/bin`
  has no Git.

## Constraints
- Keep the security property. Never consult the audited repo's PATH or overrides, and
  resolve only from `TRUSTED_DIRECTORIES`, in the existing order.
- Windows keeps its current `realpathSync` path. The new walk is POSIX only, so Windows
  canonicalization (case, junctions) and the bound `git.exe` check do not change.
- Do not change which Git is chosen (no `xcrun --find`, no Homebrew preference). The
  defect is path canonicalization, not the choice of Git.
- No change to `childEnvironment()`.
