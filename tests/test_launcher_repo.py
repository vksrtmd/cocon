"""Launcher: repo identity (allowlist, volumes, slugs), --destroy and --doctor."""
import os
import re
import shutil
import unittest

from harness import ROOT, import_kit
from launcher_case import COMPOSE, LauncherCase


class Destroy(LauncherCase):
    def test_declined_lists_volumes_removes_nothing(self):
        """--destroy declined, or answered with anything but y or yes: lists per-repo volumes, removes nothing"""
        r = self.new_repo("dest")
        for answer in ("n", "yeah no", "yikes"):
            with self.subTest(answer=answer):
                res = self.launch(r, "--destroy", answer=answer,
                                  env={"STUB_VOLUMES": "cocon-dest_pgdata cocon-dest-node-modules"})
                self.assertLaunch(res, 1, "cocon-dest_pgdata", "cocon-dest-node-modules", "aborted")
                self.assertNoCall(res, " down")
                self.assertNoCall(res, "volume rm")

    def test_confirmed_down_then_removes_only_this_repos_volumes(self):
        """--destroy confirmed (yes, any case and spacing): down, then removes only this repo's volumes"""
        r = self.new_repo("dest")
        vols = "cocon-dest_pgdata cocon-dest-node-modules cocon-dest-config cocon-dest-npm cocon-dest-cargo"
        override = self.tmp / "xdg/cocon/sidecar/dest.json"
        self.assertEqual(self.launch(r, "--with", "docker").rc, 0)
        self.assertTrue(override.is_file())
        res = self.launch(r, "--destroy", answer=" YES ", env={"STUB_VOLUMES": vols})
        self.assertLaunch(res, 0, "deletes the repo's Claude transcripts")
        self.assertFalse(override.exists(), "the sidecar's override is left")
        self.assertCall(res, f"compose -f {COMPOSE} -p cocon-dest --profile * down")
        self.assertCall(res, f"volume rm {vols}")

    def test_on_a_worktree_keeps_config_and_caches(self):
        r = self.new_repo("dest")
        self.launch(r)  # the first launch makes the baseline destroy checks against
        self.git(r, "worktree", "add", "-q", str(r / ".claude" / "worktrees" / "w1"), "-b", "w1")
        res = self.launch(r, "--worktree", "w1", "--destroy", answer="y",
                          env={"STUB_VOLUMES": "cocon-dest-config cocon-dest-cache"})
        self.assertEqual(res.rc, 0, res.text)
        self.assertNoCall(res, "volume rm")
        self.assertNotIn("transcripts", res.text)

    def test_on_a_github_repo_volumes_named_after_the_repo_id(self):
        r = self.new_repo("dest2")
        self.git(r, "remote", "add", "origin", "https://github.com/Acme/Dest2.git")
        res = self.launch(r, "--destroy", answer="n",
                          env={"STUB_VOLUMES": "cocon-acme__dest2_pgdata cocon-acme__dest2-node-modules"})
        self.assertIn("cocon-acme__dest2_pgdata", res.text)
        self.assertIn("cocon-acme__dest2-node-modules", res.text)


