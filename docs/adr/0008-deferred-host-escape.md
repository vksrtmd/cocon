# Deferred host escape: read-only hooks and kit, git config checked against a baseline

A session can't break out of the container, but it can plant code the host runs later: a git hook, a git config
entry that runs a command (`core.fsmonitor`, filters, `!` aliases, ...), or, in the main checkout, the kit itself. Hooks
dirs (and the kit in the main checkout) are mounted read-only, which prevents planting. `.git/config` stays writable
because normal work writes it (`git push -u`, `git switch -c` with tracking, `git worktree add`, `gh`); mounting it
read-only would break those. Config entries that run code are detected instead: host git checks them against a
trusted baseline before it runs, and the launcher warns after each session about each added or changed one, with the
command to remove it ([ADR 0015](0015-host-git-never-trusts-session-writable-state.md)). It fails loud (exit 2) when
it can't read a config file.
