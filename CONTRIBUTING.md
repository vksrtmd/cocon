# Contributing

Issues and pull requests are welcome. For a vulnerability, follow [SECURITY.md](SECURITY.md) instead.

## Before you start

- [README.md](README.md): what cocon does and how to run it.
- [CLAUDE.md](CLAUDE.md): the working rules, written for agents but binding for everyone: layout, worktrees,
  tests, and how a `.github/workflows/` change is pushed.
- [docs/threat-model.md](docs/threat-model.md): what each trust boundary promises. A change that touches one keeps
  each promise, or updates the doc in the same PR.
- [docs/adr/](docs/adr/): why things are the way they are. [GLOSSARY.md](GLOSSARY.md) and
  [CODING_STANDARDS.md](CODING_STANDARDS.md) for terms and review judgement calls.

## Making a change

1. Work on a branch; the kit's Python stays within 3.9 (ADR 0013).
2. Run `shellcheck` on any shell script you edit, then `tests/check.sh` (about 4 minutes) on the commit you push.
3. In the PR body, say what changed and why, list the evidence (tests run, and each skipped test with the reason),
   and, when the change assumes something about host Docker, a `## Host checklist` to run on OrbStack, Colima
   and Docker Desktop.

By contributing you agree that your contribution is licensed under the [Apache License 2.0](LICENSE).
