#!/bin/bash
# Run a QML file in the offscreen harness: no window ever reaches the
# user's display and nothing talks to the live Omarchy shell.
#
#   run.sh [--test] <file.qml> [KEY=VALUE ...]
#
# Without --test the file runs under the plain `qml` runtime and is expected
# to exit by itself (see lib/Shot.qml). With --test it runs under
# qmltestrunner, so TestCase can drive real key and mouse events.
#
# KEY=VALUE arguments are readable in QML as Quickshell.opt("KEY", fallback).
#
# Environment knobs:
#   READER_HARNESS_DIR   reuse this import tree + throwaway HOME (and keep it)
#                       instead of a fresh temporary one, e.g. to check that
#                       state written by one run is restored by the next
#   READER_DPR           device pixel ratio (default 1.25)
#   READER_TIMEOUT       wall-clock limit in seconds (default 120)
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mode=run
if [[ ${1:-} == --test ]]; then mode=test; shift; fi
(( $# >= 1 )) || { sed -n '2,19p' "${BASH_SOURCE[0]}" >&2; exit 64; }
target=$(realpath -- "$1")
shift

if [[ -n ${READER_HARNESS_DIR:-} ]]; then
  dir=$READER_HARNESS_DIR
  keep=1
else
  dir=$(mktemp -d /tmp/omarchy-reader-harness.XXXXXX)
  keep=0
fi
"$here/setup.sh" "$dir" >/dev/null || exit 1

# Everything the harness runs - the QML and the commands it spawns through
# the bridge - sees the throwaway HOME, so state and cache writes stay there.
real_home=$HOME
export HOME="$dir/home"
export XDG_CACHE_HOME="$dir/home/.cache"
export XDG_STATE_HOME="$dir/home/.local/state"
export XDG_CONFIG_HOME="$dir/home/.config"
# Nor may anything reach the session the tests are run from: no display or
# clipboard, no session bus, no sound server. A command the tests forgot to
# stand in for then fails instead of opening a window or copying over the
# clipboard.
export XDG_RUNTIME_DIR="$dir/run"
mkdir -p -m 700 "$XDG_RUNTIME_DIR"
unset WAYLAND_DISPLAY DISPLAY DBUS_SESSION_BUS_ADDRESS PULSE_SERVER PIPEWIRE_REMOTE PIPEWIRE_RUNTIME_DIR

bridge=$(mktemp -d "$dir/bridge.XXXXXX")
mkdir -p "$bridge/req" "$bridge/res" "$bridge/kill"
python3 -B "$here/procd.py" "$bridge" $$ &
procd=$!
cleanup() {
  touch "$bridge/stop"
  wait "$procd" 2>/dev/null
  rm -rf "$bridge"
  (( keep )) || rm -rf "$dir"
}
trap cleanup EXIT

python3 -B - "$dir/imports/Quickshell/config.json" "$bridge" "$real_home" "$@" <<'PY'
import json, os, sys
out, bridge, real_home, *pairs = sys.argv[1:]
opt = {"bridge": bridge, "realHome": real_home}
for pair in pairs:
    key, _, value = pair.partition("=")
    opt[key] = value
env = {name: os.environ[name] for name in
       ("HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME")}
env["OMARCHY_PATH"] = os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")
with open(out, "w", encoding="utf-8") as handle:
    json.dump({"env": env, "opt": opt}, handle)
PY

qt_env=(-u WAYLAND_DISPLAY -u DISPLAY -u QT_QPA_PLATFORMTHEME -u QT_IM_MODULE -u QT_STYLE_OVERRIDE
  QT_QPA_PLATFORM=offscreen
  QT_QUICK_BACKEND=software
  QT_FORCE_STDERR_LOGGING=1
  QT_SCALE_FACTOR="${READER_DPR:-1.25}"
  QML_DISABLE_DISK_CACHE=1
  QML_XHR_ALLOW_FILE_READ=1
  QML_XHR_ALLOW_FILE_WRITE=1)

if [[ $mode == test ]]; then
  env "${qt_env[@]}" timeout --kill-after=5 "${READER_TIMEOUT:-120}" \
    /usr/lib/qt6/bin/qmltestrunner -input "$target" -import "$dir/imports"
else
  env "${qt_env[@]}" timeout --kill-after=5 "${READER_TIMEOUT:-120}" \
    /usr/lib/qt6/bin/qml -I "$dir/imports" "$target"
fi
rc=$?
(( rc == 124 || rc == 137 )) && echo "run.sh: timed out after ${READER_TIMEOUT:-120}s" >&2
exit $rc
