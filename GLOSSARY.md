# cocon

Running a Claude Code session for one repo inside an isolated container.

## Language

**cocon**:
The tool that runs Claude Code for one repo in an isolated container, with egress only through an allowlist.
_Avoid_: box (for the tool)

**Session**:
One run of cocon on one checkout, from launch until Claude (or the shell) exits.
_Avoid_: box (for a run), container

**Cocoon**:
The container a session runs Claude in (compose service `dev`), apart from its broker, proxy and sidecars.
_Avoid_: box

**Kit**:
The files under `cocon/` that the host runs: launcher, compose file, image sources, proxy config.
_Avoid_: config, setup (for these files)

**Extra**:
A named capability a session asks for at launch: tools added to its image (Python, Rust), a sidecar service, or
both (Postgres: the server and `psql`).
_Avoid_: profile (compose's word), language pack, service (for the extra as a whole)

**Repo ID**:
`<owner>__<repo>` from a checkout's GitHub `origin`: the identity a repo's token, allowlist and volumes follow,
shared by all its checkouts and worktrees.
_Avoid_: slug (for this), repo name

**Pinned repo ID**:
The repo ID recorded on a main checkout's first launch, which its worktrees share. A launch whose `origin` maps to a
different one is refused until the user re-pins.
_Avoid_: remembered repo, cached origin

**Slug**:
The name that identifies one checkout to cocon: its pinned repo ID, sanitized, or `<repo ID>--<worktree>`
for a worktree; without a GitHub `origin`, the main checkout's folder name instead. A second clone of a repo shares
it (unsupported, warned). Everything per-checkout (services, their data, `node_modules`, the detached session) is named after it.
_Avoid_: project name, box name

**Worktree session**:
A cocon session on a git worktree under the repo's `.claude/worktrees/`, with its own slug, so parallel
tasks on one repo share neither files nor services.
_Avoid_: slot, parallel box

**Detached session**:
A cocon session that keeps running after its terminal closes, until it ends on its own or is reattached.
_Avoid_: background session, daemon

**Credential broker**:
The per-session container between the session and the egress proxy that holds the real Claude and GitHub tokens and
swaps them in for the session's placeholders on the hosts they belong to.
_Avoid_: token proxy, MITM (for this)

**Placeholder**:
The stand-in for a real token that the session holds: it works only through the credential broker, on the token's hosts.
_Avoid_: fake token, dummy token

**Host entry point**:
A file in the checkout that host git or host Claude Code runs without asking: hook scripts, hook-manager config,
Claude Code project settings and MCP config, and, in a cocon checkout, the kit dir.
_Avoid_: risky file, executable config

**Trusted git config baseline**:
The risky git config entries of a repo as the user last accepted them, kept where no session can write. Entries
beyond it are reported until the user trusts or removes them.
_Avoid_: snapshot (the per-launch copy it replaced), allowlist, known-good config

**Trust boundary**:
A line a session must not cross, such as container to host or one repo to another. Each has its promises and known
gaps in `docs/threat-model.md`.
_Avoid_: attack surface, perimeter

**Known gap**:
A way a trust boundary's promise doesn't hold today, listed in `docs/threat-model.md` with its severity, and its open issue or
where it is accepted.
_Avoid_: hole, TODO (for these)

**Audit log**:
The record of what a session did, kept where the session can't write it, for reading after the fact.
Transcripts are not part of it: the session can edit them.
_Avoid_: transcript, session log, monitoring (for this)
