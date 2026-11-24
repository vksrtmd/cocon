"""The container entrypoint run on this machine: a copy with /opt/cocon/ and the CA bundle moved under the test's
temp dir, a stub `claude` that records its argv, and the real jq. No Docker. A repo's settings files are written by
the session, so they are hostile input: they install nothing (docs/threat-model.md, Claude setup)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import KIT

STUB = '#!/bin/sh\nprintf "%s\\n" "$*" >>"$STUB_LOG"\n'


@unittest.skipUnless(shutil.which("jq"), "needs jq")
class Entrypoint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.opt = self.tmp / "opt"
        self.cfg = self.tmp / "home" / ".claude"
        self.repo = self.tmp / "repo"
        self.log = self.tmp / "claude.log"
        for d in (self.opt, self.cfg, self.repo / ".claude", self.tmp / "bin"):
            d.mkdir(parents=True)
        (self.opt / "broker-ca").mkdir()
        (self.opt / "broker-ca" / "ca.pem").write_text("ca\n")
        (self.opt / "container-notes.md").write_text("notes\n")
        (self.tmp / "roots.crt").write_text("roots\n")
        text = (KIT / "entrypoint.sh").read_text()
        for old, new in (("/opt/cocon/", f"{self.opt}/"), ("/etc/ssl/certs/ca-certificates.crt", f"{self.tmp}/roots.crt")):
            assert old in text, old
            text = text.replace(old, new)
        (self.tmp / "entrypoint.sh").write_text(text)
        self.stub("claude")

    def stub(self, name):
        p = self.tmp / "bin" / name
        p.write_text(STUB)
        p.chmod(0o755)

    def write(self, path, data):
        Path(path).write_text(data if isinstance(data, str) else json.dumps(data))

    def run_entry(self, *cmd):
        env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GIT_USER_NAME", "GIT_USER_EMAIL")}
        env.update(HOME=str(self.tmp / "home"), CLAUDE_CONFIG_DIR=str(self.cfg), STUB_LOG=str(self.log),
                   PATH=f"{self.tmp / 'bin'}:{os.environ['PATH']}")
        r = subprocess.run(["bash", str(self.tmp / "entrypoint.sh"), *(cmd or ("true",))], cwd=self.repo, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_repo_settings_install_nothing_and_warn_per_file_naming_the_plugins(self):
        cfg = {"enabledPlugins": {"evil@mkt": True, "off@mkt": False},
               "extraKnownMarketplaces": {"mkt": {"source": {"source": "github", "repo": "o/r"}}}}
        self.write(self.repo / ".claude" / "settings.json", cfg)
        self.write(self.repo / ".claude" / "settings.local.json", cfg)
        r = self.run_entry()
        self.assertEqual(self.calls(), [])
        lines = [l for l in r.stderr.splitlines() if "doesn't install repo-declared plugins" in l]
        self.assertEqual(len(lines), 2, r.stderr)
        self.assertIn("settings.json", lines[0])
        self.assertIn("settings.local.json", lines[1])
        for l in lines:
            self.assertIn('"evil@mkt"', l)
            self.assertNotIn("off@mkt", l)

    def test_a_plugin_name_with_control_characters_reaches_the_terminal_escaped(self):
        name = "a\x1b[31m\nb\x9bc\x7f@m"
        self.write(self.repo / ".claude" / "settings.local.json", {"enabledPlugins": {name: True}})
        r = self.run_entry()
        for bad in ("\x1b", "\x9b", "\x7f", "\r"):
            self.assertNotIn(bad, r.stderr)
        lines = [l for l in r.stderr.splitlines() if "repo-declared" in l]
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertIn("\\u001b", lines[0])
        self.assertEqual(len([l for l in r.stderr.splitlines() if l.startswith("b")]), 0, r.stderr)

    def test_a_repo_plugin_the_volume_has_installed_draws_no_warning(self):
        (self.cfg / "plugins").mkdir()
        self.write(self.cfg / "plugins" / "installed_plugins.json", {"plugins": {"mine@mkt": [{}]}})
        self.write(self.repo / ".claude" / "settings.json", {"enabledPlugins": {"mine@mkt": True}})
        r = self.run_entry()
        self.assertNotIn("repo-declared", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_user_settings_still_add_marketplaces_and_install(self):
        self.write(self.opt / "settings.json", {
            "enabledPlugins": {"p@mkt": True},
            "extraKnownMarketplaces": {"mkt": {"source": {"source": "github", "repo": "o/r"}}}})
        self.run_entry()
        self.assertEqual(self.calls(), ["plugin marketplace add o/r", "plugin install p@mkt -s user"])

    def test_a_user_settings_file_that_does_not_parse_installs_nothing_and_starts(self):
        self.write(self.opt / "settings.json", '{"enabledPlugins": {"p@mkt": true},}')
        r = self.run_entry()
        self.assertEqual(self.calls(), [])
        self.assertIn(f"can't read the plugins in {self.cfg}/settings.json; skipping it", r.stderr)

    def test_a_repo_settings_file_that_does_not_parse_is_skipped_and_starts(self):
        self.write(self.repo / ".claude" / "settings.local.json", '{"enabledPlugins": {},}')
        r = self.run_entry("echo", "started")
        self.assertIn(f"can't read the plugins in {self.repo}/.claude/settings.local.json; skipping it", r.stderr)
        self.assertEqual(r.stdout, "started\n")


if __name__ == "__main__":
    unittest.main()
