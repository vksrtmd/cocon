# Coding standards

Read in review; judgement calls only. Mechanical rules live in `tests/check.sh`.

## Shell

- A failed command substitution is invisible to `set -e` inside another substitution, an `echo "$(...)"`, an `export X="$(...)"` (`tests/test_dockerfile.py` checks the Dockerfile's), or a
  line of several assignments (a later `$(...)` overwrites the earlier one's status). Assign each
  substitution on its own line, then `|| die "..."` where its failure matters.

## Python (the kit, ADR 0013)

- Text a session wrote (a file under the checkout or its `.git`, a ref name, a git config value) is hostile input on
  the host. Read a file of it through `kit.util.read_pointer` or `kit.workflowpush.sessionfiles`, which refuse what isn't a
  regular file without blocking (`tests/test_kit_reads.py` makes each open site say which kind of file it reads).
  Before such text reaches a command line, refuse what the command can't take (a NUL byte) with a message, before
  anything has acted.
- Match a command's output by exact field, never by position: `git ls-remote URL refs/heads/main` also lists a
  session-pushed `refs/heads/x/refs/heads/main`, sorted first.
- Output a session controls that the user reads before confirming (a diff before y/N) is captured as bytes and shown
  through `cat_v` (`kit.workflowpush.cli`): `kit.util.run` captures text, which turns a carriage return into a newline
  before `cat_v` can show it as `^M`.

## Tests

- A stub models what the real tool returns, including the superset cases (GitHub's `user/repos` also lists public
  repos the user collaborates on); a test of a filter feeds it what the filter must drop. An external API claim goes
  into a test only after one live read-only call, or the report marks it unverified.
