#!/usr/bin/env bash
# on-base.sh [REF] -- UNITTEST_ARGS...: run the Python tests against REF's cocon/ (default origin/main), the "before"
# of a PR's Evidence: a new test should fail here and pass on the branch. The tests are this checkout's; nothing in
# it changes. The kit is unpacked under /tmp, which the cocoon's Docker daemon can't see: pass -p.
#   tests/on-base.sh -- -p test_workflow_push.py -k merged
set -euo pipefail
ref=origin/main
[[ "${1-}" == -- ]] || { ref="${1-}"; shift || true; }
[[ "${1-}" == -- ]] || { echo "usage: tests/on-base.sh [REF] -- UNITTEST_ARGS..." >&2; exit 2; }
shift
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
git archive "$ref" cocon | tar -x -C "$tmp"
COCON_TEST_KIT="$tmp/cocon" python3 -I -B -m unittest discover -s tests "$@"
