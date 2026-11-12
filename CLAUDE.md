# Cocon

Isolated container per repo for running Claude Code; humans start at [README.md](README.md).

## Layout

```
bin/                   symlinks to the commands below; what users put on PATH
cocon/cocon     launcher: a bash shim, like host-guard and workflow-push, running cocon/py (ADR 0013)
cocon/workflow-push    commit the workflow patch a session left onto its pushed branch, show it, push it
cocon/py/kit/          the commands' Python 3.9: launcher/ (docs/agents/launcher-map.md says where it decides what),
                       guard/ (host-guard), workflowpush/
cocon/                 Dockerfile, compose.yml, squid/, broker/, entrypoint.sh, npm-tools/ (the image's npm lockfile)
cocon/claude/          container-notes.md: the system prompt every session gets, baked into the image (reaches sessions after --build)
tests/                 Python unittest suites for the kit (ADR 0013); check.sh runs them, ci.vars is what CI sets for compose.yml
```

## Working rules

- The host runs the kit (`cocon/`) from the main checkout, so a session there sees the whole checkout
  read-only, except `.git` and `.claude/worktrees/`: make a worktree to change anything. A worktree's kit is writable and runs only when the user tests the branch: change the
  kit in the branch's worktree. On the host, the branch's launcher runs as
  `.claude/worktrees/<name>/cocon/cocon`; its `--build` replaces the shared
  `cocon:latest` image.
- A change to a trust boundary (session mounts, host git, `workflow-push`, egress, credentials, the Docker sidecar,
  isolation between repos, CI and the image build) is checked against [docs/threat-model.md](docs/threat-model.md):
  it keeps each promise there. A change that closes or opens a known gap, or moves a promise's mechanism or test,
  updates the doc in the same PR.
- Push the branch and open the PR yourself (`git push -u origin <name>`, `gh pr create` with a body
  holding the sections named below); never push to main.
- When the branch changes `.github/workflows/`, the user pushes that change: the token lacks `workflow`
  scope, so GitHub rejects it. Keep it out of the commits: run `actionlint` on it, write its commit subject as
  the patch's first line, then `git diff -- .github/workflows`, to
  `$(git rev-parse --git-common-dir)/<name>-workflows.patch` (symlinks are refused), and undo it in the tree with
  `git apply -R` on that patch, which also proves the patch applies. Push the rest of the branch and open its PR as usual, then end with the
  command for the user to run on the host, from the main checkout. It commits the patch onto the pushed branch,
  shows the commit and pushes it:

  ```sh
  workflow-push <name>
  ```

- The cocoon's Docker daemon reaches outside only through the broker and its allowlist, and not for a build's
  `RUN` steps, so a `cocon/Dockerfile` change first builds in CI (`cocon.yml`, after the push). In the cocoon, run `docker build --check cocon` and look up each new
  pin as `docs/agents/pins.md` says; read a CI failure with `gh run view <id> --log-failed`.
- That daemon is Linux dind; the host runs OrbStack, Colima or Docker Desktop (ADR 0004), which
  differ in bind-mount ownership, file modes and networking. Each assumption about host Docker
  becomes an item under one lead-in, `Run on each of OrbStack, Colima and Docker Desktop (the cocoon can't):`, in the PR
  body's `## Host checklist` section, between Evidence and Merge Danger.

- After changing the kit (`cocon/`, its Python included) or a test, commit, then run `tests/check.sh` on the commit
  (about 4 minutes: use `run_in_background` and leave the tree as it is, so its `checked tree:` line equals
  `git rev-parse HEAD^{tree}` of the tip you push). The PR's Evidence names each test in its `skipped:` list
  with the reason. The `tests/harness.py` docstring covers writing new tests. The kit's Python stays within 3.9
  (ADR 0013): no `match`, no `X | Y` at runtime, no library call newer than 3.9. After editing a shell script, run
  `shellcheck` on it (about 1 s) first. One test:
  `python3 -I -B -m unittest discover -s tests -p test_X.py -k name` (against main's kit, a PR's "before": `tests/on-base.sh -- -p test_X.py -k name`); `-k` without `-p` also runs the docker
  integration tests (106-128 s, past a 120 s tool timeout). For a `cocon/compose.yml` change,
  ask the user to restart with `--with docker`, or `tests/test_compose_integration.py` skips itself. Even then its
  tests that start the sidecar skip in the cocoon (nested rootless dind, ADR 0016), and so do those needing outside DNS
  (the cocoon's daemon has none): CI runs them; say so in the PR's Evidence. The other integration tests run in the cocoon. An integration
  test that skips for a missing image skips in CI too, where `shell.yml` fails a skip: list the image in its pull step.
- When you amend an ADR in `docs/adr/`, edit the body statements the amendment supersedes; an appended section
  alone leaves the old claim standing above it.
