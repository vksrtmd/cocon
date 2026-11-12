"""Launcher: the per-session credential broker (ADR 0014): tokens, placeholders, start, cleanup, sweeps."""
import hashlib
import os
import re
import signal
import subprocess
import time
import unittest

from harness import KIT
from launcher_case import LauncherCase

PLACEHOLDERS = KIT / "broker" / "placeholders.py"


def brk_sum(slug):
    """The hash the launcher puts in its broker's name: the slug's sha1, its first 10 hex digits."""
    return hashlib.sha1(slug.encode()).hexdigest()[:10]


def name(pid, slug):
    return f"cocon-brk-{pid}-{brk_sum(slug)}"


def placeholder(prefix):
    """The broker's placeholder starting with prefix, from the table it shares with the launcher."""
    m = re.search(rf'"({prefix}[A-Za-z0-9_-]*)"', PLACEHOLDERS.read_text())
    assert m, f"no {prefix} placeholder in placeholders.py"
    return m.group(1)


class Broker(LauncherCase):
    def setUp(self):
        super().setUp()
        self.cfg = self.tmp / "xdg" / "cocon"
        self.cfg.mkdir(parents=True)

    def claude_env(self, token):
        f = self.cfg / "claude.env"
        f.write_text(f"CLAUDE_CODE_OAUTH_TOKEN={token}\n")
        f.chmod(0o600)

    def gh_token(self, slug, token):
        f = self.cfg / "tokens" / f"{slug}.env"
        f.parent.mkdir(exist_ok=True)
        f.write_text(f"GH_TOKEN={token}\n")
        f.chmod(0o600)
        return f

    def broker_runs(self, res):
        return [c for c in res.calls if "compose" in c.args and "run" in c.args and "broker" in c.args]

    def broker_of(self, res):
        (run,) = self.broker_runs(res)
        return run.args[run.args.index("--name") + 1], run

    def github_repo(self, name):
        r = self.new_repo(name)
        self.git(r, "remote", "add", "origin", f"https://github.com/acme/{name}.git")
        return r

    def test_broker_gets_real_tokens_dev_only_placeholders_and_the_broker_as_proxy(self):
        r = self.github_repo("cred")
        self.gh_token("acme__cred", "github_pat_real-gh")
        self.claude_env("real-claude-file")
        res = self.launch(r, env={"STUB_GH_REPOS": "acme/cred"})
        self.assertEqual(res.rc, 0, res.text)
        brk, run = self.broker_of(res)
        self.assertRegex(brk, rf"^cocon-brk-\d+-{brk_sum('acme__cred')}$")
        self.assertEqual(run.env["GH_TOKEN"], "github_pat_real-gh")
        self.assertEqual(run.env["CLAUDE_CODE_OAUTH_TOKEN"], "real-claude-file")
        passed = {run.args[i + 1] for i, a in enumerate(run.args) if a == "-e"}
        self.assertEqual(passed, {"CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"})  # by name: the value never rides the command line
        for m in (f"{brk}-ca-key:/ca", f"{brk}-ca:/pub"):
            self.assertIn(m, run.args)
        self.assertNotIn("--use-aliases", run.args)  # no shared name: the Docker sidecar has its own broker
        sess = self.session(res)
        self.assertIn(f"cocon-acme__cred-dev-{brk.split('-')[2]}", sess)  # named after the same launcher
        self.assertMount(res, f"{brk}-ca:/opt/cocon/broker-ca:ro")
        for v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            self.assertEnv(res, f"{v}=http://{brk}:8080")
        self.assertEnv(res, f"CLAUDE_CODE_OAUTH_TOKEN={placeholder('sk-ant-')}")
        self.assertEnv(res, f"GH_TOKEN={placeholder('github_pat_')}")
        self.assertNotIn("real-", " ".join(sess), "dev got a real token")
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", sess)
        self.assertNotIn("GH_TOKEN", sess)
        # The broker's compose run has the real tokens in its env; only compose.yml could hand them on to dev.
        compose = (KIT / "compose.yml").read_text()
        for name in ("GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            self.assertNotIn(name, compose, f"compose.yml mentions {name}: dev could inherit the real token")

    def test_only_the_brokers_docker_call_carries_the_real_tokens(self):
        r = self.github_repo("only")
        self.gh_token("acme__only", "github_pat_real-gh")
        self.claude_env("real-claude-file")
        # GH_TOKEN as a gh user exports it: the launcher must drop it before any docker call but the broker's.
        res = self.launch(r, "--with", "docker", "--port", "3000",
                          env={"STUB_GH_REPOS": "acme/only", "GH_TOKEN": "github_pat_real-caller"})
        self.assertEqual(res.rc, 0, res.text)
        _, brk = self.broker_of(res)
        others = [c for c in res.calls if c.cmd == "docker" and c is not brk]
        self.assertTrue(others)
        for c in others:
            self.assertNotIn("GH_TOKEN", c.env, c)
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", c.env, c)

    def test_no_github_token_no_github_placeholder_in_dev_and_none_in_the_broker(self):
        r = self.github_repo("cred")
        res = self.launch(r)
        self.assertLaunch(res, 0, f"no token at {self.cfg}/tokens/acme__cred.env")
        self.assertFalse([a for a in self.session(res) if a.startswith("GH_TOKEN")], self.session(res))
        _, run = self.broker_of(res)
        self.assertNotIn("GH_TOKEN", run.env)
        self.assertEqual(run.env["CLAUDE_CODE_OAUTH_TOKEN"], "real-claude")
        self.assertNotIn("GH_TOKEN", run.args)

    def test_no_claude_token_refused_naming_both_nothing_started(self):
        res = self.launch(self.new_repo("cred"), env={"CLAUDE_CODE_OAUTH_TOKEN": ""})
        self.assertLaunch(res, 1, "CLAUDE_CODE_OAUTH_TOKEN", f"{self.cfg}/claude.env")
        self.assertNoCall(res, " run ")
        self.assertNoCall(res, "volume create")

    def test_removed_with_both_ca_volumes_after_the_session_also_when_it_fails(self):
        r = self.new_repo("cred")
        for code in (0, 3):
            with self.subTest(rc=code):
                res = self.launch(r, rc=code)
                self.assertEqual(res.rc, code, res.text)
                brk, _ = self.broker_of(res)
                self.assertCall(res, f"rm -fv {brk}")  # -v: the image's anonymous volume too
                self.assertCall(res, f"volume rm {brk}-ca-key {brk}-ca")

    def test_doesnt_start_no_session_says_so_still_removed(self):
        res = self.launch(self.new_repo("cred"), env={"STUB_BROKER_RC": "1"})
        self.assertLaunch(res, 1, "credential broker didn't start")
        self.assertIsNone(self.session(res), "session ran")
        self.assertCallRe(res, rf"^rm -fv cocon-brk-\d+-{brk_sum('cred')}$")

    def test_restarts_after_a_crash_within_the_session(self):
        """broker: restarts after a crash within the session (compose run applies no restart policy)"""
        res = self.launch(self.new_repo("crash"))
        brk, _ = self.broker_of(res)
        self.assertCall(res, f"update --restart on-failure {brk}")

    def test_detach_relaunch_starts_the_broker_dev_gets_only_placeholders(self):
        r = self.new_repo("detb")
        self.claude_env("real-claude-file")
        del self.env["CLAUDE_CODE_OAUTH_TOKEN"]
        res = self.launch("--detach", r)
        self.assertEqual(res.rc, 0, res.text)
        self.assertEqual(self.broker_runs(res), [], "a broker before the relaunch")
        (cmd,) = re.findall(r"respawn-pane -k -t =cocon-detb: (.*)", "\n".join(self.lines(res)))
        # The pane gets the tmux server's environment, not the caller's token.
        res = self.run_kit("bash", "-c", cmd, env={"STUB": "true", "STUB_RC": "0"})
        self.assertEqual(res.rc, 0, res.out + res.err)
        brk, run = self.broker_of(res)
        self.assertEqual(run.env["CLAUDE_CODE_OAUTH_TOKEN"], "real-claude-file")
        self.assertNotIn("GH_TOKEN", run.env)
        self.assertEnv(res, f"HTTPS_PROXY=http://{brk}:8080")
        self.assertEnv(res, f"CLAUDE_CODE_OAUTH_TOKEN={placeholder('sk-ant-')}")
        self.assertNotIn("real-", " ".join(self.session(res)))

    def test_two_sessions_each_their_own_broker_ending_one_leaves_the_others(self):
        other = name(os.getpid(), "two")  # a live launcher's
        res = self.launch(self.new_repo("two"), env={"STUB_BROKERS": other,
                                                     "STUB_VOLUMES": f"{other}-ca-key {other}-ca"})
        self.assertEqual(res.rc, 0, res.text)
        brk, _ = self.broker_of(res)
        self.assertNotEqual(brk, other)
        self.assertCall(res, f"rm -fv {brk}")
        self.assertNoCall(res, other)

    def test_down_and_destroy_remove_this_checkouts_left_brokers_and_volumes_only(self):
        r = self.new_repo("left")
        mine = [name(111, "left"), name(222, "left")]
        others = [name(333, "left--wt"), name(444, "leftover"), "cocon-left-ports-555"]
        vols = [f"{mine[0]}-ca-key", f"{mine[0]}-ca", f"{mine[1]}-ca", f"{others[0]}-ca", "cocon-left-config"]
        for flag in ("--down", "--destroy"):
            with self.subTest(flag=flag):
                res = self.launch(r, flag, answer="y", env={"STUB_BROKERS": " ".join(mine + others), "STUB_VOLUMES": " ".join(vols)})
                self.assertEqual(res.rc, 0, res.text)
                for b in mine:
                    self.assertCall(res, f"rm -fv {b}")
                for b in others:
                    self.assertNoCall(res, f"rm -fv {b}")
                self.assertCall(res, f"volume rm {mine[0]}-ca-key {mine[0]}-ca {mine[1]}-ca")
                self.assertNoCall(res, f"{others[0]}-ca")

    def test_one_it_cant_remove_after_the_session_is_named_with_what_removes_it(self):
        res = self.launch(self.new_repo("stuck"), env={"STUB_RM_FAIL": "1"})
        brk, _ = self.broker_of(res)
        self.assertLaunch(res, 0, f"couldn't remove the credential broker {brk}", "cocon --down")

    def test_hangup_mid_session_removes_the_broker_exits_129(self):
        r = self.new_repo("hup")
        ready = self.tmp / "in-session"
        p = subprocess.Popen([str(KIT / "cocon"), str(r)], cwd=self.tmp, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env={**self.env, "STUB": f"touch {ready}; exec sleep 60"})
        self.addCleanup(p.kill)
        deadline = time.monotonic() + 30
        while not ready.exists() and p.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(ready.exists(), "the session never started")
        p.send_signal(signal.SIGHUP)
        out = p.communicate(timeout=30)[0]
        self.assertEqual(p.returncode, 128 + signal.SIGHUP, out)
        self.assertTrue([c for c in self.calls() if re.fullmatch(rf"rm -fv cocon-brk-{p.pid}-{brk_sum('hup')}", c.line)],
                        self.calls())

    def test_down_cant_list_brokers_fails_never_a_silent_success(self):
        res = self.launch(self.new_repo("stuck"), "--down", env={"STUB_PS_FAIL": "1"})
        self.assertNotEqual(res.rc, 0, res.text)
        self.assertIn("docker ps", res.text)

    def test_down_cant_list_ca_volumes_fails_never_a_silent_success(self):
        res = self.launch(self.new_repo("stuck"), "--down", env={"STUB_FAIL": "volume ls"})
        self.assertNotEqual(res.rc, 0, res.text)
        self.assertIn("docker volume ls", res.text)

    def test_start_removes_brokers_whose_launcher_is_gone_with_volumes_one_line_each(self):
        p = subprocess.Popen(["true"])
        p.wait()
        dead = p.pid
        gone, live = name(dead, "sweep"), name(os.getpid(), "sweep")
        decoys = [name(dead, "sweep--wt"), name(dead, "sweepx"), f"cocon-sweep-ports-{dead}"]
        res = self.launch(self.new_repo("sweep"), env={"STUB_BROKERS": " ".join([gone, live] + decoys)})
        self.assertEqual(res.rc, 0, res.text)
        self.assertCall(res, f"rm -fv {gone}")
        self.assertCall(res, f"volume rm {gone}-ca-key {gone}-ca")
        self.assertEqual(res.text.count(f"removed the broker {gone} "), 1, res.text)
        self.assertNoCall(res, f"rm -fv {live}")
        for b in decoys:
            self.assertNoCall(res, f"rm -fv {b}")

    def test_leftover_with_this_sessions_name_goes_before_ours_starts(self):
        """session start: a leftover broker with this session's name (a reused pid) goes before ours starts"""
        r = self.new_repo("sweep")
        # The launcher's pid is unknown beforehand: let a shell exec it with its own pid in the name.
        res = self.run_kit("sh", "-c", f'STUB_BROKERS="cocon-brk-$$-{brk_sum("sweep")}" exec "$0" "$1"', KIT / "cocon", r,
                           env={"STUB": "true", "STUB_RC": "0"})
        self.assertEqual(res.rc, 0, res.out + res.err)
        brk, _ = self.broker_of(res)
        lines = [c.line for c in res.calls]
        rm_at = next(i for i, l in enumerate(lines) if l == f"rm -fv {brk}")
        run_at = next(i for i, l in enumerate(lines) if " run -d " in l and f" --name {brk} " in l)
        self.assertLess(rm_at, run_at, lines)
        self.assertIn(f"removed the broker {brk} ", res.out + res.err)

    def test_names_dev_resolves_stay_within_a_dns_label(self):
        """broker: its name is dev's proxy hostname, so it stays within 63 characters whatever the slug"""
        r = self.new_repo("c" * 40 + "-folder")
        self.git(r, "remote", "add", "origin", f"https://github.com/{'o' * 30}/{'c' * 40}.git")
        wt = "w" * 30
        self.git(r, "worktree", "add", "-q", str(r / ".claude" / "worktrees" / wt), "-b", wt)
        res = self.launch(r, "--worktree", wt)
        self.assertEqual(res.rc, 0, res.text)
        brk, _ = self.broker_of(res)
        self.assertLessEqual(len(brk), 63, brk)
        self.assertEnv(res, f"HTTPS_PROXY=http://{brk}:8080")


if __name__ == "__main__":
    unittest.main()
