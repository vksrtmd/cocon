"""broker/addon.py (ADR 0014): placeholder swap, refusals and the audit line, with a stub `mitmproxy` module."""
import base64
import contextlib
import importlib.util
import io
import os
import re
import runpy
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from harness import KIT


class Response:
    def __init__(self, code, body):
        self.status_code, self.content = code, body

    @classmethod
    def make(cls, code, body):
        return cls(code, body)


class Request:
    def __init__(self, host, headers, scheme="https", method="GET", path="/", authority=None):
        self.host, self.headers, self.scheme, self.method, self.path = host, dict(headers), scheme, method, path
        self.authority = authority  # HTTP/2's :authority

    @property
    def host_header(self):  # mitmproxy's: :authority over HTTP/2, else Host; clients send the host they connect to
        return self.authority or self.headers.get("host", self.host)


class Flow:
    def __init__(self, *args, **kw):
        self.request, self.response, self.metadata = Request(*args, **kw), None, {}


def server_names(*entries):
    """A server_name extension's body: the list of (name type, name) entries."""
    body = b"".join(bytes([t]) + len(n).to_bytes(2, "big") + n for t, n in entries)
    return len(body).to_bytes(2, "big") + body


class Hello:  # mitmproxy.tls.ClientHello: the SNI if valid, and (type, raw body) per extension
    def __init__(self, sni, *ext_types, names=None):
        sni_body = names if names is not None else server_names((0, (sni or "").encode()))
        self.sni, self.extensions = sni, [(0, sni_body)] * bool(sni) + [(t, b"") for t in ext_types]


HELLOS = {}  # client bytes -> what the stub parse_client_hello returns (None: incomplete) or raises


def parse_client_hello(data):
    r = HELLOS[data]
    if isinstance(r, Exception):
        raise r
    return r


class Layer:
    def __init__(self, context):
        self.context = context

    def handle_event(self, event):
        yield from self._handle_event(event)


class HttpLayer:
    pass


class CloseConnection:
    def __init__(self, conn):
        self.connection = conn


class NextLayer:
    """A tunnel's next-layer decision: CONNECT host:port, client bytes so far; tls: the client side already decrypted."""
    def __init__(self, host, data, port=443, tls=False, layers=(HttpLayer(),)):
        client = types.SimpleNamespace(tls=tls)
        server = types.SimpleNamespace(address=(host, port) if host else None)
        self.context = types.SimpleNamespace(client=client, server=server, layers=list(layers))
        self.layer, self._data = None, data

    def data_client(self):
        return self._data


def stub_mitmproxy():
    http = types.ModuleType("mitmproxy.http")
    http.Response = Response
    pkg = types.ModuleType("mitmproxy")
    pkg.http = http
    layer = types.ModuleType("mitmproxy.proxy.layer")
    layer.Layer = Layer
    commands = types.ModuleType("mitmproxy.proxy.commands")
    commands.CloseConnection = CloseConnection
    layers = types.ModuleType("mitmproxy.proxy.layers")
    layers.HttpLayer = HttpLayer
    tls = types.ModuleType("mitmproxy.proxy.layers.tls")
    tls.parse_client_hello = parse_client_hello
    proxy = types.ModuleType("mitmproxy.proxy")
    proxy.layer, proxy.commands, proxy.layers, layers.tls = layer, commands, layers, tls
    pkg.proxy = proxy
    return mock.patch.dict(sys.modules, {"mitmproxy": pkg, "mitmproxy.http": http, "mitmproxy.proxy": proxy,
                                         "mitmproxy.proxy.layer": layer, "mitmproxy.proxy.commands": commands,
                                         "mitmproxy.proxy.layers": layers, "mitmproxy.proxy.layers.tls": tls})


