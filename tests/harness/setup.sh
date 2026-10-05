#!/bin/bash
# Build the QML import tree the offscreen harness runs against.
#
#   setup.sh <out-dir>
#
# <out-dir>/imports ends up with:
#   Quickshell, Quickshell/Io   - the stubs from tests/harness/stubs
#   qs/Commons                  - the REAL Omarchy Style/Color/Border/Util
#   qs/Ui                       - the REAL Omarchy UI kit, except the few
#                                 components that need a compositor
# <out-dir>/home is a throwaway HOME whose theme and font settings point at
# the user's real ones, so the kit renders with the active theme while every
# write (state, cache) lands in the throwaway tree.
#
# The tree is made of symlinks, which a plugin folder may not contain, so it
# is always generated outside the repository.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
out=${1:?usage: setup.sh <out-dir>}
shell_dir=${OMARCHY_PATH:-/usr/share/omarchy}/shell
real_home=${READER_REAL_HOME:-$HOME}

[[ -d $shell_dir/Ui && -d $shell_dir/Commons ]] || {
  echo "setup.sh: Omarchy shell sources not found under $shell_dir" >&2
  exit 1
}

rm -rf "$out/imports"
mkdir -p "$out/imports/qs/Commons" "$out/imports/qs/Ui" "$out/imports/Quickshell"

cp -r "$here/stubs/Quickshell/." "$out/imports/Quickshell/"
mkdir -p "$out/imports/ReaderHarness"
cp -r "$here/lib/." "$out/imports/ReaderHarness/"
# The scoped shell facade third-party plugins receive, straight from Omarchy.
ln -s "$shell_dir/services/PluginShellApi.qml" "$out/imports/ReaderHarness/PluginShellApi.qml"

for file in "$shell_dir"/Commons/*; do
  ln -s "$file" "$out/imports/qs/Commons/$(basename "$file")"
done

for file in "$shell_dir"/Ui/*; do
  ln -s "$file" "$out/imports/qs/Ui/$(basename "$file")"
done
# Replace the compositor-bound components with their stand-ins.
for stub in "$here"/stubs/ui/*.qml; do
  rm -f "$out/imports/qs/Ui/$(basename "$stub")"
  cp "$stub" "$out/imports/qs/Ui/$(basename "$stub")"
done

mkdir -p "$out/home/.local/state/omarchy/settings" "$out/home/.config/omarchy" \
  "$out/home/.config/fontconfig" "$out/home/.cache"
if [[ -e $real_home/.local/state/omarchy/current ]]; then
  ln -sfn "$real_home/.local/state/omarchy/current" "$out/home/.local/state/omarchy/current"
fi
if [[ -f $real_home/.config/omarchy/shell.toml ]]; then
  cp "$real_home/.config/omarchy/shell.toml" "$out/home/.config/omarchy/shell.toml"
fi
if [[ -f $real_home/.config/fontconfig/fonts.conf ]]; then
  cp "$real_home/.config/fontconfig/fonts.conf" "$out/home/.config/fontconfig/fonts.conf"
fi

echo "$out"