class RepoIdentity(LauncherCase):
    def test_github_repo_allowlist_file_created_and_passed_to_compose(self):
        r = self.new_repo("allow")
        self.git(r, "remote", "add", "origin", "https://github.com/acme/widget.git")
        res = self.launch(r)
        self.assertTrue((self.tmp / "xdg" / "cocon" / "allow" / "acme__widget.txt").is_file())
        self.assertIn("acme__widget.txt", [c.env.get("REPO_ALLOW") for c in res.calls])

    def test_compose_gets_the_projects_variables_whatever_the_users_env_holds(self):
        r = self.new_repo("allow")
        self.git(r, "remote", "add", "origin", "https://github.com/acme/widget.git")
        bogus = {v: "/evil" for v in ("ALLOW_DIR", "REPO_ALLOW", "REPO_VOLUMES", "LOG_DIR", "LOG_NAME", "COCON_TAG")}
        res = self.launch(r, "--with", "python", env={**bogus, "WITH_RUST": "1"})
        self.assertEqual(res.rc, 0, res.text)
        calls = [c for c in res.calls if c.args[:2] == ["compose", "-f"]]
        self.assertTrue(calls)
        for c in calls:
            self.assertEqual(c.env["ALLOW_DIR"], str(self.tmp / "xdg" / "cocon" / "allow"), c)
            self.assertEqual(c.env["REPO_ALLOW"], "acme__widget.txt", c)
            self.assertEqual(c.env["REPO_VOLUMES"], "cocon-acme__widget", c)
            self.assertEqual(c.env["LOG_NAME"], "acme__widget.log", c)
            self.assertEqual((c.env["COCON_TAG"], c.withs), ("python", ["python"]), c)

    def test_repo_volumes_keyed_by_owner_repo_shared_by_worktrees(self):
        r = self.new_repo("vols")
        self.git(r, "remote", "add", "origin", "git@github.com:Acme/Widget.git")
        wt = r / ".claude" / "worktrees" / "wt"
        self.git(r, "worktree", "add", "-q", str(wt), "-b", "wt")
        for d in (r, wt):
            res = self.launch(d)
            for v in ("config", "cache", "npm"):
                self.assertCall(res, f"volume create cocon-Acme__Widget-{v}")
            self.assertIn("cocon-Acme__Widget", [c.env.get("REPO_VOLUMES") for c in res.calls])

    def test_every_repo_volume_compose_mounts_is_created_without_extras(self):
        """repo volumes: a launch with no extras creates each one compose.yml mounts in every session (an extra's own,
        made only with it, is mounted by the launcher instead)"""
        mounted = re.findall(r'\$\{REPO_VOLUMES:\?set by cocon\}-([a-z]+)", external: true',
                             COMPOSE.read_text())
        self.assertTrue(mounted)
        res = self.launch(self.new_repo("vols"))
        for v in mounted:
            self.assertCall(res, f"volume create cocon-vols-{v}")

    def test_repo_volumes_without_github_origin_main_checkouts_folder_name(self):
        r = self.new_repo("Local.Repo")
        lw = r / ".claude/worktrees/lw"
        self.git(r, "worktree", "add", "-q", str(lw), "-b", "lw")
        for d in (r, lw):
            res = self.launch(d)
            self.assertCall(res, "volume create cocon-local-repo-config")
            self.assertIn("cocon-local-repo", [c.env.get("REPO_VOLUMES") for c in res.calls])

    def test_worktree_by_path_slug_is_repo_worktree(self):
        """worktree launched by path: slug is <repo>--<worktree>, plain checkout keeps its folder name"""
        r = self.new_repo("slugs")
        w = r / ".claude/worktrees/fix-x"
        self.git(r, "worktree", "add", "-q", str(w), "-b", "fix-x")
        self.assertProject(self.launch(w), "slugs--fix-x")
        self.assertProject(self.launch(r), "slugs")

    def test_github_origin_slug_is_the_repo_id_whatever_the_folder(self):
        """github origin: slug is <owner>__<repo>, so two repos in same-named folders stay apart, by one owner or two"""
        for o, n in (("acme", "foo"), ("acme", "bar"), ("other", "foo")):
            d = self.new_repo(f"{o}-{n}/api")
            self.git(d, "remote", "add", "origin", f"https://github.com/{o}/{n}.git")
            self.assertProject(self.launch(d), f"{o}__{n}")
        d = self.new_repo("own-acme/folder")
        self.git(d, "remote", "add", "origin", "git@github.com:Acme/Slugs.git")
        w = d / ".claude/worktrees/fix-x"
        self.git(d, "worktree", "add", "-q", str(w), "-b", "fix-x2")
        self.assertProject(self.launch(w), "acme__slugs--fix-x")


class SecondClone(LauncherCase):
    """A second clone of one repo shares its slug, unsupported: the launch and --doctor warn (ADR 0010)."""

    def clone(self, folder, url="https://github.com/acme/api.git"):
        d = self.new_repo(folder)
        self.git(d, "remote", "add", "origin", url)
        return d

    def test_shares_the_slug_and_warns_while_the_other_exists(self):
        first, second = self.clone("x/api"), self.clone("y/api-review")
        self.assertNotIn("also cloned", self.launch(first).text)
        res = self.launch(second)
        self.assertProject(res, "acme__api")
        self.assertLaunch(res, 0, f"cocon: {first} also cloned acme/api: they share its slug acme__api")
        self.assertIn(f"WARN  clones: {first} also cloned acme/api", self.launch(second, "--doctor").text)
        shutil.rmtree(first)
        self.assertNotIn("also cloned", self.launch(second).text)

    def test_an_origin_differing_only_in_case_is_the_same_repo(self):
        first = self.clone("x/api", "git@github.com:Acme/API.git")
        self.launch(first)
        res = self.launch(self.clone("y/api"))
        self.assertProject(res, "acme__api")
        self.assertIn(f"{first} also cloned acme/api", res.text)