def load(tmp, **tokens):
    """addon.py, imported afresh with exactly these tokens in its environment; its log lands under tmp."""
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")}
    with stub_mitmproxy(), mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(os.environ, tokens):
        spec = importlib.util.spec_from_file_location("addon", KIT / "broker" / "addon.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    mod.open = lambda path, mode="r": open(Path(tmp) / Path(path).name, mode)  # /var/log/broker/<LOG_NAME>
    return mod


class Addon(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        patch = mock.patch.dict(os.environ, {"LOG_NAME": "t.log"})  # read per line, as the broker's compose sets it
        patch.start()
        self.addCleanup(patch.stop)
        self.a = load(self.d, CLAUDE_CODE_OAUTH_TOKEN="REAL-claude", GH_TOKEN="REAL-gh")
        self.claude_ph, self.gh_ph = "sk-ant-oat01-COCON_PLACEHOLDER", "github_pat_COCON_PLACEHOLDER"

    def send(self, host, headers, **kw):
        f = Flow(host, headers, **kw)
        self.a.requestheaders(f)
        return f

    def assertRefused(self, f):
        self.assertEqual(f.response.status_code, 403)
        self.assertIn(b"token not for this host", f.response.content)

    def test_bearer_x_api_key_token_and_basic_swapped_at_their_own_hosts(self):
        f = self.send("api.anthropic.com", {"authorization": f"Bearer {self.claude_ph}"})
        self.assertEqual(f.request.headers["authorization"], "Bearer REAL-claude")
        f = self.send("api.anthropic.com", {"x-api-key": self.claude_ph})
        self.assertEqual(f.request.headers["x-api-key"], "REAL-claude")
        f = self.send("api.github.com", {"authorization": f"token {self.gh_ph}"})
        self.assertEqual(f.request.headers["authorization"], "token REAL-gh")
        basic = lambda t: "Basic " + base64.b64encode(f"x-access-token:{t}".encode()).decode()
        f = self.send("github.com", {"authorization": basic(self.gh_ph)})
        self.assertEqual(f.request.headers["authorization"], basic("REAL-gh"))
        self.assertEqual(f.metadata["swapped"], "github")

    def test_placeholder_on_another_decrypted_host_or_without_a_real_token_refused(self):
        self.assertRefused(self.send("api.anthropic.com", {"authorization": f"token {self.gh_ph}"}))
        self.assertRefused(self.send("api.github.com", {"authorization": f"Bearer {self.claude_ph}"}))
        self.a = load(self.d, CLAUDE_CODE_OAUTH_TOKEN="REAL-claude")  # a repo without a GitHub token
        self.assertRefused(self.send("api.github.com", {"authorization": f"token {self.gh_ph}"}))

    def test_plain_http_and_unknown_hosts_refused_never_sent_on(self):
        self.assertRefused(self.send("api.github.com", {"authorization": f"token {self.gh_ph}"}, scheme="http"))
        self.assertRefused(self.send("example.com", {"authorization": f"token {self.gh_ph}"}))

    def test_a_host_header_naming_another_host_is_refused_on_every_decrypted_host(self):
        for host in ("api.anthropic.com", "registry.npmjs.org", "files.pythonhosted.org", "storage.googleapis.com"):
            for forged in ("api.github.com", "other-tenant.example", "", "registry.npmjs.org.evil"):
                f = self.send(host, {"host": forged, "authorization": f"token {self.gh_ph}"}, path="/proxy-golang-org-prod/x")
                self.assertEqual(f.response.status_code, 403, (host, forged))
                self.assertIn(b"Host header isn't the connection's host", f.response.content)
                self.assertEqual(f.metadata["reason"], "host-mismatch")
        # The same name, by DNS's rules or with the default port: passes.
        for same in ("REGISTRY.npmjs.org.", "registry.npmjs.org:443"):
            self.assertIsNone(self.send("registry.npmjs.org", {"host": same}).response, same)

    def test_http2_host_header_beside_a_matching_authority_is_checked_too(self):
        """over HTTP/2 mitmproxy's host_header is :authority; a Host header sent beside it reaches the origin as-is"""
        for host in ("api.github.com", "registry.npmjs.org"):
            f = self.send(host, {"host": "other-tenant.example", "authorization": f"token {self.gh_ph}"}, authority=host)
            self.assertEqual(f.response.status_code, 403, host)
            self.assertEqual(f.metadata["reason"], "host-mismatch")
        f = self.send("api.github.com", {"host": "api.github.com", "authorization": f"token {self.gh_ph}"},
                      authority="api.github.com")
        self.assertIsNone(f.response)
        self.assertEqual(f.request.headers["authorization"], "token REAL-gh")

    def test_undecodable_basic_refused_without_a_placeholder_untouched_byte_for_byte(self):
        good = base64.b64encode(f"x-access-token:{self.gh_ph}".encode()).decode()
        self.assertRefused(self.send("github.com", {"authorization": "Basic " + good[:-1]}))  # padding cut
        raw = "Basic " + base64.b64encode(b"user:\xff\xfe").decode()  # not UTF-8: decodes, but is a foreign credential
        self.assertRefused(self.send("github.com", {"authorization": raw}))
        f = self.send("registry.npmjs.org", {"authorization": raw})  # not a token host: untouched byte for byte
        self.assertIsNone(f.response)
        self.assertEqual(f.request.headers["authorization"], raw)

    def test_no_placeholder_nothing_changes(self):
        f = self.send("registry.npmjs.org", {"authorization": "Bearer something"})
        self.assertIsNone(f.response)
        self.assertEqual(f.request.headers["authorization"], "Bearer something")
        self.assertNotIn("swapped", f.metadata)

    def test_foreign_credentials_refused_on_token_hosts_no_credential_passes(self):
        basic = lambda t: "Basic " + base64.b64encode(f"x-access-token:{t}".encode()).decode()
        for host in ("api.github.com", "github.com", "api.anthropic.com"):
            for h in ({"authorization": "Bearer FOREIGN"}, {"authorization": basic("FOREIGN")}, {"x-api-key": "FOREIGN"}):
                self.assertRefused(self.send(host, h))
        self.assertRefused(self.send("api.github.com", {"authorization": f"token {self.gh_ph}", "x-api-key": "FOREIGN"}))
        f = self.send("api.github.com", {})
        self.assertIsNone(f.response)

    def test_foreign_credential_refusal_audit_line_has_a_reason_and_no_header_value(self):
        f = self.send("api.github.com", {"authorization": "Bearer FOREIGN-SECRET"}, path="/gists")
        self.a.response(f)
        log = (Path(self.d) / "t.log").read_text()
        self.assertRegex(log, r" broker GET api\.github\.com/gists 403 - foreign-credential\n$")
        self.assertNotIn("FOREIGN-SECRET", log)

    def test_audit_line_has_destination_status_and_swap_never_query_or_secrets(self):
        f = self.send("api.github.com", {"authorization": f"token {self.gh_ph}"}, path="/repos/o/r?q=SECRET-QUERY")
        f.response = Response(200, b"")
        self.a.response(f)
        f = self.send("api.anthropic.com", {"authorization": f"token {self.gh_ph}"}, path="/wrong-host")
        self.a.response(f)
        f = Flow("api.github.com", {}, path="/hangup")
        self.a.error(f)
        log = (Path(self.d) / "t.log").read_text().splitlines()
        want = ["GET api.github.com/repos/o/r 200 github", "GET api.anthropic.com/wrong-host 403 -",
                "GET api.github.com/hangup error -"]
        self.assertEqual(len(log), 3, log)
        for line, w in zip(log, want):
            self.assertRegex(line, rf"^\d{{4}}-\d\d-\d\dT[0-9:]{{8}}\+0000 broker {w}$")
        for never in ("SECRET-QUERY", "REAL-", "PLACEHOLDER"):
            self.assertNotIn(never, "\n".join(log))

    def test_unwritable_audit_log_stops_the_broker_rather_than_serving_unaudited(self):
        def deny(path, mode="r"):
            raise PermissionError("read-only")
        self.a.open = deny
        f = self.send("api.github.com", {"authorization": f"token {self.gh_ph}"})
        f.response = Response(200, b"")
        with mock.patch.object(self.a.os, "_exit", side_effect=SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.a.response(f)
        stop.assert_called_once_with(1)

    def test_every_placeholder_and_header_pairing_is_checked(self):
        basic = lambda t: "Basic " + base64.b64encode(f"x:{t}".encode()).decode()
        self.assertRefused(self.send("github.com", {"authorization": basic(self.claude_ph)}))  # Claude's, at GitHub
        self.assertRefused(self.send("api.anthropic.com", {"authorization": basic(self.gh_ph)}))
        self.assertRefused(self.send("api.anthropic.com", {"x-api-key": self.gh_ph}))
        self.assertRefused(self.send("api.github.com", {"x-api-key": self.claude_ph}))
        self.assertRefused(self.send("api.github.com", {"authorization": f"token {self.gh_ph} {self.claude_ph}"}))
        # Both headers: the second one bad refuses the request, though the first was fine.
        self.assertRefused(self.send("api.github.com", {"authorization": f"token {self.gh_ph}", "x-api-key": self.claude_ph}))
        # The scheme is case-insensitive.
        f = self.send("github.com", {"authorization": basic(self.gh_ph).replace("Basic", "basic")})
        self.assertEqual(f.request.headers["authorization"], "Basic " + base64.b64encode(b"x:REAL-gh").decode())
        # No real token for it: refused, never the placeholder sent on.
        self.a = load(self.d, CLAUDE_CODE_OAUTH_TOKEN="REAL-claude")
        self.assertRefused(self.send("github.com", {"authorization": basic(self.gh_ph)}))

    def test_writes_to_the_public_registries_refused_reads_pass_with_or_without_credentials(self):
        for host in ("registry.npmjs.org", "registry-1.docker.io", "auth.docker.io", "pypi.org", "files.pythonhosted.org",
                     "index.crates.io", "static.crates.io"):
            for m in ("PUT", "PATCH", "DELETE"):
                f = self.send(host, {"authorization": "Bearer MINE"}, method=m, path="/v2/x/y")
                self.assertEqual(f.response.status_code, 403, (host, m))
                self.assertIn(b"registry writes refused", f.response.content)
            for h in ({}, {"authorization": "Bearer MINE"}):
                for m in ("GET", "HEAD"):
                    self.assertIsNone(self.send(host, h, method=m).response, (host, m))
        for path in ("/v2/me/app/blobs/uploads/", "/v2/me/app/blobs/uploads/abc-123?digest=sha256:x"):
            self.assertEqual(self.send("registry-1.docker.io", {}, method="POST", path=path).response.status_code, 403)
        # POSTs that read or log in stay: npm audit, the token endpoint.
        self.assertIsNone(self.send("registry.npmjs.org", {}, method="POST", path="/-/npm/v1/security/advisories/bulk").response)
        self.assertIsNone(self.send("auth.docker.io", {}, method="POST", path="/token").response)
        # Not a registry: a PUT elsewhere is no business of this rule.
        self.assertIsNone(self.send("api.github.com", {}, method="PUT", path="/repos/o/r/contents/x").response)

    def test_google_storage_passes_only_reads_inside_the_go_proxys_bucket(self):
        gcs = "storage.googleapis.com"
        for m in ("GET", "HEAD"):
            # A signed URL's signature has encoded slashes: the escape rule is the path's, not the query's.
            f = self.send(gcs, {}, method=m, path="/proxy-golang-org-prod/7883-github.com:aws:aws-sdk-go-v1.55.5.zip?Expires=1&Signature=x%2Fy%2B")
            self.assertIsNone(f.response, m)
        for m in ("PUT", "POST", "DELETE", "PATCH"):
            self.assertEqual(self.send(gcs, {}, method=m, path="/proxy-golang-org-prod/x").response.status_code, 403, m)
        for path in ("/attacker-bucket/x", "/proxy-golang-org-prod", "/proxy-golang-org-prod-2/x", "/storage/v1/b/x/o",
                     "/upload/storage/v1/b/proxy-golang-org-prod/o", "/proxy-golang-org-prod/../attacker/x",
                     "/proxy-golang-org-prod/%2e%2E/attacker/x", "/proxy-golang-org-prod/..%2Fattacker/x",
                     "/proxy-golang-org-prod/.%5cattacker", "/proxy-golang-org-prod/..\\attacker/x", "/proxy-golang-org-prod/./x"):
            f = self.send(gcs, {}, path=path)
            self.assertEqual(f.response.status_code, 403, path)
            self.assertIn(b"only reads of proxy-golang-org-prod", f.response.content)
            self.assertEqual(f.metadata["reason"], "bucket")

    def test_registry_write_refusal_writes_one_audit_line_with_a_reason(self):
        f = self.send("registry.npmjs.org", {"authorization": "Bearer SECRET-NPM"}, method="PUT", path="/pkg?x=1")
        self.a.response(f)
        log = (Path(self.d) / "t.log").read_text()
        self.assertRegex(log, r" broker PUT registry\.npmjs\.org/pkg 403 - registry-write\n$")
        self.assertEqual(len(log.splitlines()), 1)
        self.assertNotIn("SECRET-NPM", log)

    def tunnel(self, host, hello, **kw):
        """The broker's decision on a tunnel to host whose client sent hello: 'dropped', or None (left to mitmproxy)."""
        data = os.urandom(8)
        HELLOS[data] = hello
        n = NextLayer(host, data, **kw)
        self.a.next_layer(n)
        if n.layer is None:
            return None
        cmds = list(n.layer.handle_event("data")) + list(n.layer.handle_event("more data"))
        self.assertEqual([c.connection for c in cmds], [n.context.client], "closes the client once")
        return "dropped"

    def audit(self):
        p = Path(self.d) / "t.log"
        return p.read_text().splitlines() if p.exists() else []

    def assertAudited(self, line):
        self.assertRegex(self.audit()[-1], rf"^\d{{4}}-\d\d-\d\dT[0-9:]{{8}}\+0000 broker CONNECT {re.escape(line)}$")

    def test_tunnel_whose_sni_names_another_host_is_dropped_with_one_audit_line(self):
        self.assertEqual(self.tunnel("registry.npmjs.org", Hello("other-tenant.example")), "dropped")
        self.assertAudited("registry.npmjs.org:443 refused - sni-mismatch sni=other-tenant.example")
        self.assertEqual(self.tunnel("api.github.com", Hello("evil.example")), "dropped")  # decrypted hosts too
        self.assertEqual(len(self.audit()), 2)

    def test_tunnel_whose_sni_matches_is_left_to_mitmproxy_unlogged(self):
        self.assertIsNone(self.tunnel("registry.npmjs.org", Hello("registry.npmjs.org")))
        self.assertIsNone(self.tunnel("API.github.com", Hello("api.GitHub.com")))  # names are case-insensitive
        self.assertEqual(self.audit(), [])

    def test_no_sni_ech_or_not_tls_is_dropped(self):
        self.assertEqual(self.tunnel("registry.npmjs.org", Hello(None)), "dropped")
        self.assertAudited("registry.npmjs.org:443 refused - no-sni sni=-")
        self.assertEqual(self.tunnel("registry.npmjs.org", Hello("registry.npmjs.org", 0xFE0D)), "dropped")
        self.assertAudited("registry.npmjs.org:443 refused - ech sni=registry.npmjs.org")
        self.assertEqual(self.tunnel("registry.npmjs.org", Hello("registry.npmjs.org", 0)), "dropped")  # two SNIs
        self.assertAudited("registry.npmjs.org:443 refused - sni-mismatch sni=registry.npmjs.org")
        self.assertEqual(self.tunnel("registry.npmjs.org", ValueError("Expected TLS record")), "dropped")
        self.assertAudited("registry.npmjs.org:443 refused - not-tls sni=-")
        self.assertEqual(self.tunnel("registry.npmjs.org", KeyError("parser bug")), "dropped")  # fails closed
        self.assertAudited("registry.npmjs.org:443 refused - not-tls sni=-")

    def test_server_name_extension_must_hold_exactly_one_host_name(self):
        two = server_names((0, b"registry.npmjs.org"), (0, b"other-tenant.example"))
        self.assertEqual(self.tunnel("registry.npmjs.org", Hello("registry.npmjs.org", names=two)), "dropped")
        self.assertAudited("registry.npmjs.org:443 refused - sni-mismatch sni=registry.npmjs.org")
        other_type = server_names((0, b"registry.npmjs.org"), (1, b"x"))
        self.assertEqual(self.tunnel("registry.npmjs.org", Hello("registry.npmjs.org", names=other_type)), "dropped")

    def test_the_older_ech_draft_codepoint_is_ech_too(self):
        self.assertEqual(self.tunnel("registry.npmjs.org", Hello("registry.npmjs.org", 0xFF02)), "dropped")
        self.assertAudited("registry.npmjs.org:443 refused - ech sni=registry.npmjs.org")

    def test_host_case_and_trailing_dots_dont_dodge_the_checks(self):
        # The SNI check compares names as DNS does.
        self.assertIsNone(self.tunnel("registry.npmjs.org.", Hello("registry.npmjs.org")))
        self.assertIsNone(self.tunnel("Registry.npmjs.org", Hello("registry.npmjs.org.")))
        self.assertEqual(self.audit(), [])
        # Such a tunnel is decrypted: mitmproxy matches allow_hosts ignoring case, against host:port.
        out = io.StringIO()
        with stub_mitmproxy(), contextlib.redirect_stdout(out):
            runpy.run_path(str(KIT / "broker" / "addon.py"), run_name="__main__")
        decrypt = re.compile(out.getvalue(), re.IGNORECASE)
        for h in ("registry.npmjs.org.:443", "REGISTRY.npmjs.org..:443", "API.github.com.", "auth.docker.io:443"):
            self.assertRegex(h, decrypt)
        for h in ("registry.npmjs.org.evil.example:443", "xregistry.npmjs.org:443", "api.github.com:8443"):
            self.assertNotRegex(h, decrypt)
        # And the request checks see the same name.
        for host in ("Registry.NPMjs.org.", "registry-1.docker.io.."):
            self.assertEqual(self.send(host, {}, method="PUT", path="/x").response.status_code, 403, host)
        for host in ("API.github.com", "github.com.", "Api.Anthropic.Com."):
            self.assertRefused(self.send(host, {"authorization": "Bearer FOREIGN"}))
        f = self.send("API.GitHub.com.", {"authorization": f"token {self.gh_ph}"})
        self.assertEqual(f.request.headers["authorization"], "token REAL-gh")

    def test_incomplete_hello_waits_and_other_decisions_are_not_the_tunnels(self):
        self.assertIsNone(self.tunnel("registry.npmjs.org", None))
        self.assertIsNone(self.tunnel("api.github.com", Hello("evil.example"), tls=True))  # inside a decrypted tunnel
        self.assertIsNone(self.tunnel(None, ValueError("CONNECT line"), layers=()))  # the proxy's own connection
        self.assertEqual(self.audit(), [])

    def test_run_as_a_script_prints_the_hosts_to_decrypt(self):
        out = io.StringIO()
        with stub_mitmproxy(), contextlib.redirect_stdout(out):
            runpy.run_path(str(KIT / "broker" / "addon.py"), run_name="__main__")
        self.assertEqual(out.getvalue(), r"^(api\.anthropic\.com|api\.github\.com|auth\.docker\.io|files\.pythonhosted\.org|github\.com|"
                         r"index\.crates\.io|pypi\.org|registry\-1\.docker\.io|registry\.npmjs\.org|static\.crates\.io|"
                         r"storage\.googleapis\.com)\.*(:443)?$")


if __name__ == "__main__":
    unittest.main()
