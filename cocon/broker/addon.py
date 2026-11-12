# The credential broker's mitmproxy addon (ADR 0014): swap each placeholder for its real token on the hosts that
# token belongs to; refuse it on any other host the broker decrypts; one audit line per decrypted request, never a
# header, body or query (ADR 0012). Every tunnel, decrypted or not, is dropped unless its TLS SNI is the CONNECT host,
# with one audit line. The public package registries are decrypted too, to refuse uploads there, and storage.googleapis.com,
# to pass only reads of the Go module proxy's bucket. A decrypted request whose Host header names another host is refused:
# on a shared CDN edge it would reach another tenant. Run as a script, it prints the hosts to decrypt (mitmproxy's
# allow_hosts), so that list comes from the tables below.
import base64, os, re, sys, time

from mitmproxy import http
from mitmproxy.proxy import commands, layer
from mitmproxy.proxy.layers import HttpLayer
from mitmproxy.proxy.layers.tls import parse_client_hello

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # mitmproxy loads a script by path: its dir isn't on sys.path
from placeholders import PLACEHOLDERS

TOKENS = {  # placeholder -> (name for the log, real token, hosts it may go to)
    PLACEHOLDERS["CLAUDE_CODE_OAUTH_TOKEN"]: ("claude", os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), {"api.anthropic.com"}),
    PLACEHOLDERS["GH_TOKEN"]: ("github", os.environ.get("GH_TOKEN"), {"github.com", "api.github.com"}),
}
TOKEN_HOSTS = {h for _, _, hs in TOKENS.values() for h in hs}
REGISTRY_HOSTS = {"registry.npmjs.org", "registry-1.docker.io", "auth.docker.io",  # reads only, any credential
                  "pypi.org", "files.pythonhosted.org", "index.crates.io", "static.crates.io"}
# host -> the one bucket its GETs may read: proxy.golang.org redirects large module zips there, signed.
BUCKETS = {"storage.googleapis.com": "proxy-golang-org-prod"}
# A dot segment, or an encoded dot, slash or backslash: a front end that normalises the path could leave the bucket.
ESCAPE = re.compile(r"(^|/)\.\.?(/|$)|%(2e|2f|5c)|\\", re.I)


def _name(host):  # as DNS compares names: API.github.com. is api.github.com
    return host.lower().rstrip(".")


def _swap(value, host, flow):
    swapped = False
    for placeholder, (name, real, hosts) in TOKENS.items():
        if placeholder in value:
            if host not in hosts or not real:  # no real token: refuse rather than send the placeholder on
                return None
            value = value.replace(placeholder, real)
            flow.metadata["swapped"] = name
            swapped = True
    if not swapped and _name(flow.request.host) in TOKEN_HOSTS:  # someone else's credential: no way to act as them
        flow.metadata["reason"] = "foreign-credential"
        return None
    return value


def _request_refusal(flow):
    """Why a decrypted request is refused, and the reply's text; None if it passes on to the token checks."""
    r = flow.request
    host = _name(r.host)
    # Over HTTP/2 host_header is :authority; a Host header sent beside it, if any, stays when the request goes on as HTTP/1.
    for h in {r.host_header, r.headers.get("host") or r.host_header}:
        if _name(re.sub(r":443$", "", h or "")) != host:
            return "host-mismatch", "Host header isn't the connection's host"
    path = r.path.split("?")[0]
    if host in REGISTRY_HOSTS and (r.method in ("PUT", "PATCH", "DELETE")
                                   or r.method == "POST" and "/blobs/uploads" in path):  # docker push
        return "registry-write", "registry writes refused"
    if host in BUCKETS and (r.method not in ("GET", "HEAD") or not path.startswith(f"/{BUCKETS[host]}/")
                            or ESCAPE.search(path)):
        return "bucket", "only reads of " + BUCKETS[host]
    return None