class UniqueSlug(LauncherCase):
    """Names that sanitize alike get different slugs (ADR 0010)."""

    def owned(self, name):
        d = self.new_repo(f"own/{name}")
        self.git(d, "remote", "add", "origin", f"https://github.com/acme/{name}.git")
        return d

    def test_names_that_sanitize_alike_by_the_same_owner_differ(self):
        """foo.bar and foo-bar by the same owner: different slugs, the changed name hashed"""
        dot, dash = self.project(self.launch(self.owned("foo.bar"))), self.project(self.launch(self.owned("foo-bar")))
        self.assertNotEqual(dot, dash)
        self.assertRegex(dot, r"^acme__foo-bar_[0-9a-f]{8}$")

    def test_worktree_name_sanitizing_changes_is_hashed_too(self):
        """worktrees Fix.X and fix-x of one repo: different slugs"""
        r = self.new_repo("Slugs")
        slugs = set()
        for n in ("Fix.X", "fix-x"):
            self.git(r, "worktree", "add", "-q", str(r / ".claude/worktrees" / n), "-b", n)
            slugs.add(self.project(self.launch(r / ".claude/worktrees" / n)))
        self.assertEqual(len(slugs), 2, slugs)
        self.assertRegex(" ".join(sorted(slugs)), r"^slugs_[0-9a-f]{8}--fix-x slugs_[0-9a-f]{8}--fix-x_[0-9a-f]{8}$")

    def test_clean_folder_name_keeps_todays_slug(self):
        self.assertEqual(self.project(self.launch(self.owned("foo-bar"))), "acme__foo-bar")
        self.assertEqual(self.project(self.launch(self.new_repo("plain"))), "plain")

    def test_main_folder_with_separator_and_a_worktree_differ(self):
        """a main folder a--b and repo a's worktree b get different slugs"""
        a = self.owned("a")
        wt = a / ".claude/worktrees/b"
        self.git(a, "worktree", "add", "-q", str(wt), "-b", "b")
        self.assertEqual(self.project(self.launch(wt)), "acme__a--b")
        self.assertRegex(self.project(self.launch(self.owned("a--b"))), r"^acme__a--b_[0-9a-f]{8}$")

    def test_names_at_a_separators_edge_are_hashed(self):
        """repo x- with worktree w and repo x with worktree -w: different slugs"""
        slugs = set()
        for repo, wt in (("x-", "w"), ("x", "-w")):
            d = self.owned(repo)
            self.git(d, "worktree", "add", "-q", str(d / ".claude/worktrees" / wt), "-b", f"b{len(slugs)}")
            slugs.add(self.project(self.launch(d / ".claude/worktrees" / wt)))
        self.assertEqual(len(slugs), 2, slugs)

    def test_worktree_of_a_hashed_main_keeps_the_mains_slug_as_prefix(self):
        d = self.owned("foo.bar")
        main = self.project(self.launch(d))
        wt = d / ".claude/worktrees/w"
        self.git(d, "worktree", "add", "-q", str(wt), "-b", "w")
        self.assertEqual(self.project(self.launch(wt)), f"{main}--w")

    def test_repo_ids_differing_only_in_case_share_a_slug(self):
        """GitHub names ignore case: Acme/Foo.Bar and acme/foo.bar are one repo, one slug; case alone hashes nothing"""
        import_kit()
        from kit.launcher.project import repo_part
        self.assertEqual(repo_part("Acme__Foo.Bar"), repo_part("acme__foo.bar"))
        self.assertRegex(repo_part("acme__foo.bar"), r"^acme__foo-bar_[0-9a-f]{8}$")
        self.assertEqual(repo_part("Acme__Widget"), "acme__widget")

    @unittest.skipIf(os.geteuid() == 0, "root enters a folder whatever its mode")
    def test_a_folder_it_cant_enter_refuses_rather_than_hash_nothing(self):
        """a hashed name whose folder can't be entered refuses, no slug from an empty path (in-process: the folder shuts
        between the launch reaching it and the slug naming it)"""
        import_kit()
        from kit.launcher.project import slug_part
        from kit.util import Fail
        shut = self.tmp / "Odd.Name"
        shut.mkdir(mode=0)
        self.addCleanup(shut.chmod, 0o755)
        with self.assertRaises(Fail) as e:
            slug_part(str(shut))
        self.assertEqual(e.exception.msg, f"can't enter {shut} to name its slug")
        self.assertEqual(slug_part(str(self.tmp / "plain")), "plain")  # a clean name needs no path

    FOO_BAR_VOLUMES = "cocon-acme__foo-bar_pgdata cocon-acme__foo-bar-node-modules"

    def test_destroy_names_only_its_own_project(self):
        """--destroy on foo.bar: stops and removes its own project, never foo-bar's"""
        d = self.owned("foo.bar")
        slug = self.project(self.launch(d))
        own = f"cocon-{slug}_pgdata"
        res = self.launch(d, "--destroy", answer="y", env={"STUB_VOLUMES": f"{own} {self.FOO_BAR_VOLUMES}"})
        self.assertLaunch(res, 0, f"Stops cocon-{slug} and removes its volumes:\n  {own}\n")
        self.assertCall(res, f"compose -f {COMPOSE} -p cocon-{slug} --profile * down")
        self.assertCall(res, f"volume rm {own}")
        self.assertEqual(self.project(res), slug)


