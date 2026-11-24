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
        # PATH holds only what the entrypoint runs, so a language server on this machine doesn't count.
        for tool in ("seq", "sleep", "cat", "mktemp", "rm", "cp", "git", "jq", "grep", "node", "dirname", "uname", "true", "echo"):
            if shutil.which(tool):
                (self.tmp / "bin" / tool).symlink_to(shutil.which(tool))
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
                   PATH=str(self.tmp / "bin"))
        r = subprocess.run([shutil.which("bash"), str(self.tmp / "entrypoint.sh"), *(cmd or ("true",))], cwd=self.repo, env=env,
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
        self.assertIn(f"can't read the plugins in {self.opt}/settings.json; skipping it", r.stderr)

    def test_a_repo_settings_file_that_does_not_parse_is_skipped_and_starts(self):
        self.write(self.repo / ".claude" / "settings.local.json", '{"enabledPlugins": {},}')
        r = self.run_entry("echo", "started")
        self.assertIn(f"can't read the plugins in {self.repo}/.claude/settings.local.json; skipping it", r.stderr)
        self.assertEqual(r.stdout, "started\n")

    def test_a_settings_json_the_session_left_in_the_volume_installs_nothing_without_the_hosts(self):
        self.write(self.cfg / "settings.json", {
            "enabledPlugins": {"p@mkt": True},
            "extraKnownMarketplaces": {"mkt": {"source": {"source": "github", "repo": "o/r"}}}})
        self.run_entry()
        self.assertEqual(self.calls(), [])

    def test_a_plugin_the_user_loop_just_installed_draws_no_repo_warning(self):
        (self.cfg / "plugins").mkdir()
        (self.tmp / "bin" / "claude").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >>"$STUB_LOG"\n'
            'printf \'{"version":2,"plugins":{"p@mkt":[{"scope":"user"}]}}\' >"$CLAUDE_CONFIG_DIR/plugins/installed_plugins.json"\n')
        self.write(self.opt / "settings.json", {"enabledPlugins": {"p@mkt": True}})
        self.write(self.repo / ".claude" / "settings.json", {"enabledPlugins": {"p@mkt": True}})
        r = self.run_entry()
        self.assertEqual(self.calls(), ["plugin install p@mkt -s user"])
        self.assertNotIn("repo-declared", r.stderr)

    def test_a_repo_settings_file_that_does_not_parse_leaks_none_of_its_text_through_jq(self):
        (self.repo / ".claude" / "settings.json").write_bytes(b'{"enabledPlugins": "\xc2\x9b[31mxx"}')
        r = self.run_entry()
        self.assertNotIn("\x9b", r.stderr)
        self.assertIn("skipping it", r.stderr)

    def test_a_plugin_name_with_bidi_or_zero_width_characters_reaches_the_terminal_replaced(self):
        name = "a\u202eb\u200bc\u2066d@m"
        self.write(self.repo / ".claude" / "settings.json", {"enabledPlugins": {name: True}})
        r = self.run_entry()
        self.assertIn("repo-declared", r.stderr)
        for bad in ("\u202e", "\u200b", "\u2066"):
            self.assertNotIn(bad, r.stderr)

    LSP = KIT / "claude" / "lsp-plugins"

    def claude_args(self, *servers, installed=None, scope="user"):
        for s in servers:
            self.stub(s)
        if installed:
            (self.cfg / "plugins").mkdir(exist_ok=True)
            self.write(self.cfg / "plugins" / "installed_plugins.json",
                       {"version": 2, "plugins": {k: [{"scope": scope, "installPath": "/x"}] for k in installed}})
        self.run_entry("claude", "-p", "hi")
        (line,) = self.calls()
        return line

    def dirs(self, line):
        return [w for w in line.split() if w.startswith(f"{self.opt}/lsp-plugins/")]

    def test_claude_loads_each_vendored_lsp_plugin_whose_server_is_on_path(self):
        self.stage_lsp()
        line = self.claude_args("typescript-language-server", "gopls")
        self.assertEqual(self.dirs(line), [f"{self.opt}/lsp-plugins/gopls-lsp", f"{self.opt}/lsp-plugins/typescript-lsp"])
        self.assertTrue(line.endswith("-p hi"), line)

    def test_claude_loads_no_lsp_plugin_when_no_server_is_on_path(self):
        self.stage_lsp()
        self.assertEqual(self.dirs(self.claude_args()), [])

    def test_claude_skips_an_lsp_plugin_the_volume_has_installed(self):
        self.stage_lsp()
        line = self.claude_args("gopls", "pyright-langserver", installed=["gopls-lsp@claude-plugins-official"])
        self.assertEqual(self.dirs(line), [f"{self.opt}/lsp-plugins/pyright-lsp"])

    def test_claude_still_loads_an_lsp_plugin_the_volume_has_installed_at_local_scope_only(self):
        self.stage_lsp()
        line = self.claude_args("gopls", installed=["gopls-lsp@claude-plugins-official"], scope="local")
        self.assertEqual(self.dirs(line), [f"{self.opt}/lsp-plugins/gopls-lsp"])

    def test_claude_gets_tmp_as_a_working_directory(self):
        self.assertIn(" --add-dir /tmp ", f" {self.claude_args()} ")

    def test_another_command_gets_no_plugin_dir(self):
        self.stage_lsp()
        self.stub("gopls")
        r = self.run_entry("echo", "--plugin-dir")
        self.assertEqual(r.stdout, "--plugin-dir\n")
        self.assertEqual(self.calls(), [])

    def test_the_vendored_manifests_name_a_server_command_and_their_license(self):
        names = sorted(p.name for p in self.LSP.iterdir() if p.is_dir())
        self.assertEqual(names, ["gopls-lsp", "pyright-lsp", "rust-analyzer-lsp", "typescript-lsp"])
        for n in names:
            m = json.loads((self.LSP / n / ".claude-plugin" / "plugin.json").read_text())
            self.assertEqual(m["name"], n)
            self.assertTrue(all(s["command"] for s in m["lspServers"].values()))
            self.assertIn("Apache License", (self.LSP / n / "LICENSE").read_text())

    def stage_lsp(self):
        shutil.copytree(self.LSP, self.opt / "lsp-plugins")


if __name__ == "__main__":
    unittest.main()
