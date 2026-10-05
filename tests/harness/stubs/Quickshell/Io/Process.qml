import QtQuick
import Quickshell

// Test stand-in for Quickshell.Io.Process. The plain `qml` runtime cannot
// spawn processes, so a request file is written into the bridge directory
// and tests/harness/procd.py runs the command for real and writes the
// result back. Without a bridge every command exits 127.
QtObject {
  id: root

  property var command: []
  property bool running: false
  property string workingDirectory: ""
  property var environment: ({})
  property bool clearEnvironment: false
  property bool stdinEnabled: false
  property var stdout: null
  property var stderr: null
  property int processId: 0

  signal started()
  signal exited(int exitCode, int exitStatus)

  property string _id: ""
  property bool _active: false

  function exec(args) {
    if (args !== undefined && args !== null) command = Array.isArray(args) ? args : (args.command || [])
    if (_active) {
      _active = false
      _poll.stop()
      if (Quickshell.bridgeDir && _id) _put("kill/" + _id, "1")
    }
    running = true
    _start()
  }

  function signal(n) { running = false }
  function write(data) {}
  function startDetached() { Quickshell.execDetached(command) }

  function _fileUrl(name) {
    return "file://" + (Quickshell.bridgeDir + "/" + name).split("/").map(encodeURIComponent).join("/")
  }

  // Local-file PUT only works asynchronously. The bridge tolerates the
  // half-written request by retrying until it parses.
  function _put(name, body) {
    var xhr = new XMLHttpRequest()
    xhr.open("PUT", _fileUrl(name))
    xhr.send(body)
  }

  function _get(name) {
    var xhr = new XMLHttpRequest()
    try {
      xhr.open("GET", _fileUrl(name), false)
      xhr.send()
    } catch (e) {
      return ""
    }
    return xhr.responseText || ""
  }

  function _finish(code, out, err) {
    _active = false
    _poll.stop()
    if (stdout && typeof stdout._feed === "function") stdout._feed(out)
    if (stderr && typeof stderr._feed === "function") stderr._feed(err)
    running = false
    exited(code, 0)
  }

  function _start() {
    if (_active) return
    _active = true
    started()
    if (!command || command.length === 0 || !Quickshell.bridgeDir) {
      var why = Quickshell.bridgeDir ? "empty command" : "no process bridge"
      Qt.callLater(function() { if (root._active) root._finish(127, "", why) })
      return
    }
    _id = Date.now().toString(36) + "-" + Math.floor(Math.random() * 1e9).toString(36)
    _put("req/" + _id + ".json", JSON.stringify({
      id: _id,
      command: command,
      cwd: workingDirectory,
      environment: environment
    }))
    _poll.start()
  }

  // Property initialisation order is undefined, so `running: true` can be
  // seen before `command` is set; act on the settled state a tick later.
  function _sync() {
    if (running && !_active) {
      _start()
    } else if (!running && _active) {
      // Stopped by the caller: ask the bridge to kill it and report a
      // signal-style exit, as the real type does.
      _active = false
      _poll.stop()
      if (Quickshell.bridgeDir && _id) _put("kill/" + _id, "1")
      exited(143, 1)
    }
  }

  onRunningChanged: Qt.callLater(_sync)
  Component.onCompleted: Qt.callLater(_sync)
  Component.onDestruction: if (_active && Quickshell.bridgeDir && _id) _put("kill/" + _id, "1")

  property Timer _poll: Timer {
    interval: 12
    repeat: true
    onTriggered: {
      var body = root._get("res/" + root._id + ".json")
      if (!body) return
      var res = null
      try { res = JSON.parse(body) } catch (e) { return }
      root._finish(res.code, res.stdout || "", res.stderr || "")
    }
  }
}
