"""CI sets every variable cocon/compose.yml requires: `compose config` with only tests/ci.vars's
values, no daemon needed, and cocon.yml loads that file. Skipped without docker compose (CI's
macOS runner). Runs the real docker, so it is outside the stub-docker harness."""
import json
import os
import re
import shutil
import subprocess
import unittest

from harness import KIT, ROOT

CI_VARS = ROOT / "tests/ci.vars"


def have_compose():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


def config(*args, env=None):
    env = {**dict(line.split("=", 1) for line in CI_VARS.read_text().splitlines() if line and not line.startswith("#")),
           **(env or {}), "PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    return subprocess.run(["docker", "compose", "-f", str(KIT / "compose.yml"), "--profile", "*", "config", *args],
                          env=env, cwd="/", capture_output=True, text=True)


@unittest.skipUnless(have_compose(), "no `docker compose` (the docker CLI with its compose plugin) on PATH")
class Compose(unittest.TestCase):
    def test_compose_config_with_only_ci_vars(self):
        r = config("-q")
        self.assertEqual(r.returncode, 0, f"cocon/compose.yml requires a variable tests/ci.vars doesn't set; add it to tests/ci.vars (CI's cocon.yml loads it): {r.stderr}")

    def test_only_the_broker_shares_a_network_with_egress(self):
        services = json.loads(config("--format", "json").stdout)["services"]
        nets = {name: set(s.get("networks") or ["default"]) for name, s in services.items()}
        self.assertEqual(nets["broker"], {"internal", "broker-egress"})
        self.assertNotIn("internal", nets["egress"])
        self.assertEqual(nets["docker-broker"], {"internal", "broker-egress"})  # the Docker sidecar's own
        self.assertEqual(sorted(n for n, ns in nets.items() if ns & nets["egress"] - {"outside"}),
                         ["broker", "docker-broker", "egress"])
        self.assertEqual(services["docker"]["environment"]["HTTPS_PROXY"], "http://docker-broker:8080")

    def test_docker_sidecar_mounts_nothing_of_the_host_writable(self):
        """The launcher's override gives the sidecar the checkout, with the session's modes; compose.yml alone, nothing."""
        docker = json.loads(config("--format", "json").stdout)["services"]["docker"]
        self.assertEqual([v for v in docker["volumes"] if v["type"] == "bind" and not v.get("read_only")], [])

    def test_docker_sidecar_is_unprivileged_on_the_internal_network_only(self):
        """ADR 0016: no privileged, no added capability, a tap device and nothing else of the VM."""
        docker = json.loads(config("--format", "json").stdout)["services"]["docker"]
        self.assertFalse(docker.get("privileged"))
        self.assertNotIn("cap_add", docker)
        self.assertEqual([(d["source"], d["target"]) for d in docker["devices"]], [("/dev/net/tun", "/dev/net/tun")])
        self.assertEqual(set(docker["networks"]), {"internal"})

    def test_dev_has_no_root_path(self):
        """The session: no capabilities, no-new-privileges, no privileged mode, devices or added capability; its user
        is the image's last USER unless compose.yml sets one, and neither is root."""
        dev = json.loads(config("--format", "json").stdout)["services"]["dev"]
        self.assertEqual(dev.get("cap_drop"), ["ALL"])
        self.assertEqual(dev.get("security_opt"), ["no-new-privileges:true"])
        self.assertFalse(dev.get("privileged"))
        for key in ("cap_add", "devices"):
            self.assertFalse(dev.get(key), key)
        users = re.findall(r"^USER\s+(\S+)", (KIT / "Dockerfile").read_text(), re.M)
        self.assertTrue(dev.get("user") or users, "no USER line in cocon/Dockerfile and no user in compose.yml")
        user = dev.get("user") or users[-1]
        self.assertNotIn(user.split(":")[0], ("root", "0"))
        for v in dev.get("volumes", []):
            self.assertNotIn("docker.sock", v.get("source", ""), v)
            self.assertEqual(v["type"], "volume", f"dev has a host mount: {v}")

    def test_docker_sidecar_has_the_sessions_limits(self):
        services = json.loads(config("--format", "json").stdout)["services"]
        self.assertEqual((services["docker"]["mem_limit"], services["docker"]["pids_limit"]), ("8589934592", 4096))
        services = json.loads(config("--format", "json", env={"COCON_MEMORY": "3g", "COCON_PIDS": "99"})
                              .stdout)["services"]
        for s in ("dev", "docker"):
            self.assertEqual((services[s]["mem_limit"], services[s]["pids_limit"]), ("3221225472", 99), s)

    def test_docker_sidecar_reaps_its_containers_shims(self):
        """An init as PID 1: rootlesskit leaves each exited container's shim a zombie, counted against pids_limit."""
        self.assertIs(json.loads(config("--format", "json").stdout)["services"]["docker"].get("init"), True)


class Workflow(unittest.TestCase):
    def test_workflow_loads_ci_vars(self):
        wf = (ROOT / ".github/workflows/cocon.yml").read_text()
        self.assertIn('tests/ci.vars >> "$GITHUB_ENV"', wf)

    def test_workflow_pulls_every_image_the_integration_tests_skip_without(self):
        """A test skips when its image isn't on the daemon, and shell.yml fails a skip: its pull step lists them."""
        tests = "".join(p.read_text() for p in (ROOT / "tests").glob("test_*_integration.py"))
        used = set(re.findall(r'\bimage\("(\w+)"\)', tests))
        listed = re.search(r"for s in ([\w ]+); do\n\s+docker pull", (ROOT / ".github/workflows/shell.yml").read_text())
        self.assertTrue(listed, "shell.yml has no `for s in ...; do docker pull` step")
        self.assertEqual(set(listed.group(1).split()), used)
