"""The launcher reads its credential files as KEY=VALUE lines and never runs them: a token file that ran would run
whoever wrote it as the user. One that others can read or write is refused first."""
from launcher_case import LauncherCase


class CredentialFiles(LauncherCase):
    def setUp(self):
        super().setUp()
        self.cfg = self.tmp / "xdg" / "cocon"
        self.cfg.mkdir(parents=True)
        self.marker = self.tmp / "ran"
        del self.env["CLAUDE_CODE_OAUTH_TOKEN"]

    def claude_env(self, text, mode=0o600):
        f = self.cfg / "claude.env"
        f.write_text(text)
        f.chmod(mode)
        return f

    def broker_env(self, res):
        (run,) = [c for c in res.calls if "compose" in c.args and "run" in c.args and "broker" in c.args]
        return run.env

    def test_export_quotes_and_comments_are_read_as_the_shell_would(self):
        self.claude_env("# from claude setup-token\n\nexport CLAUDE_CODE_OAUTH_TOKEN='sk-ant oat'  # mine\n")
        res = self.launch(self.new_repo("q"))
        self.assertEqual(res.rc, 0, res.text)
        self.assertEqual(self.broker_env(res)["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant oat")

    def test_a_line_that_would_run_a_command_refuses_and_never_runs(self):
        for text in (f"touch '{self.marker}'\nCLAUDE_CODE_OAUTH_TOKEN=x\n",
                     f"CLAUDE_CODE_OAUTH_TOKEN=$(touch {self.marker})\n",
                     "CLAUDE_CODE_OAUTH_TOKEN=x y\n", "CLAUDE_CODE_OAUTH_TOKEN='x\n"):
            with self.subTest(text=text):
                f = self.claude_env(text)
                res = self.launch(self.new_repo("cmd"))
                self.assertLaunch(res, 1, f"{f}: line ", "isn't KEY=VALUE")
                self.assertFalse(self.marker.exists(), "the file ran")
                self.assertNoCall(res, " run ")

    def test_a_quoted_substitution_is_a_literal_value(self):
        self.claude_env(f"CLAUDE_CODE_OAUTH_TOKEN='$(touch {self.marker})'\n")
        res = self.launch(self.new_repo("lit"))
        self.assertEqual(res.rc, 0, res.text)
        self.assertEqual(self.broker_env(res)["CLAUDE_CODE_OAUTH_TOKEN"], f"$(touch {self.marker})")
        self.assertFalse(self.marker.exists())

    def test_doctor_fails_the_file_the_launch_refuses(self):
        f = self.claude_env("CLAUDE_CODE_OAUTH_TOKEN=x\nnot KEY=VALUE\n")
        res = self.launch(self.new_repo("doc"), "--doctor")
        self.assertLaunch(res, 1, f"FAIL  claude.env: {f}: line 2 isn't KEY=VALUE")
        self.assertNotIn("ok    claude.env", res.out)

    def test_loose_mode_refused_before_the_file_is_read(self):
        f = self.claude_env("not even KEY=VALUE\n", mode=0o644)
        self.assertLaunch(self.launch(self.new_repo("mode")), 1, f"{f} has mode 644; run: chmod 600 '{f}'")


if __name__ == "__main__":
    import unittest
    unittest.main()
