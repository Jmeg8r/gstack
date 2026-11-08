---
change: 2026-11-08-cso-symlink-alias
status: draft
track: T3
intent: ./intent.md
accepted_by:
linear: pending
skills_in_force: []
claude_config_sha: 6ab4082
---
# Spec: /cso audits repositories that contain tracked symlinks

## Flagged concerns

**F1. This fork would diverge on a documented upstream rule.** Source: upstream
`docs/troubleshooting.md` ("Symlinks must be removed"). Every future upstream merge into
`jfcadm/gstack` may conflict in `snapshot.ts` or that doc section. Recommended: keep the
change inside `lib/cso/snapshot.ts`, one new test file and the doc section; keep the FIFO
error text byte-identical; do not touch `cli.ts` or `contracts.ts` (coverage already works
from the transformation prefix, see Design). Offering it upstream stays James's later call.
*James:* pending.

**F2. Policy skills.** Source: /spec step 2. `security-review` and `gstack-cso` review a
diff; no code exists yet. `gstack-cso` is also the thing being changed, and the fork itself
cannot be audited today (it tracks the `connect-chrome` symlink). Recommended: run
`security-review` at VERIFY, and run the *fixed* `gstack-cso` from this branch on its own
diff as a live check. That run is only possible because of this change.
*James:* pending.

**F3. Aliases into withheld files.** Source: `SECRET_FILE` (`snapshot.ts:54`) already
excludes `AGENTS.md` and `CLAUDE.md`. ClaudeClaw's `AGENTS.md` is therefore an alias of an
excluded file. Recommended: an alias is classified by target *type and location* only. What
happens to the target's own payload (audited, excluded or withheld) is reported on the
target's entry, unchanged. The alias adds no second copy and claims nothing about the target.
*James:* pending.

**F4. Comprehensive mode cannot rebuild a symlink.** Source: the execution copy never
contains symlinks (`assertSnapshot`), and `verification.ts:1887-1906` refuses any input with
a transformation. An application that needs a symlink at runtime will report that input as
withheld, not silently run without it. Recommended: accept, and document it in the doc
section. Rebuilding symlinks in the execution copy is a separate change.
*James:* pending.

## Requirements

- **R1 Walk.** `rejectSpecialFiles` no longer reports symlinks. FIFOs, sockets and devices
  still fail with the existing text `Symlink or special source file: <path>`. *(intent:
  outcome, "FIFOs … still refused")*
- **R2 Never follow.** For a symlink path, capture uses only `lstat` and
  `readlink` (as a Buffer). No `open`, `stat` or `realpath` through the link. Its target
  payload is never read through the link, hashed or copied. *(constraint)*
- **R3 Alias classification.** The link text must decode as UTF-8 and be relative. The
  target is computed with POSIX `normalize(join(dirname(path), text))`, purely lexically.
  It is an **alias** only if all of these hold:
  1. the result is not empty and does not start with `..`;
  2. it is in the capture list (`paths()`);
  3. it is outside dependency trees (`noReadPath` false);
  4. `containedFile(repo, target)` passes (no symlink anywhere along the path);
  5. `lstat` shows a regular file.

  Otherwise it is **unaudited**, with one reason: `absolute`, `escapes the repository`,
  `missing target`, `target is a directory`, `target is a symlink or crosses one`,
  `target not in the source inventory`, or `link text is not UTF-8`. *(constraint,
  decision)*
- **R4 Entries.** A symlink entry has `originalHash: 'not-read'` and no `executionHash`.
  `bytes` and `mode` come from `lstat`.
  - Alias: `transformation: 'excluded: symlink alias of <snapshotPathHandle(target pathId)>'`.
  - Unaudited: `transformation: 'withheld: symlink not followed (<reason>)'`.

  The raw link text never enters the manifest, the readable copy or any report.
  *(decision)*
- **R5 Coverage.** With no `cli.ts` change, an alias goes on the exclusions list and leaves
  `snapshot-inputs` coverage `assessed`. An unaudited symlink is a gap and makes it `partial`
  (`cli.ts` `snapshotCoverage`: `excluded:` prefix versus other unread entries).
  *(decision)*
- **R6 --paths.** A symlink outside the audit paths gets `OUTSIDE_AUDIT_PATHS`. A symlink
  inside a dependency tree gets the dependency exclusion. Neither throws (today both throw
  at `snapshot.ts:743`). *(outcome)*
- **R7 Diff mode.** For every symlink entry: `gitModes` is `'120000'`, and `gitHashes` is
  the git blob hash of the link-text bytes (`blob <len>\0<text>`, in the repository's object
  format), computed whenever a base is set. An unchanged symlink is therefore not a changed
  path, and a retargeted one is. *(constraint)*
