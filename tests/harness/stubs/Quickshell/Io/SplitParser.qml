import QtQuick

// Test stand-in for Quickshell.Io.SplitParser: emits read() once per
// delimited chunk. Output arrives all at once from the bridge, so chunks
// are replayed in order rather than streamed.
QtObject {
  property string splitMarker: "\n"

  signal read(string data)

  function _feed(output) {
    if (output === "") return
    var parts = splitMarker === "" ? [output] : output.split(splitMarker)
    // A trailing delimiter leaves an empty tail the real parser never emits.
    if (parts.length > 0 && parts[parts.length - 1] === "") parts.pop()
    for (var i = 0; i < parts.length; i++) read(parts[i])
  }
}
