# WHAT: Pure logic for fork-sync: free-runner log parsing, failure classification,
#       fork-delta comparison, hooks manifest, run state, report rendering.
# WHY:  Everything here decides whether a sync may reach the forge, so it is kept free
#       of subprocess calls and covered by test_fork_sync.py.

import fnmatch
import hashlib
import json
import os
import re
from pathlib import Path

TEMP_PATH = re.compile(r"\s*/private/\S*")

STAGES = ["preflight", "hooks-backup", "merge", "fork-delta", "checks", "suite", "codex", "hooks-verify", "pr"]


class SyncError(Exception):
    """A condition the script must not continue past."""


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
BUN_COMPLETED = re.compile(r"^Ran \d+ tests? across (\d+) files?\b", re.M)
BUN_ERRORS = re.compile(r"^\s*(\d+) errors?$", re.M)


def parse_bun_run(test_file, exit_code, output):
    """Failing tests from one standalone `bun test <file>` run. A timeout is a failing
    run: what it named before the hang plus `<timed out>`, so a hang is compared with
    upstream like any failure and never reads as clean. A missing `Ran N tests` line or
    a failing exit with no named failure is no evidence either way and is refused.
    Unhandled errors between tests count as a failure of their own."""
    if exit_code == TIMEOUT_EXIT:
        return parse_bun_fails(test_file, output) | {f"{test_file} :: <timed out>"}
    done = BUN_COMPLETED.search(output)
    if not done:
        raise SyncError(f"`bun test {test_file}` exited {exit_code} without bun's `Ran N tests` line; it did not complete")
    if done.group(1) != "1":
        raise SyncError(f"`bun test {test_file}` ran {done.group(1)} files; the selector must match exactly one, or its failures cannot be attributed")
    names = parse_bun_fails(test_file, output)
    errors = sum(int(n) for n in BUN_ERRORS.findall(output))
    if errors:
        names.add(f"{test_file} :: <unhandled error>")
    if exit_code != 0 and not names:
        raise SyncError(f"`bun test {test_file}` exited {exit_code} but named no failing test; refusing to read it as clean")
    return names


# What bun auto-loads: .env, .env.local, .env.<NODE_ENV>, .env.<NODE_ENV>.local.
# Templates such as the tracked .env.example are never loaded.
DOTENV = re.compile(r"^\.env(\.(development|production|test))?(\.local)?$")


def dotenv_files(names):
    """Files bun would auto-load as dotenv from a working directory."""
    return sorted(n for n in names if DOTENV.match(n))


# Paths a concurrently running agent host writes in its own home while the suite
# runs (Claude Code's config backups, the Codex app's SQLite journal, shell
# snapshots and locks). The runner's own volatile list misses these; override with
# FORK_SYNC_EXTERNAL_HOME_WRITERS (colon-separated globs).
DEFAULT_EXTERNAL_HOME_WRITERS = [
    "~/.claude/backups/.claude.json.backup.*", "~/.claude/sessions/*.json", "~/.claude/state/*",
    "~/.claude/.git/*", "~/.claude/memory-sweeper/*",
    "~/.codex/*.sqlite-wal", "~/.codex/*.sqlite-shm",
    "~/.codex/shell_snapshots/*", "~/.codex/thread-writer-locks/*",
]
CONTAINMENT_LINE = re.compile(r"^\[test:free\] shard (\d+)/\d+ shard cleanup or home containment failed: real home changed while this shard ran \([^)]*\): (.*?)\. Give the writer", re.M)


# The runner's watched home surfaces and the live-session paths it skips
# (scripts/lib/free-home-guard.ts FREE_HOME_SURFACES / FREE_HOME_VOLATILE).
HOME_SURFACES = [".gstack", ".claude", ".codex", ".agents", ".config/gstack"]
HOME_VOLATILE = [
    ".claude/projects", ".claude/todos", ".claude/shell-snapshots", ".claude/statsig", ".claude/ide",
    ".claude/session-env", ".claude/file-history", ".claude/debug", ".claude/telemetry", ".claude/history.jsonl",
    ".claude/.credentials.json", ".codex/sessions", ".codex/archived_sessions", ".codex/log", ".codex/history.jsonl",
    ".codex/auth.json",
]


