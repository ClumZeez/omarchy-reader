#!/bin/bash
# Runs the tests: Reader.js under node, the backend under unittest, and the
# interface scenarios in the offscreen harness (nothing touches the live
# shell or opens a window).
#
#   tests/run.sh            everything
#   tests/run.sh js|py|qml|e2e  one suite
#
# READER_SHOTS=<dir> makes the interface scenarios save screenshots there.
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
suite=${1:-all}
status=0

if [[ $suite == all || $suite == js ]]; then
  echo "== Reader.js"
  node "$root/tests/js/test_reader.js" | grep -v '^PASS' || status=1
fi

if [[ $suite == all || $suite == py ]]; then
  echo "== backend"
  (cd "$root" && python3 -B -m unittest discover -s tests/py 2>&1 | tail -n 4) || status=1
fi

if [[ $suite == all || $suite == qml ]]; then
  for scenario in "$root"/tests/qml/tst_*.qml; do
    echo "== $(basename "$scenario")"
    args=("plugin=$root")
    [[ -n ${READER_SHOTS:-} ]] && args+=("shots=$READER_SHOTS")
    "$root/tests/harness/run.sh" --test "$scenario" "${args[@]}" 2>&1 |
      grep -E '^(FAIL|QWARN|Totals)|Loc:' || status=1
  done
fi

# The real backend on real files, across shell sessions that share a
# throwaway HOME: read a book; find it again after it was moved and renamed;
# keep the place when the file becomes another edition; cope with the book
# having been deleted, and with a state file that can no longer be read.
if [[ $suite == all || $suite == e2e ]]; then
  work=$(mktemp -d /tmp/omarchy-reader-e2e.XXXXXX)
  python3 -B "$root/tests/e2e/make_books.py" "$work/books" || status=1
  journey() {
    echo "== tst_journey.qml ($1)"
    local args=("plugin=$root" "folder=$work/books" "phase=$1")
    [[ -n ${READER_SHOTS:-} ]] && args+=("shots=$READER_SHOTS")
    READER_HARNESS_DIR="$work/harness" "$root/tests/harness/run.sh" --test \
      "$root/tests/e2e/tst_journey.qml" "${args[@]}" 2>&1 |
      grep -E '^(FAIL|QWARN|Totals)|Loc:|Actual|Expected' || status=1
  }
  mkdir -p "$work/harness"
  journey read
  mkdir -p "$work/books/elsewhere"
  mv "$work/books/The Long Walk.epub" "$work/books/elsewhere/a-long-walk.epub"
  journey resume
  python3 -B "$root/tests/e2e/make_books.py" --second-edition "$work/books/elsewhere/a-long-walk.epub" || status=1
  journey edition
  rm -f "$work/books/elsewhere/a-long-walk.epub"
  journey gone
  printf '{ "books": { "cut off in the mid' > "$work/harness/home/.local/state/omarchy/settings/reader.json"
  journey damaged
  rm -rf "$work"
fi

exit $status
