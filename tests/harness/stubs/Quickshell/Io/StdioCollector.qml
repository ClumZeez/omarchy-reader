import QtQuick

// Test stand-in for Quickshell.Io.StdioCollector. The bridge delivers a
// process's whole output at exit, so `text` is set once.
QtObject {
  property bool waitForEnd: true
  property string text: ""
  property string data: ""

  signal streamFinished()

  function _feed(output) {
    text = output
    data = output
    streamFinished()
  }
}
