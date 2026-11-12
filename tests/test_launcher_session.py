"""Launcher: the session itself and the config dir mounts."""
import os
import shutil
import socket
import tempfile
import unittest

from harness import KIT
from launcher_case import LauncherCase


class Session(LauncherCase):
    def test_git_repo_hooks_read_only_exit_code_passed_through_quiet(self):
        r = self.new_repo("plain")
        res = self.launch(r, rc=7)
        self.assertEqual(res.rc, 7)
        self.assertMount(res, f"{r}/.git/hooks:{r}/.git/hooks:ro")
        self.assertEqual(res.text, "")

    def test_git_identity_is_the_worktrees_own_in_a_worktree_session(self):
        r = self.new_repo("ident")
        self.git(r, "config", "user.name", "Repo Person")
        self.git(r, "config", "user.email", "repo@example.com")
        self.git(r, "config", "extensions.worktreeConfig", "true")
        self.launch(r)  # the first launch makes the baseline the worktree's launch checks against
        wt = r / ".claude" / "worktrees" / "w1"
        self.git(r, "worktree", "add", "-q", str(wt), "-b", "w1")
        self.git(wt, "config", "--worktree", "user.email", "wt@example.com")
        res = self.launch(wt)
        self.assertEqual(res.rc, 0, res.text)
        self.assertEnv(res, "GIT_USER_NAME=Repo Person")
        self.assertEnv(res, "GIT_USER_EMAIL=wt@example.com")

    def test_non_git_dir_no_guard_exit_code_passed_through(self):
        d = self.tmp / "nogit"
        d.mkdir()
        res = self.launch(f"{d}/", rc=5)
        self.assertEqual(res.rc, 5)
        self.assertEqual(res.text, "")

    def test_no_docker_socket_ssh_dir_or_agent_whatever_the_host_sets(self):
        home = self.tmp / "home"
        (home / ".ssh").mkdir(parents=True)
        (home / ".ssh/id_ed25519").write_text("key")
        short = tempfile.mkdtemp(prefix="ag")  # a Unix socket path must fit macOS's 104 bytes
        self.addCleanup(shutil.rmtree, short, True)
        sock = os.path.join(short, "a.sock")
        agent = socket.socket(socket.AF_UNIX)
        self.addCleanup(agent.close)
        agent.bind(sock)
        res = self.launch(self.new_repo("creds"), env={"SSH_AUTH_SOCK": str(sock), "DOCKER_HOST": "unix:///var/run/docker.sock"})
        args = self.session(res)
        self.assertIsNotNone(args, "session did not run")
        for a in args:
            for bad in ("docker.sock", f"{home}/.ssh", "SSH_AUTH_SOCK", sock, f"{home}/.ssh/id_ed25519"):
                self.assertNotIn(bad, a)


class ConfigDir(LauncherCase):
    def test_setup_mounted_read_only_claude_md_as_user_level(self):
        cfg = self.tmp / "cfg"
        (cfg / "skills").mkdir(parents=True)
        (cfg / "hooks").mkdir()
        (cfg / "rules").mkdir()
        (cfg / "docs").mkdir()
        (cfg / "CLAUDE.md").touch()
        (cfg / "settings.json").touch()
        res = self.launch(self.new_repo("cfgrepo"), env={"COCON_CONFIG": str(cfg)})
        self.assertMount(res, f"{cfg}/skills:/home/claude/.claude/skills:ro")
        self.assertMount(res, f"{cfg}/hooks:/home/claude/.claude/hooks:ro")
        self.assertMount(res, f"{cfg}/rules:/home/claude/.claude/rules:ro")
        self.assertMount(res, f"{cfg}/docs:/home/claude/.claude/docs:ro")
        self.assertMount(res, f"{cfg}/CLAUDE.md:/home/claude/.claude/CLAUDE.md:ro")
        self.assertMount(res, f"{cfg}/settings.json:/opt/cocon/settings.json:ro")
        self.assertNoMount(res, f"{cfg}/agents:/home/claude/.claude/agents:ro")

    def test_defaults_to_home_claude(self):
        home = self.tmp / "home"
        (home / ".claude" / "skills").mkdir(parents=True)
        res = self.launch(self.new_repo("r"), env={"COCON_CONFIG": ""})
        self.assertMount(res, f"{home}/.claude/skills:/home/claude/.claude/skills:ro")

    def test_inside_the_project_checkout_read_only_but_git_and_worktrees(self):
        c = self.new_repo("cfgproj")
        (c / "skills").mkdir()
        (c / "CLAUDE.md").touch()
        res = self.launch(c, env={"COCON_CONFIG": str(c)})
        self.assertEqual(res.rc, 0, res.text)
        self.assertMount(res, f"{c}:{c}:ro")
        self.assertNoMount(res, f"{c}:{c}")
        self.assertMount(res, f"{c}/.git:{c}/.git")
        self.assertMount(res, f"{c}/.git/hooks:{c}/.git/hooks:ro")
        self.assertMount(res, f"{c}/.claude/worktrees:{c}/.claude/worktrees")
        self.assertTrue((c / ".claude" / "worktrees").is_dir())
        self.assertMount(res, f"{c}/skills:/home/claude/.claude/skills:ro")

    def test_symlinked_into_the_project_checkout_read_only(self):
        c = self.new_repo("cfgproj")
        (c / "skills").mkdir()
        cfg = self.tmp / "cfg"
        cfg.mkdir()
        (cfg / "skills").symlink_to(c / "skills")
        res = self.launch(c, env={"COCON_CONFIG": str(cfg)})
        self.assertMount(res, f"{c}:{c}:ro")

    def test_config_repo_worktree_copy_stays_writable(self):
        c = self.new_repo("cfgproj")
        (c / "skills").mkdir()
        (c / "skills" / "s").touch()
        self.git(c, "add", "-A")
        self.git(c, "commit", "-q", "-m", "cfg")
        self.git(c, "branch", "8-cfg")
        res = self.launch(c, "--worktree", "8-cfg", env={"COCON_CONFIG": str(c)})
        self.assertEqual(res.rc, 0, res.text)
        wt = f"{c}/.claude/worktrees/8-cfg"
        self.assertMount(res, f"{wt}:{wt}")
        self.assertNoMount(res, f"{wt}:{wt}:ro")

    def test_outside_the_project_no_extra_mounts(self):
        cfg = self.tmp / "cfg"
        (cfg / "skills").mkdir(parents=True)
        r = self.new_repo("elsewhere")
        res = self.launch(r, env={"COCON_CONFIG": str(cfg)})
        self.assertNoMount(res, f"{cfg}/skills:{cfg}/skills:ro")
        self.assertMount(res, f"{r}:{r}")


