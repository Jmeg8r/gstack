# WHAT: Tests for fork-sync: pure logic in fork_sync_lib, plus integration runs of the
#       fork-sync CLI against throwaway git repos.
# WHY:  The script's job is to stop before anything reaches the forge when a sync has a
#       problem, so the stop paths are what the integration tests exercise.
# Run:  python3 -m unittest discover -s .forgejo/scripts -p 'test_*.py' -v

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fork_sync_lib as lib  # noqa: E402

CLI = HERE / "fork-sync"


class ParseFreeLog(unittest.TestCase):
    def test_collects_failing_files_from_both_line_forms(self):
        log = "\n".join([
            "[test:free] shard 2/6",
            "  ✗ test/a.test.ts — suite > case one",
            "  ✗ test/a.test.ts — suite > case two",
            "  ✗ browse/test/b.test.ts (crashed: exited)",
            "[test:free] FAIL — 3 failing test(s) in 2 file(s), 0 crashed worker(s).",
        ])
        self.assertEqual(lib.parse_free_log(log), ({
            "test/a.test.ts": {"test/a.test.ts :: suite > case one", "test/a.test.ts :: suite > case two"},
            "browse/test/b.test.ts": {"browse/test/b.test.ts :: <crashed>"},
        }, []))

    def test_clean_run_has_no_failing_files(self):
        self.assertEqual(lib.parse_free_log("[test:free] PASS — 36 tests, 1 files, 0s."), ({}, []))

    def test_home_containment_failures_are_returned_even_beside_named_failures(self):
        log = ("  ✗ test/a.test.ts — g > a\n"
               "[test:free] shard 2/6 shard cleanup or home containment failed: real home changed: ~/.codex/x; retained /tmp/s\n")
        failures, diagnostics = lib.parse_free_log(log)
        self.assertEqual(set(failures), {"test/a.test.ts"})
        self.assertEqual(len(diagnostics), 1)
        self.assertIn("home containment failed", diagnostics[0])

    def test_concurrent_phase_home_changes_are_diagnostics_too(self):
        log = ("  ✗ test/a.test.ts — g > a\n"
               "[test:free] real home changed while 6 shards ran concurrently (watches ~/.codex): ~/.codex/x. They share one HOME\n")
        self.assertEqual(len(lib.parse_free_log(log)[1]), 1)

    def test_runner_incompleteness_markers_fail_closed_even_beside_named_failures(self):
        named = "  ✗ test/a.test.ts — suite > case\n"
        for marker in ["  ⚠ 2 failure(s) reported without named result lines",
                       "  ⚠ unhandled error between tests (around test/b.test.ts)",
                       "  ⚠ test process ended before its summary; in flight: test/c.test.ts",
                       "  ⏱ in flight at kill: test/c.test.ts",
                       "[test:free] shard 3/6 exceeded the 845s wall-clock deadline — killed",
                       "[test:free] shard 1/6 exited 0 but bun's summary reported 11 file(s), expected 12. Treating as FAILED."]:
            with self.assertRaises(lib.SyncError, msg=marker):
                lib.parse_free_log(named + marker)

    def test_a_crash_that_passed_on_retry_is_not_incomplete(self):
        self.assertEqual(lib.parse_free_log("  ⚠ crashed+retried: test/d.test.ts\n[test:free] PASS — 3 tests, 1 files, 0s."), ({}, []))

    def test_reported_failures_without_attributable_lines_fail_closed(self):
        with self.assertRaises(lib.SyncError):
            lib.parse_free_log("[test:free] FAIL — 4 failing test(s) in 2 file(s), 0 crashed worker(s).")


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


class BlockingDirty(unittest.TestCase):
    def test_tool_state_dot_entries_are_ignored_and_everything_else_blocks(self):
        lines = ["?? .serena/", "?? .DS_Store", " M lib/x.ts", "?? lib/new.ts", "?? .forgejo/scripts/new", ""]
        self.assertEqual(lib.blocking_dirty(lines), [" M lib/x.ts", "?? lib/new.ts", "?? .forgejo/scripts/new"])

    def test_modified_tracked_dot_file_still_blocks(self):
        self.assertEqual(lib.blocking_dirty([" M .gitignore"]), [" M .gitignore"])


class SuiteCompleted(unittest.TestCase):
    EPILOGUE = "  ✗ test/a.test.ts — g > a\n[test:free] FAIL — 1 failing test(s) in 1 file(s), 0 crashed worker(s). Full log: x\n"

    def test_normal_failing_exit_with_the_epilogue_is_complete(self):
        lib.suite_completed(1, self.EPILOGUE)

    def test_killed_runner_is_incomplete_even_with_a_named_failure(self):
        with self.assertRaises(lib.SyncError):
            lib.suite_completed(-9, self.EPILOGUE)

    def test_missing_epilogue_is_incomplete(self):
        with self.assertRaises(lib.SyncError):
            lib.suite_completed(1, "  ✗ test/a.test.ts — g > a\n")


class DotenvFiles(unittest.TestCase):
    def test_finds_every_dotenv_variant_bun_would_load(self):
        names = [".env", ".env.local", ".env.test", ".envrc-not", "env", ".gitignore", "README.md"]
        self.assertEqual(lib.dotenv_files(names), [".env", ".env.local", ".env.test"])


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
        git(seed, "add", "VERSION", "shared.txt", "fork.txt", env=self.env)
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

    def test_suite_with_a_killed_shard_stops_even_though_another_shard_named_a_failure(self):
        """End to end through `checks` and `suite`, with a stub bun on PATH: one shard
        names a failure while another was killed at its deadline."""
        stub = self.root / "stub-bin"
        stub.mkdir()
        (stub / "bun").write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> "{self.root}/bun-calls.log"\n'
            'case "$*" in\n'
            '  "--no-env-file run test --verbose") printf \'  ✗ test/a.test.ts — g > a\\n  ⏱ in flight at kill: test/b.test.ts\\n\'; exit 1;;\n'
            '  "--no-env-file run typecheck:test") echo "test typecheck ratchet: 0 known diagnostics, none new."; exit 0;;\n'
            "  *) exit 0;;\n"
            "esac\n")
        (stub / "bun").chmod(0o755)
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        env = {**self.env, "PATH": f"{stub}:{os.environ['PATH']}"}
        r = subprocess.run([sys.executable, str(CLI), "--repo", str(self.repo), "--run-dir", str(self.run_dir), "--mirror", str(self.forge), "--until", "suite"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        report = (self.run_dir / "report.md").read_text()
        self.assertIn("Stopped at stage `suite`", report)
        self.assertIn("in flight at kill", report)
        calls = (self.root / "bun-calls.log").read_text().splitlines()
        self.assertGreaterEqual(len(calls), 6)
        self.assertTrue(all(c.startswith("--no-env-file ") for c in calls), calls)

    def test_dotenv_file_in_the_merge_worktree_stops_before_checks(self):
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        self.assertEqual(self.run_cli("--until", "fork-delta").returncode, 0)
        (self.run_dir / "merge" / ".env").write_text("SECRET=1\n")
        r = self.run_cli("--until", "checks")
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        self.assertIn(".env", (self.run_dir / "report.md").read_text())

    def test_second_run_while_locked_exits_3(self):
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "lock").write_text(str(os.getpid()))
        self.commit_upstream({"other.txt": "new\n"}, "1.1.0.0")
        r = self.run_cli("--until", "merge")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
