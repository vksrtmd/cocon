"""Launcher: --build and --with (extras)."""
import re
import unittest

from harness import KIT
from launcher_case import LauncherCase


def is_build(line):
    return re.search(r"^compose .* build dev$", line) is not None


def tagged(res, tag, withs):
    return any(c.env.get("COCON_TAG") == tag and c.withs == withs for c in res.calls)


class Build(LauncherCase):
    def setUp(self):
        super().setUp()
        self.r = self.new_repo("bld")

    def test_build_builds_the_image_and_exits_without_a_session(self):
        res = self.launch(self.r, "--build")
        self.assertEqual(res.rc, 0, res.text)
        self.assertTrue([l for l in self.lines(res) if is_build(l)], self.lines(res))
        self.assertNoCall(res, "volume create")
        self.assertIsNone(self.session(res), "session started")

    def test_launch_with_the_image_present_no_build(self):
        self.assertNoCall(self.launch(self.r), " build dev")

    def test_build_with_port_refused(self):
        self.assertLaunch(self.launch(self.r, "--build", "--port", "8080"), 1, "--port only applies to starting a session")

    def test_build_rebuilds_latest_and_every_existing_extras_tag(self):
        res = self.launch(self.r, "--build", env={"STUB_TAGS": "latest clickhouse-postgres-rust <none> bogus-tag"})
        self.assertEqual(res.rc, 0, res.text)
        self.assertTrue(tagged(res, "latest", []))
        self.assertTrue(tagged(res, "clickhouse-postgres-rust", ["clickhouse", "postgres", "rust"]))
        self.assertEqual(len([l for l in self.lines(res) if is_build(l)]), 2, self.lines(res))
        self.assertIn("bogus-tag", res.text)

    def test_build_skips_tag_with_unsorted_extras(self):
        res = self.launch(self.r, "--build", env={"STUB_TAGS": "postgres-clickhouse"})
        self.assertIn("skipping cocon:postgres-clickhouse", res.text)
        self.assertFalse([c for c in res.calls if c.env.get("COCON_TAG") == "clickhouse-postgres"],
                         "rebuilt under another name")

    def test_build_with_with_refused(self):
        self.assertLaunch(self.launch(self.r, "--build", "--with", "rust"), 1, "--with doesn't apply to --build")


