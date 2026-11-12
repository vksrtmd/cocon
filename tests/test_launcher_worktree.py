"""Launcher: --worktree."""
import os
import stat
import unittest

from launcher_case import COMPOSE, LauncherCase


class Worktree(LauncherCase):
    def setUp(self):
        super().setUp()
        # A repo with a local bare repo as origin: ls-remote, fetch and origin/HEAD without network.
        self.r = r = self.new_repo("wt")
        origin = self.tmp / "wt-origin.git"
        self.git(self.tmp, "clone", "-q", "--bare", str(r), str(origin))
        self.git(r, "remote", "add", "origin", str(origin))
        self.git(r, "fetch", "-q", "origin")
        self.git(r, "remote", "set-head", "-a", "origin")
        self.base = self.rev(r, "origin/HEAD")
        self.git(r, "commit", "-q", "--allow-empty", "-m", "local-only")
        self.wt = r / ".claude" / "worktrees" / "45-auth"

    def rev(self, repo, ref):
        return self.git(repo, "rev-parse", ref, out=True).strip()

    def exclude_count(self):
        return (self.r / ".git" / "info" / "exclude").read_text().splitlines().count(".claude/worktrees/")

    def test_new_name_branches_from_origin_default_and_launches_there(self):
        res = self.launch(self.r, "--worktree", "45-auth")
        self.assertEqual(res.rc, 0, res.text)
        self.assertEqual(self.rev(self.wt, "45-auth"), self.base)
        self.assertProject(res, "wt--45-auth")
        self.assertMount(res, f"{self.wt}:{self.wt}")
        self.assertMount(res, f"{self.r}/.git:{self.r}/.git")
        self.assertEqual(self.exclude_count(), 1)

    def test_docker_sidecar_gets_the_worktree_and_the_main_repos_git(self):
        res = self.launch(self.r, "--worktree", "45-auth", "--with", "docker")
        self.assertEqual(res.rc, 0, res.text)
        g = self.r / ".git"
        self.assertCountEqual(self.sidecar_mounts(res), [f"{self.wt}:{self.wt}", f"{g}:{g}", f"{g}/hooks:{g}/hooks:ro"])

    def test_symlinked_info_exclude_left_untouched(self):
        """--worktree: a symlinked info/exclude is not appended to (its target could be any host file)"""
        target = self.tmp / "zshrc"
        target.write_text("x\n")
        ex = self.r / ".git" / "info" / "exclude"
        ex.unlink(missing_ok=True)
        ex.symlink_to(target)
        res = self.launch(self.r, "--worktree", "45-auth")
        self.assertEqual(res.rc, 0, res.text)
        self.assertEqual(target.read_text(), "x\n")

    def test_fifo_info_exclude_left_alone_without_hanging(self):
        """--worktree on an existing worktree: a fifo the session planted as info/exclude neither hangs the launch nor
        gets written. (Creating one runs host git, which reads info/exclude itself and blocks on it.)"""
        self.assertEqual(self.launch(self.r, "--worktree", "45-auth").rc, 0)
        ex = self.r / ".git" / "info" / "exclude"
        ex.unlink(missing_ok=True)
        os.mkfifo(ex)
        res = self.launch(self.r, "--worktree", "45-auth")
        self.assertEqual(res.rc, 0, res.text)
        self.assertTrue(stat.S_ISFIFO(os.lstat(ex).st_mode))

    def test_existing_worktree_reused_from_inside_a_worktree_too(self):
        self.launch(self.r, "--worktree", "45-auth")
        self.git(self.wt, "commit", "-q", "--allow-empty", "-m", "wip")
        res = self.launch("--worktree", "45-auth", cwd=self.wt)
        self.assertEqual(res.rc, 0, res.text)
        self.assertProject(res, "wt--45-auth")
        self.assertEqual(self.exclude_count(), 1)

    def test_existing_local_branch_is_checked_out(self):
        self.git(self.r, "branch", "picked-up")
        res = self.launch(self.r, "--worktree", "picked-up")
        self.assertEqual(res.rc, 0, res.text)
        out = self.git(self.r / ".claude/worktrees/picked-up", "branch", "--show-current", out=True)
        self.assertEqual(out.strip(), "picked-up")

    def test_name_only_on_origin_refused(self):
        self.git(self.r, "push", "-q", "origin", "HEAD:refs/heads/theirs")
        res = self.launch(self.r, "--worktree", "theirs")
        self.assertLaunch(res, 1, f"git -C '{self.r}' fetch origin theirs:theirs")
        self.assertFalse((self.r / ".claude/worktrees/theirs").exists(), "worktree created")

    def plant_uploadpack(self):
        marker = self.tmp / "marker"
        self.git(self.r, "config", "remote.origin.uploadpack", f"touch {marker}; git-upload-pack")
        return marker

    def test_untrusted_risky_config_refuses_worktree_creation_before_any_git(self):
        self.launch(self.r)  # baseline of the clean config
        marker = self.plant_uploadpack()
        res = self.launch(self.r, "--worktree", "new")
        self.assertLaunch(res, 1, "remote.origin.uploadpack", "--trust-git")
        self.assertFalse(marker.exists(), "planted command ran")
        self.assertFalse((self.r / ".claude/worktrees/new").exists(), "worktree created")
        self.assertNoCall(res, "compose")

    def test_bad_name_is_reported_before_the_risky_config_refusal(self):
        self.launch(self.r)
        self.plant_uploadpack()
        self.assertLaunch(self.launch(self.r, "--worktree", "a/b"), 1, "bad --worktree name: a/b")

    def test_existing_worktree_launches_without_the_creation_check(self):
        self.launch(self.r, "--worktree", "45-auth")
        self.git(self.r, "config", "remote.origin.uploadpack", "true")
        self.assertNotIn("refusing", self.launch(self.r, "--worktree", "45-auth").text)

    def test_planted_commondir_refuses_the_launch(self):
        self.launch(self.r)
        fake = self.new_repo("fake")
        self.git(fake, "remote", "add", "origin", "https://github.com/evil/other.git")
        (self.r / ".git/commondir").write_text(f"{fake}/.git\n")
        res = self.launch(self.r)
        self.assertEqual(res.rc, 1, res.text)
        self.assertEqual(res.calls, [])
        self.assertNotIn("evil__other", (self.tmp / "xdg/cocon/pins").read_text())

    def test_worktree_file_pointing_at_a_fake_repo_refuses_the_launch(self):
        self.launch(self.r, "--worktree", "45-auth")
        fake = self.new_repo("fake")
        adm = fake / ".git/worktrees/x"
        adm.mkdir(parents=True)
        (adm / "gitdir").write_text(f"{self.wt}/.git\n")
        (adm / "commondir").write_text("../..\n")
        (adm / "HEAD").write_text("ref: refs/heads/master\n")
        (self.wt / ".git").write_text(f"gitdir: {adm}\n")
        res = self.launch("--worktree", "45-auth", cwd=self.wt)
        self.assertEqual(res.rc, 1, res.text)
        self.assertEqual(res.calls, [])

    def test_destroy_runs_no_planted_fsmonitor(self):
        self.launch(self.r, "--worktree", "45-auth")
        marker = self.tmp / "fsmonitor-ran"
        hook = self.tmp / "fsm"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        self.git(self.r, "config", "core.fsmonitor", str(hook))
        # Trusted, so host-guard lets git run: only the command-line override keeps the hook from running.
        self.assertEqual(self.launch(self.r, "--trust-git", answer="y").rc, 0)
        res = self.launch(self.r, "--worktree", "45-auth", "--destroy", answer="y")
        self.assertEqual(res.rc, 0, res.text)
        self.assertFalse(marker.exists(), "fsmonitor ran")

    def test_destroy_on_dirty_worktree_git_refuses_worktree_kept(self):
        self.launch(self.r, "--worktree", "45-auth")
        (self.wt / "untracked").touch()
        res = self.launch(self.r, "--worktree", "45-auth", "--destroy", answer="y")
        self.assertLaunch(res, 128, f"worktree {self.wt}", "untracked")
        self.assertTrue(self.wt.is_dir(), "dirty worktree removed")

    def test_destroy_lists_ignored_files_removes_worktree_keeps_branch(self):
        self.launch(self.r, "--worktree", "45-auth")
        with open(self.r / ".git" / "info" / "exclude", "a") as f:
            f.write(".env\n")
        (self.wt / ".env").touch()
        res = self.launch(self.r, "--worktree", "45-auth", "--destroy", answer="y")
        self.assertLaunch(res, 0, "ignored files", "  .env")
        self.assertCall(res, f"compose -f {COMPOSE} -p cocon-wt--45-auth --profile * down")
        self.assertFalse(self.wt.exists(), "worktree not removed")
        self.git(self.r, "show-ref", "-q", "--verify", "refs/heads/45-auth")

    def test_down_on_missing_worktree_is_an_error_nothing_created(self):
        res = self.launch(self.r, "--worktree", "45-auth", "--down")
        self.assertLaunch(res, 1, f"no worktree {self.wt}")
        self.assertFalse(self.wt.exists(), "worktree created")

    def test_leftover_folder_that_is_not_a_worktree_refused(self):
        (self.r / ".claude/worktrees/leftover").mkdir(parents=True)
        res = self.launch(self.r, "--worktree", "leftover", "--destroy", answer="y")
        self.assertLaunch(res, 1, f"{self.r}/.claude/worktrees/leftover isn't a git worktree")
        self.assertNoCall(res, " down")

    def test_bad_names_refused(self):
        for bad in ("../x", "feat/x"):
            with self.subTest(bad=bad):
                self.assertLaunch(self.launch(self.r, "--worktree", bad), 1, f"bad --worktree name: {bad}")

    def test_worktree_git_cannot_read_refuses_before_touching_services(self):
        broken = self.tmp / "broken-wt"
        self.git(self.r, "worktree", "add", "-q", str(broken), "-b", "broken")
        (broken / ".git").write_text(f"gitdir: {self.tmp}/nowhere\n")
        res = self.launch(broken, "--destroy", answer="y")
        self.assertLaunch(res, 1, f"git can't read {broken}/.git")
        self.assertNoCall(res, " down")


if __name__ == "__main__":
    unittest.main()
