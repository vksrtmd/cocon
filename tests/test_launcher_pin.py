"""Launcher: the repo ID is pinned on first launch; a changed origin refuses."""
from launcher_case import LauncherCase


class Pin(LauncherCase):
    def gh_repo(self, name, url="https://github.com/acme/widget.git"):
        r = self.new_repo(name)
        self.git(r, "remote", "add", "origin", url)
        return r

    def pins(self):
        f = self.tmp / "xdg" / "cocon" / "pins"
        return f.read_text() if f.is_file() else ""

    def test_first_launch_pins_repo_id_keyed_by_main_checkout(self):
        """first launch: pins the repo ID as '<main checkout path>TAB<repo id>'"""
        r = self.gh_repo("p1")
        self.assertLaunch(self.launch(r), 0)
        self.assertEqual(self.pins(), f"{r.resolve()}\tacme__widget\n")

    def test_changed_origin_refuses_before_broker_or_session(self):
        r = self.gh_repo("p2")
        self.launch(r)
        self.git(r, "remote", "set-url", "origin", "https://github.com/evil/other.git")
        res = self.launch(r)
        self.assertLaunch(res, 1, "acme__widget", "evil__other", "--repin")
        self.assertEqual(res.calls, [])
        self.assertNotIn("evil__other", self.pins())

    def test_non_github_host_gives_no_repo_id(self):
        r = self.gh_repo("p3")
        self.launch(r)
        for url in ("https://evil/github.com/acme/widget", "https://github.com.evil.io/acme/widget.git"):
            self.git(r, "remote", "set-url", "origin", url)
            res = self.launch(r)
            self.assertLaunch(res, 1, "acme__widget", "none")
            self.assertEqual(res.calls, [])

    def test_scp_and_ssh_urls_still_work(self):
        """git@github.com:o/r and ssh://git@github.com/o/r give the same repo ID"""
        r = self.gh_repo("p4", "git@github.com:acme/widget.git")
        self.launch(r)
        self.git(r, "remote", "set-url", "origin", "ssh://git@github.com/acme/widget")
        self.assertLaunch(self.launch(r), 0)

    def test_repin_shows_both_and_updates(self):
        r = self.gh_repo("p5")
        self.launch(r)
        self.git(r, "remote", "set-url", "origin", "https://github.com/acme/moved.git")
        res = self.launch(r, "--repin")
        self.assertLaunch(res, 0, "acme__widget", "acme__moved")
        self.assertEqual(res.calls, [])
        self.assertLaunch(self.launch(r), 0)

    def test_worktree_uses_main_checkouts_pin(self):
        r = self.gh_repo("p6")
        self.launch(r)
        self.git(r, "worktree", "add", "-q", str(r / ".claude" / "worktrees" / "w1"), "-b", "w1")
        self.git(r, "remote", "set-url", "origin", "https://github.com/evil/other.git")
        res = self.launch(r, "--worktree", "w1")
        self.assertLaunch(res, 1, "acme__widget", "evil__other")
        self.assertEqual(res.calls, [])

    def test_worktree_name_validation_fails_before_pin_check(self):
        r = self.gh_repo("p7")
        self.launch(r)
        self.git(r, "remote", "set-url", "origin", "https://github.com/evil/other.git")
        res = self.launch(r, "--worktree", "a/b")
        self.assertLaunch(res, 1, "bad --worktree name")
        self.assertNotIn("evil__other", res.text)

    def test_build_attach_proxy_log_need_no_token_so_pass_the_pin_check(self):
        r = self.gh_repo("p9")
        self.launch(r)
        self.git(r, "remote", "set-url", "origin", "https://github.com/evil/other.git")
        tailbin = self.tmp / "tailbin"  # --proxy-log execs tail -F, which would never end
        tailbin.mkdir()
        (tailbin / "tail").write_text('#!/bin/sh\necho "tail $*"\n')
        (tailbin / "tail").chmod(0o755)
        env = {"STUB_SESSIONS": "cocon-acme__p9", "PATH": f"{tailbin}:{self.env['PATH']}"}
        for flag in ("--build", "--attach", "--proxy-log"):
            with self.subTest(flag=flag):
                res = self.launch(r, flag, env=env)
                self.assertNotIn("--repin", res.text)
                self.assertNotIn("refusing to launch", res.text)
                self.assertNotIn("evil", "".join(c.line for c in res.calls))

    def test_down_and_destroy_still_work_on_the_pinned_repos_services(self):
        r = self.gh_repo("p8")
        self.launch(r)
        self.git(r, "remote", "set-url", "origin", "https://github.com/evil/other.git")
        for flag in ("--down", "--destroy"):
            res = self.launch(r, flag, answer="y")
            self.assertLaunch(res, 0)
            self.assertProject(res, "acme__widget")
            self.assertNoCall(res, "evil")
