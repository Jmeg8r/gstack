---
change: 2026-11-08-cso-git-shim
status: accepted
track: T3
intent: ./intent.md
linear: pending
---
# Spec: the CSO helper always runs the Git it resolved

## Requirements
- **R1** On POSIX, `executable(name)` returns the trusted candidate path with every
  symlink resolved. It never returns a different hard-link name of the same inode.
  Repeated calls in one process return the same string.
- **R2** Symlinks still resolve, relative and absolute targets alike, across chains and
  through symlinked parent directories. A loop, or more than 40 hops, fails as the
  candidate does today: it is skipped, and the next trusted directory is tried.
- **R3** `hardenGit()` canonicalizes the spawned file with the same function as
  `executable()`. A spawn of the trusted Git is always hardened.
- **R4** On Windows, behavior is unchanged: `realpathSync` stays in both places.
- **R5** Candidate order, the name regex, the `isFile` and `X_OK` checks, and the
  `TOOL_UNAVAILABLE` error are unchanged.

## Design
Add `canonicalExecutable(path)` to `lib/cso/process.ts`:

```
win32 → realpathSync(path)
POSIX → walk components from "/": lstat each; on a symlink, readlinkSync it and push its
        components back onto the queue (reset to "/" if absolute); count hops, ELOOP at 40.
        ".." and "." are applied to the already-resolved prefix, which never holds a symlink.
```

`executable()` returns `canonicalExecutable(p)` in place of `realpathSync(p)`.
`hardenGit()` compares `canonicalExecutable(file) !== trusted`.

Why a component walk, not a fix to the shim choice: the kernel path lookup is what goes
wrong, and it goes wrong for any multiply-linked binary in a trusted directory, not only
Git. `lstat` and `readlink` read directory entries by name, so the name the caller gave
survives.

## Concerns flagged
- **C1 (follow-up, not in scope).** `hardenGit()` falls back to unhardened arguments
  whenever a file named `git` is not the trusted Git. After this fix the trusted Git
  always matches, but a fail-closed refusal there would be stronger. It needs a survey of
  callers that spawn a non-trusted `git` on purpose (test doubles), so it is proposed
  separately.
- **C2.** Other `realpathSync` calls in `lib/cso` canonicalize directories and repository
  files, not executables. A directory cannot be hard-linked on APFS, so they are not
  exposed to this race. Not changed.
- **C3.** `test/helpers/providers/claude.ts:160` compares `fs.realpathSync(executable)`
  for a provider binary. That is test-only and outside CSO. Not changed.
