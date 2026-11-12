"""cocon/compose.yml on a real Docker daemon: what the launcher tests' stubs can't show.
Skipped without a daemon; in the cocoon that means a session started with --with docker.
Uses the real docker: the stub dir is dropped from PATH.

Pitfalls:
- The sidecar's daemon has no way to Docker Hub here: an image reaches it by `docker save | docker exec -i ... docker load`
  (`load_image`). Saved from a digest-pinned image, it loses its name: `load` prints `Loaded image ID: sha256:...`,
  and that ID is what to run.
- A path the nested daemon must see is made on the outer daemon's host and mounted into the sidecar at the same
  path; `checkout_on_daemon_host` makes it under /var/tmp.
- The `docker`, `broker` and `ports` images (compose.yml's) must already be on the daemon, or the test skips
  (`need_sidecar_images`, and the forwarder's check); `docker images` shows what the cocoon has. CI pulls them in shell.yml, which fails a skip.
  busybox:1.37 is pulled by the test itself.
- The tests that start the sidecar (`up_sidecar`) skip in the cocoon: its daemon is the cocoon's own rootless sidecar, which
  can't map UIDs for a nested rootless daemon (ADR 0016). They skip on that error only; CI runs them, and fails a skip.
"""
import atexit
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import KIT, STUB_BIN, KitCase, assert_daemon_sees_repo, eventually, have_daemon, shell, stale_runs

IMG = "busybox:1.37"
P = f"cocon-itest-{os.getpid()}"
# Host ports on the daemon, per run like P: parallel runs (one per worktree) share the daemon.
# 10000-29999, below the ephemeral range.
PORT = 10000 + os.getpid() % 2000 * 10
# rootlesskit's error when the daemon running the sidecar is rootless itself, as the cocoon's is (CI fails the skip).
NESTED_USERNS = "newuidmap: write to uid_map failed: Operation not permitted"
LOG_DIR, ALLOW_DIR, LOG_NAME, ALLOW = f"/tmp/{P}-log", f"/tmp/{P}-allow", "itest.log", "itest.txt"
COMPOSE_ENV = {"REPO_VOLUMES": P, "LOG_DIR": LOG_DIR, "LOG_NAME": LOG_NAME,
               "ALLOW_DIR": ALLOW_DIR, "REPO_ALLOW": ALLOW}
sh = shell(COMPOSE_ENV)
COMPOSE = ["docker", "compose", "-f", str(KIT / "compose.yml"), "-p", P]
P2 = f"{P}-other"  # a second checkout's project
CERTS = "/home/rootless/.config/docker/certs.d"  # the rootless sidecar's


def compose(*args, **kw):
    return sh(*COMPOSE, *args, **kw)


def get(port):
    """The published port's answer, seen from the daemon's host: the container's hostname."""
    r = sh("docker", "run", "--rm", "--network", "host", IMG, "wget", "-qO-", "-T", "3",
           f"http://127.0.0.1:{port}/hostname")
    return r.returncode, (r.stdout + r.stderr).strip()


def logs(script):
    return sh("docker", "run", "--rm", "-v", f"{LOG_DIR}:/d", IMG, "sh", "-c", script)


def via(url):
    """url through egress, from where the broker is: only it reaches egress."""
    return sh("docker", "run", "--rm", "--network", f"{P}_broker-egress", "-e", "http_proxy=http://egress:3128",
       IMG, "wget", "-q", "-O-", url)


