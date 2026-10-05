import QtQuick
import qs.Commons
import qs.Ui
import "Reader.js" as Reader

// The table of contents: an indented list whose branches fold, with the
// chapter being read marked and one cursor shared by keyboard and mouse.
Item {
  id: view

  property var service: null
  property string filter: ""
  property color fg: Color.foreground
  property color dim: Qt.darker(fg, 1.5)
  property color accent: Color.accent
  property string fontFamily: Style.font.family

  // Branch index -> true for folded branches. Replaced, never mutated, so
  // the rows binding sees every change.
  property var collapsed: ({})
  property int cursor: 0

  readonly property var toc: service ? service.toc : []
  readonly property int current: service ? service.tocIndex : -1
  readonly property var rows: Reader.tocRows(toc, collapsed, filter, current)
  readonly property int count: rows.length

  signal jumped()

  // Called when the view is opened: fold a long list down to the branch
  // being read and put the cursor on the current chapter.
  function reset() {
    collapsed = Reader.tocDefaultCollapsed(toc, current)
    var row = Reader.rowOfTocIndex(Reader.tocRows(toc, collapsed, "", current), current)
    cursor = Math.max(0, row)
    Qt.callLater(function() { list.positionViewAtIndex(view.cursor, ListView.Center) })
  }

  function moveTo(row) {
    if (count === 0) return
    cursor = Math.max(0, Math.min(count - 1, row))
    list.positionViewAtIndex(cursor, ListView.Contain)
  }

  function moveCursor(delta) {
    moveTo(cursor + delta)
  }

  function setFolded(tocIndex, folded) {
    var next = {}
    for (var key in collapsed) next[key] = true
    if (folded) next[tocIndex] = true
    else delete next[tocIndex]
    collapsed = next
  }

  function unfold() {
    var row = rows[cursor]
    if (row && row.kids && !row.open) setFolded(row.index, false)
  }

  // Folds the branch under the cursor, or steps out to its parent.
  function fold() {
    var row = rows[cursor]
    if (!row) return
    if (row.kids && row.open) {
      setFolded(row.index, true)
      return
    }
    var parent = Reader.tocParent(toc, row.index)
    if (parent < 0) return
    var parentRow = Reader.rowOfTocIndex(rows, parent)
    if (parentRow >= 0) moveTo(parentRow)
  }

  function jumpTo(row) {
    var entry = rows[row]
    if (!entry || !service) return
    service.jumpTo(entry.b)
    jumped()
  }

  function jump() {
    jumpTo(cursor)
  }

  onCountChanged: if (cursor >= count) cursor = Math.max(0, count - 1)
  onFilterChanged: cursor = 0

  ListView {
    id: list
    anchors.fill: parent
    visible: view.count > 0
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height
    model: view.rows

    delegate: CursorSurface {
      id: row

      required property var modelData
      required property int index

      readonly property int inset: Style.space(10) + modelData.d * Style.space(16)

      width: ListView.view.width
      height: Math.max(Style.space(30), label.implicitHeight + (path.visible ? path.implicitHeight : 0) + Style.space(12))
      hasCursor: index === view.cursor
      foreground: view.fg
      accent: view.accent

      Rectangle {
        visible: row.modelData.cur
        x: Style.space(2)
        width: 2
        height: parent.height - Style.space(10)
        anchors.verticalCenter: parent.verticalCenter
        color: view.accent
      }

      Text {
        visible: row.modelData.kids
        x: row.inset
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: String.fromCodePoint(row.modelData.open ? 0xF0140 : 0xF0142)
        color: view.dim
        font.family: view.fontFamily
        font.pixelSize: Style.font.body
      }

      Column {
        x: row.inset + Style.space(18)
        width: parent.width - x - Style.space(56)
        anchors.verticalCenter: parent.verticalCenter

        Text {
          id: path
          width: parent.width
          visible: row.modelData.path !== ""
          textFormat: Text.PlainText
          text: row.modelData.path
          color: view.dim
          font.family: view.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideMiddle
        }

        Text {
          id: label
          width: parent.width
          textFormat: Text.PlainText
          text: row.modelData.t
          color: row.modelData.cur ? view.accent : view.fg
          font.family: view.fontFamily
          font.pixelSize: Style.font.body
          font.bold: row.modelData.kids && row.modelData.d === 0
          wrapMode: Text.Wrap
          maximumLineCount: 2
          elide: Text.ElideRight
          lineHeight: 1.2
        }
      }

      Text {
        anchors.right: parent.right
        anchors.rightMargin: Style.space(10)
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: view.service && view.service.prep
          ? Reader.percentLabel(Reader.progressAt(view.service.prep, row.modelData.b, 0)) : ""
        color: view.dim
        font.family: view.fontFamily
        font.pixelSize: Style.font.caption
      }

      MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onPositionChanged: view.cursor = row.index
        onClicked: function(mouse) {
          // The fold arrow toggles its branch; anywhere else jumps.
          if (row.modelData.kids && mouse.x < row.inset + Style.space(18)) {
            view.cursor = row.index
            view.setFolded(row.modelData.index, row.modelData.open)
          } else {
            view.jumpTo(row.index)
          }
        }
      }
    }
  }

  Text {
    visible: view.count === 0
    anchors.centerIn: parent
    width: parent.width - Style.space(80)
    textFormat: Text.PlainText
    horizontalAlignment: Text.AlignHCenter
    wrapMode: Text.Wrap
    color: view.dim
    font.family: view.fontFamily
    font.pixelSize: Style.font.body
    text: view.filter !== "" ? "No chapters match “" + view.filter + "”" : "This book has no table of contents"
  }
}
