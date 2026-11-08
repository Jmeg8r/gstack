---
change: 2026-11-08-cso-git-fail-closed
status: accepted
track: T3
intent: ./intent.md
linear: pending
---
# Spec: CSO refuses a Git-named child it cannot harden

## Requirements
- **R1.** `runProcess(file, …)` where `basename(file)` matches `/^(?:git|git\.exe)$/i` and
  `canonicalExecutable(file) !== executable('git')` rejects with
  `CsoError('UNSAFE_PATH')`. The child is never spawned.
- **R2.** When `executable('git')` throws, the same call rejects with that error (a
  `CsoError`, `TOOL_UNAVAILABLE` today). The child is never spawned.
- **R3.** A Git-named file that canonicalizes to the trusted Git, including a symlink
  to it, still takes the hardened path, with no behavior change.
- **R4.** Files that are not Git-named are not affected.
- **R5.** The error message names neither the file path nor the arguments. CSO errors
  avoid echoing repository-controlled data; `git()` already follows this rule.

## Design
In `hardenGit()`:

```ts
let trusted: string;
try {
  trusted = executable('git');
} catch {
  return { args };                                  // before
}
if (canonicalExecutable(file) !== trusted) return { args };   // before
```

becomes

```ts
const trusted = executable('git');                  // R2: its CsoError propagates
if (canonicalExecutable(file) !== trusted)
  throw new CsoError('UNSAFE_PATH', 'Refusing a Git executable that is not the trusted Git');
```

`hardenGit` runs in `runProcess` before `spawn` (synchronously, inside the async
function), so a throw becomes a rejected promise and nothing is spawned.

If `canonicalExecutable(file)` throws (a missing file, a symlink loop), that error
propagates as today. A missing file could not have spawned anyway.

## Test (`test/cso-git-fail-closed.test.ts`, POSIX)
- A temp script named `git`, `git.exe` and `GIT` that writes a marker file when run:
  `runProcess` rejects `UNSAFE_PATH`, and the marker is absent (R1).
- A symlink named `git` pointing at `executable('git')`, called with no `-C`: rejects
  `INVALID_ARGUMENT` from the hardening step, which proves it took the hardened path (R3).
  This passes before and after the fix, as a control.
- `spyOn(processModule, 'executable')` throws `TOOL_UNAVAILABLE`: a Git-named lookalike
  rejects with `TOOL_UNAVAILABLE` and the marker is absent (R2). Bun's `spyOn` on a
  module namespace also patches calls inside the module (checked in a scratch test).

On the current code, the four R1/R2 cases fail (the promise resolves) and the control
passes.

## Concerns
- **C1 (not in scope).** Detection is by basename. A trusted Git reached under another
  name (a hard link or symlink not called `git`) is not hardened. Every caller uses
  `executable('git')`, so no caller does this today. A stricter rule would harden any
  file that canonicalizes to the trusted Git, whatever its name. That is a separate change.
- **C2.** On Windows, the case-insensitive match also covers `GIT.EXE`. The trusted
  `git.exe` comes from `GSTACK_CSO_TRUSTED_GIT` or setup. A caller passing another
  `git.exe`, for example under `cmd\` instead of `mingw64\bin\`, is now refused. No
  current caller does this. Not testable on this Mac; CI's Windows job runs the CSO
  suites.
- **C3.** `git.cmd` and other wrapper names are not Git-named under the regex and stay
  unaffected. That is unchanged behavior.
