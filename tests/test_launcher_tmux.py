"""Launcher: --detach, --attach, --ls and the missing-tmux paths."""
import os
import re
import unittest

from harness import KIT, import_kit
from launcher_case import LauncherCase, shq


class Detach(LauncherCase):
    def test_new_session_keeps_its_pane_runs_launcher_without_detach(self):
        r = self.new_repo("det")
        res = self.launch("--detach", r, "--shell")
        self.assertEqual(res.rc, 0, res.text)
        self.assertCall(res, f"new-session -d -s cocon-det -c {self.tmp}")
        self.assertCall(res, "set-option -t =cocon-det: remain-on-exit on")
        self.assertTrue([l for l in self.lines(res) if f"{KIT}/cocon {r} --shell " in l], "relaunch not run without --detach")
        self.assertIn(f"cocon --attach {r}", res.text)
        self.assertNoCall(res, " run ")

    def test_relaunch_carries_with(self):
        r = self.new_repo("det")
        res = self.launch("--detach", r, "--with", "rust,go")
        self.assertEqual(res.rc, 0, res.text)
        self.assertTrue([l for l in self.lines(res) if f"cocon {r} --with {shq('rust,go')} " in l], self.lines(res))

    def test_relaunch_keeps_quoting_worktree_and_callers_path(self):
        r2 = self.new_repo("sp ace")
        self.git(r2, "branch", "9-x")
        res = self.launch(r2, "--worktree", "9-x", "--port", "80", "--detach", "--", "-c")
        self.assertEqual(res.rc, 0, res.text)
        out = "\n".join(self.lines(res))
        for t in ("respawn-pane -k -t =cocon-sp-ace_", f"--9-x: env {shq('PATH=' + self.env['PATH'])} ",
                  f"cocon {shq(r2)} --worktree 9-x --port 80 -- -c "):
            self.assertIn(t, out)

    def test_slug_with_a_session_attaches_starts_nothing(self):
        res = self.launch("--detach", self.new_repo("det"), env={"STUB_SESSIONS": "cocon-det"})
        self.assertCall(res, "attach-session -t =cocon-det")
        self.assertNoCall(res, "new-session")

    def test_slug_with_a_dead_pane_relaunches_in_it(self):
        r = self.new_repo("det")
        res = self.launch("--detach", r, env={"STUB_SESSIONS": "cocon-det", "STUB_DEAD": "cocon-det:"})
        self.assertEqual(res.rc, 0, res.text)
        self.assertTrue([l for l in self.lines(res) if l.startswith("respawn-pane -k -t =cocon-det: ")], res.text)
        self.assertNoCall(res, "attach-session")
        self.assertNoCall(res, "new-session")

    def test_attach_inside_tmux_switches_client(self):
        res = self.launch("--attach", self.new_repo("det"), env={"TMUX": "1", "STUB_SESSIONS": "cocon-det"})
        self.assertCall(res, "switch-client -t =cocon-det")

    def test_attach_without_a_session_fails(self):
        self.assertLaunch(self.launch("--attach", self.new_repo("det")), 1, "no detached session for det")


class Ls(LauncherCase):
    def test_every_slug_with_running_services_and_session_state(self):
        panes = "\n".join(["cocon-a 1 0", "cocon-b 0 1", "cocon-b 0 1", "cocon-d 0 0",
                           "cocon-e 0 1", "cocon-e 0 0", "other 0 0"])
        res = self.launch("--ls", env={
            "STUB_PROJECTS": "cocon-a cocon-b cocon-c",
            "STUB_RUNNING": "cocon-a=egress,postgres cocon-b=egress,ports",
            "STUB_PORTS": "cocon-b=5173>5173,5174>5174,15173>80",
            "STUB_PANES": panes})
        self.assertEqual(res.rc, 0, res.text)
        out = "\n".join(" ".join(l.split()) for l in res.text.splitlines())
        for t in ("SLUG SERVICES PORTS SESSION", "a egress,postgres - attached", "b egress,ports 5173,5174,15173:80 exited",
                  "c - - -", "d - - detached", "e - - detached"):
            self.assertIn(t, out)
        self.assertNotIn("other", out)

    def test_nothing_running_header_only(self):
        res = self.launch("--ls")
        self.assertEqual(res.rc, 0, res.text)
        self.assertTrue(res.text.startswith("SLUG") and "\n" not in res.text.strip("\n"), res.text)

    def test_docker_failing_fails_naming_docker_never_an_empty_list(self):
        for call in ("compose ls", "ps"):
            with self.subTest(call=call):
                res = self.launch("--ls", env={"STUB_FAIL": call, "STUB_PROJECTS": "cocon-a"})
                self.assertNotEqual(res.rc, 0, res.text)
                self.assertIn("docker is not answering", res.text)


class NoTmux(LauncherCase):
    def no_tmux_path(self):
        """PATH with each dir holding a tmux swapped for a copy without it."""
        out = []
        for i, d in enumerate(self.env["PATH"].split(":")):
            if os.access(os.path.join(d, "tmux"), os.X_OK):
                copy = self.tmp / f"notmux{i}"
                copy.mkdir()
                for f in os.listdir(d):
                    if f != "tmux":
                        (copy / f).symlink_to(os.path.join(d, f))
                d = str(copy)
            out.append(d)
        return ":".join(out)

    def test_detach_attach_say_how_to_install_ls_warns_once_doctor_warns(self):
        path = self.no_tmux_path()
        r = self.new_repo("det")
        for flag in ("--detach", "--attach"):
            with self.subTest(flag=flag):
                self.assertLaunch(self.launch(r, flag, env={"PATH": path}), 1, "install tmux: brew install tmux")
        res = self.launch("--ls", env={"PATH": path, "STUB_PROJECTS": "cocon-a"})
        self.assertEqual(res.rc, 0, res.text)
        self.assertEqual(len([l for l in res.text.splitlines() if "tmux" in l]), 1, res.text)
        self.assertIn("%-36s %-24s %-16s %s" % ("a", "-", "-", "-"), res.text)
        self.assertIn("WARN  tmux: missing", self.launch(r, "--doctor", env={"PATH": path}).text)


if __name__ == "__main__":
    unittest.main()


class HostVars(unittest.TestCase):
    def test_relaunch_carries_every_variable_the_kit_and_compose_read_from_the_host(self):
        """HOST_VARS, which --detach's relaunch carries, is exactly what the kit's Python and compose.yml read from the
        environment, plus docker's, besides the shell's own and the variables the launcher sets for compose"""
        import_kit()
        from kit.launcher.options import image_env
        from kit.util import HOST_VARS
        read = set()
        for f in (KIT / "py").rglob("*.py"):
            read |= set(re.findall(r"os\.environ(?:\.get\(|\[)[\"']([A-Z_]+)[\"']", f.read_text()))
        read |= set(re.findall(r"\$\{([A-Z_]+)", (KIT / "compose.yml").read_text()))
        read |= {"DOCKER_HOST", "DOCKER_CONTEXT"}  # docker's own: every docker call the launcher makes reads them
        read -= {"HOME", "PWD", "TMUX"}
        read -= set(image_env([])) | {"ALLOW_DIR", "REPO_ALLOW", "REPO_VOLUMES", "LOG_DIR", "LOG_NAME"}
        self.assertEqual(read, set(HOST_VARS))
