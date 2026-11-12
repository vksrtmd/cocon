"""Launcher and --doctor: the repo's GitHub token is fine-grained and reaches no other private repo (ADR 0007)."""
import unittest

from launcher_case import LauncherCase

FINE = "github_pat_11AAREAL"


class Token(LauncherCase):
    def setUp(self):
        super().setUp()
        self.cfg = self.tmp / "xdg" / "cocon"
        self.repo = self.new_repo("tok")
        self.git(self.repo, "remote", "add", "origin", "https://github.com/acme/tok.git")

    def token(self, value):
        f = self.cfg / "tokens" / "acme__tok.env"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"GH_TOKEN={value}\n")
        f.chmod(0o600)
        return f

    def gh_calls(self, r):
        return [c for c in r.calls if c.cmd == "gh"]

    def brokers(self, r):
        return [c for c in r.calls if c.cmd == "docker" and "broker" in c.args]

    def test_classic_token_refused_naming_the_readme_no_broker_no_gh(self):
        tok = self.token("ghp_classicREAL")
        r = self.launch(self.repo)
        self.assertLaunch(r, 1, str(tok), "classic", "README > GitHub access")
        self.assertEqual(self.brokers(r), [])
        self.assertIsNone(self.session(r), "session ran")
        self.assertEqual(self.gh_calls(r), [])
        self.assertNotIn("ghp_classicREAL", r.text)

    def assertAskedGitHub(self, r):
        """One gh call, on the host: the private repos the token sees, with that token, on github.com, outside the checkout."""
        (gh,) = self.gh_calls(r)
        self.assertEqual(gh.args[:2], ["api", "user/repos?per_page=100&visibility=private"])
        self.assertEqual(gh.env["GH_TOKEN"], FINE)
        self.assertEqual(gh.env["GH_HOST"], "github.com")
        self.assertFalse(gh.env["PWD"].startswith(str(self.repo)), gh.env["PWD"])

    def test_token_reaching_another_private_repo_warns_and_launches(self):
        self.token(FINE)
        r = self.launch(self.repo, env={"STUB_GH_REPOS": "acme/tok acme/other:private"})
        self.assertLaunch(r, 0, "reaches more than acme/tok: acme/other")
        self.assertAskedGitHub(r)
        self.assertIsNotNone(self.session(r))

    def test_token_reaching_only_its_repo_launches_without_a_warning(self):
        self.token(FINE)
        r = self.launch(self.repo, env={"STUB_GH_REPOS": "Acme/Tok:private"})
        self.assertLaunch(r, 0)
        self.assertNotIn("token", r.err)
        self.assertAskedGitHub(r)

    def test_public_repo_the_user_collaborates_on_launches_without_a_warning(self):
        """launch: GitHub lists public repos the user collaborates on for any token; they don't count as reach"""
        self.token(FINE)
        r = self.launch(self.repo, env={"STUB_GH_REPOS": "acme/tok:private jacob-tucker/love-spreads"})
        self.assertLaunch(r, 0)
        self.assertNotIn("token", r.err)
        self.assertAskedGitHub(r)

    def test_failing_gh_warns_and_launches(self):
        self.token(FINE)
        r = self.launch(self.repo, env={"STUB_GH_API_FAIL": "1"})
        self.assertLaunch(r, 0, "couldn't check which repos", "token expired or revoked")
        self.assertAskedGitHub(r)
        self.assertIsNotNone(self.session(r))

    def doctor(self, **env):
        return self.launch(self.repo, "--doctor", env=env)

    def test_doctor_reports_a_fine_grained_token_reaching_no_other_private_repo(self):
        self.token(FINE)
        r = self.doctor(STUB_GH_REPOS="acme/tok:private acme/public")
        self.assertIn("ok    token: fine-grained, reaches no private repo besides acme/tok\n", r.out)
        self.assertAskedGitHub(r)

    def test_doctor_fails_a_classic_token_without_asking_github(self):
        tok = self.token("ghp_classicREAL")
        r = self.doctor()
        self.assertLaunch(r, 1, f"FAIL  token: {tok} holds a classic token (ghp_)", "README > GitHub access")
        self.assertEqual(self.gh_calls(r), [])

    def test_doctor_doesnt_run_a_token_file_with_a_loose_mode(self):
        marker = self.tmp / "ran"
        f = self.token(FINE)
        f.write_text(f"touch '{marker}'\nGH_TOKEN={FINE}\n")
        f.chmod(0o666)
        r = self.doctor()
        self.assertLaunch(r, 1, f"FAIL  token: {f} has mode 666")
        self.assertFalse(marker.exists(), "the token file ran")
        self.assertEqual(self.gh_calls(r), [])

    def test_doctor_with_origin_rewritten_away_from_the_pin_fails_and_checks_the_pinned_token(self):
        self.token(FINE)
        self.assertLaunch(self.launch(self.repo), 0)  # pins acme__tok
        self.git(self.repo, "remote", "set-url", "origin", "https://github.com/evil/other.git")
        other = self.cfg / "tokens" / "evil__other.env"
        other.write_text("GH_TOKEN=github_pat_OTHER\n")
        other.chmod(0o600)
        r = self.doctor()
        self.assertLaunch(r, 1, "FAIL  pin: origin maps to repo ID evil__other, but it is pinned to acme__tok", "--repin")
        self.assertEqual([c.env["GH_TOKEN"] for c in self.gh_calls(r)], [FINE])
        self.assertNotIn("github_pat_OTHER", r.text)

    def test_doctor_warns_on_other_private_repos_and_when_github_doesnt_answer(self):
        self.token(FINE)
        cases = {
            "acme/x:private acme/tok acme/y:private acme/z:private acme/w:private":
                "WARN  token: fine-grained, but reaches more than acme/tok: acme/x, acme/y, acme/z and 1 more",
            "acme/x:private acme/pub": "WARN  token: fine-grained, but reaches more than acme/tok: acme/x;",
            None: "WARN  token: fine-grained; couldn't check which repos it reaches",
        }
        for repos, line in cases.items():
            with self.subTest(repos=repos):
                env = {"STUB_GH_API_FAIL": "1"} if repos is None else {"STUB_GH_REPOS": repos}
                r = self.doctor(**env)
                self.assertIn(line, r.out)
                self.assertNotIn("FAIL  token", r.out)
                self.assertAskedGitHub(r)

    def test_every_subcommand_with_origin_rewritten_uses_the_pin_not_origin(self):
        """origin rewritten away from the pin: no subcommand reads the repo ID from origin (ADR 0015); it runs on the pin or refuses"""
        self.token(FINE)
        self.assertLaunch(self.launch(self.repo), 0)  # pins acme__tok
        self.git(self.repo, "remote", "set-url", "origin", "https://github.com/evil/other.git")
        other = self.cfg / "tokens" / "evil__other.env"
        other.write_text("GH_TOKEN=github_pat_OTHER\n")
        other.chmod(0o600)
        tailbin = self.tmp / "tailbin"  # --proxy-log execs tail -F, which would never end
        tailbin.mkdir()
        (tailbin / "tail").write_text('#!/bin/sh\necho "tail $*"\n')
        (tailbin / "tail").chmod(0o755)
        for flag in ("--doctor", "--down", "--destroy", "--reload-proxy", "--proxy-log", "--rotate-logs", "--attach", "--build", "--ls"):
            with self.subTest(flag=flag):
                r = self.launch(self.repo, flag, answer="y", env={"PATH": f"{tailbin}:{self.env['PATH']}"})
                self.assertEqual([c.line for c in r.calls if "evil" in c.line], [])
                self.assertEqual([c.env["GH_TOKEN"] for c in self.gh_calls(r)], [FINE] if flag == "--doctor" else [])
                self.assertNotIn("github_pat_OTHER", r.text)


if __name__ == "__main__":
    unittest.main()