- **R8 Stability.** `assertEntriesStable` re-checks every symlink entry: it must still be a
  symlink with the same `lstat` identity (`dev`, `ino`) and the same link-text hash. Any
  swap to a file or directory, a retarget, or a replacement fails with `SNAPSHOT_RACE`.
  The final `rejectSpecialFiles` and membership checks are unchanged. *(constraint)*
- **R9 Copies.** Nothing is written for a symlink in `readable/` or `snapshot/`;
  `assertSnapshot` is unchanged and still refuses any symlink there. *(constraint)*
- **R10 Docs.** `docs/troubleshooting.md#cso-capacity` describes alias, unaudited and the
  comprehensive-mode limit (F4), and no longer says symlinks must be removed. *(outcome)*

## Design

- **`rejectSpecialFiles`:** `if (!stat.isFile() && !stat.isSymbolicLink()) special.push(path)`.
  A symlink to a directory is not descended into: `lstat` reports a link, not a directory.
- **New `classifySymlink(repo, path, inventory)`** returns
  `{ kind: 'alias', target } | { kind: 'unaudited', reason }` plus `linkHash`. It reads the
  link once as a Buffer, computes the blob hash from those bytes, and discards the text.
  `inventory` is a `Set` of `list`.
- **Capture loop:** after `lstat` and before the directory and `--paths` branches, handle
  `source.isSymbolicLink()`:
  - out of scope or dependency → the existing transformation text;
  - else alias or unaudited, per R4;
  - in every case set `gitModes` to `120000` and `gitHashes` to `linkHash`, record the
    identity in a new `symlinks: Map<path, {dev, ino, linkHash}>`, and `continue`.
- **`assertEntriesStable`:** a `not-read` entry found in `symlinks` takes a symlink branch
  (`lstat` isSymbolicLink, same `dev`/`ino`, re-read link hash equal), before the regular
  file branch.
- **Tests:** a new `test/cso-snapshot-symlink.test.ts`, in the style of
  `cso-snapshot-identity.test.ts` (git fixture, `capture()` direct), plus one coverage test
  through `snapshotCoverage` if it is exported (else through the CLI path the existing
  coverage tests use). `skipIf(win32)` for symlink creation, like the existing tests.

## Security and data

- **Read boundary:** the only new read of repository data is `readlink` on the symlink
  itself. No target payload is read through a link, and alias targets are read only under
  their own path by the existing hardened reader (`readStable`, `containedFile`,
  `assertOpenedFileContained`).
- **Escape:** absolute, `..` and chained targets are never aliases, and an alias target
  must be an inventory member with no symlink along the path. A symlink cannot make /cso
  read outside the repository.
- **Disclosure:** no link text in any output. Entry paths are handled exactly like other
  entries, through `publicSnapshotPath`.
- **Race:** identity and link-hash re-check (R8), plus the existing membership re-check.
- **Assurance honesty:** an unaudited symlink makes coverage partial; only a verified
  in-repo alias is "nothing missing".

## Acceptance checks

| Req | Check | Expected |
|---|---|---|
| R1 | existing FIFO test in `cso-snapshot-identity.test.ts` | still rejects with the same text |
| R2–R4 | new tests: alias `AGENTS.md -> CLAUDE.md`; nested alias `docs/a.md -> ../README.md`; absolute; `../outside` escape; dangling; directory; chain; non-UTF-8 link text | alias entries `excluded: symlink alias of <handle>`; each other case `withheld: symlink not followed (<reason>)`; no target bytes or link text in the manifest, `readable/` or `snapshot/` (grep for a planted marker, with a positive control) |
| R5 | coverage for the alias-only fixture versus the escape fixture | `assessed` versus `partial` with the symlink listed as a gap |
| R6 | symlink outside `--paths`; symlink under `node_modules/` | run succeeds; `OUTSIDE_AUDIT_PATHS` or the dependency exclusion |
| R7 | `--base` fixture: unchanged symlink; retargeted symlink; file replaced by symlink | not in `changedPaths`; in `changedPaths`; in `changedPaths` |
| R8 | hook between capture and the final check retargets the link / swaps it for a file | `SNAPSHOT_RACE` |
| R9 | `assertSnapshot` on every new fixture | passes; no symlink in either copy |
| all | mutation check: drop the alias containment test, drop the 120000 mode, drop the symlink stability branch, allow absolute targets | each turns at least one new test red |
| regress | `bun test test/cso-*.test.ts` (the suites that run without Docker) | all pass, same counts as on `main` plus the new tests |
| live | fixed launcher from this branch: `gstack-cso start --diff --base origin/main` on the ClaudeClaw ASTGL-74 worktree, and on this fork | run created; `inspect` shows `AGENTS.md` (or `connect-chrome`) classified, coverage as R5 predicts |

## Open questions
- How the live check runs the *branch* launcher, not the installed one. To be settled in
  the plan after reading `bin/gstack-cso-launcher`'s build (it is a compiled Mach-O binary).
- F1–F4: resolutions pending (above).
