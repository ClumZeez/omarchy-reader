import QtQuick
import QtQuick.Window
import Quickshell
import qs.Commons

// Offscreen window that grabs itself to a PNG and exits. Declare content
// as children; pass `out=/path.png` to run.sh. Scenarios that need to act
// before the grab set `auto: false` and call shoot(path, then) themselves.
Window {
  id: win

  default property alias content: holder.data
  property int delay: 500
  property bool auto: true
  property alias holder: holder

  visible: true
  color: Color.background

  function shoot(path, then) {
    var ok = win.contentItem.grabToImage(function(result) {
      var saved = result.saveToFile(path)
      console.log((saved ? "shot: wrote " : "shot: FAILED ") + path)
      if (then) then(saved)
    })
    if (!ok) {
      console.log("shot: grab refused for " + path)
      if (then) then(false)
    }
  }

  Item {
    id: holder
    anchors.fill: parent
  }

  Timer {
    interval: win.delay
    running: win.auto
    onTriggered: win.shoot(Quickshell.opt("out", "/tmp/omarchy-reader-shot.png"), function(saved) {
      Qt.exit(saved ? 0 : 1)
    })
  }
}