REGISTRY = '''import http.server, json, ssl
# A read-only registry on 443 serving the image saved in /r/img (an OCI layout) as bb:latest.
B = "/r/img/blobs/sha256/"
top = json.load(open("/r/img/index.json"))["manifests"][0]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self, body=True):
        p = self.path.split("?")[0].replace("/v2/library/bb/", "/v2/bb/")  # `docker pull bb` means library/bb
        ct, data = "application/json", None
        hub = self.headers.get("host", "").startswith("registry-1.")  # Docker Hub's: asks for a token first
        if p == "/token":
            data = b'{"token": "t"}'
        elif hub and self.headers.get("authorization") != "Bearer t":
            self.send_response(401)
            self.send_header("www-authenticate", 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"')
            self.send_header("content-length", "0"); self.end_headers(); return
        elif p == "/v2/":
            data = b"{}"
        elif p.startswith("/v2/bb/manifests/") or p.startswith("/v2/bb/blobs/"):
            ref = p.rsplit("/", 1)[1]
            d = top["digest"] if ref == "latest" else ref
            try:
                data = open(B + d.removeprefix("sha256:"), "rb").read()
            except OSError:
                pass
            if data is not None and "/manifests/" in p:
                ct = json.loads(data).get("mediaType") or top["mediaType"]
            elif data is not None:
                ct = "application/octet-stream"
        if data is None:
            self.send_response(404); self.send_header("content-length", "0"); self.end_headers(); return
        self.send_response(200); self.send_header("content-type", ct); self.send_header("content-length", str(len(data)))
        if "/v2/bb/" in p:
            self.send_header("docker-content-digest", d)
        self.end_headers()
        if body:
            self.wfile.write(data)
    def do_HEAD(self):
        self.do_GET(body=False)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain("/r/reg.pem", "/r/reg-key.pem")
s = http.server.ThreadingHTTPServer(("", 443), H); s.socket = ctx.wrap_socket(s.socket, server_side=True)
s.serve_forever()
'''


def image(service):
    return re.search(rf"^  {service}:\n(?:    .*\n)*?    image: (\S+)", (KIT / "compose.yml").read_text(), re.M).group(1)


def broker_egress_ip(container):
    return sh("docker", "inspect", "-f", f'{{{{(index .NetworkSettings.Networks "{P}_broker-egress").IPAddress}}}}',
              container).stdout.strip()


def remove_run(p):
    """Everything run p (a P) and its second checkout made on the daemon, and its dirs on the daemon's host."""
    p2 = f"{p}-other"
    ids = sh("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={p}").stdout.split()
    ids += sh("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={p2}").stdout.split()
    names = [f"{p}-dev-1", f"{p}-alone", f"{p}-sink", f"{p}-ports-1", f"{p}-ports-2", f"{p}-reg", f"{p}-broker", f"{p}-broker2",
             f"{p}-dind", f"{p2}-broker"]
    sh("docker", "rm", "-fv", *ids, *names)
    sh("docker", "volume", "rm", f"{p}-regfix", f"{p}_dockerdata-rootless", f"{p}_docker-ca")
    sh("docker", "network", "rm", f"{p}_internal", f"{p}_broker-egress", f"{p}_outside", f"{p}-int",
       f"{p2}_internal", f"{p2}_broker-egress")
    sh("docker", "run", "--rm", "-v", "/tmp:/t", IMG, "rm", "-rf", f"/t/{p}-log", f"/t/{p}-allow")
    sh("docker", "run", "--rm", "-v", "/var/tmp:/t", IMG, "rm", "-rf", f"/t/{p}-proj")