class CoconCheckout(LauncherCase):
    def checkout(self):
        """A fake cocon checkout: the launcher guards the cocon/ it runs from."""
        m = self.new_repo("cocon-main")
        (m / "cocon").mkdir()
        for f in ("cocon", "host-guard", "compose.yml"):
            (m / "cocon" / f).write_bytes((KIT / f).read_bytes())
            os.chmod(m / "cocon" / f, 0o755)
        shutil.copytree(KIT / "py", m / "cocon" / "py")
        shutil.copytree(KIT / "broker", m / "cocon" / "broker")  # the launcher reads the broker's placeholder table
        return m

    def test_main_checkout_read_only_but_git_and_worktrees_hooks_too(self):
        m = self.checkout()
        res = self.run_kit(m / "cocon" / "cocon", m, env={"STUB": "true"})
        self.assertEqual(res.rc, 0, res.err)
        run = self.session(res)
        self.assertIn(f"{m}:{m}:ro", run)
        self.assertNotIn(f"{m}:{m}", run)
        self.assertIn(f"{m}/.git:{m}/.git", run)
        self.assertIn(f"{m}/.claude/worktrees:{m}/.claude/worktrees", run)
        self.assertTrue((m / ".claude" / "worktrees").is_dir())
        self.assertIn(f"{m}/.git/hooks:{m}/.git/hooks:ro", run)

    def test_read_only_checkout_changed_on_the_host_isnt_reported(self):
        m = self.checkout()
        (m / ".claude" / "settings.json").parent.mkdir(exist_ok=True)
        stub = f"echo x >'{m}/cocon/new'; echo x >'{m}/.claude/settings.json'"
        res = self.run_kit(m / "cocon" / "cocon", m, env={"STUB": stub})
        self.assertEqual(res.rc, 0, res.err)
        self.assertEqual(res.out + res.err, "")

    def test_main_checkout_read_only_for_the_docker_sidecar_too(self):
        m = self.checkout()
        res = self.run_kit(m / "cocon" / "cocon", m, "--with", "docker", env={"STUB": "true"})
        self.assertEqual(res.rc, 0, res.err)
        self.assertCountEqual(self.sidecar_mounts(res), [
            f"{m}:{m}:ro", f"{m}/.git:{m}/.git", f"{m}/.claude/worktrees:{m}/.claude/worktrees",
            f"{m}/.git/hooks:{m}/.git/hooks:ro"])
        self.assertIn(f"{m}:{m}:ro", self.session(res))

    def test_another_repo_cocon_is_just_a_folder(self):
        m = self.checkout()
        r = self.new_repo("other")
        (r / "cocon").mkdir()
        res = self.run_kit(m / "cocon" / "cocon", r, env={"STUB": "true"})
        run = self.session(res)
        self.assertIn(f"{r}:{r}", run)
        self.assertNotIn(f"{r}/cocon:{r}/cocon:ro", run)

    def test_worktree_on_cocon_kit_writable_main_not_mounted(self):
        m = self.checkout()
        self.git(m, "add", "-A")
        self.git(m, "commit", "-q", "-m", "kit")
        self.git(m, "branch", "7-kit")
        res = self.run_kit(m / "cocon" / "cocon", m, "--worktree", "7-kit", env={"STUB": "true"})
        self.assertEqual(res.rc, 0, res.err)
        run = self.session(res)
        self.assertNotIn(f"{m}/.claude/worktrees/7-kit/cocon:{m}/.claude/worktrees/7-kit/cocon:ro", run)
        self.assertNotIn(f"{m}:{m}", run)


if __name__ == "__main__":
    unittest.main()
