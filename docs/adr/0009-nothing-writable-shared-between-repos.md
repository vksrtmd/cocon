# Nothing writable is shared between repos

Each repo gets its own Claude config volume (`~/.claude`: transcripts, prompt history, file backups, plugins) and
its own caches (Go modules and build cache, npm, Playwright browsers); its worktree sessions share them. They are keyed by
repo ID (`<owner>__<repo>`), not the folder name, so two same-named checkouts of different repos stay apart.
With one shared config volume, a session misled by content in repo A could read repo B's transcripts and
pasted secrets and push them out with A's token, or edit a plugin, cached module or build output that B's
session then runs. Splitting the whole config dir, not a list of sensitive paths, covers state files Claude Code
adds later. The costs: auth by `claude setup-token` in `claude.env`, plugins
installed once per repo, cold first builds and duplicated cache disk, and `--destroy` also deletes the repo's
transcripts.
