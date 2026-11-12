"""Launcher: the git hooks guard and the host entry point report."""
import os
import shutil
import unittest

from launcher_case import LauncherCase


def ro(res_args):
    return [a for a in res_args if a.endswith(":ro")]


class Hooks(LauncherCase):
    def test_submodule_hooks_dir_read_only_too(self):
        r, src = self.new_repo("super"), self.new_repo("subsrc")
        self.git(r, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "sub")
        res = self.launch(r)
        self.assertMount(res, f"{r}/.git/modules/sub/hooks:{r}/.git/modules/sub/hooks:ro")

    def test_missing_hooks_dir_is_created_on_the_host(self):
        r = self.new_repo("nohooks")
        shutil.rmtree(r / ".git" / "hooks")
        self.launch(r)
        self.assertTrue((r / ".git" / "hooks").is_dir())

    def test_planted_git_config_entry_is_reported(self):
        r = self.new_repo("planted")
        res = self.launch(r, stub=f"git -C '{r}' config core.fsmonitor 'sh evil'", rc=3)
        self.assertLaunch(res, 3, "core.fsmonitor=sh evil", f"remove it: git -C / config --file {r}/.git/config")

    def test_unreadable_config_after_session_loud_warning_exit_code_kept(self):
        r = self.new_repo("broken")
        stub = f"git -C '{r}' config core.fsmonitor 'sh evil'; echo '[x' >> '{r}/.git/config'"
        self.assertLaunch(self.launch(r, stub=stub), 0, "git config check incomplete")

    def test_replaced_git_is_reported(self):
        r = self.new_repo("swapped")
        res = self.launch(r, stub=f"mv '{r}/.git' '{r}/.git.old' && git init -q '{r}'")
        self.assertIn("a hooks dir was replaced in the cocoon", res.text)
        self.assertIn(f"{r}/.git/hooks", res.text)

    def test_unreadable_git_before_session_refuses_to_start(self):
        r = self.new_repo("unreadable")
        with open(r / ".git" / "config", "a") as f:
            f.write("[x\n")
        res = self.launch(r)
        self.assertLaunch(res, 1, f"git can't read {r}/.git")
        self.assertIsNone(self.session(res), "session ran")

    def test_include_of_a_fifo_before_session_refuses_to_start(self):
        r = self.new_repo("fifo")
        os.mkfifo(r / ".git" / "pipe")
        self.git(r, "config", "include.path", "pipe")
        res = self.launch(r, env={"COCON_GIT_TIMEOUT": "1"})
        self.assertLaunch(res, 1, f"git didn't answer reading {r}/.git's config", f"git can't read {r}/.git")
        self.assertIsNone(self.session(res), "session ran")

    def test_include_of_a_fifo_planted_in_session_is_reported(self):
        r = self.new_repo("fifoafter")
        stub = f"mkfifo '{r}/.git/pipe' && git -C '{r}' config include.path pipe"
        res = self.launch(r, stub=stub, rc=3, env={"COCON_GIT_TIMEOUT": "1"})
        self.assertLaunch(res, 3, f"git didn't answer reading {r}/.git's config", "include.path=pipe",
                          f"remove it: git -C / config --file {r}/.git/config", "git config check incomplete")

    def test_in_tree_hooks_path_created_and_mounted_read_only(self):
        r = self.new_repo("husky")
        self.git(r, "config", "core.hooksPath", ".husky/_")
        res = self.launch(r)
        self.assertTrue((r / ".husky" / "_").is_dir())
        self.assertMount(res, f"{r}/.husky/_:{r}/.husky/_:ro")

    def test_global_hooks_path_honoured_normalised(self):
        r = self.new_repo("globalhooks")
        cfg = self.tmp / "gitconfig"
        cfg.write_text("[core]\n\thooksPath = ./.githooks/\n")
        res = self.launch(r, env={"GIT_CONFIG_GLOBAL": str(cfg)})
        self.assertMount(res, f"{r}/.githooks:{r}/.githooks:ro")

    def test_hooks_path_outside_checkout_itself_or_git_hooks_no_extra_mount(self):
        base = ro(self.session(self.launch(self.new_repo("hpbase"))))
        for i, hp in enumerate((str(self.tmp / "outside-hooks"), "../x", ".", ".git/hooks")):
            with self.subTest(hp=hp):
                r = self.new_repo(f"hp{i}")
                self.git(r, "config", "core.hooksPath", hp)
                res = self.launch(r)
                self.assertEqual(res.rc, 0, res.text)
                self.assertEqual(len(ro(self.session(res))), len(base), ro(self.session(res)))
        self.assertFalse((self.tmp / "outside-hooks").exists(), "outside hooks dir created")

    def test_symlinked_submodule_hooks_dir_refuses_the_launch(self):
        r = self.new_repo("hookslink")
        outside = self.tmp / "host-config"
        outside.mkdir()
        m = r / ".git" / "modules" / "x"
        (m / "objects").mkdir(parents=True)
        (m / "HEAD").write_text("ref: refs/heads/main\n")
        (m / "hooks").symlink_to(outside)
        res = self.launch(r)
        self.assertLaunch(res, 1, f"{m}/hooks is a symlink", "remove it")
        self.assertIsNone(self.session(res), "session ran")
        self.assertNotIn(str(outside), " ".join(self.lines(res)))

    def test_submodule_git_dir_with_a_colon_refuses_the_launch(self):
        r = self.new_repo("colon")
        m = r / ".git" / "modules" / "a:b"
        (m / "objects").mkdir(parents=True)
        (m / "HEAD").write_text("ref: refs/heads/main\n")
        res = self.launch(r)
        self.assertLaunch(res, 1, f"a colon in a checkout path isn't supported: {m}/hooks")
        self.assertIsNone(self.session(res), "session ran")

    def test_symlinked_component_in_in_tree_hooks_path_refuses_the_launch(self):
        r = self.new_repo("huskylink")
        outside = self.tmp / "husky-target"
        outside.mkdir()
        (r / ".husky").symlink_to(outside)
        self.git(r, "config", "core.hooksPath", ".husky/_")
        res = self.launch(r)
        self.assertLaunch(res, 1, f"{r}/.husky is a symlink", "remove it")
        self.assertIsNone(self.session(res), "session ran")
        self.assertFalse((outside / "_").exists(), "hooks dir created at the symlink's target")

    def test_hooks_path_with_dotdot_inside_checkout_resolved(self):
        r = self.new_repo("dotdot")
        self.git(r, "config", "core.hooksPath", ".husky/../.githooks")
        self.assertMount(self.launch(r), f"{r}/.githooks:{r}/.githooks:ro")

    def test_hooks_path_git_hooks_in_linked_worktree_no_extra_mount(self):
        r = self.new_repo("wthooks")
        w = r / ".claude" / "worktrees" / "w"
        self.git(r, "worktree", "add", "-q", str(w), "-b", "w")
        self.git(r, "config", "core.hooksPath", ".git/hooks")
        res = self.launch(w)
        self.assertEqual(res.rc, 0, res.text)
        self.assertNoMount(res, f"{w}/.git/hooks:{w}/.git/hooks:ro")

    def test_renamed_in_tree_hooks_dir_is_reported(self):
        r = self.new_repo("husky2")
        self.git(r, "config", "core.hooksPath", ".husky/_")
        res = self.launch(r, stub=f"mv '{r}/.husky' '{r}/.old' && mkdir -p '{r}/.husky/_'")
        self.assertIn("a hooks dir was replaced in the cocoon", res.text)
        self.assertIn(f"{r}/.husky/_", res.text)


