import QtQuick
import QtTest
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Harness self-test under qmltestrunner: the real Omarchy kit loads, the
// process bridge and FileView stubs work, synthetic keys reach
// PanelKeyCatcher, and an item can be grabbed to a PNG.
Item {
  id: root
  width: 420
  height: 240

  property string procOut: ""
  property int procCode: -1
  property string fileOut: ""
  property var moves: []
  property int closes: 0

  Process {
    id: echo
    command: ["sh", "-c", "printf 'bridge-ok'; exit 3"]
    stdout: StdioCollector { onStreamFinished: root.procOut = text }
    onExited: function(code) { root.procCode = code }
  }

  FileView {
    id: file
    path: Quickshell.env("HOME") + "/.local/state/omarchy/settings/harness-test.json"
    printErrors: false
    onLoaded: root.fileOut = text()
  }

  Rectangle {
    anchors.fill: parent
    color: Color.popups.background

    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      blocked: field.activeFocus
      onMoveRequested: function(dx, dy) { root.moves = root.moves.concat([[dx, dy]]) }
      onCloseRequested: root.closes++

      Column {
        anchors.fill: parent
        anchors.margins: Style.spacing.popupPadding
        spacing: Style.space(10)

        Text {
          text: "harness test"
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.subtitle
        }
        Button { id: button; text: "Bordered"; bordered: true }
        TextField { id: field; width: parent.width; placeholderText: "search" }
      }
    }
  }

  TestCase {
    name: "Harness"
    when: windowShown

    function test_kit_theme() {
      verify(Style.font.body >= 1)
      verify(String(Color.foreground).length > 0)
      verify(button.implicitHeight > 0)
    }

    function test_process_bridge() {
      echo.running = true
      tryCompare(root, "procCode", 3, 5000)
      compare(root.procOut, "bridge-ok")
      compare(echo.running, false)
    }

    function test_fileview_roundtrip() {
      file.setText('{"n":1}')
      wait(150)
      file.reload()
      tryCompare(root, "fileOut", '{"n":1}', 3000)
    }

    function test_keys_reach_catcher() {
      keys.forceActiveFocus()
      keyClick(Qt.Key_Down)
      keyClick(Qt.Key_J)
      keyClick(Qt.Key_Left)
      keyClick(Qt.Key_Escape)
      compare(root.moves.length, 3)
      compare(root.moves[0][1], 1)
      compare(root.moves[2][0], -1)
      compare(root.closes, 1)
    }

    function test_grab() {
      var image = grabImage(root)
      verify(image.width > 0)
      var out = Quickshell.opt("out", "")
      // save() reports nothing; a failed write shows up as a missing file.
      if (out) image.save(out)
    }
  }
}
