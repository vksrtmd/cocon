# Egress is a CONNECT domain allowlist, without TLS inspection

The session's only route out is a squid proxy that allows CONNECT to listed domains (shared list plus the repo's
own) and denies the rest; DNS for outside names doesn't resolve on the internal network. Squid sees host names,
never paths or bodies, so it breaks no client's certificate pinning. The cost is coarse rules: allowing
`github.com` allows all of it, and a host serving many tenants (`storage.googleapis.com`) can't be allowed by name
alone: it is allowlisted only because the broker decrypts it and passes reads of one bucket (ADR 0014).

- Names are matched as given, never resolved, so a PTR record can't satisfy the list and a denied request leaks
  nothing through DNS; a raw-IP destination is denied.
- Squid sees only the CONNECT host; the client picks the TLS server name inside the tunnel, so an allowlisted host
  on a shared edge (npm and a Docker Hub CDN on Cloudflare, the other on CloudFront) could reach any tenant there;
  the broker drops a tunnel whose SNI isn't the CONNECT host, and a decrypted request whose Host header isn't
  (ADR 0014). An undecrypted tunnel's Host header is left to the edge.
- Not covered: what the allowlisted hosts themselves accept, such as GitHub through the repo's own token.
