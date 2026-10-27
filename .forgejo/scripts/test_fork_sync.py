# WHAT: Tests for fork-sync: pure logic in fork_sync_lib, plus integration runs of the
#       fork-sync CLI against throwaway git repos.
# WHY:  The script's job is to stop before anything reaches the forge when a sync has a
#       problem, so the stop paths are what the integration tests exercise.
# Run:  python3 -m unittest discover -s .forgejo/scripts -p 'test_*.py' -v

import json
import os
import subprocess
import time
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fork_sync_lib as lib  # noqa: E402

CLI = HERE / "fork-sync"




PLAN = {"id": "plan-1", "revision": "rev-1", "version": 1, "shards": [
    {"shard": 1, "files": ["test/a.test.ts", "test/b.test.ts"], "predictedMs": 1},
    {"shard": 2, "files": ["test/c.test.ts"], "predictedMs": 1},
    {"shard": 3, "files": ["test/tree.test.ts"], "predictedMs": 1}]}


def result(shard, status="passed", failing=(), unattributed=0, files_ran=None, failures=(), plan_id="plan-1", revision="rev-1", saw=True):
    files = next(s["files"] for s in PLAN["shards"] if s["shard"] == shard)
    return {"planId": plan_id, "revision": revision, "retry": None, "outcome": {
        "shard": shard, "files": files, "status": status, "exitCode": 0 if status == "passed" else 1, "elapsedMs": 1, "groupPid": 1,
        "summary": {"testsRan": 5, "filesRan": len(files) if files_ran is None else files_ran, "sawTerminalSummary": saw},
        "failingFiles": list(failing), "unattributedFailures": unattributed,
        "failures": [{"file": f, "testName": n, "excerpt": []} for f, n in failures]}}


EXTERNAL = ["~/.claude/backups/.claude.json.backup.*", "~/.codex/*.sqlite-wal"]
CONTAIN = ("[test:free] shard {i}/3 shard cleanup or home containment failed: real home changed while this shard ran "
           "(watches ~/.gstack, ~/.claude, ~/.codex, ~/.agents, ~/.config/gstack only): {paths}. Give the writer a private HOME. Shard files: x; retained /tmp/s")


class AssessCiResults(unittest.TestCase):
    def assess(self, results, logs=None):
        return lib.assess_ci_results(PLAN, results, logs or {}, EXTERNAL)

    def test_all_passing_shards_have_nothing_to_compare(self):
        a = self.assess([result(1), result(2), result(3)])
        self.assertEqual((a["problems"], a["unaccounted"], a["failing_files"]), ([], [], []))

    def test_named_failures_go_to_the_comparison_with_their_test_names(self):
        a = self.assess([result(1, "failed", ["test/b.test.ts"], failures=[("test/b.test.ts", "g > x")]), result(2), result(3)])
        self.assertEqual(a["problems"], [])
        self.assertEqual(a["failing_files"], ["test/b.test.ts"])
        self.assertEqual(a["failures"], {"test/b.test.ts": {"test/b.test.ts :: g > x"}})

    def test_a_missing_shard_result_is_a_hard_problem(self):
        self.assertTrue(self.assess([result(1), result(3)])["problems"])

    def test_a_duplicate_or_foreign_result_is_a_hard_problem(self):
        self.assertTrue(self.assess([result(1), result(1), result(2), result(3)])["problems"])
        self.assertTrue(self.assess([result(1, plan_id="other"), result(2), result(3)])["problems"])
        self.assertTrue(self.assess([result(1, revision="rev-0"), result(2), result(3)])["problems"])

    def test_an_incomplete_summary_is_a_coverage_gap_not_a_hard_problem(self):
        for r1 in (result(1, files_ran=1), result(1, saw=False)):
            a = self.assess([r1, result(2), result(3)])
            self.assertEqual(a["problems"], [])
            self.assertEqual([g["shard"] for g in a["gaps"]], [1])

    def test_a_timed_out_shard_is_a_coverage_gap(self):
        a = self.assess([result(1, "timed-out"), result(2), result(3)])
        self.assertEqual(([g["shard"] for g in a["gaps"]], a["problems"]), ([1], []))

    def test_named_failures_inside_an_incomplete_shard_still_reach_the_comparison(self):
        a = self.assess([result(1, "failed", ["test/b.test.ts"], files_ran=1, failures=[("test/b.test.ts", "g > x")]), result(2), result(3)])
        self.assertEqual(a["failing_files"], ["test/b.test.ts"])

    def test_failure_attribution_outside_the_shard_is_a_coverage_gap(self):
        a = self.assess([result(1, "failed", ["test/c.test.ts"]), result(2), result(3)])
        self.assertEqual(([g["shard"] for g in a["gaps"]], a["problems"]), ([1], []))

    def test_a_failure_with_no_attribution_at_all_is_unaccounted(self):
        a = self.assess([result(1, "failed"), result(2), result(3)])
        self.assertEqual(a["problems"], [])
        self.assertEqual(len(a["unaccounted"]), 1)

    def test_containment_touching_only_external_writer_paths_is_environmental(self):
        log = CONTAIN.format(i=1, paths="~/.claude/backups/.claude.json.backup.1792, ~/.codex/state_5.sqlite-wal")
        a = self.assess([result(1, "failed", ["test/b.test.ts"], 1), result(2), result(3)], {1: log})
        self.assertEqual(a["unaccounted"], [])
        self.assertEqual(len(a["environmental"]), 1)
        self.assertEqual(a["failing_files"], ["test/b.test.ts"])

    def test_containment_touching_any_other_path_is_unaccounted(self):
        log = CONTAIN.format(i=1, paths="~/.claude/backups/.claude.json.backup.1792, ~/.claude/settings.json")
        self.assertEqual(len(self.assess([result(1, "failed", [], 1), result(2), result(3)], {1: log})["unaccounted"]), 1)

    def test_a_truncated_path_list_is_unaccounted(self):
        log = CONTAIN.format(i=1, paths="~/.claude/backups/.claude.json.backup.1792 (+93 more)")
        self.assertEqual(len(self.assess([result(1, "failed", [], 1), result(2), result(3)], {1: log})["unaccounted"]), 1)

    def test_more_unattributed_failures_than_containment_lines_is_unaccounted(self):
        log = CONTAIN.format(i=1, paths="~/.codex/state_5.sqlite-wal")
        self.assertEqual(len(self.assess([result(1, "failed", [], 2), result(2), result(3)], {1: log})["unaccounted"]), 1)

    def test_a_containment_line_for_another_shard_does_not_count(self):
        log = CONTAIN.format(i=2, paths="~/.codex/state_5.sqlite-wal")
        self.assertEqual(len(self.assess([result(1, "failed", [], 1), result(2), result(3)], {1: log})["unaccounted"]), 1)


