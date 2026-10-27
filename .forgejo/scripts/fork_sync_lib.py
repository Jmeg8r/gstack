# WHAT: Pure logic for fork-sync: free-runner log parsing, failure classification,
#       fork-delta comparison, hooks manifest, run state, report rendering.
# WHY:  Everything here decides whether a sync may reach the forge, so it is kept free
#       of subprocess calls and covered by test_fork_sync.py.

import hashlib
import json
import re
from pathlib import Path

TEMP_PATH = re.compile(r"\s*/private/\S*")

STAGES = ["preflight", "hooks-backup", "merge", "fork-delta", "checks", "suite", "codex", "hooks-verify", "pr"]


class SyncError(Exception):
    """A condition the script must not continue past."""


FAILED_TEST = re.compile(r"^  ✗ (\S+) — (.+)$")
CRASHED_FILE = re.compile(r"^  ✗ (\S+) \(crashed: ")
FAIL_SUMMARY = re.compile(r"^\[test:free\] FAIL — (\d+) failing test")
# The runner's own markers for a run that did not finish or could not name what failed
# (buildRunEpilogue in scripts/test-free-shards.ts). A crash that passed on its retry
# is the one `⚠` line that still means the tests ran.
INCOMPLETE = [re.compile(r"^  ⚠ (?!crashed\+retried: )"), re.compile(r"^  ⏱ "), re.compile(r"exceeded the \d+s wall-clock deadline")]


def parse_free_log(text):
    """Failing tests in a `bun run test --verbose` log, as {file: {"file :: name"}}.
    Fails closed when the runner says a shard did not finish, a failure has no named
    result, an error happened between tests, or failures are reported but none named."""
    failures = {}
    reported = 0
    for line in text.splitlines():
        if any(p.search(line) for p in INCOMPLETE):
            raise SyncError(f"the free runner reports an incomplete or unattributed run: {line.strip()!r}; an unfinished suite cannot clear the gate")
        m = FAILED_TEST.match(line)
        if m:
            failures.setdefault(m.group(1), set()).add(f"{m.group(1)} :: {TEMP_PATH.sub('', m.group(2)).strip()}")
            continue
        c = CRASHED_FILE.match(line)
        if c:
            failures.setdefault(c.group(1), set()).add(f"{c.group(1)} :: <crashed>")
            continue
        s = FAIL_SUMMARY.match(line)
        if s:
            reported += int(s.group(1))
    if reported and not failures:
        raise SyncError(f"the free runner reported {reported} failing test(s) but no line names a file; refusing to call the suite clean")
    return failures


BUN_FAIL = re.compile(r"^\(fail\) (.*?)(?: \[[0-9.]+m?s\])?$")


def parse_bun_fails(test_file, output):
    """Failing test names from one `bun test <file>` run, keyed `<file> :: <name>`.
    Timings and temp paths are stripped so runs in different worktrees compare equal."""
    names = set()
    for line in output.splitlines():
        m = BUN_FAIL.match(line)
        if m:
            names.add(f"{test_file} :: {TEMP_PATH.sub('', m.group(1)).strip()}")
    return names


TIMEOUT_EXIT = 124
BUN_COMPLETED = re.compile(r"^Ran \d+ tests? across ", re.M)
BUN_ERRORS = re.compile(r"^\s*(\d+) errors?$", re.M)


def parse_bun_run(test_file, exit_code, output):
    """Failing tests from one standalone `bun test <file>` run, refusing any run that
    did not finish. A timeout, a missing `Ran N tests` line, or a failing exit with no
    named failure means the run is no evidence either way, so it must not count as a
    clean observation. Unhandled errors between tests count as a failure of their own."""
    if exit_code == TIMEOUT_EXIT:
        raise SyncError(f"`bun test {test_file}` timed out; an unfinished run cannot be classified")
    if not BUN_COMPLETED.search(output):
        raise SyncError(f"`bun test {test_file}` exited {exit_code} without bun's `Ran N tests` line; it did not complete")
    names = parse_bun_fails(test_file, output)
    errors = sum(int(n) for n in BUN_ERRORS.findall(output))
    if errors:
        names.add(f"{test_file} :: <unhandled error>")
    if exit_code != 0 and not names:
        raise SyncError(f"`bun test {test_file}` exited {exit_code} but named no failing test; refusing to read it as clean")
    return names


def blocking_dirty(porcelain_lines):
    """`git status --porcelain` lines that mean the worktree differs from its commit.
    Untracked top-level dot entries (tool state: .serena/, .context/, .DS_Store) are
    not source and are ignored; any other change or untracked file blocks."""
    out = []
    for line in porcelain_lines:
        if not line.strip():
            continue
        path = line[3:]
        if line.startswith("?? ") and path.startswith(".") and "/" not in path.rstrip("/"):
            continue
        out.append(line)
    return out


