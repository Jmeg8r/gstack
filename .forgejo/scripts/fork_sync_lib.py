# WHAT: Pure logic for fork-sync: free-runner log parsing, failure classification,
#       fork-delta comparison, hooks manifest, run state, report rendering.
# WHY:  Everything here decides whether a sync may reach the forge, so it is kept free
#       of subprocess calls and covered by test_fork_sync.py.

import hashlib
import json
import re
from pathlib import Path

STAGES = ["preflight", "hooks-backup", "merge", "fork-delta", "checks", "suite", "codex", "hooks-verify", "pr"]


class SyncError(Exception):
    """A condition the script must not continue past."""


FAILED_TEST = re.compile(r"^  ✗ (\S+) — ")
CRASHED_FILE = re.compile(r"^  ✗ (\S+) \(crashed: ")
FAIL_SUMMARY = re.compile(r"^\[test:free\] FAIL — (\d+) failing test")


def parse_free_log(text):
    """Files with a failure in a `bun run test --verbose` log. Fails closed when the
    runner reports failures that no line attributes to a file."""
    files = set()
    reported = 0
    for line in text.splitlines():
        m = FAILED_TEST.match(line) or CRASHED_FILE.match(line)
        if m:
            files.add(m.group(1))
            continue
        s = FAIL_SUMMARY.match(line)
        if s:
            reported += int(s.group(1))
    if reported and not files:
        raise SyncError(f"the free runner reported {reported} failing test(s) but no line names a file; refusing to call the suite clean")
    return files


BUN_FAIL = re.compile(r"^\(fail\) (.*?)(?: \[[0-9.]+m?s\])?$")
TEMP_PATH = re.compile(r"\s*/private/\S*")


def parse_bun_fails(test_file, output):
    """Failing test names from one `bun test <file>` run, keyed `<file> :: <name>`.
    Timings and temp paths are stripped so runs in different worktrees compare equal."""
    names = set()
    for line in output.splitlines():
        m = BUN_FAIL.match(line)
        if m:
            names.add(f"{test_file} :: {TEMP_PATH.sub('', m.group(1)).strip()}")
    return names


def classify(merge_runs, upstream_runs, baseline_runs):
    """Classify every test that failed in a merge or upstream run.

    fork-caused   fails in every merge run, no upstream run, no baseline run (blocks)
    pre-existing  as fork-caused, but also fails on the pre-sync fork (reported)
    flaky         intermittent on the merge, or on upstream (reported)
    upstream      fails in every upstream run (reported as environment/upstream)
    A test failing only on the baseline was fixed by the merge and is not listed."""
    if not merge_runs or not upstream_runs or not baseline_runs:
        raise SyncError("classification needs at least one run in each of merge, upstream and baseline")
    names = set().union(*merge_runs, *upstream_runs)
    out = {}
    for name in names:
        m = sum(name in r for r in merge_runs)
        u = sum(name in r for r in upstream_runs)
        b = sum(name in r for r in baseline_runs)
        if u == len(upstream_runs):
            out[name] = "upstream"
        elif m == len(merge_runs) and u == 0:
            out[name] = "pre-existing" if b else "fork-caused"
        else:
            out[name] = "flaky"
    return out


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
    if r.get("stopped"):
        lines += [f"**Stopped at stage `{r['stopped']}`.** Nothing was pushed. Fix the items below in the merge worktree, then re-run fork-sync to resume.", ""]
    if r.get("problems"):
        lines += ["## Problems", ""] + [f"- {p}" for p in r["problems"]] + [""]
    for title, body in r.get("sections", []):
        lines += [f"## {title}", "", body.rstrip(), ""]
    return "\n".join(lines).rstrip() + "\n"
