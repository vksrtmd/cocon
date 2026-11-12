# Host git never trusts session-writable state

Host git must not act on state a session can write (the repo's `.git` config, a worktree's pointer files, the
files `workflow-push` reads, `origin`) without first checking it against something the session can't write; when the
check fails, the host refuses and says what it found. Host git runs while a session on the repo may be live: the
launcher runs `fetch` and `worktree add` beside other sessions, and `workflow-push` commits and pushes while the session
that prepared the branch is still open. A planted `remote.origin.url` with a `pre-receive` hook, a rewritten worktree
`.git` file, `filter.*.clean`, `core.fsmonitor`, a forced `remote.origin.push`, a symlinked PR body or a hostile
title in a pasted `workflow-push` command each runs code on the Mac when host git trusts it.

- **`host-guard git`** is the one entry point for host git. It finds the git dir from the filesystem path, never
  from git's answer or pointer files, and only asks git to agree. It overrides on the command line the settings that
  run code, and refuses a risky config entry not in the trusted baseline.
- **The trusted git config baseline** is in host config, rewritten only by `--trust-git` after showing the entries,
  so a planted entry is flagged until the user acts. A baseline taken fresh at each launch wouldn't do: an entry
  planted in one session becomes the next one's normal. It is keyed by the main checkout's path: `--repin` changes
  the repo ID and must not re-baseline config a session may have planted.
- **The pinned repo ID** is recorded on a checkout's first launch. Token, allowlist and volumes follow the pin, never
  `origin`; an `origin` that disagrees refuses the launch until `--repin`.
- **`workflow-push NAME`** takes only a branch name. It builds the workflow commit on the branch's tip read from the pinned
  repo, never on local state, shows the whole commit for a y/N, and pushes it without force.

## Considered Options

- **Mounting `.git/config` read-only**: normal work writes it (ADR 0008).
- **Keying the baseline by repo ID**: `--repin` would re-baseline config a session may have planted (above).

## Consequences

- Known gap: `host-guard git` checks, then runs git, a window of milliseconds in which a live session could write
  config that git then reads. Closing it would take a copy of the config git reads, or a git that takes config only
  from the command line; we accept the window, since the entry would have to be planted in those milliseconds and is
  flagged on the next call.
