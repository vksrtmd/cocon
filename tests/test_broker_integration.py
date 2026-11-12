"""The credential broker (ADR 0014) on a real Docker daemon, offline: a fake upstream on `outside` answers for the
token hosts and a non-brokered one, behind a fake root the broker trusts, and echoes the auth it got.
Skipped without a daemon; in the cocoon that means a session started with --with docker."""
import atexit
import base64
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from harness import KIT, assert_daemon_sees_repo, eventually, have_daemon, shell, stale_runs

IMG = "busybox:1.37"
P = f"cocon-bitest-{os.getpid()}"
LOG_DIR, ALLOW_DIR, LOG_NAME = f"/tmp/{P}-log", f"/tmp/{P}-allow", "itest.log"
COMPOSE_ENV = {"REPO_VOLUMES": P, "LOG_DIR": LOG_DIR, "LOG_NAME": LOG_NAME,
               "ALLOW_DIR": ALLOW_DIR, "REPO_ALLOW": "itest.txt"}
sh = shell(COMPOSE_ENV)
COMPOSE = ["docker", "compose", "-f", str(KIT / "compose.yml"), "-p", P]
GH_PH, CLAUDE_PH = "github_pat_COCON_PLACEHOLDER", "sk-ant-oat01-COCON_PLACEHOLDER"
DENIED = "cocon broker: token not for this host"


def mitm_image():
    m = re.search(r"^\s*image: (mitmproxy/\S+)", (KIT / "compose.yml").read_text(), re.M)
    return m.group(1)


def logs(script):
    return sh("docker", "run", "--rm", "-v", f"{LOG_DIR}:/d", IMG, "sh", "-c", script).stdout


def basic(token):
    return base64.b64encode(f"x-access-token:{token}".encode()).decode()


UPSTREAM = '''import http.server, json, ssl, threading, time
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("authorization") or self.headers.get("x-api-key")
        print(json.dumps({"host": self.headers.get("host"), "path": self.path, "auth": auth}), flush=True)
        if self.path == "/sse":  # three events a second apart, like a streamed reply
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
            for i in range(3):
                self.wfile.write(b"data: %d\\n\\n" % i); self.wfile.flush(); time.sleep(1)
            return
        if self.path == "/hangup":  # an upstream failure: no response at all
            self.close_connection = True; return
        if auth is None and "info/refs" in self.path:  # git sends its credentials only when asked
            self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="GitHub"')
            self.send_header("content-length", "0"); self.end_headers(); return
        body = json.dumps({"auth": auth}).encode()
        self.send_response(200); self.send_header("content-length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    do_POST = do_GET
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain("/f/up.pem", "/f/up.key")
threading.Thread(target=http.server.ThreadingHTTPServer(("", 80), H).serve_forever).start()  # plain HTTP too
s = http.server.ThreadingHTTPServer(("", 443), H); s.socket = ctx.wrap_socket(s.socket, server_side=True)
s.serve_forever()
'''

CLIENT = '''# client.py PROXY CAFILE URL [HEADER:VALUE...]: the status and body; with M set, that method; with N set, only the ms to its first N bytes.
import os, ssl, sys, time, urllib.request
proxy, ca, url = sys.argv[1:4]
o = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy}),
                                urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
req = urllib.request.Request(url, method=os.environ.get("M"), data=b"x" if os.environ.get("M") else None, headers=dict(h.split(":", 1) for h in sys.argv[4:]))
t = time.time()
try:
    r = o.open(req, timeout=10)
    if "N" in os.environ:
        r.read(int(os.environ["N"])); print("ms=%d" % ((time.time() - t) * 1000))
    else:
        print(r.status, r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())
except Exception as e:
    print("error", e)
'''


SNI = '''# sni.py PROXY HOST [SNI]: CONNECT to HOST through PROXY, then TLS with that SNI (none if absent), no cert check.
import socket, ssl, sys
proxy, host = sys.argv[1].rsplit("/", 1)[-1].split(":"), sys.argv[2]
sni = sys.argv[3] if len(sys.argv) > 3 else None
try:
    s = socket.create_connection((proxy[0], int(proxy[1])), 10)
    s.sendall(b"CONNECT %s:443 HTTP/1.1\\r\\nHost: %s:443\\r\\n\\r\\n" % (host.encode(), host.encode()))
    head = b""
    while b"\\r\\n\\r\\n" not in head:
        head += s.recv(1) or sys.exit("error no CONNECT reply")
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    t = ctx.wrap_socket(s, server_hostname=sni)
    t.sendall(b"GET /sni HTTP/1.1\\r\\nHost: %s\\r\\nConnection: close\\r\\n\\r\\n" % host.encode())
    print(t.recv(100).split(b"\\r\\n")[0].decode())
except Exception as e:
    print("error", e)
'''