class CompareGaps(unittest.TestCase):
    def gap(self, shard):
        return {"shard": shard, "files": next(s["files"] for s in PLAN["shards"] if s["shard"] == shard), "reason": "x"}

    def test_a_gap_also_present_upstream_is_environmental(self):
        env, blocking = lib.compare_gaps([self.gap(1)], [self.gap(1), self.gap(2)], PLAN, PLAN)
        self.assertEqual(([g["shard"] for g in env], blocking), ([1], []))

    def test_a_gap_only_on_the_merge_blocks(self):
        env, blocking = lib.compare_gaps([self.gap(1), self.gap(2)], [self.gap(1)], PLAN, PLAN)
        self.assertEqual(([g["shard"] for g in env], [g["shard"] for g in blocking]), ([1], [2]))

    def test_plans_with_different_shard_file_sets_cannot_be_compared(self):
        other = {**PLAN, "shards": [{**PLAN["shards"][0], "files": ["test/a.test.ts"]}, *PLAN["shards"][1:]]}
        with self.assertRaises(lib.SyncError):
            lib.compare_gaps([self.gap(1)], [self.gap(1)], PLAN, other)


class FocusedTests(unittest.TestCase):
    def test_changed_tests_and_tests_naming_a_changed_source_are_focused(self):
        sources = {
            "test/outside-review-result.test.ts": "import { x } from '../lib/outside-review-result';",
            "test/probe.test.ts": "const PROBE = path.join(ROOT, 'bin', 'gstack-codex-probe');",
            "test/unrelated.test.ts": "import { y } from '../lib/other';",
            "test/plain-name.test.ts": "spawn('bin/gstack-codex-probe')",
        }
        changed = ["lib/outside-review-result.ts", "bin/gstack-codex-probe", "test/codex-hardening.test.ts",
                   "docs/changes/x/plan.md", ".forgejo/scripts/fork-sync"]
        got = lib.focused_test_files(changed, list(sources) + ["test/codex-hardening.test.ts"], lambda f: sources.get(f, ""))
        self.assertEqual(got, ["test/codex-hardening.test.ts", "test/outside-review-result.test.ts", "test/plain-name.test.ts", "test/probe.test.ts"])


class ExclusiveShards(unittest.TestCase):
    def test_a_shard_made_only_of_tree_mutating_files_runs_alone(self):
        self.assertEqual(lib.exclusive_shards(PLAN, {"test/tree.test.ts": "mutates the tree"}), [3])

    def test_a_mixed_shard_is_not_exclusive(self):
        self.assertEqual(lib.exclusive_shards(PLAN, {"test/a.test.ts": "x"}), [])


class ParseBunFails(unittest.TestCase):
    def test_strips_timings_and_temp_paths_and_keys_by_file(self):
        out = "\n".join([
            "(fail) group > case [12.34ms]",
            "(fail) group > reads /private/var/folders/x/T/fixture-AbC123/repo [5.00s]",
            "(pass) group > fine",
        ])
        self.assertEqual(lib.parse_bun_fails("test/a.test.ts", out), {
            "test/a.test.ts :: group > case",
            "test/a.test.ts :: group > reads",
        })