def home_snapshot(home, surfaces=HOME_SURFACES, volatile=HOME_VOLATILE):
    """Every file, link and directory under the watched surfaces, untruncated, so a
    run window's real-home changes can be listed in full (the runner caps its list).
    Directory mtimes are ignored, as the runner does."""
    home = Path(home)
    skip = {Path(v) for v in volatile}
    snap = {}
    for surface in surfaces:
        root = home / surface
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            rel_dir = Path(dirpath).relative_to(home)
            dirnames[:] = [d for d in dirnames if rel_dir / d not in skip]
            for d in dirnames:
                snap[str(rel_dir / d)] = "dir"
            for f in filenames:
                rel = rel_dir / f
                if rel in skip:
                    continue
                try:
                    st = (home / rel).lstat()
                except FileNotFoundError:
                    continue
                snap[str(rel)] = f"{'link' if os.path.islink(home / rel) else 'file'}:{st.st_size}:{st.st_mtime_ns}"
    return snap


def home_changes(before, after):
    """Paths added, removed or changed between two snapshots, as `~/<path>`."""
    keys = set(before) | set(after)
    return sorted(f"~/{k}" for k in keys if before.get(k) != after.get(k))


def containment_paths(log_text, shard):
    """Path lists from the runner's own containment-failure lines for one shard, as
    printed by that shard's --ci-run process. None for a list the runner truncated."""
    out = []
    for m in CONTAINMENT_LINE.finditer(log_text):
        if int(m.group(1)) != shard:
            continue
        listed = m.group(2)
        out.append(None if re.search(r"\(\+\d+ more\)", listed) else [p.strip() for p in listed.split(", ") if p.strip()])
    return out


def is_external_writer(path, patterns):
    return any(fnmatch.fnmatchcase(path, pat) for pat in patterns)


def exclusive_shards(plan, tree_mutating):
    """Shards made only of files the runner marks tree-mutating: they run alone."""
    return [s["shard"] for s in plan["shards"] if s["files"] and all(f in tree_mutating for f in s["files"])]


def assess_ci_results(plan, results, shard_logs, external_patterns, window_changes=None):
    """Judge `--ci-run` results that `--ci-verify` rejected.

    problems      results that are not evidence at all: missing, duplicate or foreign
                  results, a pass that reports failures (hard stop)
    gaps          shards whose run is not coverage: incomplete summary, timeout, or
                  failure attribution outside the plan; compared with upstream later
    unaccounted   failures no named test explains, as {shard, files, reason}; compared
                  with upstream like gaps, then stop unless accepted
    environmental containment failures touching only external agent-host paths
    failing_files files with named failures, for the paired comparison
    failures      {file: {"file :: test name"}} from the runner's named failures
    window_changes, when given, is fork-sync's own untruncated list of real-home
    changes over the whole run; it settles containment lines the runner truncated."""
    problems, unaccounted, environmental, gaps = [], [], [], []
    failing, failures = set(), {}
    by_shard = {}
    for r in results:
        o = r.get("outcome") or {}
        n = o.get("shard")
        if r.get("planId") != plan["id"] or r.get("revision") != plan["revision"]:
            problems.append(f"result for shard {n} belongs to another plan or revision")
            continue
        if n in by_shard:
            problems.append(f"duplicate result for shard {n}")
            continue
        by_shard[n] = o
    for shard in plan["shards"]:
        n, files = shard["shard"], shard["files"]
        o = by_shard.get(n)
        if o is None:
            problems.append(f"shard {n} has no result; it did not run or did not finish")
            continue
        if o.get("files") != files:
            problems.append(f"shard {n} ran a different file set than planned")
            continue
        summary = o.get("summary") or {}
        complete = isinstance(summary.get("testsRan"), int) and summary["testsRan"] >= 0 and summary.get("filesRan") == len(files) \
            and (summary.get("sawTerminalSummary") is True if files else True)
        status = o.get("status")
        named = o.get("failingFiles") or []
        unattr = o.get("unattributedFailures") or 0
        outside = sorted(set(named) - set(files))
        in_plan = [f for f in named if f in files]
        reason = ("timed out" if status == "timed-out" else f"incomplete execution summary {summary}" if not complete
                  else f"failure attribution outside its plan: {outside}" if outside else None)
        if reason:
            # Not coverage, but its in-plan named failures are still real failures to compare.
            gaps.append({"shard": n, "files": files, "reason": reason})
            failing.update(in_plan)
            for fl in o.get("failures") or []:
                if fl.get("file") in in_plan:
                    failures.setdefault(fl["file"], set()).add(f"{fl['file']} :: {TEMP_PATH.sub('', str(fl.get('testName', ''))).strip()}")
            continue
        if status == "passed":
            if named or unattr:
                problems.append(f"shard {n} passed but reports failures")
            continue
        if status != "failed":
            problems.append(f"shard {n} has unknown status {status!r}")
            continue
        failing.update(named)
        for fl in o.get("failures") or []:
            if fl.get("file") in named:
                failures.setdefault(fl["file"], set()).add(f"{fl['file']} :: {TEMP_PATH.sub('', str(fl.get('testName', ''))).strip()}")
        if unattr:
            lists = containment_paths(shard_logs.get(n, ""), n)
            window_external = window_changes is not None and all(is_external_writer(p, external_patterns) for p in window_changes)
            if len(lists) == unattr and all(l is not None and all(is_external_writer(p, external_patterns) for p in l) for l in lists):
                environmental.append(f"shard {n}: real-home writes by agent hosts only: {', '.join(p for l in lists for p in l)}")
            elif len(lists) == unattr and any(l is None for l in lists) and window_external:
                environmental.append(f"shard {n}: the runner truncated its path list; fork-sync's own full snapshot of the run window shows only agent-host writes ({len(window_changes)} paths)")
            elif len(lists) == unattr and any(l is None for l in lists) and window_changes is not None:
                outside = [p for p in window_changes if not is_external_writer(p, external_patterns)]
                unaccounted.append({"shard": n, "files": files, "reason": f"home containment failed, and the run window changed paths outside the external-writer list: {', '.join(outside[:20])}" + (f" (+{len(outside) - 20} more)" if len(outside) > 20 else "")})
            else:
                why = "a truncated path list" if any(l is None for l in lists) else (
                    f"{unattr} unattributed failure(s), {len(lists)} containment line(s)" if len(lists) != unattr else
                    "paths outside the external-writer list: " + ", ".join(p for l in lists for p in l if not is_external_writer(p, external_patterns)))
                unaccounted.append({"shard": n, "files": files, "reason": f"unattributed failure(s) not explained by a named test ({why})"})
        elif not named:
            unaccounted.append({"shard": n, "files": files, "reason": "failed with no named failure and no unattributed count"})
    return {"problems": problems, "gaps": gaps, "unaccounted": unaccounted, "environmental": environmental,
            "failing_files": sorted(failing), "failures": failures}


