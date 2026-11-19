"""Launcher: --import-transcript copies one named session between repos' Claude config volumes."""
import os
import subprocess
import unittest

from harness import import_kit
from launcher_case import LauncherCase

SID = "0a1b2c3d-1111-4222-8333-444455556666"
OTHER = "ffffffff-1111-4222-8333-444455556666"
VOLS = "cocon-acme__widget-config cocon-me__mine-config"


class Parse(LauncherCase):
    def refused(self, *args, text):
        r = self.launch(self.new_repo("imp"), "--import-transcript", *args)
        self.assertLaunch(r, 1, text)
        self.assertEqual(r.calls, [], "docker called before the refusal")

    def test_missing_values_name_the_usage(self):
        self.refused(text="OWNER/REPO SESSION_ID")
        self.refused("acme/widget", text="OWNER/REPO SESSION_ID")

    def test_bad_session_id_is_refused(self):
        for bad in ("../x", SID.upper(), SID[:-1], f"{SID}x", "latest"):
            with self.subTest(bad=bad):
                self.refused("acme/widget", bad, text="bad SESSION_ID")

    def test_bad_repo_is_refused(self):
        for bad in ("acme", "acme/widget/x", "acme/wid get", "/widget", "acme/", "a;b/c"):
            with self.subTest(bad=bad):
                self.refused(bad, SID, text="bad OWNER/REPO")


class Volumes(LauncherCase):
    def imp(self, vols, repo="me/mine", src="acme/widget", **kw):
        r = self.new_repo("imp")
        self.git(r, "remote", "add", "origin", f"https://github.com/{repo}.git")
        return self.launch(r, "--import-transcript", src, SID, env={"STUB_VOLUMES": vols}, **kw)

    def test_missing_source_volume_is_refused(self):
        res = self.imp("cocon-me__mine-config")
        self.assertLaunch(res, 1, "acme/widget has no Claude config volume")
        self.assertNoCall(res, "run --rm")

    def test_missing_destination_volume_is_refused(self):
        res = self.imp("cocon-acme__widget-config")
        self.assertLaunch(res, 1, "launch this repo once first")
        self.assertNoCall(res, "run --rm")

    def test_same_repo_is_refused_whatever_the_case(self):
        res = self.imp("cocon-me__mine-config", src="ME/Mine")
        self.assertLaunch(res, 1, "is this repo")

    def test_missing_image_says_to_build(self):
        res = self.imp_noimg()
        self.assertLaunch(res, 1, "cocon --build")

    def imp_noimg(self):
        r = self.new_repo("imp")
        self.git(r, "remote", "add", "origin", "https://github.com/me/mine.git")
        return self.launch(r, "--import-transcript", "acme/widget", SID,
                           env={"STUB_VOLUMES": VOLS, "STUB_NOIMG": "cocon:latest"})

    def test_source_matched_case_insensitively_and_copied_read_only_without_network(self):
        res = self.imp(VOLS.replace("acme__widget", "Acme__Widget"), stub="echo /p/x.jsonl; echo 3")
        self.assertLaunch(res, 0, "/p/x.jsonl")
        (c,) = [c for c in res.calls if "--rm" in c.args and "run" in c.args]
        a = c.args
        self.assertIn("-v cocon-Acme__Widget-config:/src:ro", " ".join(a))
        self.assertIn("-v cocon-me__mine-config:/dst", " ".join(a))
        self.assertEqual(a[a.index("--network") + 1], "none")
        self.assertEqual(a[-3:], ["/src", "/dst", SID])

    def test_failed_copy_exits_with_dockers_code(self):
        res = self.imp(VOLS, rc=7)
        self.assertEqual(res.rc, 7, res.text)


class Script(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.src, self.dst = Path(self._t.name, "src"), Path(self._t.name, "dst")
        self.proj = self.src / "projects" / "-work-app"
        self.proj.mkdir(parents=True)
        self.dst.mkdir()

    def run_script(self, sid=SID):
        import_kit()
        from kit.launcher.transcripts import SCRIPT
        return subprocess.run(["bash", "-c", SCRIPT, "bash", str(self.src), str(self.dst), sid],
                              capture_output=True, text=True)

    def test_copies_main_and_subagent_transcripts_keeping_paths(self):
        (self.proj / f"{SID}.jsonl").write_text("main")
        (self.proj / SID / "subagents").mkdir(parents=True)
        (self.proj / SID / "subagents" / "agent-1.jsonl").write_text("sub")
        (self.proj / f"{OTHER}.jsonl").write_text("other")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[:2], [f"/home/claude/.claude/projects/-work-app/{SID}.jsonl", "2"])
        d = self.dst / "projects" / "-work-app"
        self.assertEqual((d / f"{SID}.jsonl").read_text(), "main")
        self.assertEqual((d / SID / "subagents" / "agent-1.jsonl").read_text(), "sub")
        self.assertFalse((d / f"{OTHER}.jsonl").exists())

    def test_symlinked_session_file_carries_nothing(self):
        (self.src / "history.jsonl").write_text("secret")
        os.symlink("../../history.jsonl", self.proj / f"{SID}.jsonl")
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("found 0", r.stderr)
        self.assertEqual(list(self.dst.rglob("*")), [])

    def test_symlink_inside_session_dir_is_skipped(self):
        (self.proj / f"{SID}.jsonl").write_text("main")
        (self.src / "secret").write_text("secret")
        (self.proj / SID).mkdir()
        os.symlink("../../../secret", self.proj / SID / "agent-x.jsonl")
        os.symlink("../../..", self.proj / SID / "dir")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[1], "1")
        self.assertEqual([p.name for p in self.dst.rglob("*") if not p.is_dir()], [f"{SID}.jsonl"])

    def test_symlinked_session_dir_is_not_followed(self):
        (self.proj / f"{SID}.jsonl").write_text("main")
        (self.src / "other").mkdir()
        (self.src / "other" / "x.jsonl").write_text("x")
        os.symlink("../../other", self.proj / SID)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p.name for p in self.dst.rglob("*") if not p.is_dir()], [f"{SID}.jsonl"])

    def test_two_hits_are_refused(self):
        (self.proj / f"{SID}.jsonl").write_text("a")
        other = self.src / "projects" / "-work-other"
        other.mkdir()
        (other / f"{SID}.jsonl").write_text("b")
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("found 2", r.stderr)
        self.assertEqual(list(self.dst.rglob("*")), [])

    def test_no_hit_is_refused(self):
        r = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("found 0", r.stderr)


if __name__ == "__main__":
    unittest.main()