class ParseBunRun(unittest.TestCase):
    COMPLETE = "(fail) g > a [1.00ms]\n 3 pass\n 1 fail\nRan 4 tests across 1 file. [20.00ms]"

    def test_completed_run_returns_its_failures(self):
        self.assertEqual(lib.parse_bun_run("t.ts", 1, self.COMPLETE), {"t.ts :: g > a"})

    def test_failing_exit_without_a_completion_line_is_incomplete(self):
        with self.assertRaises(lib.SyncError):
            lib.parse_bun_run("t.ts", 1, "error: Cannot find module './gone'\n")

    def test_timeout_is_incomplete_even_with_partial_results(self):
        with self.assertRaises(lib.SyncError):
            lib.parse_bun_run("t.ts", 124, "(fail) g > a\n[fork-sync] timed out after 600s\n")

    def test_nonzero_exit_with_no_named_failure_is_incomplete(self):
        with self.assertRaises(lib.SyncError):
            lib.parse_bun_run("t.ts", 1, " 4 pass\n 0 fail\nRan 4 tests across 1 file.")

    def test_a_selector_that_ran_more_than_one_file_is_rejected(self):
        with self.assertRaises(lib.SyncError):
            lib.parse_bun_run("t.ts", 1, "(fail) g > a\n 3 pass\n 1 fail\nRan 4 tests across 2 files. [20.00ms]")

    def test_unhandled_errors_count_as_a_named_failure(self):
        out = "# Unhandled error between tests\n 4 pass\n 0 fail\n 1 error\nRan 4 tests across 1 file."
        self.assertEqual(lib.parse_bun_run("t.ts", 1, out), {"t.ts :: <unhandled error>"})


