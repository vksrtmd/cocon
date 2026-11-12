# One compose project per worktree

Each worktree session gets its own compose project, `cocon-<slug>--<worktree>`: its own egress proxy,
databases, Docker daemon and node_modules volume. Sharing the parent repo's project is lighter, but parallel tasks
would then run migrations and tests against the same databases and install into the same node_modules, which is
the interference worktrees exist to avoid. The cost is one more egress container per active worktree (plus the
`--with` sidecar services), and `--destroy` cleans up per worktree.