class Doctor(LauncherCase):
    def test_without_github_origin_volumes_keyed_by_folder_name(self):
        env = self.tmp / "xdg" / "cocon" / "claude.env"
        env.parent.mkdir(parents=True)
        env.write_text("CLAUDE_CODE_OAUTH_TOKEN=x\n")
        env.chmod(0o600)
        res = self.launch(self.new_repo("NoHub"), "--doctor")
        self.assertLaunch(res, 0, "--    volumes: no github.com origin, slug and volumes follow the folder name, "
                                  "shared with any same-named folder without one: cocon-nohub-*")

    def test_all_checks_pass(self):
        r = self.new_repo("doc")
        self.git(r, "remote", "add", "origin", "git@github.com:acme/doc.git")
        tok = self.tmp / "xdg" / "cocon" / "tokens" / "acme__doc.env"
        tok.parent.mkdir(parents=True)
        tok.write_text("GH_TOKEN=github_pat_x\n")
        tok.chmod(0o600)
        env = tok.parent.parent / "claude.env"
        env.write_text("CLAUDE_CODE_OAUTH_TOKEN=x\n")
        env.chmod(0o600)
        res = self.launch(r, "--doctor", env={"STUB_GH_REPOS": "acme/doc"})
        self.assertLaunch(res, 0, "ok    docker", "ok    image", "ok    token", "ok    token: fine-grained, reaches no private repo besides acme/doc", "ok    allowlist", "ok    hooks", "ok    tmux",
                          "ok    volumes: cocon-acme__doc-*", f"ok    claude.env: {env}")
        self.assertNotIn("FAIL", res.text)
        self.assertNoCall(res, " run")

    def test_points_to_branch_protection_section(self):
        """--doctor can't read branch protection with the repo token: it points to the README section"""
        r = self.new_repo("doc")
        self.git(r, "remote", "add", "origin", "git@github.com:acme/doc.git")
        res = self.launch(r, "--doctor")
        self.assertIn("--    branch protection: can't be read with a repo-scoped token; see README > Branch protection",
                      res.text)
        readme = (ROOT / "README.md").read_text()
        self.assertRegex(readme, r"(?m)^#{2,3} Branch protection$")
        self.assertRegex(readme, r"(?m)^## GitHub access \(repo-scoped tokens\)$")

    def test_one_line_per_failure_exits_non_zero(self):
        r = self.new_repo("doc")
        self.git(r, "remote", "add", "origin", "git@github.com:acme/doc.git")
        cfg = self.tmp / "xdg" / "cocon"
        (cfg / "tokens").mkdir(parents=True)
        tok = cfg / "tokens" / "acme__doc.env"
        tok.write_text("GH_TOKEN=x\n")
        tok.chmod(0o644)
        env = cfg / "claude.env"
        env.write_text("CLAUDE_CODE_OAUTH_TOKEN=x\n")
        env.chmod(0o644)
        (cfg / "allow").mkdir()
        (cfg / "allow" / "acme__doc.txt").write_text("ok.example.com\nhttps://bad.example.com/\n*.x.com\n")
        shutil.rmtree(r / ".git" / "hooks")
        res = self.launch(r, "--doctor", env={"STUB_DOCKER_DOWN": "1"})
        self.assertLaunch(res, 1, "FAIL  docker", f"FAIL  token: {tok} has mode 644", f"FAIL  claude.env: {env} has mode 644",
                          f"FAIL  allowlist: {cfg}/allow/acme__doc.txt: bad lines: 2:https://bad.example.com/ 3:*.x.com",
                          f"--    hooks: {r}/.git/hooks missing")

    def test_claude_env_missing_or_without_token_is_a_failure(self):
        r = self.new_repo("doc")
        env = self.tmp / "xdg" / "cocon" / "claude.env"
        res = self.launch(r, "--doctor")
        self.assertLaunch(res, 1, f"FAIL  claude.env: {env} missing; put the output of claude setup-token there")
        env.parent.mkdir(parents=True)
        env.write_text("X=1\n")
        env.chmod(0o600)
        res = self.launch(r, "--doctor")
        self.assertLaunch(res, 1, f"FAIL  claude.env: no CLAUDE_CODE_OAUTH_TOKEN in {env}")

    def test_unparsable_github_origin_warned_at_start_fail_in_doctor(self):
        r = self.new_repo("alias")
        self.git(r, "remote", "add", "origin", "git@github.com-work:acme/alias.git")
        self.assertIn("can't read owner/repo from origin git@github.com-work:acme/alias.git", self.launch(r).text)
        self.assertLaunch(self.launch(r, "--doctor"), 1, "FAIL  token: can't read owner/repo")


if __name__ == "__main__":
    unittest.main()
