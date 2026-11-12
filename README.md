# cocon

Run Claude Code in a Docker container on your Mac. *Cocon* is French for cocoon.

- One or more cocoons per repo; reads only what you give it, writes only in the repo.
- Outbound traffic only through an allowlisting proxy with an audit log.
- Your tokens held outside the cocoon by a credential broker.
- Your own Claude setup (skills, agents, plugins, `CLAUDE.md`) mounted in.

## Contents

- [Quick start](#quick-start)
- [Setup](#setup)
- [GitHub access](#github-access-repo-scoped-tokens), [branch protection](#branch-protection)
- [Daily use](#daily-use)
- [Configuration reference](#configuration-reference)
- [Security](#security)

For contributing to cocon itself, see [CONTRIBUTING.md](CONTRIBUTING.md); to report a vulnerability,
[SECURITY.md](SECURITY.md).

## Quick start

1. [Set up](#setup) the launcher and the Claude token (once).
2. [Create a GitHub token](#github-access-repo-scoped-tokens) for the repo.
3. Run:

   ```sh
   cocon clone acme/foo     # prints the token file to create
   cocon foo                # start Claude on ~/cocon-repos/foo
   cocon --doctor           # if anything looks off
   ```

## Setup

Requirements: macOS with the Command Line Tools (`xcode-select --install`; they give `git` and `python3`),
Docker (OrbStack or Colima recommended; Docker Desktop works), tmux (optional).

1. Install the launcher:

   ```sh
   git clone https://github.com/vksrtmd/cocon.git ~/cocon
   export PATH="$HOME/cocon/bin:$PATH"       # add to your shell rc
   ```

2. Sign in. Run `claude setup-token` on the host and put the result in
   `~/.config/cocon/claude.env` (mode 600):

   ```sh
   CLAUDE_CODE_OAUTH_TOKEN=...
   ```

   - It's valid for a year and covers the whole account, so it never enters the cocoon. Each session's
     broker holds it and swaps it into requests to `api.anthropic.com`.
   - A `/login` credential can't be brokered: the cocoon deletes one from the config volume at each start.
     Remote Control and other full-scope features aren't available.
   - Your own `ANTHROPIC_API_KEY` doesn't work: the broker refuses any credential on `api.anthropic.com` but the
     session's placeholder (a 403, `foreign-credential` in the audit log).

3. Optional: choose where things live (all on the host):

   | Variable | Default | What |
   |---|---|---|
   | `COCON_CONFIG` | `~/.claude` | your Claude setup, mounted read-only |
   | `COCON_REPOS` | `~/cocon-repos` | where `clone` puts repos and `cocon foo` looks them up |
   | `COCON_MEMORY` / `COCON_PIDS` | `8g` / `4096` | caps of the cocoon, and of the Docker sidecar; raise for big builds |
   | `COCON_LOG_MAX_MB` | `10` | audit log size that triggers archiving |

## GitHub access (repo-scoped tokens)

One fine-grained token per repository, so a session can only touch that repo.

1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate.
2. **Repository access**: *Only select repositories* → one repo.
3. **Expiration**: 30–90 days.
4. **Repository permissions**:

   | Permission | Access | Why |
   |---|---|---|
   | Metadata | Read | required |
   | Contents | Read and write | push branches |
   | Issues | Read and write | create/triage issues, labels |
   | Pull requests | Read and write | open/update PRs, review comments |
   | Actions | Read | optional: let Claude read CI runs |

5. Save it named after the `origin` remote (`cocon clone` prints the exact path):

   ```sh
   mkdir -p ~/.config/cocon/tokens
   printf 'GH_TOKEN=%s\n' 'github_pat_...' > ~/.config/cocon/tokens/OWNER__REPO.env
   chmod 600 ~/.config/cocon/tokens/OWNER__REPO.env
   ```

6. Set up [branch protection](#branch-protection). Without it a session can merge its own work.
7. Check: `cocon foo --shell`, then `gh issue list` and `git push --dry-run`. The first launch builds the
   image first.

The launcher refuses a token that isn't fine-grained (`github_pat_`), and warns when the token reaches
private repos besides this one.

**Do not grant** anything beyond the table above, and never *All repositories*. The launcher can't see a token's
write access to your other public repos: GitHub lists the public repos you collaborate on whatever the token reaches.

- **Workflows**: lets a session edit `.github/workflows/`, and so what CI runs with the repo's secrets. Without it a
  session leaves its workflow change as a patch, and `workflow-push NAME` shows it to you before pushing it.
- **Administration**: lets a session change the repo's settings, including the branch protection below.
- Any other permission (Secrets, Environments, Pages, Webhooks, Deployments...): the session needs none.

### Branch protection

Set it on the default branch (repo → Settings → Rules → Rulesets), with no bypass for your own account:

- Require a pull request before merging.
- Require at least 1 approval.
- Require approval of the most recent reviewable push.
- Do not let anyone bypass: an empty bypass list, or *Do not allow bypassing the above settings*. The token acts as
  you, so a bypass for you is a bypass for the session.

Why each one:

- A pull request required with **0 approvals** (GitHub's default when you only tick "require a pull request"), or no
  protection at all: the session runs `gh pr create && gh pr merge` and its code is on the default branch with no
  human having looked.
- With **1 approval** the session can't approve its own PR (GitHub refuses an author's own approval), but it can push a
  commit onto another author's open PR (a Renovate one, say) and approve it as you. **Approval of the most recent
  push** voids that: the push was made as you, so your approval of it doesn't count and the PR needs someone else's.

These settings need a second reviewer: a PR opened or pushed to under your account can't be approved by you.

## Daily use

### Start a session

```sh
cocon clone acme/foo                    # clone into ~/cocon-repos/foo
cocon foo                               # Claude on that repo, from anywhere
cd ~/cocon-repos/foo && cocon    # same
cocon foo --yolo                        # no permission prompts
cocon foo -- -c                         # anything after -- goes to claude (here: continue)
cocon foo --shell                       # a shell instead of Claude
```

### Add services: `--with`

```sh
cocon foo --with postgres,kafka
```

| Extra | Adds |
|---|---|
| `postgres`, `clickhouse`, `kafka` | server and client |
| `docker` | CLI and a Docker daemon sidecar (testcontainers works) |
| `playwright` | Chromium's system libraries |
| `go` | Go toolchain, gopls; modules in a per-repo volume |
| `helm` | Helm |
| `python` | uv, pip, venv |
| `rust` | rustup toolchain (fixed: rustup installs nothing), cmake, protoc, cargo-nextest; crates and each checkout's build in a per-repo volume |
| `tauri` | WebKitGTK, librsvg, appindicator dev libraries and patchelf, for building Tauri apps; pair it with `rust` |

Inside the cocoon: `postgres:5432`, `clickhouse:8123` (HTTP) / `9000`, `kafka:9092`, user and password `dev`;
`DOCKER_HOST=tcp://docker:2375`.

### Parallel and background sessions

```sh
cocon foo --worktree 45-auth            # task on ~/cocon-repos/foo/.claude/worktrees/45-auth
cocon foo --worktree 45-auth --detach   # same, in a tmux session that outlives the terminal
cocon --attach foo --worktree 45-auth   # back to it
cocon --ls                              # every repo's services, ports and sessions
```

- `--worktree <name>`: reuses the worktree if it exists, checks out local branch `<name>` if
  there is one, else branches from `origin/<default>`.
- Each worktree has its own slug (`<owner>__<repo>--<name>`), and so its own proxy, databases, Docker
  daemon, `node_modules` and Claude history.
- Keep one clone per repo and use worktrees for parallel work: a second clone shares the first's slug, so its
  databases, `node_modules` and detached session, and `--destroy` in one removes the other's. The launch warns.
- `--detach` needs `brew install tmux`. It starts tmux session `cocon-<slug>` and prints the
  `--attach` command. The pane stays after the session ends so warnings are still there; close it with
  `tmux kill-session -t cocon-<slug>`.
- `--ls` shows each session as `attached`, `detached`, `exited` (ended, output waiting) or `-`.

### Dev servers and files from the Mac

- `--port 5173` opens the cocoon's port at `127.0.0.1:5173`; `--port 15173:5173` puts it on host port 15173.
  Ports are fixed for the session: to add one, relaunch with it and `-- -c` to keep the conversation.
- Dev servers must listen on `0.0.0.0` (`vite --host`, `next dev -H 0.0.0.0`).
- On OrbStack, `<container>.orb.local` reaches the cocoon directly, every port open.
- To hand a session a file, copy it into the repo: the checkout is a live mount, so it appears at once, no relaunch.
  Put it under a git-ignored path so a `git add -A` doesn't commit it.

### Node repos

- `node_modules` lives in a per-checkout volume (`cocon-<slug>-node-modules`).
- First launch has no `node_modules`: run `npm ci` in the cocoon, then relaunch.
- npm in the cocoon skips install scripts, refuses versions published less than a day ago and saves exact
  versions.

### Maintenance flags

These do their job and exit without starting a session. `cocon --help` lists every flag.

| Flag | Does |
|---|---|
| `--build` | rebuild `latest` and every image built so far; run after merging a Renovate PR |
| `--doctor` | check Docker, image, token and `claude.env` modes, the repo ID pin, allowlists, hooks dirs and more |
| `--repin` | pin the checkout's current `origin` as its repo ID after a move (`~/.config/cocon/pins`) |
| `--proxy-log` | follow the proxy and broker logs ([Audit log](#audit-log)) |
| `--rotate-logs` | archive this repo's logs now |
| `--reload-proxy` | re-read the allowlists |
| `--down` | stop the session's services and remove this checkout's brokers and port forwarders |
| `--trust-git` | trust the risky git config entries added since the trusted baseline |
| `--destroy` | `--down`, then remove the repo's volumes; asks first |

Images, Claude Code and npm/Go tools are pinned in `cocon/`; apt packages are not. Claude Code, TypeScript and
its language server install from a lockfile (`cocon/npm-tools/`), dependencies included, with install scripts off
except Claude Code's.

## Configuration reference

### Your Claude setup

The launcher mounts these from `COCON_CONFIG`, whichever exist; missing ones are skipped.

| Path | In the cocoon |
|---|---|
| `CLAUDE.md`, `skills/`, `agents/`, `rules/`, `docs/`, `output-styles/`, `commands/`, `hooks/`, `mods/` | mounted **read-only** as user-level config |
| `settings.json` | **copied over** the cocoon's settings at every start |

- At start the cocoon adds missing marketplaces and installs missing enabled plugins into the repo's config volume.
- Changes made in-session with `/config` or `/plugin` are lost at the next start.
- Repo-only plugins: `claude plugin install <id> --scope local` (personal,
  `.claude/settings.local.json`) or `--scope project` (shared, `.claude/settings.json`).
- Edits take effect on restart.

### Allowlist

- `cocon/squid/allowlist.txt` starts with Claude Code, GitHub, Docker Hub and the package registries the extras use
  (npm, the Go module proxy, crates.io's index and downloads, PyPI), none of their publish endpoints.
- Hosts are matched by name as given: the proxy never resolves a name to match it, and refuses raw-IP
  destinations. [What egress stops](#what-egress-stops) says what the allowlist cannot.
- A host only one repo needs goes in `~/.config/cocon/allow/<owner>__<repo>.txt`.
- Keep both short: every allowed domain is a possible exfiltration path. The repo-scoped token limits
  what can be written even though `github.com` is reachable.

### Volumes

Per repo, so one repo's session can't read another's transcripts or tamper with what it runs:
`cocon-<owner>__<repo>-{config,cache,npm}`, plus `-gomod`, `-cargo` and `-playwright` for
`--with go`, `rust` and `playwright`. Worktrees of a repo share them; the first build in each repo starts cold.

### Audit log

- Every proxy request lands in `~/.local/state/cocon/<owner>__<repo>/<slug>.log`,
  with `session start` / `session end` lines from the launcher. Each line: time (UTC), client (always a
  broker), result (`TCP_DENIED/403` = blocked host or raw IP), bytes up/down, method, host.
- The broker adds a line per request it decrypts to `broker/<slug>.log`.
- `--proxy-log` follows both files, so a squid denial and a broker refusal show in one place.
- The proxy writes it and the session never sees it, so a misled session can't edit its own record.
  Claude's transcripts are not part of it: the session can rewrite them.
- A session start archives a log over 10 MB to `<slug>.log.<date>.gz`; `--rotate-logs` does it on
  demand for long-running sessions. cocon never deletes a log, `--destroy` included. Prune
  yourself:

  ```sh
  find ~/.local/state/cocon -name '*.log.*.gz' -mtime +180 -delete
  ```

## Security

[docs/threat-model.md](docs/threat-model.md) lists every promise cocon makes, what enforces it and which
test checks it, and the known gaps, boundary by boundary. This section is the user's view of it.

### Credential broker

Each session runs its own mitmproxy container (`cocon/broker/`) between the cocoon and the egress proxy.

- The cocoon holds placeholders for the Claude and GitHub tokens and trusts the broker's per-session CA.
- The broker holds the real tokens and swaps one in only on TLS requests to the host it belongs to:
  `api.anthropic.com`; `github.com` and `api.github.com`.
- A session can act as you while it runs, but can't copy a token for later or elsewhere.
- Broker down means no network. Broker and CA volumes are removed when the session ends (`--down`
  clears any a crash left).

### What egress stops

The cocoon's only way out is the broker, then the egress proxy, which only the broker can reach (the cocoon
has no route to it).

| Stopped | How |
| --- | --- |
| Raw-IP destinations, and a PTR record standing in for an allowlisted name | the proxy matches names without lookups and denies IPs |
| Reaching another tenant of a shared CDN through an allowlisted host | broker drops a tunnel whose SNI isn't the CONNECT host, and a decrypted request whose Host header isn't |
| Writing to, or reading another bucket of, Google Cloud Storage (allowlisted for the Go module proxy's redirects) | broker passes only reads of that proxy's bucket |
| Acting on GitHub with someone else's token (gists, other repos) | broker refuses a non-placeholder credential there |
| `npm publish`, `docker push`, and writes to PyPI's and crates.io's download hosts (also from nested containers) | broker refuses writes to those registries |
| Skipping the broker's checks and audit line | the proxy accepts only the broker |

Not stopped:

- What the session can do with the repo's own token on `github.com`, within that token's scope.
- The upload features of other allowlisted hosts. Keep the allowlist short.
- Reads from the package registries. A request's path and query can carry data out, and the broker refuses none of it.

### What is protected

- Everything Claude runs (file tools, MCP servers, hooks) is inside the container: no host
  credentials, SSH keys or Docker socket.
- The cocoon runs as non-root, no capabilities, `no-new-privileges`, no setuid or setgid files.
- No real token in the cocoon ([Credential broker](#credential-broker)).

What `--with docker` doesn't protect:

- Egress. Nested containers reach the allowlisted hosts through the sidecar's broker, like the cocoon.
- The checkout. A nested `docker run -v` writes what the cocoon can write.
- The VM from an escape. The sidecar runs with seccomp, AppArmor and the `/proc` and `/sys` masks off, which
  rootless needs, so a kernel exploit out of its user namespace meets fewer walls than one out of the cocoon.
- What rootless drops: limits on single nested containers (only the sidecar's caps apply), AppArmor, and the
  client's address on published ports.

### What is not protected: planting code the host runs later

The repo is writable and changes land on the host. cocon closes or reports these paths:

- **Closed:** `.git/hooks`, every submodule's hooks dir and an in-tree `core.hooksPath` (`.husky/_`,
  `.githooks`) are read-only, created empty if missing. `.git/config` stays writable so `git switch -c`
  and `git push -u` work.
- **Reported after each session** (skipped if the terminal closes mid-session):
  - git config entries added or changed that run code on the next host `git` (`core.fsmonitor`,
    `core.hooksPath`, filters, `!` aliases, `include.path`...) or send its traffic and credentials elsewhere
    (any `http.*`, `remote.*.proxy`).
  - host entry points added, removed or changed: `.husky/`, lefthook config, `.pre-commit-config.yaml`,
    `.claude/` (not `.claude/worktrees/`), `.mcp.json`.
- **Host git is guarded:** the launcher and `workflow-push` run git through `host-guard git`, which refuses while
  an untrusted risky config entry is set (`--trust-git`), a worktree's pointer files disagree, or `origin`
  no longer matches the pinned repo ID (`--repin`).
- **Still writable; review diffs before running anything on the host:** Makefiles, `.envrc`,
  `.vscode/tasks.json`, hooks dirs of submodules added mid-session, nested repos created in the
  worktree. If your hooks run project code (`npm test` in `pre-push`), push from the cocoon or review first.
- **cocon's own checkout:** the main checkout is read-only, since the host runs its `cocon/`;
  only `.git` and `.claude/worktrees/` stay writable. All work on the cocoon itself happens in worktrees.

## License

[Apache License 2.0](LICENSE). Copyright 2026 Dmitrii Skopintsev.

cocon is an independent project, not affiliated with or endorsed by Anthropic. Claude and Claude Code are
trademarks of Anthropic.
