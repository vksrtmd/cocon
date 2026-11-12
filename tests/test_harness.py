"""The harness and the stub docker, through the launcher's and the stub's process boundary."""
import unittest
from datetime import datetime, timezone

from harness import KIT, KitCase, daemon_blind_message, stale_in


class StubDocker(KitCase):
    def test_records_args_and_launcher_env(self):
        repo = self.new_repo("web")
        r = self.run_kit("cocon", repo, "--with", "python,go",
                        env={"STUB_NOIMG": "cocon:go-python"})
        self.assertEqual(r.rc, 0, r.out + r.err)
        build = self.calls_matching("compose", "build", "dev")
        self.assertEqual(len(build), 1)
        self.assertEqual(build[0].env["COCON_TAG"], "go-python")
        self.assertEqual(build[0].withs, ["go", "python"])

    def test_session_run_is_recorded(self):
        r = self.run_kit("cocon", self.new_repo("web"))
        self.assertEqual(r.rc, 0, r.out + r.err)
        run = self.calls_matching("compose", "run", "dev")
        self.assertEqual(len(run), 1)
        self.assertTrue(run[0].env["REPO_VOLUMES"].startswith("cocon-"))

    def test_compose_run_must_name_a_service_of_the_compose_file(self):
        compose = str(KIT / "compose.yml")
        env = {"LOG_NAME": "x.log", "LOG_DIR": str(self.tmp), "REPO_VOLUMES": "cocon-x"}
        r = self.run_kit("docker", "compose", "-f", compose, "run", "--rm", "nosuch", env=env)
        self.assertEqual(r.rc, 97)
        self.assertIn("names no service", r.err)
        r = self.run_kit("docker", "compose", "-f", compose, "run", "--rm", "dev", env=env)
        self.assertEqual(r.rc, 0, r.err)

    def test_compose_fails_without_a_variable_the_compose_file_requires(self):
        """compose interpolates ${VAR:?...} for every command on the file: one unset fails, as compose's does"""
        compose = str(KIT / "compose.yml")
        r = self.run_kit("docker", "compose", "-f", compose, "ps", "-q", "egress",
                         env={"LOG_NAME": "x.log", "REPO_VOLUMES": "cocon-x"})
        self.assertEqual(r.rc, 98)
        self.assertIn("required variable LOG_DIR is missing a value", r.err)


class DaemonBlind(unittest.TestCase):
    """daemon_blind_message on the probe's outcomes, as docker reports them; no daemon needed."""
    ROOT, MAIN = "/tmp/wt", "/Users/u/repo"

    def msg(self, rc, err="", main=MAIN):
        return daemon_blind_message(self.ROOT, main, rc, err)

    def test_missing_path_fails_with_where_to_run_from(self):
        err = 'docker: Error response from daemon: invalid mount config for type "bind": ' \
              "bind source path does not exist: /tmp/wt\n"
        self.assertEqual(self.msg(125, err),
                         "kit at /tmp/wt isn't visible to the Docker daemon (under /tmp?): run from "
                         "/Users/u/repo/.claude/worktrees/<name> or a worktree nested under your session's "
                         ".claude/worktrees")

    def test_path_resolving_elsewhere_fails(self):
        self.assertIn("isn't visible", self.msg(1))

    def test_unknown_main_repo_keeps_the_placeholder(self):
        self.assertIn("run from <main repo>/.claude/worktrees/<name>", self.msg(1, main=""))

    def test_visible_repo_and_other_errors_pass(self):
        self.assertIsNone(self.msg(0))
        self.assertIsNone(self.msg(125, "Unable to find image 'busybox:1.37' locally"))


class StaleRuns(unittest.TestCase):
    def test_old_containers_and_networks_are_stale_young_and_foreign_ones_not(self):
        out = ('/cocon-bitest-12-egress-1 "2026-11-10T08:00:00.063640222Z"\n'
               'cocon-bitest-34_internal "2026-11-10T09:00:00.5+01:00"\n'
               'cocon-bitest-56_internal "2026-11-10T11:30:00Z"\n'
               'cocon-other-78_internal "2026-11-10T08:00:00Z"\n')
        now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        self.assertEqual(stale_in("cocon-bitest-", out, now, 3600),
                         {"cocon-bitest-12", "cocon-bitest-34"})


if __name__ == "__main__":
    unittest.main()
