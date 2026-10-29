---
change: 2026-10-28-codex-exec-review-gate
status: accepted
track: T3
linear: pending
---
# Intent: Codex review gates run on `codex exec` so a clean review can PASS

## What James asked for (2026-10-28)
"Switch to codex exec." Move every Codex review that feeds the structured gate
off native `codex review --base` and onto `codex exec` with a prompt that
requires the exact `NO_FINDINGS` line on a clean review. Keep the validator's
fail-closed rule unchanged. Probe on a clean diff before any rollout.
Plan approved in session ("Approve plan").

## Why it matters
The structured gate (2026-09-26-outside-review-p0-gate, R4) passes an untagged
review only on an exact `NO_FINDINGS` line. Native `codex review --base` takes
no prompt and ignores `-c developer_instructions` (probe 2026-10-28, codex-cli
0.156.1), so a clean native run is always UNVERIFIED. On 2026-10-28 the Codex
ruling digest held 21 PRs for a hand ruling, 19 of them clean untagged reviews.

## Affected systems
- gstack: `codex/sections/review-mode.md.tmpl` (/codex review),
  `scripts/resolvers/outside-voice.ts` and `outside-voice-steps.ts` (the
  structured outside review in /review and /ship), generated skill docs, a new
  prompt builder under `bin/`.
- sovereign-forge: `bin/gstack-fork-sync.sh` (weekly fork-sync Codex step).
- Not changed: `lib/outside-review-result.ts`.