class EntryPointsUnreadable(LauncherCase):
    def setUp(self):
        super().setUp()
        self.r = self.new_repo("unreadable-entry")
        (self.r / ".claude").mkdir()
        self.x = self.r / ".claude" / "x"
        self.x.write_text("a")
        self.addCleanup(os.chmod, self.x, 0o644)

    @unittest.skipIf(os.geteuid() == 0, "root reads mode 000 files")
    def test_unreadable_host_entry_point_before_session_refuses_to_start(self):
        os.chmod(self.x, 0)
        res = self.launch(self.r)
        self.assertLaunch(res, 1, f"can't read every host entry point in {self.r}")
        self.assertIsNone(self.session(res), "session ran")

    def test_host_entry_point_unreadable_after_session_loud_warning(self):
        res = self.launch(self.r, stub=f"chmod 000 '{self.x}'", rc=6)
        self.assertLaunch(res, 6, "host entry point check incomplete")


class EntryPoints(LauncherCase):
    def test_changed_entry_points_reported_exit_code_kept(self):
        r = self.new_repo("entry")
        (r / ".husky").mkdir()
        (r / ".claude" / "worktrees" / "x").mkdir(parents=True)
        stub = f"echo x >'{r}/.husky/pre-push'; echo x >'{r}/.claude/worktrees/x/f'; echo x >'{r}/src'"
        res = self.launch(r, stub=stub, rc=4)
        self.assertEqual(res.rc, 4)
        self.assertEqual(res.text.rstrip("\n"),
                         "cocon: the session changed host entry points; review them before your next host git or claude:\n"
                         f"{r}/.husky/pre-push")

    def test_non_git_dir_and_shell_claude_changes_still_reported(self):
        d = self.tmp / "nogit-entry"
        d.mkdir()
        res = self.launch(d, "--shell", stub=f"echo x >'{d}/.mcp.json'")
        self.assertIn("changed host entry points", res.text)
        self.assertIn(f"{d}/.mcp.json", res.text)
        res = self.launch(d, stub=f"mkdir '{d}/.claude'; echo x >'{d}/.claude/settings.local.json'")
        self.assertIn(f"{d}/.claude/settings.local.json", res.text)


if __name__ == "__main__":
    unittest.main()
