import QtQuick
import Quickshell

// Test stand-in for Quickshell.Io.IpcHandler. Handlers declare plain
// functions; a scenario reaches them the way `omarchy-shell <target> …`
// would, through Quickshell.ipc(target).
QtObject {
  id: handler

  property string target: ""
  property bool enabled: true

  Component.onCompleted: Quickshell.registerIpc(handler)
  Component.onDestruction: Quickshell.unregisterIpc(handler)
}
