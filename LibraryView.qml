import QtQuick
import qs.Commons
import "Reader.js" as Reader

// The library: a grid of covers with one cursor shared by keyboard and
// mouse. The book being read comes first.
Item {
  id: view

  property var service: null
  property string filter: ""
  property color fg: Color.foreground
  property color dim: Qt.darker(fg, 1.5)
  property color accent: Color.accent
  property string fontFamily: Style.font.family

  readonly property var shown: Reader.filterBooks(service ? service.books : [], filter)
  readonly property int count: shown.length
  readonly property int columns: 4
  property int cursor: 0

  function reset() {
    cursor = 0
    grid.positionViewAtBeginning()
  }

  function moveTo(index) {
    if (count === 0) return
    cursor = Math.max(0, Math.min(count - 1, index))
    grid.positionViewAtIndex(cursor, GridView.Contain)
  }

  function moveCursor(dx, dy) {
    moveTo(Reader.gridMove(cursor, count, columns, dx, dy))
  }

  function movePage(direction) {
    var rows = Math.max(1, Math.floor(grid.height / grid.cellHeight))
    moveTo(cursor + direction * rows * columns)
  }

  // A book Reader does not draw itself was handed to the system's viewer.
  signal handedOff()

  function openAt(index) {
    var book = shown[index]
    if (!book || !service) return
    if (book.external === true) {
      service.openExternal(book.path)
      handedOff()
    } else {
      service.openBook(book.path, book.title)
    }
  }

  function openSelected() {
    openAt(cursor)
  }

  onCountChanged: if (cursor >= count) cursor = Math.max(0, count - 1)

  GridView {
    id: grid

    // One gutter wider than the view, so the last column's trailing gutter
    // falls outside and the covers span the full width.
    readonly property int gutter: Style.space(16)
    readonly property int coverWidth: cellWidth - gutter
    readonly property int coverHeight: Math.round(coverWidth * 1.5)

    anchors.top: parent.top
    anchors.bottom: parent.bottom
    anchors.left: parent.left
    width: parent.width + gutter
    visible: view.count > 0
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height
    cellWidth: Math.floor(width / view.columns)
    cellHeight: coverHeight + Style.space(58)
    model: view.shown

    delegate: CoverTile {
      required property var modelData
      required property int index

      width: grid.cellWidth
      height: grid.cellHeight
      book: modelData
      selected: index === view.cursor
      progress: view.service ? view.service.progressOf(modelData.key) : 0
      coverWidth: grid.coverWidth
      coverHeight: grid.coverHeight
      fg: view.fg
      dim: view.dim
      accent: view.accent
      fontFamily: view.fontFamily
      onEntered: view.cursor = index
      onClicked: view.openAt(index)
    }
  }

  // Nothing to show: say why, and what to do about it.
  Column {
    visible: view.count === 0
    anchors.centerIn: parent
    width: parent.width - Style.space(80)
    spacing: Style.space(10)

    Text {
      width: parent.width
      textFormat: Text.PlainText
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.Wrap
      color: view.fg
      font.family: view.fontFamily
      font.pixelSize: Style.font.body
      text: {
        if (!view.service) return ""
        if (view.service.libraryError) return view.service.libraryError
        if (view.filter !== "") return "No books match “" + view.filter + "”"
        if (view.service.scanning || !view.service.scanned) return "Looking for books…"
        return "No books yet"
      }
    }

    Text {
      width: parent.width
      visible: view.service && view.service.scanned && !view.service.scanning
        && view.filter === "" && !view.service.libraryError
      textFormat: Text.PlainText
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.Wrap
      color: view.dim
      font.family: view.fontFamily
      font.pixelSize: Style.font.bodySmall
      lineHeight: 1.4
      text: view.service && view.service.folder
        ? "Nothing readable was found in " + view.service.folder + "."
        : "Put ebooks in ~/Books or ~/Documents/Books and press r,\nor choose another folder with\nomarchy bar set clumzeez.reader folder ~/your/books"
    }
  }
}
