"""host-guard, through its CLI only, against throwaway repos: `check` flags risky git config
a session planted, `files-check` lists host entry points a session changed."""
import hashlib
import os
import subprocess
import unittest

from harness import KitCase

HEADER = ("cocon: the session changed host entry points; "
          "review them before your next host git or claude:")

# Each is a way to run code at the next host git command.
PLANTED = ["core.hooksPath=/tmp/h", "filter.x.clean=sh evil", "alias.st=!sh evil",
           "include.path=../inc", "includeIf.gitdir:/a/.path=../inc", "credential.helper=!sh evil",
           "remote.origin.uploadpack=sh evil", "diff.x.textconv=sh evil", "pager.log=sh evil",
           "protocol.ext.allow=always", "url.ext::sh evil.insteadOf=https://github.com/",
           "submodule.x.update=!sh evil", "core.alternateRefsCommand=sh evil",
           "gpg.ssh.defaultKeyCommand=sh evil", "mergetool.x.cmd=sh evil", "trailer.x.command=sh evil",
           "remote.origin.url=/tmp/evil.git", "remote.origin.pushurl=/tmp/evil.git",
           "remote.origin.push=+refs/heads/x:refs/heads/main", "remote.origin.mirror=true"]


class HostGuard(KitCase):
    def snapshot(self, repo):
        r = self.run_kit("host-guard", "snapshot", repo)
        self.assertEqual(r.rc, 0, f"snapshot exited {r.rc}: {r.err}")
        snap = self.tmp / "snap"
        snap.write_text(r.out)
        return snap

    def check(self, repo, snap):
        return self.run_kit("host-guard", "check", repo, snap)

    def guarded(self, repo, act):
        snap = self.snapshot(repo)
        act()
        return self.check(repo, snap), snap

    def assertQuiet(self, r):
        self.assertEqual((r.rc, r.err), (0, ""))

    def assertFlagged(self, r, *texts):
        self.assertEqual(r.rc, 1, r.err)
        for t in texts:
            self.assertIn(t, r.err)

    def apply_fix(self, r):
        fix = r.err.rsplit("remove it: ", 1)[1]
        subprocess.run(["bash", "-c", fix], check=True, env=self.env)

    def repo_with_submodule(self, name):
        r, src = self.new_repo(name), self.new_repo(name + "src")
        self.git(r, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "sub")
        return r

    # git config

    def test_clean_repo_stays_quiet(self):
        r = self.new_repo("clean")
        self.assertQuiet(self.guarded(r, lambda: None)[0])

    def test_planted_fsmonitor_is_flagged_with_a_working_fix(self):
        r = self.new_repo("fsmonitor")
        res, snap = self.guarded(r, lambda: self.git(r, "config", "core.fsmonitor", "touch /tmp/pwned"))
        self.assertFlagged(res, "core.fsmonitor=touch /tmp/pwned", f"--file {r}/.git/config")
        self.apply_fix(res)
        self.assertQuiet(self.check(r, snap))

    def test_planted_risky_keys_are_flagged(self):
        for i, kv in enumerate(PLANTED):
            key, value = kv.split("=", 1)
            with self.subTest(key):
                r = self.new_repo(f"fam{i}")
                res, _ = self.guarded(r, lambda: self.git(r, "config", key, value))
                self.assertFlagged(res, f"{key.lower()}={value}")  # git lists keys lowercased

    def test_keys_that_redirect_host_git_credentials_are_flagged(self):
        r = self.new_repo("http")

        def plant():
            for key, value in [("http.proxy", "http://evil:8080"), ("http.sslVerify", "false"),
                               ("http.https://github.com/.extraHeader", "X: y"),
                               ("remote.origin.proxy", "http://evil:8080")]:
                self.git(r, "config", key, value)
        res, _ = self.guarded(r, plant)
        self.assertFlagged(res, "http.proxy=http://evil:8080", "http.sslverify=false",
                           "http.https://github.com/.extraheader=X: y", "remote.origin.proxy=http://evil:8080")

    def test_plain_alias_stays_quiet(self):
        r = self.new_repo("alias")
        self.assertQuiet(self.guarded(r, lambda: self.git(r, "config", "alias.st", "status"))[0])

    def test_pre_existing_entry_left_alone_stays_quiet(self):
        r = self.new_repo("husky")
        self.git(r, "config", "core.hooksPath", ".husky")
        self.assertQuiet(self.guarded(r, lambda: self.git(r, "config", "user.name", "changed"))[0])

    def test_changed_value_of_a_pre_existing_entry_is_flagged(self):
        r = self.new_repo("changed")
        self.git(r, "config", "core.pager", "less")
        res, _ = self.guarded(r, lambda: self.git(r, "config", "core.pager", "sh evil"))
        self.assertFlagged(res, "core.pager=sh evil")

    def test_key_matching_is_case_insensitive(self):
        r = self.new_repo("case")

        def plant():
            with open(r / ".git/config", "a") as f:
                f.write('[CORE]\n\tHooksPath = /tmp/h\n[filter "LFS"]\n\tClean = sh evil\n')
        res, _ = self.guarded(r, plant)
        self.assertFlagged(res, "core.hookspath=/tmp/h", "filter.LFS.clean=sh evil")

    def test_key_in_a_submodule_config_is_flagged_fix_names_its_file(self):
        r = self.repo_with_submodule("super")
        res, _ = self.guarded(r, lambda: self.git(r / "sub", "config", "core.fsmonitor", "sh evil"))
        self.assertFlagged(res, "core.fsmonitor=sh evil", f"--file {r}/.git/modules/sub/config")

    def test_gitdirs_lists_the_common_dir_and_submodule_git_dirs(self):
        r = self.repo_with_submodule("super")
        res = self.run_kit("host-guard", "gitdirs", r)
        self.assertEqual(res.out, f"{r}/.git\n{r}/.git/modules/sub\n")

    def linked_worktree(self):
        r = self.new_repo("wtmain")
        self.git(r, "config", "extensions.worktreeConfig", "true")
        self.git(r, "worktree", "add", "-q", str(r / ".claude/worktrees/wt"))
        return r, r / ".claude/worktrees/wt"

    def test_key_in_a_linked_worktrees_config_worktree_is_flagged_checked_from_it(self):
        r, wt = self.linked_worktree()
        res, _ = self.guarded(wt, lambda: self.git(wt, "config", "--worktree", "core.fsmonitor", "sh evil"))
        self.assertFlagged(res, "core.fsmonitor=sh evil", f"--file {r}/.git/worktrees/wt/config.worktree")

    def test_key_in_the_main_worktrees_config_worktree_is_flagged(self):
        r, _ = self.linked_worktree()
        res, _ = self.guarded(r, lambda: self.git(r, "config", "--worktree", "core.fsmonitor", "sh evil"))
        self.assertFlagged(res, f"--file {r}/.git/config.worktree")

    def test_multi_line_value_cant_hide_behind_a_pre_existing_first_line(self):
        r = self.new_repo("multiline")
        self.git(r, "config", "core.pager", "less")
        res, _ = self.guarded(r, lambda: self.git(r, "config", "core.pager", "less\nsh evil"))
        self.assertFlagged(res, "core.pager=less\\nsh evil")

    def test_fix_for_a_planted_second_value_removes_only_that_value(self):
        r = self.new_repo("multivalue")
        self.git(r, "config", "credential.helper", "osxkeychain")
        res, snap = self.guarded(r, lambda: self.git(r, "config", "--add", "credential.helper", "!sh evil"))
        self.assertFlagged(res, "credential.helper=!sh evil")
        self.apply_fix(res)
        self.assertQuiet(self.check(r, snap))
        self.assertEqual(self.git(r, "config", "--get-all", "credential.helper", out=True), "osxkeychain\n",
                         "user's own helper is gone")

    def test_a_second_copy_of_a_trusted_entry_is_flagged(self):
        """counted as comm counts: a duplicate of a trusted helper is an entry added"""
        r = self.new_repo("dup")
        self.git(r, "config", "credential.helper", "store")
        res, _ = self.guarded(r, lambda: self.git(r, "config", "--add", "credential.helper", "store"))
        self.assertFlagged(res, "credential.helper=store")

    def test_a_deep_entry_point_tree_is_digested_or_refused_never_a_traceback(self):
        d = self.tmp / "deep"
        (d / ".claude").mkdir(parents=True)
        fd = os.open(d / ".claude", os.O_RDONLY | os.O_DIRECTORY)
        for _ in range(1100):  # deeper than Python's recursion limit; made through fds, as the path outgrows PATH_MAX
            os.mkdir("a", dir_fd=fd)
            fd, old = os.open("a", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd), fd
            os.close(old)
        os.close(fd)
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        r = self.run_kit("host-guard", "files-snapshot", d)
        self.assertIn(r.rc, (0, 2), r.err)  # 2 where the path outgrows PATH_MAX (macOS)

    def test_unreadable_config_file_is_reported_other_files_still_checked_exit_2(self):
        r = self.repo_with_submodule("broken")
        snap = self.snapshot(r)
        self.git(r, "config", "core.fsmonitor", "sh evil")
        with open(r / ".git/modules/sub/config", "a") as f:
            f.write("[broken\n")
        res = self.check(r, snap)
        self.assertEqual(res.rc, 2, res.err)
        self.assertIn("core.fsmonitor=sh evil", res.err)
        self.assertIn(f"can't read {r}/.git/modules/sub/config", res.err)

    def test_missing_snapshot_file_is_an_error_not_a_pass(self):
        r = self.new_repo("nosnap")
        self.assertEqual(self.check(r, self.tmp / "nope").rc, 2)

    # host entry points

    def files_session(self, d, act):
        s = self.run_kit("host-guard", "files-snapshot", d)
        self.assertEqual(s.rc, 0, f"files-snapshot exited {s.rc}: {s.err}")
        snap = self.tmp / "fsnap"
        snap.write_text(s.out)
        act()
        return self.run_kit("host-guard", "files-check", d, snap)

    def assertPaths(self, r, *paths):
        self.assertEqual(r.rc, 1, r.err)
        self.assertEqual(r.err, "".join(f"{p}\n" for p in (HEADER, *paths)))

    def entry_dir(self):
        d = self.tmp / "entry"
        for sub in (".husky/_", ".claude", ".lefthook", "src"):
            (d / sub).mkdir(parents=True)
        for f in (".husky/pre-push", ".mcp.json", "lefthook.yml", ".lefthook/x"):
            (d / f).write_text("a\n")
        return d

    def kit_dir(self, name, with_launcher):
        d = self.tmp / name
        (d / "cocon").mkdir(parents=True)
        (d / "cocon/compose.yml").write_text("a\n")
        if with_launcher:
            (d / "cocon/cocon").write_text("a\n")
        return d

    def test_files_kit_change_in_a_cocon_checkout_is_reported(self):
        d = self.kit_dir("kit", True)
        self.assertPaths(self.files_session(d, lambda: (d / "cocon/compose.yml").write_text("b\n")),
                         f"{d}/cocon/compose.yml")
        self.assertQuiet(self.files_session(d, lambda: None))

    def test_files_a_cocon_folder_in_another_repo_isnt_an_entry_point(self):
        d = self.kit_dir("other", False)
        self.assertQuiet(self.files_session(d, lambda: (d / "cocon/compose.yml").write_text("b\n")))

    def test_files_entry_points_edited_added_or_removed_are_reported_one_per_line(self):
        d = self.entry_dir()

        def act():
            with open(d / ".husky/pre-push", "a") as f:
                f.write("b\n")
            (d / ".claude/settings.local.json").write_text("{}\n")
            (d / ".mcp.json").unlink()
            for f in (".pre-commit-config.yaml", ".lefthook.yml", "lefthook-local.yml"):
                (d / f).write_text("a\n")
            (d / ".lefthook/x").write_text("b\n")
        self.assertPaths(self.files_session(d, act),
                         f"{d}/.claude/settings.local.json", f"{d}/.husky/pre-push", f"{d}/.lefthook.yml",
                         f"{d}/.lefthook/x", f"{d}/.mcp.json", f"{d}/.pre-commit-config.yaml",
                         f"{d}/lefthook-local.yml")

    def test_files_husky_underscore_claude_worktrees_and_the_rest_arent_entry_points(self):
        d = self.entry_dir()

        def act():
            (d / ".claude/worktrees/x").mkdir(parents=True)
            for f in (".claude/worktrees/x/f", ".husky/_/h", "src/f", "Makefile"):
                (d / f).write_text("a\n")
        self.assertQuiet(self.files_session(d, act))

    def test_files_no_entry_points_stays_quiet_and_so_does_an_unchanged_run(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertQuiet(self.files_session(empty, lambda: None))
        self.assertQuiet(self.files_session(self.entry_dir(), lambda: None))

    def test_files_a_symlink_counts_by_its_target_path_not_its_content(self):
        d, outside = self.entry_dir(), self.tmp / "outside"
        outside.write_text("a\n")
        (d / ".claude/link").symlink_to(outside)
        self.assertQuiet(self.files_session(d, lambda: outside.write_text("b\n")))

        def retarget():
            (d / ".claude/link").unlink()
            (d / ".claude/link").symlink_to(self.tmp / "elsewhere")
        self.assertPaths(self.files_session(d, retarget), f"{d}/.claude/link")

    def test_files_removing_the_last_entry_point_reports_just_that_path(self):
        last = self.tmp / "last"
        last.mkdir()
        (last / ".mcp.json").write_text("a\n")
        self.assertPaths(self.files_session(last, (last / ".mcp.json").unlink), f"{last}/.mcp.json")

    def test_files_an_edit_committed_in_the_session_is_still_reported(self):
        r = self.new_repo("committed")
        (r / ".husky").mkdir()
        (r / ".husky/pre-push").write_text("a\n")
        self.git(r, "add", "-A")
        self.git(r, "commit", "-qm", "h")

        def act():
            (r / ".husky/pre-push").write_text("a\nb\n")
            self.git(r, "commit", "-qam", "evil")
        self.assertPaths(self.files_session(r, act), f"{r}/.husky/pre-push")

    def test_files_an_unreadable_entry_point_is_an_error_not_a_pass(self):
        d = self.entry_dir()
        secret = d / ".claude/secret"
        secret.write_text("a\n")
        secret.chmod(0)
        self.addCleanup(secret.chmod, 0o600)
        if os.access(secret, os.R_OK):
            self.skipTest("running as root, which reads mode 000 files")
        self.assertEqual(self.run_kit("host-guard", "files-snapshot", d).rc, 2)

    def test_files_missing_snapshot_is_an_error_not_a_pass(self):
        self.assertEqual(self.run_kit("host-guard", "files-check", self.entry_dir(), self.tmp / "nope").rc, 2)


class HostGuardGit(KitCase):
    """`host-guard git REPO [--worktree NAME] [--baseline FILE] -- <git args>`."""

    def setUp(self):
        super().setUp()
        self.repo = self.new_repo("main")
        self.marker = self.tmp / "ran"
        self.hook = f"touch {self.marker}"

    def run_git(self, *args, worktree=None, baseline=None):
        pre = ["--worktree", worktree] if worktree else []
        pre += ["--baseline", baseline] if baseline else []
        return self.run_kit("host-guard", "git", self.repo, *pre, "--", *args)

    def add_worktree(self, name):
        self.git(self.repo, "worktree", "add", "-q", f".claude/worktrees/{name}", "-b", name)
        return self.repo / ".claude/worktrees" / name

    def test_runs_git_in_the_repo_and_in_a_worktree(self):
        self.add_worktree("w")
        self.assertEqual(self.run_git("rev-parse", "--show-toplevel").out.strip(), str(self.repo))
        r = self.run_git("rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD", worktree="w")
        self.assertEqual((r.rc, r.out.split()), (0, [f"{self.repo}/.claude/worktrees/w", "w"]), r.err)

    def test_refuses_a_worktree_pointing_at_another_repo(self):
        wt = self.add_worktree("w")
        other = self.new_repo("other")
        self.git(other, "config", "core.fsmonitor", self.hook)
        (wt / ".git").write_text(f"gitdir: {other}/.git\n")
        r = self.run_git("status", worktree="w")
        self.assertEqual(r.rc, 2, r.err)
        self.assertFalse(self.marker.exists())

    def test_refuses_a_commondir_record_pointing_elsewhere(self):
        self.add_worktree("w")
        other = self.new_repo("other")
        (self.repo / ".git/worktrees/w/commondir").write_text(f"{other}/.git\n")
        self.assertEqual(self.run_git("status", worktree="w").rc, 2)

    def test_a_fifo_or_unreadable_pointer_file_refuses_without_hanging(self):
        """the worktree's commondir, .git file or the main repo's gitdir record a fifo, or unreadable: exit 2, no hang"""
        wt = self.add_worktree("w")
        adm = self.repo / ".git/worktrees/w"
        for f in (adm / "commondir", wt / ".git", adm / "gitdir"):
            with self.subTest(f=f.name):
                saved = f.read_bytes()
                f.unlink()
                os.mkfifo(f)
                r = self.run_git("status", worktree="w")
                self.assertEqual(r.rc, 2, r.err)
                f.unlink()
                f.write_bytes(saved)
        if os.geteuid() != 0:
            rec = adm / "gitdir"
            rec.chmod(0)
            self.addCleanup(rec.chmod, 0o644)
            r = self.run_kit("host-guard", "top", wt)
            self.assertEqual(r.rc, 2, r.err)

    def test_an_include_of_a_fifo_refuses_without_hanging(self):
        """include.path to a fifo blocks every git that reads the repo's config: exit 2 at the timeout, never a hang;
        snapshot and check still name the entry, and its fix works when pasted inside the repo"""
        snap = self.tmp / "snap"
        snap.write_text(self.run_kit("host-guard", "snapshot", self.repo).out)
        os.mkfifo(self.repo / ".git/pipe")
        self.git(self.repo, "config", "include.path", "pipe")
        env = {"COCON_GIT_TIMEOUT": "1"}
        for args in (["git", self.repo, "--", "status"], ["top", self.repo], ["gitdirs", self.repo],
                     ["config", self.repo, "--list"]):
            with self.subTest(args[0]):
                r = self.run_kit("host-guard", *args, env=env)
                self.assertEqual(r.rc, 2, r.err)
                self.assertIn(f"git didn't answer reading {self.repo}/.git's config", r.err)
        r = self.run_kit("host-guard", "snapshot", self.repo, env=env)
        self.assertEqual(r.rc, 2, r.err)
        self.assertIn(f"{self.repo}/.git/config\tinclude.path\tpipe\n", r.out)
        r = self.run_kit("host-guard", "check", self.repo, snap, env=env)
        self.assertEqual(r.rc, 2, r.err)
        self.assertIn("include.path=pipe", r.err)
        fix = r.err.rsplit("remove it: ", 1)[1]
        subprocess.run(["bash", "-c", fix], check=True, env=self.env, cwd=self.repo, timeout=30)
        self.assertEqual(self.run_kit("host-guard", "check", self.repo, snap, env=env).rc, 0)

    def test_refuses_an_unknown_worktree(self):
        self.assertEqual(self.run_git("status", worktree="nope").rc, 2)

    def fake_repo(self):
        fake = self.new_repo("fake")
        self.git(fake, "config", "core.fsmonitor", self.hook)
        return fake

    def test_core_worktree_cant_make_a_session_made_repo_the_main_checkout(self):
        evil = self.repo / "evil"
        evil.mkdir()
        self.git(evil, "init", "-q")
        self.git(self.repo, "config", "core.worktree", str(evil))
        for sub in ("gitdirs", "snapshot"):
            r = self.run_kit("host-guard", sub, self.repo)
            self.assertEqual(r.rc, 2, (sub, r.out, r.err))
            self.assertNotIn("evil", r.out)

    def test_refuses_a_worktree_outside_claude_worktrees(self):
        elsewhere = self.tmp / "elsewhere"
        self.git(self.repo, "worktree", "add", "-q", str(elsewhere), "-b", "out")
        r = self.run_kit("host-guard", "gitdirs", elsewhere)
        self.assertEqual(r.rc, 2, r.out)

    def test_refuses_a_planted_commondir_in_the_main_git_dir(self):
        fake = self.fake_repo()
        (self.repo / ".git/commondir").write_text(f"{fake}/.git\n")
        for sub in (("gitdirs",), ("snapshot",), ("git", "--", "status")):
            r = self.run_kit("host-guard", sub[0], self.repo, *sub[1:])
            self.assertEqual(r.rc, 2, (sub, r.out, r.err))
            self.assertNotIn("fake", r.out)
        self.assertFalse(self.marker.exists())

    def test_refuses_a_linked_worktree_as_repo_without_worktree_name(self):
        wt = self.add_worktree("w")
        r = self.run_kit("host-guard", "git", wt, "--", "status")
        self.assertEqual(r.rc, 2, r.err)

    def test_a_worktree_file_pointing_at_a_fake_admin_dir_cant_move_the_main_repo(self):
        wt = self.add_worktree("w")
        fake = self.fake_repo()
        adm = fake / ".git/worktrees/w"
        adm.mkdir(parents=True)
        (adm / "gitdir").write_text(f"{wt}/.git\n")
        (adm / "commondir").write_text("../..\n")
        (adm / "HEAD").write_text("ref: refs/heads/master\n")
        (wt / ".git").write_text(f"gitdir: {adm}\n")
        r = self.run_kit("host-guard", "gitdirs", wt)
        self.assertEqual((r.rc, r.out), (2, ""), r.err)  # git's answer differs from the path's: refuse
        self.assertEqual(self.run_git("status", worktree="w").rc, 2)
        self.assertFalse(self.marker.exists())

    # Read-only helpers the launcher and workflow-push call instead of raw git.

    def test_baseline_path_is_keyed_by_the_main_checkouts_folder_and_path(self):
        main = "/Some Where/My.Repo"
        key = hashlib.sha1(main.encode()).hexdigest()[:12]
        r = self.run_kit("host-guard", "baseline-path", main)
        self.assertEqual(r.out, f"{self.tmp}/xdg/cocon/git-baseline/my-repo-{key}.snap\n", r.err)

    def test_pin_prints_the_pinned_repo_id_or_nothing(self):
        self.assertEqual(self.run_kit("host-guard", "pin", self.repo).out, "")
        cfg = self.tmp / "xdg/cocon"
        cfg.mkdir(parents=True)
        (cfg / "pins").write_text(f"/other\tx__y\n{self.repo}\tacme__widget\n")
        r = self.run_kit("host-guard", "pin", self.repo)
        self.assertEqual((r.rc, r.out), (0, "acme__widget\n"), r.err)

    def test_top_gives_the_checkout_root_from_a_subdir_and_a_worktree(self):
        wt = self.add_worktree("w")
        (self.repo / "sub").mkdir()
        self.assertEqual(self.run_kit("host-guard", "top", self.repo / "sub").out, f"{self.repo}\n")
        self.assertEqual(self.run_kit("host-guard", "top", wt).out, f"{wt}\n")
        self.assertEqual(self.run_kit("host-guard", "top", self.tmp).rc, 2)

    def test_config_reads_the_main_repos_config_and_refuses_a_misdirected_one(self):
        self.git(self.repo, "config", "remote.origin.url", "https://github.com/acme/widget.git")
        r = self.run_kit("host-guard", "config", self.repo, "remote.origin.url")
        self.assertEqual((r.rc, r.out), (0, "https://github.com/acme/widget.git\n"), r.err)
        self.assertEqual(self.run_kit("host-guard", "config", self.repo, "no.such").rc, 1)
        evil = self.repo / "evil"
        evil.mkdir()
        self.git(evil, "init", "-q")
        self.git(self.repo, "config", "core.worktree", str(evil))
        self.assertEqual(self.run_kit("host-guard", "config", self.repo, "remote.origin.url").rc, 2)

    def test_check_ref_accepts_branch_names_and_refuses_the_rest_whatever_the_cwd_repo_says(self):
        for name, rc in (("45-auth", 0), ("bad..name", 1), ("a b", 1), ("-x", 1), ("@{-1}", 1)):
            with self.subTest(name):
                self.assertEqual(self.run_kit("host-guard", "check-ref", name, cwd=self.repo).rc, rc)

    def test_refuses_an_untrusted_risky_entry_and_names_it(self):
        self.git(self.repo, "config", "remote.origin.url", "/tmp/evil.git")
        r = self.run_git("status")
        self.assertEqual(r.rc, 2)
        self.assertIn("remote.origin.url=/tmp/evil.git", r.err)
        self.assertIn(f"{self.repo}/.git/config", r.err)
        self.assertIn(f"cocon --trust-git {self.repo}", r.err)

    def test_runs_when_every_risky_entry_is_in_the_baseline(self):
        self.git(self.repo, "config", "remote.origin.url", "/tmp/ok.git")
        snap = self.tmp / "base"
        snap.write_text(self.run_kit("host-guard", "snapshot", self.repo).out)
        self.assertEqual(self.run_git("remote", "get-url", "origin", baseline=snap).out.strip(), "/tmp/ok.git")
        self.git(self.repo, "config", "remote.origin.pushurl", "/tmp/evil.git")
        self.assertEqual(self.run_git("status", baseline=snap).rc, 2)

    def test_the_users_global_config_still_applies(self):
        gc = self.tmp / "gitconfig"
        gc.write_text("[credential]\n\thelper = store\n")
        r = self.run_kit("host-guard", "git", self.repo, "--", "config", "credential.helper",
                         env={"GIT_CONFIG_GLOBAL": str(gc)})
        self.assertEqual((r.rc, r.out.strip()), (0, "store"), r.err)

    OVERRIDES = (("core.sshCommand", "ssh"), ("core.pager", "cat"), ("core.editor", "true"), ("core.askpass", "true"))

    def effective(self, key, glob=None):
        """The value host git sees for KEY with a trusted repo-local `sh evil` for every override."""
        for k, _ in self.OVERRIDES:
            self.git(self.repo, "config", k, "sh evil")
        snap = self.tmp / "base"
        snap.write_text(self.run_kit("host-guard", "snapshot", self.repo).out)
        env = {}
        if glob:
            gc = self.tmp / "gitconfig"
            gc.write_text(glob)
            env["GIT_CONFIG_GLOBAL"] = str(gc)
        r = self.run_kit("host-guard", "git", self.repo, "--baseline", snap, "--", "config", "--get", key, env=env)
        self.assertEqual(r.rc, 0, r.err)
        return r.out.strip()

    def test_each_override_neutralises_a_repo_local_value(self):
        for key, neutral in self.OVERRIDES:
            with self.subTest(key):
                self.assertEqual(self.effective(key), neutral)

    def test_each_override_keeps_the_users_global_value(self):
        for key, _ in self.OVERRIDES:
            with self.subTest(key):
                section, name = key.split(".")
                glob = f"[{section}]\n\t{name} = from-global\n"
                self.assertEqual(self.effective(key, glob), "from-global")

    def test_a_trusted_fsmonitor_still_does_not_run(self):
        self.git(self.repo, "config", "core.fsmonitor", self.hook)
        snap = self.tmp / "base"
        snap.write_text(self.run_kit("host-guard", "snapshot", self.repo).out)
        self.assertEqual(self.run_git("status", baseline=snap).rc, 0)
        self.assertFalse(self.marker.exists())

    def test_a_planted_hook_does_not_run(self):
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text(f"#!/bin/sh\n{self.hook}\n")
        hook.chmod(0o755)
        r = self.run_git("commit", "-q", "--allow-empty", "-m", "x")
        self.assertEqual(r.rc, 0, r.err)
        self.assertFalse(self.marker.exists(), "pre-commit ran")


if __name__ == "__main__":
    unittest.main()