def split_attributable(listed, is_repo_file):
    """Free-runner failure entries split into repo test files (rerunnable) and the
    rest: `(unattributed)` or temp-path files from nested runs, which cannot be."""
    files = sorted(p for p in listed if is_repo_file(p))
    return files, sorted(set(listed) - set(files))


def classify(merge_runs, upstream_runs, baseline_runs):
    """Classify every test that failed in a merge or upstream run.

    fork-caused   fails in every merge run, no upstream run, no baseline run (blocks)
    pre-existing  as fork-caused, but also fails on the pre-sync fork (reported)
    flaky         intermittent on the merge, or on upstream (reported)
    upstream      fails in every upstream run (reported as environment/upstream)
    A test failing only on the baseline was fixed by the merge and is not listed.
    None for upstream or baseline means the test file does not exist there (a
    fork-only test, or one upstream just added): that side gives no evidence and the
    test is judged against the other one."""
    if not merge_runs or (upstream_runs is not None and not upstream_runs) or (baseline_runs is not None and not baseline_runs):
        raise SyncError("classification needs at least one run in the merge and in every comparison tree that has the file")
    names = set().union(*merge_runs, *(upstream_runs or []))
    out = {}
    for name in names:
        m = sum(name in r for r in merge_runs)
        u = sum(name in r for r in upstream_runs) if upstream_runs is not None else 0
        b = sum(name in r for r in baseline_runs) if baseline_runs is not None else 0
        if upstream_runs is not None and u == len(upstream_runs):
            out[name] = "upstream"
        elif m == len(merge_runs) and u == 0:
            out[name] = "pre-existing" if b else "fork-caused"
        else:
            out[name] = "flaky"
    return out


def not_reproduced(suite_failures, verdicts):
    """Full-suite failures that no standalone run reproduced in any tree. They are
    kept in the inventory (as flaky, not blocking) instead of silently vanishing."""
    named = set().union(*suite_failures.values()) if suite_failures else set()
    return sorted(named - set(verdicts))


def fork_delta(blobs):
    """blobs maps path -> (pre-sync fork blob, upstream blob, merge blob); None = absent.
    A path the fork changed is dropped when the merge now matches upstream but upstream
    never took the fork's bytes, adopted when upstream itself took them, else kept."""
    result = {"dropped": [], "adopted": [], "kept": []}
    for path in sorted(blobs):
        fork, upstream, merged = blobs[path]
        if merged == upstream and upstream != fork:
            result["dropped"].append(path)
        elif upstream == fork:
            result["adopted"].append(path)
        else:
            result["kept"].append(path)
    return result


def hooks_manifest(directory):
    """sha256 of every regular file in a hooks directory, by name."""
    d = Path(directory)
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir()) if p.is_file()}


def hooks_diff(before, after):
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(n for n in set(before) & set(after) if before[n] != after[n]),
    }


REMOTE_SLUG = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")


def remote_slug(url):
    m = REMOTE_SLUG.search(url)
    if not m:
        raise SyncError(f"cannot read owner/name from remote URL {url!r}")
    return f"{m.group(1)}/{m.group(2)}"


class State:
    """Stages that passed and the pinned SHAs, persisted so a re-run resumes."""

    def __init__(self, path, data):
        self.path = Path(path)
        self.data = data

    @classmethod
    def load(cls, path):
        p = Path(path)
        if not p.exists():
            return cls(p, {"passed": {}})
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            raise SyncError(f"{p} is not valid JSON ({e}); remove it to start the run over") from e
        data.setdefault("passed", {})
        return cls(p, data)

    def passed(self, stage):
        return stage in self.data["passed"]

    def mark_passed(self, stage, sha=None):
        self.data["passed"][stage] = sha
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)


def render_report(r):
    """Markdown report; problems first so the reason for a stop is the first thing read."""
    lines = [f"# Fork sync to upstream v{r.get('upstream_version', '?')}", ""]
    if r.get("stopped") and r.get("pushed"):
        lines += [f"**Stopped at stage `{r['stopped']}` after the push:** {r['pushed']} is on the forge (mirroring and CI may have started). Fix the items below, then re-run fork-sync to finish.", ""]
    elif r.get("stopped"):
        lines += [f"**Stopped at stage `{r['stopped']}`.** Nothing was pushed. Fix the items below in the merge worktree, then re-run fork-sync to resume.", ""]
    if r.get("problems"):
        lines += ["## Problems", ""] + [f"- {p}" for p in r["problems"]] + [""]
    for title, body in r.get("sections", []):
        lines += [f"## {title}", "", body.rstrip(), ""]
    return "\n".join(lines).rstrip() + "\n"
