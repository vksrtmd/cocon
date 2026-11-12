# Credentials are brokered outside the session

A session holds only placeholders for its GitHub and Claude tokens; its own credential broker (mitmproxy with a small
addon), between `dev` and squid, swaps in the real token only over TLS to the host it belongs to. Anything in the
session can then still act as the user while the session runs, but can't copy a token for use elsewhere or later,
which matters most for the account-wide, year-long Claude token. Squid stays the only route out, keeps the allowlist
and holds no secrets, so a compromised broker can reach only allowlisted hosts.

Being the one way out, the broker also enforces what squid, seeing only the CONNECT host, can't:

- **SNI and Host.** A tunnel whose TLS SNI isn't the CONNECT host is dropped, and so is a decrypted request whose
  Host header isn't, so an allowlisted host on a shared edge can't front for another tenant (ADR 0005).
- **Foreign credentials.** On a token host, a credential that isn't the session's placeholder is refused, so the
  session can't act as someone else, or use a Claude API key.
- **Registry writes.** The public npm, Docker Hub, PyPI and crates.io hosts are decrypted only to refuse writes;
  reads pass.
- **One bucket.** `storage.googleapis.com`, where `proxy.golang.org` redirects large module zips, is decrypted to pass
  only GET and HEAD inside the Go proxy's bucket: anyone can create a bucket there, and read its access logs.

## Considered Options

- **Squid `ssl_bump`**, without a new container: the pinned squid image is built without OpenSSL, and squid's header
  directives can't check placeholders.
- **An opt-in broker**: the secure path would be the one people skip, and there would be two egress topologies to
  maintain.

## Consequences

- Claude auth is `claude setup-token` only. A `/login` refresh token can't be replaced by a placeholder, so `dev`'s
  entrypoint deletes it at each start. Remote Control and other full-scope features are lost.
- `dev` trusts a CA the broker generates per session; `dev` never runs as root, so no host or image trust store
  changes.
- The broker sees the plaintext of the hosts it decrypts and holds the real tokens: it is the most sensitive
  component.
- If the broker is down, or can't write its audit line (ADR 0012), `dev` has no egress: no fallback with real tokens,
  no request served unaudited.
- The Docker sidecar (ADR 0016) outlives sessions and several can run, so it can't share theirs: it gets a broker of
  its own (`docker-broker`), with no token and a CA made fresh at each start.