def compare_gaps(merge_gaps, upstream_gaps, merge_plan, upstream_plan):
    """Split the merge's coverage gaps into environmental ones (the shard with the same
    file set also has a gap upstream) and blocking ones. Plans whose shard file sets
    differ cannot be compared shard for shard, so that fails closed."""
    def sets(plan):
        return [tuple(s["files"]) for s in plan["shards"]]
    if sets(merge_plan) != sets(upstream_plan):
        raise SyncError("the merge and upstream CI plans pack different shard file sets, so their coverage gaps cannot be compared shard for shard")
    upstream_gap_files = {tuple(g["files"]) for g in upstream_gaps}
    environmental = [g for g in merge_gaps if tuple(g["files"]) in upstream_gap_files]
    blocking = [g for g in merge_gaps if tuple(g["files"]) not in upstream_gap_files]
    return environmental, blocking


MIN_FOCUS_BASENAME = 8


def focused_test_files(changed, test_files, read_text):
    """Test files that must always run alone: those the fork changed, plus those whose
    source names a fork-changed source file by its path without extension (an import
    specifier or a spawned path). docs/ and .forgejo/ changes trigger nothing."""
    triggers = []
    for f in changed:
        if f.startswith(("docs/", ".forgejo/")) or f.endswith(".test.ts"):
            continue
        stem = re.sub(r"\.(ts|js|mjs|tsx|md|tmpl)$", "", f)
        if stem.endswith(".md"):
            stem = stem[:-3]
        triggers.append(stem)
    # The basename too: tests often build paths piecewise (path.join(ROOT, 'bin', name)).
    # Short basenames are skipped so a generic word cannot pull in the whole suite.
    names = triggers + [b for b in (t.rsplit("/", 1)[-1] for t in triggers) if len(b) >= MIN_FOCUS_BASENAME]
    focused = {f for f in changed if f.endswith(".test.ts") and f in test_files}
    for t in test_files:
        text = read_text(t)
        if any(n in text for n in names):
            focused.add(t)
    return sorted(focused)


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


def default_run_dir(root, version, upstream_sha):
    """One run per upstream commit: VERSION alone can repeat across upstream commits."""
    return Path(root) / f"v{version}-{upstream_sha[:9]}"


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
