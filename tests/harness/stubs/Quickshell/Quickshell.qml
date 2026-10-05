pragma Singleton
import QtQuick

// Test stand-in for the Quickshell singleton. The real module is compiled
// into the quickshell binary and cannot be imported by the plain `qml`
// runtime, so the offscreen harness supplies just the surface the Omarchy
// kit and this plugin touch. run.sh writes config.json next to this file:
//   { "env": { "HOME": ... }, "opt": { "bridge": ..., ... } }
QtObject {
  id: root

  readonly property var _config: {
    var xhr = new XMLHttpRequest()
    try {
      xhr.open("GET", Qt.resolvedUrl("config.json"), false)
      xhr.send()
      var parsed = JSON.parse(xhr.responseText)
      return { env: parsed.env || {}, opt: parsed.opt || {} }
    } catch (e) {
      console.warn("stub Quickshell: no config.json (" + e + ")")
      return { env: {}, opt: {} }
    }
  }

  // Directory the process bridge (procd.py) watches; empty disables it.
  readonly property string bridgeDir: _config.opt["bridge"] || ""
  readonly property string shellDir: _config.opt["shellDir"] || ""
  readonly property var screens: []

  // Commands handed to execDetached are recorded, never run: a test must
  // not open the user's browser or file manager.
  property var execLog: []

  function opt(name, fallback) {
    var v = _config.opt[name]
    return v === undefined ? fallback : v
  }

  function env(name) {
    var v = _config.env[name]
    return v === undefined ? null : v
  }

  // IPC targets by name. As in the real shell, the first handler to claim a
  // target keeps it.
  property var _ipc: ({})

  function registerIpc(handler) {
    if (handler.target && !_ipc[handler.target]) _ipc[handler.target] = handler
  }

  function unregisterIpc(handler) {
    if (_ipc[handler.target] === handler) delete _ipc[handler.target]
  }

  function ipc(target) {
    var handler = _ipc[target]
    return handler && handler.enabled ? handler : null
  }

  function execDetached(command) {
    var next = execLog.slice()
    next.push(command)
    execLog = next
    console.log("stub execDetached:", JSON.stringify(command))
  }
}
