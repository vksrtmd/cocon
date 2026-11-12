"""Launcher: --port forwarder."""
import os
import re
import subprocess
import unittest

from launcher_case import LauncherCase


class Port(LauncherCase):
    def forwarders(self, res):
        return [c for c in res.calls if "run" in c.args and "ports" in c.args]

    def test_forwarder_publishes_on_loopback_session_answers_to_dev_removed_after(self):
        r = self.new_repo("web")
        res = self.launch(r, "--port", "5173", "--port", "15173:80", rc=3)
        self.assertEqual(res.rc, 3)
        (fwd,) = self.forwarders(res)
        m = re.search(r" --profile ports run -d --rm --name cocon-web-ports-(\d+) -p 127\.0\.0\.1:5173:5173"
                      r" -p 127\.0\.0\.1:15173:80 ports cocon-web-dev-(\d+) 5173 80$", fwd.line)
        self.assertTrue(m, fwd.line)
        self.assertEqual(m.group(1), m.group(2))
        self.assertIn(f"cocon-web-dev-{m.group(1)}", self.session(res))
        self.assertEqual(res.calls[-1].line, f"rm -f cocon-web-ports-{m.group(1)}")

    def test_leftover_forwarders_of_gone_launchers_removed_starting_ones_kept(self):
        """--port: leftover forwarders whose launcher is gone are removed; a live launcher's, with no dev container yet, kept"""
        p = subprocess.Popen(["true"])
        p.wait()
        gone, live = f"cocon-web-ports-{p.pid}", f"cocon-web-ports-{os.getpid()}"
        res = self.launch(self.new_repo("web"), "--port", "80", env={"STUB_FWDS": f"{gone} {live}"})
        self.assertCall(res, f"rm -f {gone}")
        self.assertNotIn(f"rm -f {live}", self.lines(res))  # exact: the launcher's own pid may start with ours

    def test_down_and_destroy_remove_this_checkouts_forwarders_only(self):
        """--down and --destroy: remove the forwarders sessions left (compose down leaves run containers), only this checkout's"""
        mine = ["cocon-web-ports-111", "cocon-web-ports-222"]
        others = ["cocon-web--wt-ports-333", "cocon-webx-ports-444"]
        for flag in ("--down", "--destroy"):
            with self.subTest(flag=flag):
                res = self.launch(self.new_repo("web"), flag, answer="y", env={"STUB_FWDS": " ".join(mine + others)})
                self.assertEqual(res.rc, 0, res.text)
                for f in mine:
                    self.assertCall(res, f"rm -f {f}")
                for f in others:
                    self.assertNoCall(res, f)

    def test_no_port_no_forwarder(self):
        d = self.tmp / "nogit-early"
        d.mkdir()
        self.assertEqual(self.forwarders(self.launch(d)), [])

    def test_port_in_non_git_dir_forwarder_still_removed_after(self):
        d = self.tmp / "nogit-early"
        d.mkdir()
        res = self.launch(d, "--port", "8080")
        self.assertCallRe(res, r"^rm -f cocon-nogit-early-ports-\d+$")

    def test_bad_specs_refused_before_anything_starts(self):
        r = self.new_repo("web")
        for bad in ("0", "65536", "05173", "abc", "1:2:3", ":80"):
            with self.subTest(bad=bad):
                res = self.launch(r, "--port", bad)
                self.assertLaunch(res, 1, f"bad --port {bad}")
                self.assertIsNone(self.session(res), "session ran")

    def test_host_or_cocoon_port_given_twice_refused(self):
        r = self.new_repo("web")
        self.assertLaunch(self.launch(r, "--port", "80", "--port", "8080:80"), 1, "cocoon port 80 given twice")
        self.assertLaunch(self.launch(r, "--port", "80", "--port", "80:8080"), 1, "host port 80 given twice")

    def test_port_only_applies_to_starting_a_session(self):
        self.assertLaunch(self.launch(self.new_repo("web"), "--port", "80", "--down"), 1,
                          "--port only applies to starting a session")

    def test_host_port_in_use_names_host_box_form_no_session(self):
        err = "Error response from daemon: ...: Bind for 127.0.0.1:15173 failed: port is already allocated"
        res = self.launch(self.new_repo("web"), "--port", "15173:80", env={"STUB_PORTS_ERR": err})
        self.assertLaunch(res, 1, "host port 15173 is in use; try --port <free>:80")
        self.assertIsNone(self.session(res), "session ran")
        self.assertCallRe(res, r"^rm -f cocon-web-ports-\d+$")

    def test_other_forwarder_failures_show_dockers_error(self):
        res = self.launch(self.new_repo("web"), "--port", "80", env={"STUB_PORTS_ERR": "pull access denied"})
        self.assertLaunch(res, 1, "pull access denied", "--port forwarder didn't start")


if __name__ == "__main__":
    unittest.main()