@unittest.skipUnless(have_daemon(), "no Docker daemon (`docker info` failed); in a cocoon, restart with --with docker")
class ComposeOnDocker(KitCase):
    def setUp(self):
        super().setUp()
        # Real docker, not the stub; the temp HOME hides its config and compose plugin.
        self.env["PATH"] = self.env["PATH"].replace(f"{STUB_BIN}:", "", 1)
        self.env.setdefault("DOCKER_CONFIG", os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")))
        self.env.update({k: v for k, v in os.environ.items() if k.startswith("DOCKER_")})
        self.env.update(COMPOSE_ENV)

    def tearDown(self):
        remove_run(P)

    @classmethod
    def setUpClass(cls):
        for p in stale_runs("cocon-itest-"):
            remove_run(p)
        # tearDown doesn't run on Ctrl-C (unittest re-raises KeyboardInterrupt), atexit does.
        atexit.register(remove_run, P)
        sh("docker", "pull", "-q", IMG)
        assert_daemon_sees_repo(IMG)

    def start_dev_and_forwarder(self):
        ports = image("ports")
        if sh("docker", "image", "inspect", ports).returncode:
            self.skipTest(f"{ports} isn't on this daemon, which may have no network to pull it")
        r = compose("--profile", "ports", "run", "-d", "--rm", "--name", f"{P}-ports-1",
                    "-p", f"127.0.0.1:{PORT}:5173", "-p", f"127.0.0.1:{PORT + 1}:5174",
                    "-p", f"127.0.0.1:{PORT + 2}:80",
                    "ports", f"{P}-dev-1", "5173", "5174", "80")
        self.assertEqual(r.returncode, 0, "forwarder didn't start: " + r.stderr)
        r = sh("docker", "run", "-d", "--rm", "--name", f"{P}-dev-1", "--network", f"{P}_internal", IMG,
               "sh", "-c", "httpd -p 5173 -h /etc; httpd -p 5174 -h /etc; httpd -f -p 80 -h /etc")
        self.assertEqual(r.returncode, 0, r.stderr)
        for port in (5173, 5174, 80):
            eventually(f"httpd on :{port}", lambda: sh("docker", "exec", f"{P}-dev-1", "wget", "-qO-", "-T", "1",
                                                       f"http://127.0.0.1:{port}/hostname").returncode == 0)
        return sh("docker", "inspect", "-f", "{{.Config.Hostname}}", f"{P}-dev-1").stdout.strip()

    def up_egress(self):
        self.assertEqual(compose("up", "-d", "egress").returncode, 0, "egress didn't start")
        eventually("squid", lambda: compose("exec", "-T", "egress", "squid", "-f", "/etc/squid/cocon/squid.conf",
                                            "-k", "check").returncode == 0)

    def logged(self, url, needle):
        """Request url through the proxy until its log has needle; the log."""
        out = []
        def seen():
            via(url)
            out[:] = [logs("cat /d/itest.log").stdout]
            return needle in out[0]
        eventually(f"{needle} in the audit log", seen)
        return out[0]

    def test_internal_only_container_drops_published_port(self):
        # why dev can't publish
        r = sh("docker", "network", "create", "--internal", f"{P}-int")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = sh("docker", "run", "-d", "--rm", "--name", f"{P}-alone", "--network", f"{P}-int",
               "-p", f"127.0.0.1:{PORT + 9}:80", IMG, "httpd", "-f", "-p", "80", "-h", "/etc")
        self.assertEqual(r.returncode, 0, r.stderr)
        eventually("httpd in the container", lambda: sh("docker", "exec", f"{P}-alone", "wget", "-qO-", "-T", "1",
                                                        "http://127.0.0.1:80/hostname").returncode == 0)
        rc, out = get(PORT + 9)
        self.assertNotEqual(rc, 0, f"port reachable: {out}")

    def test_forwarder_reaches_session_container_and_ls_lists_ports(self):
        dev_host = self.start_dev_and_forwarder()
        for port in (PORT, PORT + 1, PORT + 2):  # low ports too
            self.assertEqual(get(port), (0, dev_host), f"127.0.0.1:{port}")
        out = self.run_kit("cocon", "--ls").out
        (row,) = [ln for ln in out.splitlines() if ln.split()[:1] == [P.replace("cocon-", "", 1)]]
        for spec in (f"{PORT}:5173", f"{PORT + 1}:5174", f"{PORT + 2}:80"):
            self.assertIn(spec, row)

    def test_busy_host_port_error_matches_launchers_pattern(self):
        self.start_dev_and_forwarder()
        r = compose("--profile", "ports", "run", "-d", "--rm", "--name", f"{P}-ports-2",
                    "-p", f"127.0.0.1:{PORT}:5173", "ports", f"{P}-dev-1", "5173")
        self.assertNotEqual(r.returncode, 0, "second forwarder started on a taken port")
        # Same pattern as the launcher's sed.
        self.assertRegex(r.stdout + r.stderr, rf"127\.0\.0\.1:{PORT}.*(allocated|in use)")

    def test_volumes_named_after_repo_volumes(self):
        r = compose("config", "--format", "json", env={"REPO_VOLUMES": "cocon-acme__w"})
        for v in ("config", "cache", "npm"):
            self.assertIn(f'"name": "cocon-acme__w-{v}"', r.stdout + r.stderr)

    def test_repo_volumes_unset_is_refused(self):
        self.assertNotEqual(compose("config", env={"REPO_VOLUMES": ""}).returncode, 0)

    def test_audit_log_format_and_reopen_after_move(self):
        # LOG_DIR lives on the daemon's host; writable by squid's user, as Docker Desktop's mounts are.
        logs("mkdir -p /d && chmod 777 /d")
        self.up_egress()
        out = self.logged("http://denied.example/one", "denied.example/one")
        # wget exits as soon as it has the 403, so squid sometimes logs the denial as _ABORTED.
        self.assertRegex(out, r"(?m)^[0-9-]{10}T[0-9:]{8}[+-][0-9]{4} [0-9.]+ TCP_DENIED(_ABORTED)?/403 "
                              r"up=[0-9]+ down=[0-9]+ GET http://denied\.example/one$")
        logs("mv /d/itest.log /d/itest.old")
        r = compose("exec", "-T", "egress", "squid", "-f", "/etc/squid/cocon/squid.conf", "-k", "rotate")
        self.assertEqual(r.returncode, 0, "rotate failed")
        # cocon's rotation waits for this: the reopen alone creates the new file, no request needed.
        eventually("the new file after the reopen", lambda: logs("test -e /d/itest.log").returncode == 0)
        out = self.logged("http://denied.example/two", "denied.example/two")
        self.assertIn("denied.example/two", out)
        self.assertNotIn("denied.example/one", out)

    def test_audit_log_counts_connect_tunnel_bytes(self):
        logs("mkdir -p /d && chmod 777 /d")
        # The repo's allowlist lets the tunnel through to its sink.
        sh("docker", "run", "--rm", "-v", f"{ALLOW_DIR}:/a", IMG, "sh", "-c", f"echo {P}-sink >/a/{ALLOW}")
        self.up_egress()
        # The sink reads until the client closes; busybox nc -l hangs up once its own stdin ends, hence the sleep.
        r = sh("docker", "run", "-d", "--rm", "--name", f"{P}-sink", "--network", f"{P}_outside", IMG,
               "sh", "-c", "sleep 10 | nc -l -p 443 >/dev/null")
        self.assertEqual(r.returncode, 0, r.stderr)
        eventually("the sink listening", lambda: sh("docker", "exec", f"{P}-sink", "sh", "-c",
                                                    "netstat -ln | grep -q ':443 '").returncode == 0)
        sh("docker", "run", "--rm", "--network", f"{P}_broker-egress", IMG, "sh", "-c",
           f"(printf 'CONNECT {P}-sink:443 HTTP/1.1\\r\\n\\r\\n'; sleep 1; head -c 3000000 /dev/zero; sleep 2)"
           " | nc egress 3128 >/dev/null")
        def counted():
            m = re.search(r" up=(\d+) ", logs(f"grep ' CONNECT {P}-sink:443' /d/itest.log").stdout)
            return m and int(m.group(1)) >= 3000000
        eventually("the tunnel's bytes in the audit log", counted)


    def test_session_network_cant_reach_another_projects_broker(self):
        """ADR 0002: each project's internal network is its own. A container on the first's can't connect to the second's
        broker by name or by address, while it can reach the first's own (so a failure means no route, not a dead probe)."""
        broker = image("broker")
        if sh("docker", "image", "inspect", broker).returncode:
            self.skipTest(f"{broker} isn't on this daemon, which may have no network to pull it")
        logs("mkdir -p /d/broker && chmod 777 /d /d/broker")
        for proj, name in ((P, f"{P}-broker"), (P2, f"{P2}-broker")):
            r = sh(*COMPOSE[:-1], proj, "run", "-d", "--no-deps", "--name", name, "-v", "/ca", "-v", "/pub",
                   "-e", "CLAUDE_CODE_OAUTH_TOKEN=x", "broker")
            self.assertEqual(r.returncode, 0, r.stderr)

        def connect(host, net=f"{P}_internal"):
            return sh("docker", "run", "--rm", "--network", net, IMG, "nc", "-z", "-w", "2", host, "8080")
        eventually("the first broker listening on its own network", lambda: connect(f"{P}-broker").returncode == 0)
        eventually("the second broker listening", lambda: connect(f"{P2}-broker", f"{P2}_internal").returncode == 0)
        ip = sh("docker", "inspect", "-f", f'{{{{(index .NetworkSettings.Networks "{P2}_internal").IPAddress}}}}',
                f"{P2}-broker").stdout.strip()
        self.assertRegex(ip, r"^\d+\.\d+\.\d+\.\d+$")
        for host in (f"{P2}-broker", ip):
            self.assertNotEqual(connect(host).returncode, 0, f"{host}:8080 reachable from {P}_internal")

    def need_sidecar_images(self):
        for img in (image("docker"), image("broker")):
            if sh("docker", "image", "inspect", img).returncode:
                self.skipTest(f"{img} isn't on this daemon, which may have no network to pull it")
        logs("mkdir -p /d/broker && chmod 777 /d /d/broker")

    def up_sidecar(self, override):
        """The sidecar as {P}-dind with its broker, up and healthy; override: a compose file's text, as the launcher's."""
        f = self.tmp / "override.yml"
        f.write_text(override)
        self.dind = lambda *a: sh(*COMPOSE[:4], "-f", str(f), *COMPOSE[4:], "--profile", "docker", *a)
        r = self.dind("up", "-d", "--wait", "docker")
        # A sidecar that exits says why only in its own log (a runner whose kernel refuses user namespaces, say).
        lg = sh("docker", "logs", "--tail", "40", f"{P}-dind") if r.returncode else None
        if lg and NESTED_USERNS in lg.stdout + lg.stderr:
            self.skipTest("the daemon is itself rootless (a cocoon's sidecar), so rootless dind can't map its UIDs")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + (lg.stdout + lg.stderr if lg else ""))

    def sidecar_and_registry(self):
        """The fake registry (as reg.example and as Docker Hub), the broker and the sidecar's daemon, all up."""
        self.need_sidecar_images()
        mitm = image("broker")
        sh("docker", "run", "--rm", "-v", f"{ALLOW_DIR}:/a", IMG, "sh", "-c", f"echo reg.example >/a/{ALLOW}")
        with tempfile.TemporaryDirectory() as fx:
            # A registry behind a root the sidecar and the broker trust (certs.d: *.crt are CAs, a *.key needs a *.cert).
            def openssl(*args):
                r = subprocess.run(["openssl", *args], capture_output=True, text=True, cwd=fx)
                self.assertEqual(r.returncode, 0, r.stderr)
            openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=fakeroot", "-keyout",
                    "root-key.pem", "-out", "ca.crt", "-addext", "basicConstraints=critical,CA:true",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                    "-addext", "subjectKeyIdentifier=hash")
            openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=reg.example", "-keyout", "reg-key.pem",
                    "-out", "reg.csr")
            Path(fx, "ext").write_text("subjectAltName=DNS:reg.example,DNS:registry-1.docker.io,DNS:auth.docker.io\n"
                                       "extendedKeyUsage=serverAuth\n"
                                       # Python 3.13+'s strict verify wants it; macOS's LibreSSL doesn't add it.
                                       "authorityKeyIdentifier=keyid\n")
            openssl("x509", "-req", "-in", "reg.csr", "-CA", "ca.crt", "-CAkey", "root-key.pem", "-CAcreateserial",
                    "-days", "2", "-extfile", "ext", "-out", "reg.pem")
            Path(fx, "registry.py").write_text(REGISTRY)
            Path(fx, "img").mkdir()
            saved = subprocess.run(["docker", "save", IMG], capture_output=True, check=True).stdout
            subprocess.run(["tar", "-C", str(Path(fx, "img")), "-xf", "-"], input=saved, check=True)
            subprocess.run(["chmod", "-R", "a+rX", fx], check=True)
            tar = subprocess.run(["tar", "-C", fx, "-cf", "-", "."], capture_output=True, check=True).stdout
        r = subprocess.run(["docker", "run", "--rm", "-i", "-v", f"{P}-regfix:/r", IMG, "tar", "-xf", "-", "-C", "/r"],
                           input=tar, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.up_egress()
        r = sh("docker", "run", "-d", "--name", f"{P}-reg", "--network", f"{P}_outside", "--network-alias", "reg.example",
               "--network-alias", "registry-1.docker.io", "--network-alias", "auth.docker.io",
               "-v", f"{P}-regfix:/r:ro", "--entrypoint", "python3", mitm, "/r/registry.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        # Two live sessions' brokers, as the launcher starts them; the sidecar, as dev's start brings it up, with its
        # own broker (both trusting the registry's root, the broker upstream, the sidecar for reg.example). Docker
        # Hub's hosts the sidecar trusts through its broker's CA.
        for b in (f"{P}-broker", f"{P}-broker2"):
            r = compose("run", "-d", "--name", b, "-v", "/ca", "-v", "/pub", "-e", "CLAUDE_CODE_OAUTH_TOKEN=x", "broker")
            self.assertEqual(r.returncode, 0, r.stderr)
        self.up_sidecar(f"""services:
  docker-broker:
    volumes: [regfix:/r:ro]
    command: [--set, ssl_verify_upstream_trusted_ca=/r/ca.crt]
  docker:
    container_name: {P}-dind
    volumes: [regfix:/regfix:ro]
volumes:
  regfix: {{name: {P}-regfix, external: true}}
""")
        # The rootless daemon's certs.d is under its home, which a volume mounted there would make root's.
        r = sh("docker", "exec", f"{P}-dind", "sh", "-c", f"mkdir -p {CERTS}/reg.example && ln -s /regfix/ca.crt {CERTS}/reg.example/")
        self.assertEqual(r.returncode, 0, r.stderr)

    def checkout_on_daemon_host(self):
        """A checkout with a hooks dir, on the daemon's host, owned by the session's user; its path."""
        d = f"/var/tmp/{P}-proj"  # not /tmp, a tmpfs in the sidecar
        r = sh("docker", "run", "--rm", "-v", "/var/tmp:/t", IMG, "sh", "-c",
               f"mkdir -p /t/{P}-proj/.git/hooks && chown -R 1000:1000 /t/{P}-proj")
        self.assertEqual(r.returncode, 0, r.stderr)
        return d

    def plant_hook(self, src, hooks):
        """Each way a session can mount src (at /g) into a nested container and touch /g/hooks/pre-commit: results."""
        admin = "--cap-add SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined"
        return {form: self.nested(f"docker run --rm {flags} {IMG} sh -c '{cmd}'") for form, flags, cmd in (
            ("-v", f"-v {src}:/g", f"touch /g/{hooks}/pre-commit"),
            ("non-recursive bind", f"--mount type=bind,src={src},dst=/g,bind-recursive=disabled", f"touch /g/{hooks}/pre-commit"),
            ("local bind volume", f"-v $(docker volume create -d local -o type=none -o o=bind -o device={src} gitbind):/g",
             f"touch /g/{hooks}/pre-commit"),
            # In the container's own user namespace: unmount the hooks mount, or bind /g without it.
            ("umount", f"-v {src}:/g {admin}", f"umount -l /g/{hooks}; touch /g/{hooks}/pre-commit"),
            ("bind in the container", f"-v {src}:/g {admin}", f"mkdir /m && mount --bind /g /m && touch /m/{hooks}/pre-commit"))}

    def assert_hooks_dir_unwritable_by_any_mount_form(self, d):
        self.load_image()
        for src, hooks in ((d, ".git/hooks"), (f"{d}/.git", "hooks")):
            tried = self.plant_hook(src, hooks)
            self.nested("docker volume rm -f gitbind")
            for form, r in tried.items():
                self.assertNotEqual(r.returncode, 0, f"{form} of {src}: a hook planted")
        r = sh("docker", "run", "--rm", "-v", "/var/tmp:/t", IMG, "ls", "-A", f"/t/{P}-proj/.git/hooks")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_nested_container_cant_write_what_the_session_cant_and_the_sidecar_has_the_sessions_limits(self):
        """A nested mount of cocon's own main checkout: read-only where the session's is, its hooks dir too."""
        self.need_sidecar_images()
        d = self.checkout_on_daemon_host()
        self.up_sidecar(json.dumps({"services": {"docker": {"container_name": f"{P}-dind", "volumes": [
            f"{d}:{d}:ro", f"{d}/.git:{d}/.git", f"{d}/.git/hooks:{d}/.git/hooks:ro"]}}}))
        self.load_image()
        r = self.nested(f"docker run --rm -v {d}:{d} {IMG} touch {d}/.git/x")
        self.assertEqual((r.returncode, r.stderr), (0, ""))  # writable in the session too: the nested run works
        r = self.nested(f"docker run --rm -v {d}:{d} {IMG} touch {d}/planted")
        self.assertNotEqual(r.returncode, 0, "planted written")
        self.assertIn("Read-only file system", r.stderr)
        self.assert_hooks_dir_unwritable_by_any_mount_form(d)
        r = sh("docker", "inspect", "-f", "{{.HostConfig.Memory}} {{.HostConfig.PidsLimit}}", f"{P}-dind")
        self.assertEqual(r.stdout.split(), ["8589934592", "4096"])

    def test_nested_container_cant_write_the_hooks_dir_of_a_writable_checkout(self):
        self.need_sidecar_images()
        d = self.checkout_on_daemon_host()
        self.up_sidecar(json.dumps({"services": {"docker": {"container_name": f"{P}-dind", "volumes": [
            f"{d}:{d}", f"{d}/.git/hooks:{d}/.git/hooks:ro"]}}}))
        self.assert_hooks_dir_unwritable_by_any_mount_form(d)

    def load_image(self, img=IMG):
        """img (busybox by default) on the sidecar's daemon, which has no way to Docker Hub here; what it is called there."""
        saved = subprocess.run(["docker", "save", img], capture_output=True, check=True).stdout
        r = subprocess.run(["docker", "exec", "-i", f"{P}-dind", "docker", "load"], input=saved, capture_output=True, text=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return re.findall(r"Loaded image(?: ID)?: (\S+)", r.stdout.decode())[-1]  # an image pulled by digest has no tag

    def nested(self, script, input=None):
        return sh("docker", "exec", "-i", f"{P}-dind", "sh", "-c", script, input=input)

    def test_neither_the_sidecar_nor_a_nested_privileged_container_can_read_a_vm_disk(self):
        """The sidecar has no block device of the Docker VM; a nested --privileged container, if it starts, reads none."""
        self.need_sidecar_images()
        self.up_sidecar("services: {docker: {container_name: " + P + "-dind}}\n")
        self.load_image()
        readable = "for d in /dev/vd* /dev/sd* /dev/xvd* /dev/nvme* /dev/loop*; do [ -b $d ] && head -c1 $d >/dev/null && echo $d; done"
        r = self.nested(f"{readable}; true")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        # Rootless in an unprivileged container can't start one at all (ADR 0016): either outcome is fine.
        r = self.nested(f"docker run --rm --privileged {IMG} sh -c '{readable}; true'")
        self.assertEqual(r.stdout, "", r.stderr)

    def test_nested_docker_run_build_and_compose_work(self):
        """Everyday Docker through the sidecar: run, build (with a RUN step), compose; their shims leave no zombie."""
        self.need_sidecar_images()
        self.up_sidecar("services: {docker: {container_name: " + P + "-dind}}\n")
        self.load_image()
        r = self.nested(f"docker run --rm {IMG} echo ran")
        self.assertEqual((r.returncode, r.stdout), (0, "ran\n"), r.stderr)
        r = self.nested("docker build -q -t built - && docker run --rm built cat /f",
                        input=f"FROM {IMG}\nRUN echo built >/f\n")
        self.assertEqual((r.returncode, r.stdout.splitlines()[-1:]), (0, ["built"]), r.stdout + r.stderr)
        r = self.nested("cd /tmp && cat >compose.yml && docker compose -p nested run --rm s",
                        input=f"services: {{s: {{image: '{IMG}', command: [echo, composed]}}}}\n")
        self.assertEqual((r.returncode, r.stdout.splitlines()[-1:]), (0, ["composed"]), r.stdout + r.stderr)
        r = self.nested("ps -o stat,comm | grep '^Z' || true")
        self.assertEqual(r.stdout, "", "zombies in the sidecar")

    def test_a_port_a_nested_container_publishes_is_on_host_docker(self):
        """Story 10: what a nested container publishes, another container on the internal network reaches at docker:PORT."""
        self.need_sidecar_images()
        self.up_sidecar("services: {docker: {container_name: " + P + "-dind}}\n")
        self.load_image()
        r = self.nested(f"docker run -d -p 8080:80 -p 80:80 --hostname nested {IMG} httpd -f -p 80 -h /etc")
        self.assertEqual(r.returncode, 0, r.stderr)
        for port in (8080, 80):
            def answers():
                r = sh("docker", "run", "--rm", "--network", f"{P}_internal", IMG, "wget", "-qO-", "-T", "2",
                       f"http://docker:{port}/hostname")
                return r.stdout.strip() == "nested"
            eventually(f"the nested container at docker:{port}", answers)

    def test_a_nested_container_reaches_an_allowlisted_host_by_the_documented_recipe(self):
        """README's and container-notes.md's `--network host -e HTTPS_PROXY=http://docker-broker:8080`: through the broker."""
        self.sidecar_and_registry()
        mitm = self.load_image(image("broker"))  # python3, whose urllib reads HTTPS_PROXY; busybox's wget ignores it for https
        get = ("import ssl, urllib.request as u; "
               "print(u.urlopen('https://reg.example/v2/', context=ssl.create_default_context(cafile='/r/ca.crt')).read())")
        r = self.nested(f"docker run --rm --network host -e HTTPS_PROXY=http://docker-broker:8080 -v /regfix:/r:ro "
                        f'--entrypoint python3 {mitm} -c "{get}"')
        self.assertEqual((r.returncode, r.stdout), (0, "b'{}'\n"), r.stderr)
        brk = broker_egress_ip(sh(*COMPOSE, "ps", "-q", "docker-broker").stdout.strip())
        self.assertRegex(logs("cat /d/itest.log").stdout, rf"(?m) {re.escape(brk)} \S+ .* CONNECT reg\.example:443$")

    def test_docker_sidecar_pulls_through_its_broker(self):
        self.sidecar_and_registry()
        r = sh("docker", "exec", f"{P}-dind", "docker", "pull", "reg.example/bb:latest")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        brk = broker_egress_ip(sh(*COMPOSE, "ps", "-q", "docker-broker").stdout.strip())
        self.assertRegex(logs("cat /d/itest.log").stdout, rf"(?m) {re.escape(brk)} \S+ .* CONNECT reg\.example:443$")

    def test_docker_sidecar_pulls_from_docker_hub_through_the_broker_and_its_push_is_refused(self):
        self.sidecar_and_registry()
        # Hub's hosts are decrypted by the broker: only a sidecar that trusts its CA gets a pull through.
        r = sh("docker", "exec", f"{P}-dind", "docker", "pull", "bb:latest")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(logs("cat /d/broker/itest.log").stdout, r"(?m) broker GET auth\.docker\.io/token 200 -$")
        # A tag the registry lacks: pushing bb:latest back finds its manifest there and writes nothing.
        r = sh("docker", "exec", f"{P}-dind", "sh", "-c", "docker tag bb:latest bb:pushed && docker push bb:pushed")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        eventually("the refusal in the broker's audit log",
                   lambda: re.search(r"(?m) broker (PUT|PATCH|POST) registry-1\.docker\.io/v2/\S+ (403|error) - registry-write$",
                                     logs("cat /d/broker/itest.log").stdout))
        self.assertRegex(logs("cat /d/broker/itest.log").stdout, r"(?m) broker GET registry-1\.docker\.io/v2/\S+ 200 -$")

    def test_docker_sidecar_uses_its_own_broker_whatever_sessions_run_and_trusts_it_after_a_restart(self):
        self.sidecar_and_registry()
        own = sh(*COMPOSE, "ps", "-q", "docker-broker").stdout.strip()
        for _ in range(3):  # a shared name would spread these over the sessions' brokers
            r = sh("docker", "exec", f"{P}-dind", "sh", "-c", "docker rmi -f bb:latest; docker pull bb:latest")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        clients = set(re.findall(r"(?m) (\S+) \S+ .* CONNECT (?:registry-1|auth)\.docker\.io:443$", logs("cat /d/itest.log").stdout))
        self.assertEqual(clients, {broker_egress_ip(own)})
        # A restart makes a new CA (its key is never stored): the sidecar trusts that one, not the last.
        cert = lambda: sh("docker", "exec", f"{P}-dind", "cat", f"{CERTS}/docker.io/ca.crt").stdout
        old = cert()
        self.assertEqual(sh("docker", "restart", own).returncode, 0)
        eventually("the new CA's cert", lambda: cert() not in ("", old), 30)
        r = sh("docker", "exec", f"{P}-dind", "sh", "-c", "docker rmi -f bb:latest; docker pull bb:latest")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
