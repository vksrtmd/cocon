# Audit log is what the session can't write

The audit log exists to reconstruct a session's actions after the fact, on the assumption that the session itself
may have been misled. A record the session can rewrite proves nothing then. So only what is captured outside `dev`
counts: the egress proxy's log and the credential broker's line per decrypted request (method, host, path,
status, never headers or bodies; ADR 0014), kept on the host and mounted into those two alone, plus
`session start`/`session end` lines the launcher writes. Claude Code's transcripts already record every tool
call, but they live in the config volume the session writes, so they are context, not audit. An OpenTelemetry
export from Claude Code to a collector outside `dev` would make tool calls append-only, at the cost of a sidecar
and its config; we defer it, since what leaves the container is in the proxy log (and on GitHub) and what changes the
checkout is in git. No live alerts or auto-kill on denied hosts: the allowlist has already blocked the request, and
most denials are honest tools missing an entry. cocon rotates the log but never deletes it, so no cleanup
erases evidence.
