# No copy mode: prevent in-tree hooks dirs, detect host entry points

ADR 0008 left in-tree files the host runs writable: hook-manager dirs and config (`.husky/`, `.githooks/`,
lefthook, pre-commit) and Claude Code's project settings and MCP config. Other sandboxes close this by giving the
agent a copy: kstenerud/yoloai clones the repo (clonefile/reflinks, gitignored files left out) and replays the
agent's commits with `yoloai apply`; schmitthub/clawker offers a snapshot workspace; madarco/agentbox uses a FUSE
overlay with a download step. We don't, because the work here depends on the host seeing the session's writes live:
gitignored `.scratch/` tickets and handoffs, `.git/pr-body-*.md`, files handed back through the repo, and the
worktree kit the user tests. Commits alone would carry none of these back. The actual exposure is host `git` and
host Claude Code run in the checkout before review. So the launcher mounts an in-tree `core.hooksPath` target
(resolved from the host's effective config, global included) read-only, as it does `.git/hooks`. After each
session it lists the host entry points whose content changed. That's detection, as in 0008. It can't cover
transitive calls: when a repo's hook runs project code (`npm test` in `pre-push`), a host `git push` runs whatever
the session changed. Revisit copy mode as an opt-in if sessions start running on repos we don't trust.