def load_cli():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("fork_sync_cli", str(CLI))
    spec = importlib.util.spec_from_loader("fork_sync_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class HooksNeverCached(unittest.TestCase):
    def test_hooks_verify_reruns_even_when_it_passed_at_the_same_head(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            state = lib.State.load(run_dir / "state.json")
            state.data["pins"] = {"version": "1", "origin": "o", "github": "g", "branch": "b"}
            state.mark_passed("hooks-verify", sha=None)
            sync = cli.Sync(type("A", (), {})(), Path(d), run_dir, None)
            self.assertFalse(sync.done("hooks-verify"))


class RunIdentity(unittest.TestCase):
    def test_default_run_dir_names_the_upstream_sha_not_just_the_version(self):
        a = lib.default_run_dir(Path("/r"), "1.2.3.4", "abcdef0123456789")
        b = lib.default_run_dir(Path("/r"), "1.2.3.4", "fedcba9876543210")
        self.assertEqual(a, Path("/r/v1.2.3.4-abcdef012"))
        self.assertNotEqual(a, b)


class TimeoutKillsTheProcessTree(unittest.TestCase):
    def test_a_child_the_command_started_does_not_outlive_the_timeout(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "child.pid"
            code, _ = cli.run(["sh", "-c", f"sleep 300 & echo $! > {pidfile}; wait"], Path(d), timeout=1)
            self.assertEqual(code, 124)
            pid = int(pidfile.read_text())
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, 9)
                self.fail(f"child {pid} outlived the timeout")


class BlockingDirty(unittest.TestCase):
    def test_tool_state_dot_entries_are_ignored_and_everything_else_blocks(self):
        lines = ["?? .serena/", "?? .DS_Store", " M lib/x.ts", "?? lib/new.ts", "?? .forgejo/scripts/new", ""]
        self.assertEqual(lib.blocking_dirty(lines), [" M lib/x.ts", "?? lib/new.ts", "?? .forgejo/scripts/new"])

    def test_modified_tracked_dot_file_still_blocks(self):
        self.assertEqual(lib.blocking_dirty([" M .gitignore"]), [" M .gitignore"])




class DotenvFiles(unittest.TestCase):
    def test_finds_every_dotenv_variant_bun_would_load(self):
        names = [".env", ".env.local", ".env.test", ".env.production.local", ".envrc-not", "env", ".gitignore", "README.md"]
        self.assertEqual(lib.dotenv_files(names), [".env", ".env.local", ".env.production.local", ".env.test"])

    def test_templates_bun_never_loads_are_not_dotenv(self):
        self.assertEqual(lib.dotenv_files([".env.example", ".env.sample", ".env.template"]), [])


class SplitAttributable(unittest.TestCase):
    def test_entries_that_are_not_repo_files_are_unattributable(self):
        listed = {"test/a.test.ts", "(unattributed)", "var/folders/x/T/nested.test.ts"}
        files, other = lib.split_attributable(listed, lambda p: p == "test/a.test.ts")
        self.assertEqual(files, ["test/a.test.ts"])
        self.assertEqual(other, ["(unattributed)", "var/folders/x/T/nested.test.ts"])


class Classify(unittest.TestCase):
    def runs(self, *sets):
        return [set(s) for s in sets]

    def test_fails_only_on_merge_every_time_is_fork_caused(self):
        got = lib.classify(self.runs({"t"}, {"t"}), self.runs(set(), set()), self.runs(set(), set()))
        self.assertEqual(got, {"t": "fork-caused"})

    def test_also_failing_on_pre_sync_fork_is_pre_existing(self):
        got = lib.classify(self.runs({"t"}, {"t"}), self.runs(set(), set()), self.runs({"t"}, set()))
        self.assertEqual(got, {"t": "pre-existing"})

    def test_intermittent_on_merge_is_flaky(self):
        got = lib.classify(self.runs({"t"}, set()), self.runs(set(), set()), self.runs(set(), set()))
        self.assertEqual(got, {"t": "flaky"})

    def test_intermittent_on_upstream_is_flaky(self):
        got = lib.classify(self.runs({"t"}, {"t"}), self.runs({"t"}, set()), self.runs(set(), set()))
        self.assertEqual(got, {"t": "flaky"})

    def test_failing_on_upstream_every_time_is_upstream(self):
        got = lib.classify(self.runs({"t"}, {"t"}), self.runs({"t"}, {"t"}), self.runs({"t"}, {"t"}))
        self.assertEqual(got, {"t": "upstream"})

    def test_failing_only_on_baseline_is_ignored(self):
        self.assertEqual(lib.classify(self.runs(set()), self.runs(set()), self.runs({"t"})), {})

    def test_a_test_absent_upstream_is_judged_against_the_pre_sync_fork(self):
        self.assertEqual(lib.classify(self.runs({"t"}, {"t"}), None, self.runs(set(), set())), {"t": "fork-caused"})
        self.assertEqual(lib.classify(self.runs({"t"}, {"t"}), None, self.runs({"t"}, {"t"})), {"t": "pre-existing"})

    def test_a_test_new_upstream_is_judged_against_upstream_alone(self):
        self.assertEqual(lib.classify(self.runs({"t"}, {"t"}), self.runs({"t"}, {"t"}), None), {"t": "upstream"})
        self.assertEqual(lib.classify(self.runs({"t"}, {"t"}), self.runs(set(), set()), None), {"t": "fork-caused"})

    def test_needs_at_least_one_run_per_side(self):
        with self.assertRaises(lib.SyncError):
            lib.classify([], self.runs(set()), self.runs(set()))


class NotReproduced(unittest.TestCase):
    def test_suite_failures_missing_from_every_standalone_run_are_kept(self):
        suite = {"t.ts": {"t.ts :: a", "t.ts :: b"}}
        self.assertEqual(lib.not_reproduced(suite, {"t.ts :: a": "flaky"}), ["t.ts :: b"])


class ForkDelta(unittest.TestCase):
    def test_merge_back_to_upstream_bytes_is_dropped(self):
        blobs = {"a": ("fork", "up", "up")}  # (pre-sync fork, upstream, merge)
        self.assertEqual(lib.fork_delta(blobs), {"dropped": ["a"], "adopted": [], "kept": []})

    def test_upstream_taking_the_fork_bytes_is_adopted_not_dropped(self):
        blobs = {"a": ("fork", "fork", "fork")}
        self.assertEqual(lib.fork_delta(blobs), {"dropped": [], "adopted": ["a"], "kept": []})

    def test_merge_still_differing_from_upstream_is_kept(self):
        blobs = {"a": ("fork", "up", "merged"), "b": ("fork", None, "fork")}
        self.assertEqual(lib.fork_delta(blobs), {"dropped": [], "adopted": [], "kept": ["a", "b"]})

    def test_fork_deletion_that_the_merge_brought_back_is_dropped(self):
        blobs = {"a": (None, "up", "up")}
        self.assertEqual(lib.fork_delta(blobs)["dropped"], ["a"])


class HooksManifest(unittest.TestCase):
    def test_diff_names_added_removed_and_changed_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "pre-push").write_text("one")
            (p / "pre-commit").write_text("two")
            before = lib.hooks_manifest(p)
            (p / "pre-push").write_text("managed")
            (p / "pre-commit").unlink()
            (p / "pre-push.local").write_text("one")
            diff = lib.hooks_diff(before, lib.hooks_manifest(p))
        self.assertEqual(diff, {"added": ["pre-push.local"], "removed": ["pre-commit"], "changed": ["pre-push"]})

    def test_unchanged_directory_has_empty_diff(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pre-push").write_text("one")
            m = lib.hooks_manifest(Path(d))
            self.assertEqual(lib.hooks_diff(m, lib.hooks_manifest(Path(d))), {"added": [], "removed": [], "changed": []})


class State(unittest.TestCase):
    def test_passed_stages_survive_a_reload(self):
        with tempfile.TemporaryDirectory() as d:
            s = lib.State.load(Path(d) / "state.json")
            s.mark_passed("merge", sha="abc")
            s.data["pins"] = {"origin": "1"}
            s.save()
            again = lib.State.load(Path(d) / "state.json")
            self.assertTrue(again.passed("merge"))
            self.assertFalse(again.passed("checks"))
            self.assertEqual(again.data["pins"], {"origin": "1"})

    def test_corrupt_state_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "state.json"
            f.write_text("{not json")
            with self.assertRaises(lib.SyncError):
                lib.State.load(f)


class RemoteSlug(unittest.TestCase):
    def test_reads_owner_and_name_from_common_url_forms(self):
        for url in ["http://host:3300/jfcadm/gstack.git", "git@host:jfcadm/gstack.git", "https://host/jfcadm/gstack"]:
            self.assertEqual(lib.remote_slug(url), "jfcadm/gstack")


class Report(unittest.TestCase):
    def test_lists_blocking_items_first_and_keeps_every_section(self):
        text = lib.render_report({
            "upstream_version": "1.91.300.0", "stopped": "merge", "problems": ["CONFLICT: lib/x.ts"],
            "sections": [("Merge", "conflicts in 1 file")],
        })
        self.assertIn("1.91.300.0", text)
        self.assertLess(text.index("CONFLICT: lib/x.ts"), text.index("## Merge"))
        self.assertIn("Stopped at stage `merge`", text)
        self.assertIn("Nothing was pushed", text)

    def test_a_stop_after_the_push_says_the_branch_reached_the_forge(self):
        text = lib.render_report({"upstream_version": "1", "stopped": "pr", "problems": ["forge-pr create failed"],
                                  "pushed": "upgrade/sync-v1 at abc1234"})
        self.assertNotIn("Nothing was pushed", text)
        self.assertIn("upgrade/sync-v1 at abc1234", text)


def git(cwd, *args, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


class Integration(unittest.TestCase):
    """Throwaway forge, upstream and clone; the CLI runs against them with an isolated git config."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        gitconfig = root / "gitconfig"
        gitconfig.write_text("[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(root / "home")}
        self.env.pop("GIT_CONFIG_PARAMETERS", None)
        (root / "home").mkdir()
        self.upstream = root / "upstream.git"
        self.forge = root / "forge.git"
        git(root, "init", "-q", "--bare", str(self.upstream), env=self.env)
        git(root, "init", "-q", "--bare", str(self.forge), env=self.env)
        seed = root / "seed"
        git(root, "init", "-q", str(seed), env=self.env)
        (seed / "VERSION").write_text("1.0.0.0\n")
        (seed / "shared.txt").write_text("base\n")
        (seed / "fork.txt").write_text("base\n")
        (seed / "test").mkdir()
        (seed / "test" / "a.test.ts").write_text("// stub\n")
        git(seed, "add", "VERSION", "shared.txt", "fork.txt", "test/a.test.ts", env=self.env)
        git(seed, "commit", "-q", "-m", "base", env=self.env)
        git(seed, "push", "-q", str(self.upstream), "main", env=self.env)
        git(seed, "push", "-q", str(self.forge), "main", env=self.env)
        self.repo = root / "live"
        git(root, "clone", "-q", str(self.forge), str(self.repo), env=self.env)
        git(self.repo, "remote", "add", "github", str(self.upstream), env=self.env)
        self.run_dir = root / "run"

    def tearDown(self):
        self.tmp.cleanup()

    def commit_upstream(self, files, version):
        work = self.root / f"up-{version}"
        git(self.root, "clone", "-q", str(self.upstream), str(work), env=self.env)
        for name, body in {**files, "VERSION": version + "\n"}.items():
            (work / name).write_text(body)
        git(work, "add", "-A", env=self.env)
        git(work, "commit", "-q", "-m", f"v{version}", env=self.env)
        git(work, "push", "-q", "origin", "main", env=self.env)

    def commit_fork(self, files):
        for name, body in files.items():
            (self.repo / name).write_text(body)
        git(self.repo, "add", *files, env=self.env)
        git(self.repo, "commit", "-q", "-m", "fork change", env=self.env)
        git(self.repo, "push", "-q", "origin", "main", env=self.env)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), "--repo", str(self.repo), "--run-dir", str(self.run_dir), "--mirror", str(self.forge), *args],
            capture_output=True, text=True, env=self.env, timeout=120)

    def forge_refs(self):
        return git(self.root, "ls-remote", str(self.forge), env=self.env)

    def test_conflict_stops_before_push_and_names_the_path(self):
        self.commit_fork({"shared.txt": "fork side\n"})
        self.commit_upstream({"shared.txt": "upstream side\n"}, "1.1.0.0")
        before = self.forge_refs()
        r = self.run_cli("--until", "pr")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertEqual(self.forge_refs(), before)
        report = (self.run_dir / "report.md").read_text()
        self.assertIn("shared.txt", report)
        self.assertIn("Stopped at stage `merge`", report)

    def test_resume_after_a_hand_resolved_merge_passes_merge_and_fork_delta(self):
        self.commit_fork({"shared.txt": "fork side\n", "fork.txt": "fork only\n"})
        self.commit_upstream({"shared.txt": "upstream side\n"}, "1.1.0.0")
        self.assertEqual(self.run_cli("--until", "fork-delta").returncode, 10)
        merge = self.run_dir / "merge"
        (merge / "shared.txt").write_text("both sides\n")
        git(merge, "add", "shared.txt", env=self.env)
        git(merge, "commit", "-q", "--no-edit", env=self.env)
        r = self.run_cli("--until", "fork-delta")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("fork.txt", (self.run_dir / "report.md").read_text())

    def test_resolving_a_conflict_to_upstream_bytes_is_caught_as_a_dropped_fork_change(self):
        self.commit_fork({"shared.txt": "fork side\n"})
        self.commit_upstream({"shared.txt": "upstream side\n"}, "1.1.0.0")
        self.run_cli("--until", "fork-delta")
        merge = self.run_dir / "merge"
        git(merge, "checkout", "--theirs", "--", "shared.txt", env=self.env)
        git(merge, "add", "shared.txt", env=self.env)
        git(merge, "commit", "-q", "--no-edit", env=self.env)
        r = self.run_cli("--until", "fork-delta")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("dropped", (self.run_dir / "report.md").read_text().lower())

    def test_live_checkout_is_left_on_its_branch_and_unchanged(self):
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        (self.repo / "untracked-note").write_text("mine\n")
        before = (git(self.repo, "rev-parse", "HEAD", env=self.env), git(self.repo, "status", "--porcelain", env=self.env))
        r = self.run_cli("--until", "fork-delta")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = (git(self.repo, "rev-parse", "HEAD", env=self.env), git(self.repo, "status", "--porcelain", env=self.env))
        self.assertEqual(after, before)
        self.assertEqual(git(self.repo, "branch", "--show-current", env=self.env), "main")

    def test_nothing_to_sync_exits_cleanly_without_a_worktree(self):
        r = self.run_cli("--until", "pr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing to sync", r.stdout.lower())
        self.assertFalse((self.run_dir / "merge").exists())

    def test_forge_and_mirror_out_of_step_stops_at_preflight(self):
        other = self.root / "mirror.git"
        git(self.root, "clone", "-q", "--bare", str(self.upstream), str(other), env=self.env)
        self.commit_fork({"fork.txt": "fork only\n"})
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        r = subprocess.run(
            [sys.executable, str(CLI), "--repo", str(self.repo), "--run-dir", str(self.run_dir), "--mirror", str(other), "--until", "merge"],
            capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("mirror", (self.run_dir / "report.md").read_text().lower())
        self.assertFalse((self.run_dir / "merge").exists())

    def test_resuming_a_preflight_stop_rechecks_the_mirror_instead_of_crashing(self):
        other = self.root / "mirror.git"
        git(self.root, "clone", "-q", "--bare", str(self.upstream), str(other), env=self.env)
        self.commit_fork({"fork.txt": "fork only\n"})
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        first = subprocess.run(
            [sys.executable, str(CLI), "--repo", str(self.repo), "--run-dir", str(self.run_dir), "--mirror", str(other), "--until", "merge"],
            capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(first.returncode, 10, first.stdout + first.stderr)
        r = self.run_cli("--until", "merge")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_uncommitted_change_in_the_merge_worktree_stops_a_cached_resume(self):
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        self.assertEqual(self.run_cli("--until", "fork-delta").returncode, 0)
        (self.run_dir / "merge" / "fork.txt").write_text("edited, not committed\n")
        r = self.run_cli("--until", "fork-delta")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("uncommitted", (self.run_dir / "report.md").read_text().lower())

    def test_relative_run_dir_resolves_against_the_callers_cwd(self):
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        r = subprocess.run([sys.executable, str(CLI), "--repo", str(self.repo), "--run-dir", "rel-run", "--mirror", str(self.forge), "--until", "fork-delta"],
                           cwd=self.root, capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.root / "rel-run" / "merge" / "VERSION").is_file())
        self.assertTrue((self.root / "rel-run" / "report.md").is_file())

    def test_dotenv_file_in_the_merge_worktree_stops_before_checks(self):
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        self.assertEqual(self.run_cli("--until", "fork-delta").returncode, 0)
        (self.run_dir / "merge" / ".env").write_text("SECRET=1\n")
        r = self.run_cli("--until", "checks")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn(".env", (self.run_dir / "report.md").read_text())

    def test_a_finished_run_is_never_resumed(self):
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        self.assertEqual(self.run_cli("--until", "fork-delta").returncode, 0)
        state = lib.State.load(self.run_dir / "state.json")
        state.data["finished"] = True
        state.save()
        r = self.run_cli("--until", "pr")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("finished", r.stdout.lower())

    STUB = r"""#!{python}
import json, os, sys
calls = os.environ["FAKE_CALLS"]
with open(calls, "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
sc = json.load(open(os.environ["FAKE_CI"]))
a = [x for x in sys.argv[1:] if x not in ("--no-env-file", "--config=/dev/null")]
def flag(name):
    return a[a.index(name) + 1]
if a[:1] == ["-e"]:
    print(json.dumps(sc.get("tree_mutating", []))); sys.exit(0)
if a[:2] == ["run", "scripts/test-free-shards.ts"]:
    if "--ci-plan" in a:
        json.dump({{"id": "p1", "revision": "r1", "version": 1, "shards": sc["shards"]}}, open(flag("--ci-plan"), "w")); sys.exit(0)
    side = "upstream" if "/upstream" in os.getcwd() else "merge"
    if "--ci-run" in a:
        n = flag("--shard"); r = sc.get("results_" + side, sc["results"]).get(n)
        print(sc.get("logs", {{}}).get(n, ""))
        if r is None:
            sys.exit(1)
        json.dump({{"planId": "p1", "revision": "r1", "retry": None, "outcome": r}}, open(flag("--result"), "w"))
        sys.exit(0 if r["status"] == "passed" else 1)
    if "--ci-verify" in a:
        print("[test:free] verify (stub)"); sys.exit(sc.get("verify_exit_" + side, sc["verify_exit"]))
if a[:2] == ["run", "typecheck:test"]:
    print("test typecheck ratchet: 0 known diagnostics, none new."); sys.exit(0)
if a[:1] == ["test"]:
    path = a[1]; tree = next((t for t in ("merge", "upstream", "baseline") if "/" + t + "/" in path), "?")
    fails = sc.get("standalone", {{}}).get(tree, [])
    for name in fails:
        print("(fail) " + name + " [1.00ms]")
    print(" 1 pass\n " + str(len(fails)) + " fail\nRan " + str(1 + len(fails)) + " tests across 1 file. [5.00ms]")
    sys.exit(1 if fails else 0)
sys.exit(0)
"""

    def outcome(self, shard, files, status="passed", failing=(), unattributed=0, failures=()):
        return {"shard": shard, "files": list(files), "status": status, "exitCode": 0 if status == "passed" else 1, "elapsedMs": 1, "groupPid": 1,
                "summary": {"testsRan": 3, "filesRan": len(files), "sawTerminalSummary": True},
                "failingFiles": list(failing), "unattributedFailures": unattributed,
                "failures": [{"file": f, "testName": n, "excerpt": []} for f, n in failures]}

    def run_suite(self, scenario, *extra):
        stub = self.root / "stub-bin"
        stub.mkdir(exist_ok=True)
        (stub / "bun").write_text(self.STUB.format(python=sys.executable))
        (stub / "bun").chmod(0o755)
        (self.root / "scenario.json").write_text(json.dumps(scenario))
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        env = {**self.env, "PATH": f"{stub}:{os.environ['PATH']}", "FAKE_CI": str(self.root / "scenario.json"), "FAKE_CALLS": str(self.root / "bun-calls.log")}
        return subprocess.run([sys.executable, str(CLI), "--repo", str(self.repo), "--run-dir", str(self.run_dir), "--mirror", str(self.forge), "--until", "suite", *extra],
                              capture_output=True, text=True, env=env, timeout=180)

    SHARDS = [{"shard": 1, "files": ["test/a.test.ts"], "predictedMs": 1}, {"shard": 2, "files": ["test/tree.test.ts"], "predictedMs": 1}]

    def test_ci_verify_pass_clears_the_suite_and_every_bun_call_ignores_dotenv(self):
        r = self.run_suite({"shards": self.SHARDS, "tree_mutating": ["test/tree.test.ts"], "verify_exit": 0,
                            "results": {"1": self.outcome(1, ["test/a.test.ts"]), "2": self.outcome(2, ["test/tree.test.ts"])}})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("`--ci-verify` passed", (self.run_dir / "report.md").read_text())
        calls = (self.root / "bun-calls.log").read_text().splitlines()
        self.assertTrue(calls and all(c.startswith("--no-env-file ") for c in calls), calls)
        runs = [c for c in calls if "--ci-run" in c]
        self.assertEqual(runs[-1].split("--shard ")[1].split()[0], "2", "the tree-mutating shard runs last, alone")

    def test_a_shard_that_never_wrote_its_result_stops(self):
        r = self.run_suite({"shards": self.SHARDS, "tree_mutating": ["test/tree.test.ts"], "verify_exit": 1,
                            "results": {"1": self.outcome(1, ["test/a.test.ts"])}})
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("shard 2 has no result", (self.run_dir / "report.md").read_text())

    def test_a_shard_timed_out_only_on_the_merge_stops(self):
        r = self.run_suite({"shards": self.SHARDS, "tree_mutating": ["test/tree.test.ts"], "verify_exit": 1, "verify_exit_upstream": 0,
                            "results": {"1": self.outcome(1, ["test/a.test.ts"], "failed", ["test/a.test.ts"], failures=[("test/a.test.ts", "g > a")]),
                                        "2": self.outcome(2, ["test/tree.test.ts"], "timed-out")},
                            "results_upstream": {"1": self.outcome(1, ["test/a.test.ts"]), "2": self.outcome(2, ["test/tree.test.ts"])}})
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("merge shard 2 (1 files): timed out, while the same shard is complete on upstream", (self.run_dir / "report.md").read_text())

    def test_a_gap_on_both_the_merge_and_upstream_is_environmental(self):
        gap = dict(self.outcome(1, ["test/a.test.ts"], "failed"), summary={"testsRan": 1, "filesRan": 1, "sawTerminalSummary": True})
        gap["files"] = ["test/a.test.ts", "test/b.test.ts"]
        shards = [{"shard": 1, "files": ["test/a.test.ts", "test/b.test.ts"], "predictedMs": 1}, self.SHARDS[1]]
        r = self.run_suite({"shards": shards, "tree_mutating": ["test/tree.test.ts"], "verify_exit": 1,
                            "results": {"1": gap, "2": self.outcome(2, ["test/tree.test.ts"])}})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("same shard gave none on upstream either", (self.run_dir / "report.md").read_text())

    def test_a_focused_file_failing_only_on_the_merge_blocks_inside_an_environmental_gap(self):
        self.commit_fork({"lib-thing.ts": "export const thing = 2;\n"})
        (self.repo / "test" / "a.test.ts").write_text("import { thing } from '../lib-thing';\n")
        git(self.repo, "add", "test/a.test.ts", env=self.env)
        git(self.repo, "commit", "-q", "-m", "fork test names the fork file", env=self.env)
        git(self.repo, "push", "-q", "origin", "main", env=self.env)
        gap = dict(self.outcome(1, ["test/a.test.ts"], "failed"), summary={"testsRan": 1, "filesRan": 0, "sawTerminalSummary": False})
        r = self.run_suite({"shards": self.SHARDS, "tree_mutating": ["test/tree.test.ts"], "verify_exit": 1,
                            "results": {"1": gap, "2": self.outcome(2, ["test/tree.test.ts"])},
                            "standalone": {"merge": ["g > a"], "upstream": [], "baseline": []}})
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        report = (self.run_dir / "report.md").read_text()
        self.assertIn("fork-caused failure", report)
        self.assertIn("test/a.test.ts :: g > a", report)

    def test_failure_on_upstream_too_with_agent_host_containment_passes_and_is_reported(self):
        log = ("[test:free] shard 1/2 shard cleanup or home containment failed: real home changed while this shard ran (watches ~/.claude only): "
               "~/.claude/backups/.claude.json.backup.1. Give the writer a private HOME. Shard files: x; retained /tmp/s")
        r = self.run_suite({"shards": self.SHARDS, "tree_mutating": ["test/tree.test.ts"], "verify_exit": 1, "logs": {"1": log},
                            "results": {"1": self.outcome(1, ["test/a.test.ts"], "failed", ["test/a.test.ts"], 1, [("test/a.test.ts", "g > a")]),
                                        "2": self.outcome(2, ["test/tree.test.ts"])},
                            "standalone": {"merge": ["g > a"], "upstream": ["g > a"], "baseline": ["g > a"]}})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        report = (self.run_dir / "report.md").read_text()
        self.assertIn("| upstream | 1 | no |", report)
        self.assertIn("agent-host writes", report)

    def test_failure_only_on_the_merge_is_fork_caused_and_stops(self):
        r = self.run_suite({"shards": self.SHARDS, "tree_mutating": ["test/tree.test.ts"], "verify_exit": 1,
                            "results": {"1": self.outcome(1, ["test/a.test.ts"], "failed", ["test/a.test.ts"], failures=[("test/a.test.ts", "g > a")]),
                                        "2": self.outcome(2, ["test/tree.test.ts"])},
                            "standalone": {"merge": ["g > a"], "upstream": [], "baseline": []}})
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("fork-caused failure", (self.run_dir / "report.md").read_text())

    def test_containment_outside_agent_host_paths_stops_unless_accepted(self):
        log = ("[test:free] shard 1/2 shard cleanup or home containment failed: real home changed while this shard ran (watches ~/.claude only): "
               "~/.claude/settings.json. Give the writer a private HOME. Shard files: x; retained /tmp/s")
        scenario = {"shards": self.SHARDS, "tree_mutating": ["test/tree.test.ts"], "verify_exit": 1, "logs": {"1": log},
                    "results": {"1": self.outcome(1, ["test/a.test.ts"], "failed", [], 1), "2": self.outcome(2, ["test/tree.test.ts"])}}
        r = self.run_suite(scenario)
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn("~/.claude/settings.json", (self.run_dir / "report.md").read_text())
        r2 = subprocess.run([sys.executable, str(CLI), "--repo", str(self.repo), "--run-dir", str(self.run_dir), "--mirror", str(self.forge), "--until", "suite", "--accept-unaccounted"],
                            capture_output=True, text=True, env={**self.env, "PATH": f"{self.root / 'stub-bin'}:{os.environ['PATH']}", "FAKE_CI": str(self.root / "scenario.json"), "FAKE_CALLS": str(self.root / "bun-calls.log")}, timeout=180)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("passed on purpose with --accept-unaccounted", (self.run_dir / "report.md").read_text())

    def test_second_run_while_locked_exits_3(self):
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "lock").write_text(str(os.getpid()))
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        r = self.run_cli("--until", "merge")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
