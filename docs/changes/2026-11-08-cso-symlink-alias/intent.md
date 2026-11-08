---
change: 2026-11-08-cso-symlink-alias
status: draft
track: T3
risk: high
risk_reason: /cso is a security audit; a defect could let a symlink hide audited code, read a file outside the repository, or report coverage it did not have
author: james
accepted_by:
linear: pending
source: conversation (ClaudeClaw ASTGL-74 VERIFY, 2026-11-08)
---
# Intent: /cso audits repositories that contain tracked symlinks

## Problem
"Let's get this fixed." (James, 2026-11-08.) `gstack-cso start` refuses any repository
that contains a tracked symlink: `UNSAFE_PATH: Symlink or special source file: AGENTS.md`.
It refuses even with `--paths` limited to other files, because `rejectSpecialFiles` in
`lib/cso/snapshot.ts` walks the whole tree. That contradicts the troubleshooting doc's own
statement that paths outside `--paths` "are never captured or assessed".

Two of James's repositories carry a tracked `AGENTS.md -> CLAUDE.md` symlink, claudeclaw and
studydeck (a scan of 173 local repositories, 2026-11-08). In claudeclaw the symlink is a
deliberate decision (#699, 2026-07-08): "a symlink makes drift structurally impossible".
So /cso cannot audit either repository, and on 2026-11-08 it blocked the security pass for
ClaudeClaw ASTGL-74 (`gstack-cso: not assessed`). The upstream answer, "Symlinks must be
removed" (`docs/troubleshooting.md`), would reverse a sound decision in each repository just
to suit the tool.

## Proposed outcome
- A tracked or nonignored-untracked symlink no longer stops the run. It becomes an explicit
  snapshot manifest entry, bound to its identity, and is **never followed, read through or
  copied**.
- A symlink whose target is a regular source file inside the repository is recorded as an
  **alias** of that file. The target is audited under its own path, so nothing is lost.
- Any other symlink (target outside the repository, missing, a directory, or another symlink)
  is recorded as an **unaudited path**. Snapshot coverage then reads partial and the report
  counts it, the same way `--paths` skips are reported.
- A symlink outside the `--paths` scope is recorded like any other out-of-scope path.
- FIFOs, sockets and devices are still refused.

## Affected users and systems
- `lib/cso/snapshot.ts`: `rejectSpecialFiles`, the per-path capture loop, the `--paths`
  branch, the capture-stability re-checks, diff-mode changed-path detection, and the final
  snapshot assertion.
- Its tests under `test/cso-*.test.ts`, and `docs/troubleshooting.md` (`#cso-capacity`).
- Users auditing claudeclaw and studydeck. The fork (`jfcadm/gstack`) first; upstream
  garrytan/gstack only if James chooses to offer it.

## Constraints
- Never follow a symlink: no read, open, stat-through or copy of its target. The link text
  is read only with `readlink`, and only to classify the alias.
- Classification is lexical and contained: the resolved target must normalise to a path
  inside the repository that is itself in the capture list as a regular file. `..` escapes,
  absolute targets and chains are unaudited, never aliases.
- Capture stays race-safe: if a symlink is swapped for a file or directory, or its link text
  changes during capture, the run fails with `SNAPSHOT_RACE`, as for other entries.
- Diff mode still detects a changed symlink. Git records a symlink as mode `120000` with
  the link text as its blob, so change detection must compare that, not file contents.
- The execution copy and the readable copy contain no symlinks; `assertSnapshot` keeps
  refusing them.
- No cap, budget or exclusion rule changes. No network. Matches the existing snapshot
  code's style and error vocabulary.

## Out of scope
- Changing claudeclaw or studydeck.
- Offering the change upstream to garrytan/gstack (James decides after merge).
- Deploying the fix to the installed `~/.claude/skills/gstack` (a separate, supervised step;
  the install is at `396dca515`, one merge (#23, 4 commits) behind the fork's `main`, with
  local changes).
- Symlinked directories as audit roots, and git submodules (already handled as boundaries).

## Success signals
- Tests (each mutation-checked): an in-repo alias is recorded and its target audited, with
  coverage unaffected; an escaping, absolute, dangling and directory symlink are each
  recorded as unaudited with partial coverage; no symlink target payload appears in the
  snapshot, readable copy or execution copy; a symlink outside `--paths` no longer blocks;
  a FIFO is still refused; a symlink swapped during capture fails with `SNAPSHOT_RACE`; a
  changed symlink appears in a `--diff` run's changed paths.
- The existing CSO test suites pass unchanged.
- Live: `gstack-cso start --diff --base origin/main` on the ClaudeClaw ASTGL-74 worktree
  creates a run, and `inspect` shows `AGENTS.md` recorded as an alias of `CLAUDE.md`.

## Open questions
- Does an in-repo alias leave coverage **complete**, as proposed, or should every symlink
  make coverage partial? (Proposed: complete, because the target is audited.)
- Should the manifest record the link text? It aids review, but it is repository content
  that bypasses the redacting reader. (Proposed: record only the classification and the
  target's snapshot path handle, not raw link text.)
- Linear: which project tracks gstack fork work, if any?
