"""Launcher: the audit log, its rotation, --rotate-logs, --proxy-log."""
import datetime
import gzip
import os
import unittest
from unittest import mock

from harness import import_kit
from launcher_case import COMPOSE, LauncherCase

ROTATE = " exec -T egress squid -f /etc/squid/cocon/squid.conf -k rotate"


class Audit(LauncherCase):
    def setUp(self):
        super().setUp()
        self.state = self.tmp / "state" / "cocon"
        self.today = datetime.date.today().isoformat()

    def log_paths(self, res):
        return {f"{c.env['LOG_DIR']}/{c.env['LOG_NAME']}" for c in res.calls if c.env.get("LOG_NAME")}

    def test_reload_proxy_reconfigures_egress_with_the_projects_variables(self):
        res = self.launch(self.new_repo("rel"), "--reload-proxy")
        self.assertEqual(res.rc, 0, res.text)
        (c,) = self.calls_matching("exec", "egress", "squid", "-k", "reconfigure")
        self.assertIn(" -p cocon-rel ", f" {c.line} ")
        self.assertEqual(c.env["LOG_NAME"], "rel.log")
        self.assertTrue(c.env["LOG_DIR"].endswith("/cocon/rel"), c.env)

    def test_per_repo_dir_per_slug_file_session_start_and_end(self):
        r = self.new_repo("audit")
        self.git(r, "remote", "add", "origin", "https://github.com/acme/audit.git")
        self.git(r, "worktree", "add", "-q", str(r / ".claude/worktrees/w1"), "-b", "w1")
        res = self.launch(r, "--with", "rust,go", rc=7)
        self.assertEqual(res.rc, 7, res.text)
        d = self.state / "acme__audit"
        self.assertEqual(self.log_paths(res), {f"{d}/acme__audit.log"})
        # squid writes as its own user: the repo's dir is open, the state dir above keeps other users out.
        self.assertEqual(os.stat(d).st_mode & 0o777, 0o777)
        self.assertEqual(os.stat(d.parent).st_mode & 0o777, 0o700)
        # The broker's own subdir, as open: the broker gets it, never squid's files (ADR 0012).
        self.assertEqual(os.stat(d / "broker").st_mode & 0o777, 0o777)
        log = (d / "acme__audit.log").read_text()
        self.assertRegex(log, r"(?m)^\d{4}-\d{2}-\d{2}T[0-9:]{8}[+-]\d{4} session start acme__audit pid=\d+ with=go,rust$")
        self.assertRegex(log, r" session end acme__audit pid=\d+ exit=7$")
        res = self.launch(r, "--worktree", "w1")
        self.assertEqual(self.log_paths(res), {f"{d}/acme__audit--w1.log"})
        self.assertRegex((d / "acme__audit--w1.log").read_text(), r"(?m) session start acme__audit--w1 pid=\d+ with=-$")

    def test_state_dir_closed_to_other_users_even_if_it_was_open(self):
        r = self.new_repo("open")
        self.state.mkdir(parents=True)
        self.state.chmod(0o755)
        self.assertEqual(self.launch(r).rc, 0)
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o700)

    def test_state_dir_closed_before_the_repos_open_dirs_exist(self):
        import_kit()
        from kit.launcher import audit
        seen = []
        makedirs = os.makedirs

        def noting(path, *a, **kw):  # the state dir's mode when the broker's dir is made
            if str(path).endswith("/broker"):
                seen.append(os.stat(self.state).st_mode & 0o777)
            return makedirs(path, *a, **kw)
        self.state.mkdir(parents=True, mode=0o755)
        with mock.patch.object(audit.os, "makedirs", noting):
            audit.prepare_log_dir(f"{self.state}/first")
        self.assertEqual(seen, [0o700])

    def test_rotation_at_session_start_over_the_limit_archived_under_it_kept(self):
        r = self.new_repo("rot")
        d = self.state / "rot"
        log = d / "rot.log"
        self.launch(r)
        self.launch(r)  # default limit, 10 MB: kept
        self.assertEqual(log.read_text().count("session start"), 2, os.listdir(d))
        with open(log, "a") as f:
            f.write("old\n")
        res = self.launch(r, env={"COCON_LOG_MAX_MB": "0"})
        self.assertEqual(res.rc, 0, res.text)
        self.assertEqual(gzip.open(d / f"rot.log.{self.today}.gz", "rt").read().splitlines()[-1], "old")
        self.assertEqual(log.read_text().count("session start"), 1, log.read_text())
        (d / "broker").joinpath("rot.log").write_text("brk\n")  # the broker's file: archived too, never reopened
        res = self.launch(r, env={"COCON_LOG_MAX_MB": "0"})
        self.assertEqual(res.rc, 0, res.text)
        self.assertEqual(gzip.open(d / "broker" / f"rot.log.{self.today}.gz", "rt").read(), "brk\n")
        self.assertFalse((d / "broker" / "rot.log").exists(), os.listdir(d / "broker"))
        self.assertNoCall(res, "-k rotate")  # egress isn't running: nothing to reopen
        res = self.launch(r, env={"COCON_LOG_MAX_MB": "0", "STUB_EGRESS_UP": "1"})
        self.assertCall(res, f"compose -f {COMPOSE} -p cocon-rot{ROTATE}")
        self.assertTrue((d / f"rot.log.{self.today}-3.gz").is_file(), os.listdir(d))

    def test_bad_log_max_mb_refused_but_down_still_works(self):
        """audit log rotation at session start: bad COCON_LOG_MAX_MB refused (08 is not octal)"""
        r = self.new_repo("rot")
        for bad in ("1x", "08"):
            with self.subTest(bad=bad):
                res = self.launch(r, env={"COCON_LOG_MAX_MB": bad})
                self.assertLaunch(res, 1, f"COCON_LOG_MAX_MB must be a whole number of MB, got {bad}")
        self.assertEqual(self.launch(r, "--down", env={"COCON_LOG_MAX_MB": "1x"}).rc, 0)

    def test_unwritable_log_at_the_end_keeps_exit_code_warns_cleanup_runs(self):
        r = self.new_repo("rot")
        log = self.state / "rot" / "rot.log"
        self.launch(r)
        self.addCleanup(os.chmod, log, 0o644)
        res = self.launch(r, stub=f"chmod 444 {log}", rc=5)
        self.assertLaunch(res, 5, f"couldn't write the session end to {log}")

    def test_rotate_logs_every_slugs_log_each_reopened_by_its_egress_no_session(self):
        r = self.new_repo("rotall")
        d = self.state / "rotall"
        (d / "broker").mkdir(parents=True)
        (d / "rotall.log").write_text("a\n")
        (d / "rotall--w1.log").write_text("b\n")
        (d / "broker" / "rotall--w1.log").write_text("c\n")
        res = self.launch(r, "--rotate-logs", env={"COCON_LOG_MAX_MB": "0", "STUB_EGRESS_UP": "1"})
        self.assertEqual(res.rc, 0, res.text)
        for f in (d / f"rotall.log.{self.today}.gz", d / f"rotall--w1.log.{self.today}.gz",
                  d / "broker" / f"rotall--w1.log.{self.today}.gz"):
            self.assertTrue(f.is_file(), f"{f}: {os.listdir(d)}")
        self.assertCall(res, f"compose -f {COMPOSE} -p cocon-rotall--w1{ROTATE}")
        self.assertIsNone(self.session(res), "started a session")

    def test_proxy_log_follows_the_slugs_audit_log(self):
        r = self.new_repo("rotall")
        tailbin = self.tmp / "tailbin"
        tailbin.mkdir()
        (tailbin / "tail").write_text('#!/bin/sh\necho "tail $*"\n')
        (tailbin / "tail").chmod(0o755)
        res = self.launch(r, "--proxy-log", env={"PATH": f"{tailbin}:{self.env['PATH']}"})
        self.assertIn(f"tail -n 50 -F {self.state}/rotall/rotall.log {self.state}/rotall/broker/rotall.log", res.text)

    def test_proxy_log_shows_broker_refusals_next_to_squid_denials(self):
        r = self.new_repo("rotall")
        d = self.state / "rotall"
        (d / "broker").mkdir(parents=True)
        (d / "rotall.log").write_text("2026-11-02T10:00:00+0000 172.20.0.3 TCP_DENIED/403 up=0 down=0 CONNECT 1.2.3.4:443\n")
        (d / "broker/rotall.log").write_text(
            "2026-11-02T10:00:01+0000 broker CONNECT registry.npmjs.org:443 refused - sni-mismatch sni=evil.example\n"
            "2026-11-02T10:00:02+0000 broker PUT registry.npmjs.org/pkg 403 - registry-write\n"
            "2026-11-02T10:00:03+0000 broker GET api.github.com/gists 403 - foreign-credential\n")
        tailbin = self.tmp / "tailbin"  # the real tail, without -F so that it ends
        tailbin.mkdir()
        (tailbin / "tail").write_text('#!/bin/sh\nshift 3; exec /usr/bin/tail -n 50 "$@"\n')
        (tailbin / "tail").chmod(0o755)
        res = self.launch(r, "--proxy-log", env={"PATH": f"{tailbin}:{self.env['PATH']}"})
        for want in ("TCP_DENIED/403", "refused - sni-mismatch sni=evil.example", "registry-write", "foreign-credential"):
            self.assertIn(want, res.text)

    def test_destroy_keeps_the_audit_log_and_says_where(self):
        r = self.new_repo("rotall")
        d = self.state / "rotall"
        self.launch(r)
        res = self.launch(r, "--destroy", answer="y")
        self.assertIn(f"keeps the audit log: {d}", res.text)
        self.assertTrue(d.is_dir(), "audit log dir removed")


if __name__ == "__main__":
    unittest.main()
