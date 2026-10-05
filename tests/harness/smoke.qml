import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Harness self-test: the real Omarchy kit renders with the active theme,
// the process bridge runs a command, and FileView round-trips a file.
Window {
  id: win
  width: 420
  height: 220
  visible: true
  color: Color.background

  property string procOut: ""
  property string fileOut: ""
  property int steps: 0

  function done() {
    steps++
    if (steps < 2) return
    grabTimer.start()
  }

  Process {
    id: echo
    command: ["sh", "-c", "printf 'bridge-ok %s' \"$HOME\""]
    running: true
    stdout: StdioCollector { onStreamFinished: { win.procOut = text; win.done() } }
  }

  FileView {
    id: file
    path: Quickshell.env("HOME") + "/.local/state/omarchy/settings/harness-smoke.json"
    printErrors: false
    onLoaded: { win.fileOut = text(); win.done() }
    onLoadFailed: setText('{"ok":true}')
    onSaved: reload()
  }

  BorderSurface {
    id: card
    anchors.fill: parent
    anchors.margins: 16
    color: Color.popups.background
    borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, 2)

    Column {
      anchors.fill: parent
      anchors.margins: Style.spacing.popupPadding
      spacing: Style.space(10)

      Text {
        text: "Reader harness · " + Style.font.resolvedFamily + " " + Style.font.body + "px"
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.subtitle
        font.bold: true
      }
      Text {
        text: "accent " + Color.accent + " · muted " + Color.muted + " · radius " + Style.cornerRadius
        color: Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
      Row {
        spacing: Style.space(8)
        Button { text: "Plain" }
        Button { text: "Bordered"; bordered: true }
        Button { text: "Selected"; selected: true; bordered: true }
        Button { iconText: "󰂺"; hasCursor: true }
      }
      TextField {
        width: parent.width
        placeholderText: "search"
      }
      Text {
        text: win.procOut + "\n" + win.fileOut
        color: Color.accent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }
  }

  Timer {
    id: grabTimer
    interval: 400
    onTriggered: {
      console.log("smoke: proc=" + win.procOut + " file=" + win.fileOut)
      win.contentItem.grabToImage(function(result) {
        var ok = result.saveToFile(Quickshell.opt("out", "/tmp/omarchy-reader-smoke.png"))
        console.log("smoke: saved=" + ok)
        Qt.exit(ok && win.procOut.indexOf("bridge-ok") === 0 && win.fileOut.indexOf("ok") > 0 ? 0 : 1)
      })
    }
  }
}
