#!/usr/bin/env bash
# Shellcheck on the shell scripts, actionlint on the workflows (when installed), then the Python tests (tests/, via tests/run_tests.py; ADR 0013). CI runs
# `/bin/bash tests/check.sh` on macOS, for bash 3.2; there the harness shims /bin/bash as the kit's `bash`. The Python compose integration test needs
# a Docker daemon and skips itself without one (CI has none).
#   tests/check.sh
set -u
cd "$(dirname "$0")/.." || exit

failed=""
if [[ -z "$(git status --porcelain)" ]]; then tree="$(git rev-parse 'HEAD^{tree}')"; else tree=dirty; fi
scripts=() lost=""
while read -r mode _ _ f; do
  # The Python tests' shebangs are not shell: shellcheck would fail them (SC1071).
  if [[ $mode != 100644 && $mode != 100755 ]] || [[ $f == *.py ]] || [[ "$(head -c2 "$f")" != '#!' ]]; then continue; fi
  scripts+=("$f")
  # Rewriting a file in place (sed -i on the cocoon's bind mount) drops its +x, while [[ -x ]] there
  # still says yes: ask git, about what's committed here and about the working tree below.
  [[ $mode == 100755 ]] || lost+="$f"$'\n'
done < <(git ls-files -s cocon tests bin)
echo "== shellcheck"
shellcheck "${scripts[@]}" || failed+=" shellcheck"
echo "== actionlint"
if command -v actionlint >/dev/null; then
  actionlint .github/workflows/*.yml || failed+=" actionlint"
else
  echo "skipped: actionlint not installed"
fi
echo "== executable bits"
lost+="$(git diff --summary | sed -n 's/^ mode change 100755 => 100644 //p')"
[[ -z $lost ]] || { echo "lost the executable bit (chmod +x, then git add):"; printf '%s\n' "$lost"; failed+=" exec-bits"; }
echo "== python tests"
if ! command -v python3 >/dev/null; then
  echo "python3 not found: the Python tests need it"; failed+=" python"
else
  python3 -I -B tests/run_tests.py || failed+=" python"
fi
if ! git diff --quiet origin/main... -- cocon/compose.yml 2>/dev/null && ! docker info >/dev/null 2>&1; then
  echo "warning: cocon/compose.yml differs from origin/main, untested here: no Docker daemon (in a cocoon, restart with --with docker)"
fi
echo "checked tree: $tree"
[[ -z $failed ]] || { echo "check.sh: FAIL:$failed"; exit 1; }
echo "check.sh: OK"