class With(LauncherCase):
    def setUp(self):
        super().setUp()
        self.r = self.new_repo("bld")

    def test_unknown_extra_dies_before_any_docker_call(self):
        res = self.launch(self.r, "--with", "rust,nope")
        self.assertLaunch(res, 1, "unknown extra nope (known: ")
        self.assertEqual(res.calls, [])

    def test_sorted_tag_deduped_one_profile_per_extra_session_env(self):
        res = self.launch(self.r, "--with", "rust,postgres", "--with", "postgres,docker")
        self.assertEqual(res.rc, 0, res.text)
        self.assertTrue(tagged(res, "docker-postgres-rust", ["docker", "postgres", "rust"]))
        self.assertCallRe(res, r" -p cocon-bld --profile docker --profile postgres --profile rust -f \S+/sidecar/bld\.json run ")
        self.assertEnv(res, "DOCKER_HOST=tcp://docker:2375")

    def test_docker_sidecar_gets_the_sessions_checkout_mounts_and_modes(self):
        r, src = self.new_repo("dk"), self.new_repo("dksub")
        self.git(r, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "sub")
        self.git(r, "config", "core.hooksPath", ".husky/_")
        res = self.launch(r, "--with", "docker")
        self.assertEqual(res.rc, 0, res.text)
        want = [f"{r}:{r}", f"{r}/.git/hooks:{r}/.git/hooks:ro",
                f"{r}/.git/modules/sub/hooks:{r}/.git/modules/sub/hooks:ro", f"{r}/.husky/_:{r}/.husky/_:ro"]
        self.assertCountEqual(self.sidecar_mounts(res), want)
        for m in want:
            self.assertMount(res, m)

    def test_docker_sidecar_starts_healthy_before_the_session(self):
        lines = self.lines(self.launch(self.r, "--with", "docker"))
        up = [i for i, l in enumerate(lines) if re.search(r" -p cocon-bld --profile docker -f \S+ up -d --wait docker$", l)]
        sess = [i for i, l in enumerate(lines) if " run --rm --name " in l]
        self.assertTrue(up and sess and up[0] < sess[-1], lines)
        self.assertFalse([l for l in self.lines(self.launch(self.r)) if " up " in l])

    def test_docker_sidecar_that_isnt_healthy_refuses_naming_the_runtime(self):
        for info, name in (("OrbStack|orbstack", "OrbStack"), ("Docker Desktop|docker-desktop", "Docker Desktop"),
                           ("Ubuntu 24.04.1 LTS|colima", "Colima"), ("Ubuntu 24.04.1 LTS|colima-work", "Colima"),
                           ("Fedora Linux 41|fedbox", "Fedora Linux 41 (fedbox)")):
            with self.subTest(runtime=name):
                res = self.launch(self.r, "--with", "docker", env={"STUB_SIDECAR_RC": "1", "STUB_INFO": info})
                self.assertLaunch(res, 1, f"the Docker sidecar didn't start on {name}")
                self.assertIsNone(self.session(res), "session ran")

    def test_an_unhealthy_sidecar_refuses_naming_the_restart(self):
        res = self.launch(self.r, "--with", "docker",
                          env={"STUB_SIDECAR_RC": "1", "STUB_UNHEALTHY": "cocon-bld-docker-1"})
        self.assertLaunch(res, 1, "the Docker sidecar cocon-bld-docker-1 is unhealthy")
        self.assertIn("docker compose -p cocon-bld restart docker", res.text)
        self.assertIsNone(self.session(res), "session ran")

    def test_checkout_path_with_a_newline_refused(self):
        """a newline in the checkout's path is refused: the sidecar's override and the hooks list are one path per line"""
        d = self.tmp / "a\nb"  # no git: nothing else on the way refuses it
        d.mkdir()
        res = self.launch(d, "--with", "docker")
        self.assertLaunch(res, 1, "newline")
        self.assertIsNone(self.session(res), "session ran")

    def test_planted_git_dir_with_a_colon_refused_before_docker(self):
        """a session-made .git/modules/a:b (a hooks dir to mount) is refused by name, not by docker's -v parser"""
        r = self.new_repo("colon")
        m = r / ".git" / "modules" / "a:b"
        (m / "objects").mkdir(parents=True)
        (m / "HEAD").write_text("ref: refs/heads/main\n")
        res = self.launch(r, "--with", "docker")
        self.assertLaunch(res, 1, "colon", str(m / "hooks"))
        self.assertIsNone(self.session(res), "session ran")

    def test_without_docker_no_sidecar_override(self):
        self.assertIsNone(self.sidecar_mounts(self.launch(self.r)))
        self.assertIsNone(self.sidecar_mounts(self.launch(self.r, "--with", "postgres")))

    def test_no_extras_is_latest_no_profiles_no_docker_env(self):
        res = self.launch(self.r)
        self.assertTrue(tagged(res, "latest", []))
        self.assertNoCall(res, "--profile")
        self.assertNoMount(res, "DOCKER_HOST=tcp://docker:2375")

    def test_builds_the_tag_when_missing(self):
        res = self.launch(self.r, "--with", "python,go", env={"STUB_NOIMG": "cocon:go-python"})
        self.assertTrue([l for l in self.lines(res) if is_build(l)], self.lines(res))
        self.assertTrue(tagged(res, "go-python", ["go", "python"]))

    def test_empty_name_refused(self):
        self.assertLaunch(self.launch(self.r, "--with", "rust,,go"), 1, "empty name")

    def test_removed_flags_are_unknown_options(self):
        for flag in ("--db", "--docker", "--playwright"):
            with self.subTest(flag=flag):
                self.assertLaunch(self.launch(self.r, flag), 1, f"unknown option {flag}")

    def test_go_per_repo_gomod_volume_created_and_mounted(self):
        res = self.launch(self.r, "--with", "go")
        self.assertCall(res, "volume create cocon-bld-gomod")
        self.assertMount(res, "cocon-bld-gomod:/home/claude/go/pkg/mod")
        self.assertNoCall(self.launch(self.r), "gomod")

    def test_rust_per_repo_cargo_volume_created_and_mounted(self):
        res = self.launch(self.r, "--with", "rust")
        self.assertCall(res, "volume create cocon-bld-cargo")
        self.assertMount(res, "cocon-bld-cargo:/home/claude/.cargo")
        self.assertNoCall(self.launch(self.r), "cargo")

    def test_playwright_downloads_then_copies_into_cache_volume_before_session(self):
        r = self.new_repo("pw")
        (r / "package.json").write_text("{}\n")
        res = self.launch(r, "--with", "playwright")
        self.assertCall(res, "volume create cocon-pw-playwright")
        lines = self.lines(res)
        dl = [i for i, l in enumerate(lines)
              if re.search(r"-v cocon-pw-playwright:/home/claude/\.cache .* cocon:playwright ", l)]
        cp = [i for i, l in enumerate(lines)
              if re.search(r" --network none -v cocon-pw-playwright:/pw:ro -v cocon-pw-cache:/home/claude/"
                           r"\.cache .* cocon:playwright ", l)]
        sess = [i for i, l in enumerate(lines) if " run --rm --name " in l]
        self.assertTrue(dl and cp and sess and dl[0] < cp[0] < sess[-1], lines)
        self.assertCallRe(res, r" -p cocon-pw --profile playwright run ")

    def test_playwright_fetch_runs_no_npm_install_scripts(self):
        r = self.new_repo("pw")
        (r / "package.json").write_text("{}\n")
        res = self.launch(r, "--with", "playwright")
        self.assertCallRe(res, r"(?s) -e npm_config_ignore_scripts=true .*cocon:playwright .*playwright install chromium")

    def test_playwright_fetch_mounts_nothing_the_session_writes(self):
        r = self.new_repo("pw")
        (r / "package.json").write_text("{}\n")
        res = self.launch(r, "--with", "playwright")
        (fetch,) = [c.args for c in res.calls if "playwright install chromium" in c.line]
        mounts = [fetch[i + 1] for i, a in enumerate(fetch) if a == "-v"]
        self.assertEqual(mounts, ["cocon-pw-playwright:/home/claude/.cache",
                                  "cocon-pw-node-modules:/nm:ro"])
        self.assertNotIn("--network", fetch)
        script = fetch[-1]  # bash -c: the version is parsed with jq; node's require() would run a planted module
        self.assertRegex(script, r"jq -r \.version /nm/playwright-core/package\.json")
        self.assertNotIn("require(", script)
        self.assertFalse([a for a in self.session(res) if "cocon-pw-playwright" in a], self.session(res))
        self.assertNotIn("}-playwright", (KIT / "compose.yml").read_text())

    def test_playwright_volume_only_with_playwright(self):
        r = self.new_repo("pw")
        (r / "package.json").write_text("{}\n")
        res = self.launch(r)
        self.assertNoCall(res, "cocon-pw-playwright")
        self.assertNoCall(res, "playwright install")

    def test_doctor_reports_the_docker_sidecars_processes_and_warns_near_its_cap(self):
        for pids, line in (("50,4096", "ok    docker sidecar: 50 of 4096 processes\n"),
                           ("3900,4096", "WARN  docker sidecar: 3900 of 4096 processes; end its sessions and run"),
                           ("", "--    docker sidecar: not running\n")):
            with self.subTest(pids=pids):
                self.assertIn(line, self.launch(self.r, "--doctor", env={"STUB_SIDECAR_PIDS": pids}).out)

    def test_doctor_fails_a_missing_or_old_docker_compose(self):
        for env, line in (({}, "ok    docker compose: 2.29.7\n"),
                          ({"STUB_COMPOSE_VERSION": "v2.40.0"}, "ok    docker compose: v2.40.0\n"),
                          ({"STUB_COMPOSE_VERSION": "2.20.1"}, "FAIL  docker compose: 2.20.1; needs 2.20.2 or newer\n"),
                          ({"STUB_FAIL": "compose version"}, "FAIL  docker compose: missing; install")):
            with self.subTest(env=env):
                self.assertIn(line, self.launch(self.r, "--doctor", env=env).out)


if __name__ == "__main__":
    unittest.main()