def requestheaders(flow):  # runs for streamed bodies too; `request` does not
    refused = _request_refusal(flow)
    if refused:
        flow.metadata["reason"] = refused[0]
        flow.response = http.Response.make(403, f"cocon broker: {refused[1]}\n".encode())
        return
    # Only over TLS, and to the host the connection goes to: plain HTTP would send the token in clear, and the Host
    # header is the client's to set. "" matches no token's hosts.
    host = _name(flow.request.host) if flow.request.scheme == "https" else ""
    for name in ("authorization", "x-api-key"):
        v = flow.request.headers.get(name)
        if v is None:
            continue
        scheme, _, cred = v.partition(" ")
        if scheme.lower() == "basic":  # git: x-access-token:<token>
            try:  # latin-1: any bytes, back unchanged
                user_pass = base64.b64decode(cred, validate=True).decode("latin-1")
            except ValueError:  # undecodable: it could hide a placeholder
                new = None
            else:
                new = _swap(user_pass, host, flow)
                if new == user_pass:
                    new = v
                elif new is not None:
                    new = "Basic " + base64.b64encode(new.encode("latin-1")).decode()
        else:
            new = _swap(v, host, flow)
        if new is None:
            flow.response = http.Response.make(403, b"cocon broker: token not for this host\n")
            return
        if new != v:  # untouched otherwise, never re-encoded
            flow.request.headers[name] = new


def _log(flow, status):
    r = flow.request
    _write(f"{r.method} {r.host}{r.path.split('?')[0]} {status} {flow.metadata.get('swapped', '-')}"
           f"{' ' + flow.metadata['reason'] if 'reason' in flow.metadata else ''}")


def response(flow):
    _log(flow, flow.response.status_code)


def error(flow):  # upstream failure or a cancelled stream: no response, still a line
    _log(flow, "error")


class _Drop(layer.Layer):  # closes the client's connection: nothing reaches upstream
    def _handle_event(self, event):
        if not getattr(self, "closed", False):
            self.closed = True
            yield commands.CloseConnection(self.context.client)


def _refusal(host, data):
    """Why a tunnel to host whose client sent data is refused, and its SNI; ("", sni) if it passes, None to wait."""
    try:
        hello = parse_client_hello(data)
    except Exception:  # not TLS, or a parser error: fail closed, never left to mitmproxy's passthrough
        return "not-tls", None
    if hello is None:
        return None
    types = [t for t, _ in hello.extensions]
    if 0xFE0D in types or 0xFF02 in types:  # encrypted client hello (final, older draft): the real SNI is hidden
        return "ech", hello.sni
    if not hello.sni:  # absent or invalid
        return "no-sni", None
    # Two SNIs, as two extensions or two entries in one: the server may route on either. mitmproxy's sni is None for
    # two entries already; counted here too, as defence in depth.
    if types.count(0) != 1 or _entries(dict(hello.extensions)[0]) != 1 or _name(hello.sni) != _name(host):
        return "sni-mismatch", hello.sni
    return "", hello.sni


def _entries(body):
    """How many names a server_name extension's body lists: 2-byte list length, then type, 2-byte length, name each."""
    n, i = 0, 2
    while i < len(body):
        i += 3 + int.from_bytes(body[i + 1:i + 3], "big")
        n += 1
    return n


def next_layer(nextlayer):
    # A CONNECT tunnel's first bytes, decrypted or not (scripts run before mitmproxy's own next_layer, which then
    # keeps our layer): SNI must equal the CONNECT host, so a shared CDN edge reaches only the allowlisted name.
    c = nextlayer.context
    if c.client.tls or not c.server.address or not any(isinstance(lay, HttpLayer) for lay in c.layers):
        return  # the proxy's own connection, or inside a tunnel the broker decrypted
    host, port = c.server.address[:2]
    r = _refusal(host, nextlayer.data_client())
    if r and r[0]:
        nextlayer.layer = _Drop(c)
        _write(f"CONNECT {host}:{port} refused - {r[0]} sni={r[1] or '-'}")


def _write(line):  # one audit line
    try:
        with open("/var/log/broker/" + os.environ["LOG_NAME"], "a") as f:  # per line: a rotated file needs no reopen
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime())} broker {line}\n")
    except OSError as e:  # mitmproxy would log a hook's exception and carry on: stop instead, unlogged requests are unaudited
        print(f"cocon broker: can't write its audit log, stopping: {e}", file=sys.stderr, flush=True)
        os._exit(1)  # the restart policy brings it back; until then dev has no network


if __name__ == "__main__":
    # mitmproxy matches it ignoring case; trailing dots as _name drops them, so the checks above see these hosts.
    sys.stdout.write("^(" + "|".join(map(re.escape, sorted(TOKEN_HOSTS | REGISTRY_HOSTS | set(BUCKETS)))) + r")\.*(:443)?$")