def remove_run(p):
    """Everything run p (a P) made on the daemon, and its dirs on the daemon's host."""
    ids = sh("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={p}").stdout.split()
    names = [f"{p}-up", f"{p}-broker", f"{p}-broker2", f"{p}-nolog", f"{p}-noname", f"{p}-nopub", f"{p}-dev"]
    sh("docker", "rm", "-fv", *ids, *names)
    sh("docker", "volume", "rm", f"{p}-fix", f"{p}-key", f"{p}-ca", f"{p}-key2", f"{p}-ca2", f"{p}-ca3", f"{p}-empty", f"{p}-cfg")
    sh("docker", "network", "rm", f"{p}_internal", f"{p}_broker-egress", f"{p}_outside")
    sh("docker", "run", "--rm", "-v", "/tmp:/t", IMG, "rm", "-rf", f"/t/{p}-log", f"/t/{p}-allow", f"/t/{p}-ro")


@unittest.skipUnless(have_daemon(), "no Docker daemon (`docker info` failed); in a cocoon, restart with --with docker")
class BrokerOnDocker(unittest.TestCase):
    mitm = ""

    @classmethod
    def compose(cls, *args):
        return sh(*COMPOSE, *args)

    @classmethod
    def setUpClass(cls):
        for p in stale_runs("cocon-bitest-"):
            remove_run(p)
        cls.addClassCleanup(cls.cleanup)  # also when the setup below fails half way
        atexit.register(remove_run, P)  # on Ctrl-C too, which skips class cleanups
        cls.mitm = mitm_image()
        sh("docker", "pull", "-q", IMG)
        assert_daemon_sees_repo(IMG)
        sh("docker", "pull", "-q", cls.mitm)
        logs("mkdir -p /d/broker && chmod 777 /d /d/broker")  # as the launcher makes them
        # A raw IP on the repo's allowlist: squid must still refuse it, the deny rule comes first.
        sh("docker", "run", "--rm", "-v", f"{ALLOW_DIR}:/a", IMG, "sh", "-c", "echo 192.0.2.1 >/a/itest.txt")
        r = cls.compose("up", "-d", "egress")  # brings up the networks the broker and the upstream join
        assert r.returncode == 0, f"egress didn't start: {r.stderr}"
        cls.fx = tempfile.TemporaryDirectory()
        fx = Path(cls.fx.name)
        cls.make_fixtures(fx)
        sh("docker", "volume", "create", f"{P}-fix")
        tar = subprocess.run(["tar", "-C", str(fx), "-cf", "-", "."], capture_output=True, check=True).stdout
        r = subprocess.run(["docker", "run", "--rm", "-i", "-v", f"{P}-fix:/f", IMG, "tar", "-xf", "-", "-C", "/f"],
                           input=tar, capture_output=True)
        assert r.returncode == 0, r.stderr
        r = sh("docker", "run", "-d", "--name", f"{P}-up", "--network", f"{P}_outside", "-v", f"{P}-fix:/f:ro",
               "--entrypoint", "python3", "--network-alias", "api.github.com", "--network-alias", "github.com",
               "--network-alias", "api.anthropic.com", "--network-alias", "registry.npmjs.org", "--network-alias", "proxy.golang.org",
               "--network-alias", "storage.googleapis.com", cls.mitm,
               "-u", "/f/upstream.py")
        assert r.returncode == 0, r.stderr
        cls.broker(f"{P}-broker", f"{P}-key", f"{P}-ca", "-e", "GH_TOKEN=REAL-gh", "-e", "CLAUDE_CODE_OAUTH_TOKEN=REAL-claude")
        cls.broker(f"{P}-broker2", f"{P}-key2", f"{P}-ca2", "-e", "CLAUDE_CODE_OAUTH_TOKEN=REAL-claude")  # no GitHub token
        for ca in (f"{P}-ca", f"{P}-ca2"):
            eventually(f"{ca}'s cert", lambda: sh("docker", "run", "--rm", "-v", f"{ca}:/ca", IMG, "test", "-s",
                                                  "/ca/ca.pem").returncode == 0, 30)

    @classmethod
    def make_fixtures(cls, fx):
        def openssl(*args):
            r = subprocess.run(["openssl", *args], capture_output=True, text=True, cwd=fx)
            assert r.returncode == 0, r.stderr
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=fakeroot", "-keyout", "root.key",
                "-out", "root.pem", "-addext", "basicConstraints=critical,CA:true", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
        openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=upstream", "-keyout", "up.key", "-out", "up.csr")
        (fx / "ext").write_text("subjectAltName=DNS:api.github.com,DNS:github.com,DNS:api.anthropic.com,DNS:registry.npmjs.org,DNS:proxy.golang.org,"
                                "DNS:storage.googleapis.com\n"
                                "authorityKeyIdentifier=keyid\nkeyUsage=digitalSignature,keyEncipherment\n"
                                "extendedKeyUsage=serverAuth\n")
        openssl("x509", "-req", "-in", "up.csr", "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial", "-days", "2",
                "-extfile", "ext", "-out", "up.pem")
        (fx / "upstream.py").write_text(UPSTREAM)
        (fx / "client.py").write_text(CLIENT)
        (fx / "sni.py").write_text(SNI)
        subprocess.run(["chmod", "-R", "a+rX", str(fx)], check=True)  # the broker is root without capabilities

    @classmethod
    def broker(cls, name, key, ca, *env):
        """The session's broker as the launcher runs it, trusting the fake root upstream."""
        r = cls.compose("run", "-d", "--name", name, "-v", f"{key}:/ca", "-v", f"{ca}:/pub", "-v", f"{P}-fix:/f:ro", *env,
                        "broker", "--set", "ssl_verify_upstream_trusted_ca=/f/root.pem")
        assert r.returncode == 0, f"{name} didn't start: {r.stderr}"
        r = sh("docker", "update", "--restart", "on-failure", name)
        assert r.returncode == 0, f"{name}: no restart policy: {r.stderr}"

    @classmethod
    def cleanup(cls):
        remove_run(P)
        if hasattr(cls, "fx"):
            cls.fx.cleanup()

    def req(self, proxy, ca, url, *headers, ca_volume=None, n=None, net="internal", method=None):
        """Like dev, from `internal`; ca 'broker' is the cert in the CA volume, 'root' the fake root."""
        ca = {"broker": "/ca/ca.pem", "root": "/f/root.pem"}.get(ca, ca)
        r = sh("docker", "run", "--rm", "--network", f"{P}_{net}", "-v", f"{ca_volume or P + '-ca'}:/ca:ro",
               "-v", f"{P}-fix:/f:ro", *(["-e", f"N={n}"] if n else []), *(["-e", f"M={method}"] if method else []), "--entrypoint", "python3", self.mitm,
               "/f/client.py", proxy, ca, url, *headers)
        return (r.stdout + r.stderr).strip()

    @property
    def B(self):
        return f"http://{P}-broker:8080"

    def upstream_log(self):
        r = sh("docker", "logs", f"{P}-up")
        return r.stdout + r.stderr

    def test_bearer_gh_token_and_git_basic_placeholders_swapped_at_their_own_hosts(self):
        self.assertEqual(self.req(self.B, "broker", "https://api.anthropic.com/v1/messages", f"Authorization:Bearer {CLAUDE_PH}"),
                         '200 {"auth": "Bearer REAL-claude"}')
        self.assertEqual(self.req(self.B, "broker", "https://api.anthropic.com/v1/messages", f"x-api-key:{CLAUDE_PH}"),
                         '200 {"auth": "REAL-claude"}')
        self.assertEqual(self.req(self.B, "broker", "https://api.github.com/user", f"Authorization:token {GH_PH}"),
                         '200 {"auth": "token REAL-gh"}')
        self.assertEqual(self.req(self.B, "broker", "https://github.com/o/r.git/info/refs", f"Authorization:Basic {basic(GH_PH)}"),
                         f'200 {{"auth": "Basic {basic("REAL-gh")}"}}')

    def test_wrong_decrypted_host_or_no_real_token_gets_the_brokers_403(self):
        out = self.req(self.B, "broker", "https://api.anthropic.com/wrong-host", f"Authorization:token {GH_PH}")
        self.assertTrue(out.startswith(f"403 {DENIED}"), out)
        out = self.req(self.B, "broker", "https://api.github.com/wrong-host", f"Authorization:Bearer {CLAUDE_PH}")
        self.assertTrue(out.startswith("403 "), out)
        out = self.req(f"http://{P}-broker2:8080", "broker", "https://api.github.com/no-token", f"Authorization:token {GH_PH}",
                       ca_volume=f"{P}-ca2")
        self.assertTrue(out.startswith("403 "), out)
        self.req(self.B, "broker", "https://api.github.com/control-1", f"Authorization:token {GH_PH}")
        up = self.upstream_log()
        self.assertIn("control-1", up)  # a request that went through shows: the absences below mean something
        self.assertNotIn("wrong-host", up)
        self.assertNotIn("no-token", up)

    def test_plain_http_or_a_forged_host_header_gets_the_brokers_403(self):
        out = self.req(self.B, "broker", "http://api.github.com/plain-http", f"Authorization:token {GH_PH}")
        self.assertTrue(out.startswith(f"403 {DENIED}"), out)
        out = self.req(self.B, "broker", "https://api.anthropic.com/host-header", "Host:api.github.com", f"Authorization:token {GH_PH}")
        self.assertTrue(out.startswith("403 cocon broker: Host header isn't the connection's host"), out)
        # On a host without a token too: a shared edge would route on the Host header, to another tenant.
        out = self.req(self.B, "broker", "https://registry.npmjs.org/fronted", "Host:other-tenant.example")
        self.assertTrue(out.startswith("403 cocon broker: Host header isn't the connection's host"), out)
        self.req(self.B, "broker", "https://api.github.com/control-2", f"Authorization:token {GH_PH}")
        up = self.upstream_log()
        self.assertIn("control-2", up)
        self.assertNotIn("plain-http", up)
        self.assertNotIn("host-header", up)
        self.assertNotIn("fronted", up)
        # its audit line names the real destination, not the forged one
        eventually("the audit line", lambda: " api.anthropic.com/host-header 403 " in logs("cat /d/broker/itest.log"))
        log = logs("cat /d/broker/itest.log")
        self.assertNotIn("api.github.com/host-header", log)

    def test_undecodable_basic_refused_one_without_a_placeholder_arrives_byte_for_byte(self):
        bad = basic(GH_PH).rstrip("=")  # padding cut: not valid base64
        out = self.req(self.B, "broker", "https://github.com/o/r.git/malformed", f"Authorization:Basic {bad}")
        self.assertTrue(out.startswith(f"403 {DENIED}"), out)
        raw = base64.b64encode(b"user:\xff\xfe").decode()  # not UTF-8
        out = self.req(self.B, "broker", "https://github.com/o/r.git/raw", f"Authorization:Basic {raw}")
        self.assertTrue(out.startswith(f"403 {DENIED}"), out)  # decodes, but it is a foreign credential
        out = self.req(self.B, "root", "https://proxy.golang.org/raw", f"Authorization:Basic {raw}")
        self.assertEqual(out, f'200 {{"auth": "Basic {raw}"}}')

    def test_foreign_authorization_on_a_github_host_gets_403_the_placeholder_still_works(self):
        out = self.req(self.B, "broker", "https://api.github.com/gists", "Authorization:Bearer FOREIGN-SECRET")
        self.assertTrue(out.startswith(f"403 {DENIED}"), out)
        self.assertTrue(self.req(self.B, "broker", "https://api.github.com/user", f"Authorization:token {GH_PH}").startswith("200 "))
        eventually("the audit line", lambda: " api.github.com/gists 403 - foreign-credential" in logs("cat /d/broker/itest.log"))
        self.assertNotIn("FOREIGN-SECRET", logs("cat /d/broker/itest.log"))

    def test_other_hosts_tunnelled_never_decrypted_their_placeholders_unchanged(self):
        out = self.req(self.B, "root", "https://proxy.golang.org/x", f"Authorization:Bearer {GH_PH}")
        self.assertEqual(out, f'200 {{"auth": "Bearer {GH_PH}"}}')  # upstream's own cert
        self.assertTrue(self.req(self.B, "broker", "https://proxy.golang.org/x").startswith("error"))
        self.assertTrue(self.req(self.B, "root", "https://api.github.com/user").startswith("error"))

    def test_npm_style_put_gets_403_with_an_audit_line_reads_pass_and_never_reach_upstream(self):
        out = self.req(self.B, "broker", "https://registry.npmjs.org/pkg/read", "Authorization:Bearer MINE")
        self.assertEqual(out, '200 {"auth": "Bearer MINE"}')  # a read with a credential: passed on
        out = self.req(self.B, "broker", "https://registry.npmjs.org/pkg-put", "Authorization:Bearer MINE", method="PUT")
        self.assertTrue(out.startswith("403 cocon broker: registry writes refused"), out)
        eventually("the audit line", lambda: re.search(r"(?m) broker PUT registry\.npmjs\.org/pkg-put 403 - registry-write$",
                                                       logs("cat /d/broker/itest.log")))
        up = self.upstream_log()
        self.assertIn("/pkg/read", up)
        self.assertNotIn("pkg-put", up)

    def test_google_storage_reads_of_the_go_proxys_bucket_pass_the_rest_never_reach_upstream(self):
        gcs = "https://storage.googleapis.com"
        out = self.req(self.B, "broker", f"{gcs}/proxy-golang-org-prod/mod.zip?Expires=1&Signature=s")
        self.assertEqual(out, '200 {"auth": null}')
        for path in ("/attacker-bucket/out", "/proxy-golang-org-prod/../attacker-bucket/dots"):
            out = self.req(self.B, "broker", gcs + path)
            self.assertTrue(out.startswith("403 cocon broker: only reads of proxy-golang-org-prod"), (path, out))
        out = self.req(self.B, "broker", f"{gcs}/proxy-golang-org-prod/gcs-put", method="PUT")
        self.assertTrue(out.startswith("403 "), out)
        eventually("the audit line", lambda: " storage.googleapis.com/attacker-bucket/out 403 - bucket" in logs("cat /d/broker/itest.log"))
        up = self.upstream_log()
        self.assertIn("/proxy-golang-org-prod/mod.zip", up)
        for absent in ("attacker-bucket", "gcs-put"):
            self.assertNotIn(absent, up)

    def sni(self, host, sni=None):
        r = sh("docker", "run", "--rm", "--network", f"{P}_internal", "-v", f"{P}-fix:/f:ro", "--entrypoint", "python3",
               self.mitm, "/f/sni.py", self.B, host, *([sni] if sni else []))
        return (r.stdout + r.stderr).strip()

    def test_tunnel_whose_sni_names_another_host_or_none_is_dropped_with_an_audit_line(self):
        self.assertEqual(self.sni("registry.npmjs.org", "registry.npmjs.org"), "HTTP/1.0 200 OK")  # control
        for host in ("registry.npmjs.org", "api.github.com"):  # tunnelled, decrypted
            self.assertTrue(self.sni(host, "other-tenant.example").startswith("error"), host)
        self.assertTrue(self.sni("registry.npmjs.org").startswith("error"))
        self.assertEqual(self.upstream_log().count('"path": "/sni"'), 1)  # the control's alone
        for want in ("registry.npmjs.org:443 refused - sni-mismatch sni=other-tenant.example",
                     "api.github.com:443 refused - sni-mismatch sni=other-tenant.example",
                     "registry.npmjs.org:443 refused - no-sni sni=-"):
            eventually(want, lambda: re.search(rf"(?m)^[0-9-]{{10}}T[0-9:]{{8}}\+0000 broker CONNECT {re.escape(want)}$",
                                               logs("cat /d/broker/itest.log")))

    def test_broker_bypassed_straight_to_squid_only_the_placeholder_arrives(self):
        out = self.req("http://squid-in:3128", "root", "https://api.github.com/user", f"Authorization:token {GH_PH}", net="broker-egress")
        self.assertEqual(out, f'200 {{"auth": "token {GH_PH}"}}')

    def test_dev_cant_open_a_connection_to_egress_by_name_or_any_of_its_addresses(self):
        ips = sh("docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
                 f"{P}-egress-1").stdout.split()
        self.assertEqual(len(ips), 2, ips)
        for host in ("egress", "squid-in", *ips):
            r = sh("docker", "run", "--rm", "--network", f"{P}_internal", IMG, "nc", "-z", "-w", "3", host, "3128")
            self.assertNotEqual(r.returncode, 0, f"dev reached egress at {host}")
        r = sh("docker", "exec", f"{P}-broker", "python3", "-c",
               "import socket; socket.create_connection(('egress', 3128), 3)")
        self.assertEqual(r.returncode, 0, f"the broker can't reach egress: {r.stderr}")  # the check above means something

    def test_dev_cant_resolve_outside_names_only_the_broker(self):
        def lookup(net, host):  # fully qualified: busybox gives up on the first search domain's SERVFAIL (CI's runners)
            return sh("docker", "run", "--rm", "--network", f"{P}_{net}", IMG, "nslookup", f"{host}.").returncode == 0
        internal = sh("docker", "network", "inspect", "-f", "{{.Internal}}", f"{P}_internal").stdout.strip()
        self.assertEqual(internal, "true", f"{P}_internal is not internal")
        if not lookup("outside", "example.com"):  # api.github.com resolves there by the fake upstream's alias
            self.skipTest("the daemon has no outside DNS, so the negative lookups below would pass vacuously")
        self.assertTrue(lookup("internal", f"{P}-broker"), "dev can't resolve the broker")
        for host in ("example.com", "api.github.com"):
            self.assertFalse(lookup("internal", host), f"dev resolved {host}")

    def test_raw_ip_connect_refused_by_squid_and_logged_allowlisted_names_still_work(self):
        for host in ("192.0.2.1", "[2001:db8::1]", "0x7f.1", "2130706433"):
            out = self.req("http://squid-in:3128", "root", f"https://{host}/", net="broker-egress")
            self.assertIn("403", out, f"{host}: {out}")
        self.assertIn("200", self.req("http://squid-in:3128", "root", "https://api.github.com/user", net="broker-egress"))
        # The client hangs up once it has the 403's headers, so squid sometimes logs the denial as _ABORTED.
        eventually("squid's denials", lambda: all(
            re.search(rf" TCP_DENIED(_ABORTED)?/403 .* CONNECT {re.escape(h)}:443$", logs("cat /d/itest.log"), re.M)
            for h in ("192.0.2.1", "[2001:db8::1]", "127.0.0.1")))  # squid logs the odd forms as dotted

    def test_connect_to_an_allowlisted_host_on_a_port_other_than_443_refused_by_squid_and_logged(self):
        proxy = "http://squid-in:3128"
        self.assertIn("200", self.req(proxy, "root", "https://api.github.com/user", net="broker-egress"))  # the control
        # 80 is in Safe_ports, so only the CONNECT !SSL_ports rule refuses it; 22 and 8443 fall to !Safe_ports too.
        for port in (22, 80, 8443):
            out = self.req(proxy, "root", f"https://api.github.com:{port}/", net="broker-egress")
            self.assertIn("403", out, f":{port}: {out}")
        # The client hangs up once it has the 403's headers, so squid sometimes logs the denial as _ABORTED.
        eventually("squid's denials", lambda: all(
            re.search(rf" TCP_DENIED(_ABORTED)?/403 .* CONNECT api\.github\.com:{port}$", logs("cat /d/itest.log"), re.M)
            for port in (22, 80, 8443)))

    def test_streamed_reply_isnt_buffered(self):
        out = self.req(self.B, "broker", "https://api.anthropic.com/sse", n=8)
        self.assertRegex(out, r"^ms=\d+$")
        self.assertLess(int(out[3:]), 2000, "first event after the whole stream")

    def test_audit_one_line_per_decrypted_request_no_query_headers_or_tokens(self):
        self.req(self.B, "root", "https://proxy.golang.org/squid")  # tunnelled: only squid's log has a line
        eventually("squid's log", lambda: logs("stat -c %u:%g /d/itest.log 2>/dev/null").strip())
        owner = logs("stat -c %u:%g /d/itest.log")
        self.req(self.B, "broker", "https://api.anthropic.com/v1/messages", f"Authorization:Bearer {CLAUDE_PH}")
        self.req(self.B, "broker", "https://github.com/o/r.git/info/refs", f"Authorization:Basic {basic(GH_PH)}")
        self.req(self.B, "broker", "https://api.anthropic.com/wrong-host", f"Authorization:token {GH_PH}")
        self.req(self.B, "broker", "https://api.github.com/repos/o/r?q=SECRET-QUERY", f"Authorization:token {GH_PH}")
        self.req(self.B, "broker", "https://api.github.com/hangup")
        self.req(self.B, "root", "https://proxy.golang.org/tunnelled")
        eventually("the last audit line", lambda: " api.github.com/hangup error " in logs("cat /d/broker/itest.log"))
        out = logs("cat /d/broker/itest.log")
        for want in ("GET api.anthropic.com/v1/messages 200 claude", "GET github.com/o/r.git/info/refs 200 github",
                     "GET api.github.com/repos/o/r 200 github", "GET api.anthropic.com/wrong-host 403 -",
                     "GET api.github.com/hangup error -"):
            self.assertRegex(out, rf"(?m)^[0-9-]{{10}}T[0-9:]{{8}}\+0000 broker {want}$")
        self.assertEqual(out.count(" api.github.com/hangup "), 1, out)
        for never in ("SECRET-QUERY", "REAL-", "PLACEHOLDER", "proxy.golang.org"):
            self.assertNotIn(never, out)
        self.assertEqual(logs("stat -c %u:%g /d/itest.log"), owner, "squid's file changed owner")
        mnt = sh("docker", "inspect", "-f", "{{range .Mounts}}{{.Destination}}={{.Source}} {{end}}", f"{P}-broker").stdout
        self.assertIn(f" /var/log/broker={LOG_DIR}/broker ", f" {mnt}")
        self.assertNotIn(f"={LOG_DIR} ", f" {mnt}")

    def test_no_capabilities_no_new_privileges(self):
        r = sh("docker", "inspect", "-f", "{{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}}", f"{P}-broker")
        self.assertEqual(r.stdout.strip(), "[ALL] [no-new-privileges:true]")

    def stopped(self, name, *args, ro=False, env=None):
        """The broker run with args, once it stopped: its exit code and output, and whether CA volume ca3 got a cert."""
        r = sh(*COMPOSE, "run", "-d", "--name", name, "-v", f"{P}-ca3:/pub{':ro' if ro else ''}", *args, "broker", env=env)
        if r.returncode:
            return "didn't run"
        try:
            code = sh("docker", "wait", name, timeout=30).stdout.strip()
        except subprocess.TimeoutExpired:  # it serves despite what should have stopped it
            sh("docker", "rm", "-f", name)
            return "still running"
        out = sh("docker", "logs", name)
        published = sh("docker", "run", "--rm", "-v", f"{P}-ca3:/pub", IMG, "sh", "-c", "test -e /pub/ca.pem && echo published")
        return f"exit={code}\n{out.stdout}{out.stderr}{published.stdout}"

    def test_unwritable_log_or_no_log_name_or_unpublishable_ca_stops_it_before_it_serves(self):
        # A directory where the log goes: a mode 555 dir stays writable through macOS hosts' file sharing.
        sh("docker", "run", "--rm", "-v", "/tmp:/t", IMG, "mkdir", "-p", f"/t/{P}-ro/broker/{LOG_NAME}")
        out = self.stopped(f"{P}-nolog", env={"LOG_DIR": f"/tmp/{P}-ro"})
        self.assertRegex(out, r"^exit=[1-9]")
        self.assertIn("audit log", out)
        self.assertNotIn("published", out)
        out = self.stopped(f"{P}-noname", "-e", "LOG_NAME=")
        self.assertRegex(out, r"^exit=[1-9]")
        self.assertIn("LOG_NAME", out)
        self.assertNotIn("published", out)
        out = self.stopped(f"{P}-nopub", ro=True)
        self.assertRegex(out, r"^exit=[1-9]")
        self.assertIn("couldn't publish", out)

    def test_restarted_within_the_session_it_keeps_its_ca(self):
        before = sh("docker", "run", "--rm", "-v", f"{P}-ca:/ca:ro", IMG, "cat", "/ca/ca.pem").stdout
        sh("docker", "exec", f"{P}-broker", "python3", "-c", """import os
for p in filter(str.isdigit, os.listdir("/proc")):
    if open("/proc/%s/cmdline" % p, "rb").read().split(b"\\0")[1:2] == [b"/usr/local/bin/mitmdump"]:
        os.kill(int(p), 9)""")
        eventually("the restart", lambda: sh("docker", "inspect", "-f", "{{.RestartCount}} {{.State.Running}}",
                                             f"{P}-broker").stdout.strip() == "1 true")
        # Until it listens again; its cert is republished then (run.sh clears the old one first), so a try before
        # that finds no cert or no listener.
        out = ""
        for _ in range(40):
            out = self.req(self.B, "broker", "https://api.github.com/user", f"Authorization:token {GH_PH}")
            if out.startswith("200 "):
                break
            time.sleep(0.5)
        self.assertEqual(out, '200 {"auth": "token REAL-gh"}')
        self.assertEqual(sh("docker", "run", "--rm", "-v", f"{P}-ca:/ca:ro", IMG, "cat", "/ca/ca.pem").stdout, before, "CA changed")

    def dev_image(self):
        """cocon:latest if built, else node:22 (curl, git, Node), pulled if need be: either runs the entrypoint."""
        for img in ("cocon:latest", "node:22"):
            if sh("docker", "image", "inspect", img).returncode == 0:
                return img
        if sh("docker", "pull", "-q", "node:22", timeout=600).returncode == 0:
            return "node:22"
        self.skipTest("no cocon:latest or node:22 image, and node:22 doesn't pull; dev's CA checks skipped")

    def dev(self, *envs, cmd, ca=None, cfg=None, workdir="/"):
        img = self.dev_image()
        args = ["docker", "run", "--rm", "--name", f"{P}-dev", "--network", f"{P}_internal", "-w", workdir,
                "-v", f"{ca or P + '-ca'}:/opt/cocon/broker-ca:ro",
                "-v", f"{KIT / 'entrypoint.sh'}:/opt/cocon/entrypoint.sh:ro",
                "-e", f"HTTPS_PROXY={self.B}", "-e", f"https_proxy={self.B}"]
        if cfg:
            args += ["-v", f"{cfg}:/home/claude/.claude", "-e", "CLAUDE_CONFIG_DIR=/home/claude/.claude"]
        for e in envs:
            args += ["-e", e]
        r = sh(*args, "--entrypoint", "bash", img, "/opt/cocon/entrypoint.sh", *cmd)
        return r.stdout + r.stderr

    def test_dev_curl_git_and_node_trust_the_brokers_ca(self):
        out = self.dev(f"GH_TOKEN={GH_PH}", "GIT_TERMINAL_PROMPT=0", "NODE_USE_ENV_PROXY=1", cmd=["sh", "-c", """
            curl -sS -H "Authorization: token $GH_TOKEN" https://api.github.com/curl; echo
            git ls-remote "https://x-access-token:$GH_TOKEN@github.com/o/r.git"
            node -e 'fetch("https://api.github.com/node", {headers: {authorization: "token " + process.env.GH_TOKEN}})
              .then(r => r.text()).then(console.log, e => console.log(e.cause || e))'"""])
        self.assertEqual(out.count('{"auth": "token REAL-gh"}'), 2, out)
        self.assertIn(f'"path": "/o/r.git/info/refs?service=git-upload-pack", "auth": "Basic {basic("REAL-gh")}"', self.upstream_log())

    def test_dev_removes_a_login_credential_at_start_with_one_line(self):
        img = self.dev_image()
        sh("docker", "run", "--rm", "-v", f"{P}-cfg:/home/claude/.claude", "--entrypoint", "sh", img, "-c",
           "cd /home/claude/.claude && echo '{}' >.credentials.json && echo h >history.jsonl")
        out = self.dev(cmd=["ls", "-A", "/home/claude/.claude"], cfg=f"{P}-cfg")
        self.assertNotIn(".credentials.json\n", out.replace("/login credential (/home/claude/.claude/.credentials.json)", ""))
        self.assertIn("history.jsonl", out)
        self.assertEqual(out.count("removed a /login credential"), 1, out)
        self.assertNotIn("/login credential", self.dev(cmd=["true"], cfg=f"{P}-cfg"))

    def test_dev_starts_onboarded_and_keeps_the_rest_of_the_config(self):
        f = "/home/claude/.claude/.claude.json"
        def config():  # dev's output also holds the entrypoint's warnings
            out = self.dev(cmd=["cat", f], cfg=f"{P}-cfg")
            return json.loads(out[out.index("{"):out.rindex("}") + 1])
        def put(text):
            sh("docker", "run", "--rm", "-v", f"{P}-cfg:/home/claude/.claude", "--entrypoint", "sh", self.dev_image(),
               "-c", f"echo '{text}' >{f}")
        put('{"userID": "u", "hasCompletedOnboarding": false}')
        self.assertEqual(config(), {"userID": "u", "hasCompletedOnboarding": True})
        sh("docker", "run", "--rm", "-v", f"{P}-cfg:/home/claude/.claude", "--entrypoint", "rm", self.dev_image(), f)
        self.assertEqual(config(), {"hasCompletedOnboarding": True})
        put("{broken")
        self.assertIn("{broken\n", self.dev(cmd=["cat", f], cfg=f"{P}-cfg"))

    def test_dev_skips_a_repo_settings_file_it_cant_read_and_still_starts(self):
        repo = "/home/claude/.claude/repo"
        sh("docker", "run", "--rm", "-v", f"{P}-cfg:/home/claude/.claude", "--entrypoint", "sh", self.dev_image(), "-c",
           f"mkdir -p {repo}/.claude && echo '{{\"enabledPlugins\": {{}},}}' >{repo}/.claude/settings.local.json")
        # node:22 has no jq: there jq's failure is the missing command, which stopped the entrypoint all the same.
        out = self.dev(cmd=["echo", "started"], cfg=f"{P}-cfg", workdir=repo)
        self.assertIn(f"can't read the plugins in {repo}/.claude/settings.local.json; skipping it", out)
        self.assertIn("started\n", out)

    def test_dev_entrypoint_fails_clearly_when_the_cas_never_appears(self):
        sh("docker", "volume", "create", f"{P}-empty")
        out = self.dev(cmd=["true"], ca=f"{P}-empty")
        self.assertIn("the credential broker's CA never appeared", out)


if __name__ == "__main__":
    unittest.main()
