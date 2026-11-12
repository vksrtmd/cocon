"""Launcher: the trusted git config baseline in host config (--trust-git), not a per-launch snapshot."""
import unittest

from launcher_case import LauncherCase

PLANT = "core.fsmonitor 'sh evil'"


class Baseline(LauncherCase):
    def plant(self, r):
        return f"git -C '{r}' config {PLANT}"

    def test_clean_sessions_report_nothing_and_baseline_is_under_host_config(self):
        r = self.new_repo("clean")
        res = self.launch(r)
        self.assertEqual(res.err.count("host-guard"), 0, res.text)
        self.assertNotIn("cocon: git config", res.text)
        files = list((self.tmp / "xdg" / "cocon" / "git-baseline").glob("*"))
        self.assertEqual(len(files), 1, files)

    def test_entry_planted_in_session_1_is_reported_after_session_2_too(self):
        r = self.new_repo("planted")
        self.launch(r)  # first launch: baseline of the clean config
        res = self.launch(r, stub=self.plant(r))
        self.assertIn("core.fsmonitor=sh evil", res.text)
        res = self.launch(r)  # the next session plants nothing
        self.assertIn("core.fsmonitor=sh evil", res.text)

    def test_trust_lists_entries_then_silences_them(self):
        r = self.new_repo("trust")
        self.launch(r)
        self.git(r, "config", "core.fsmonitor", "sh evil")
        res = self.launch(r, "--trust-git", answer="y")
        self.assertLaunch(res, 0, "core.fsmonitor=sh evil")
        self.assertNotIn("core.fsmonitor=sh evil", self.launch(r).text)

    def test_trust_declined_changes_nothing(self):
        r = self.new_repo("declined")
        self.launch(r)
        self.git(r, "config", "core.fsmonitor", "sh evil")
        self.assertEqual(self.launch(r, "--trust-git", answer="n").rc, 1)
        self.assertIn("core.fsmonitor=sh evil", self.launch(r).text)

    def test_removing_the_entry_silences_it(self):
        r = self.new_repo("removed")
        self.launch(r)
        self.launch(r, stub=self.plant(r))
        self.git(r, "config", "--unset", "core.fsmonitor")
        self.assertNotIn("core.fsmonitor", self.launch(r).text)

    def test_repin_keeps_the_baseline(self):
        r = self.new_repo("repin")
        self.git(r, "remote", "add", "origin", "https://github.com/acme/widget.git")
        self.launch(r)
        self.git(r, "config", "core.fsmonitor", "sh evil")
        self.git(r, "remote", "set-url", "origin", "https://github.com/acme/moved.git")
        self.launch(r, "--repin")
        self.assertIn("core.fsmonitor=sh evil", self.launch(r).text)

    def test_same_named_repos_in_different_folders_have_their_own_baselines(self):
        for d in ("one", "two"):
            (self.tmp / d).mkdir()
            self.git(self.tmp / d, "init", "-q", "app")
            self.git(self.tmp / d / "app", "commit", "-q", "--allow-empty", "-m", "x")
            self.launch(self.tmp / d / "app")
        files = list((self.tmp / "xdg" / "cocon" / "git-baseline").glob("*"))
        self.assertEqual(len(files), 2, files)

    def test_files_check_warning_comes_before_git_config_warning(self):
        r = self.new_repo("order")
        (r / ".husky").mkdir()
        self.launch(r)
        res = self.launch(r, stub=f"echo x >'{r}/.husky/pre-push'; {self.plant(r)}")
        self.assertLess(res.text.index("changed host entry points"), res.text.index("core.fsmonitor=sh evil"))


if __name__ == "__main__":
    unittest.main()
