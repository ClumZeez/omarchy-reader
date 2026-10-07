import QtQuick
import qs.Commons
import "Reader.js" as Reader

// One book in the library grid: its cover (or a drawn one when the book has
// no usable art), how far through it the reader is, and its title/author.
Item {
  id: tile

  property var book: ({})
  property bool selected: false
  property real progress: 0
  property int coverWidth: 140
  property int coverHeight: 210
  property color fg: Color.foreground
  property color dim: Qt.darker(fg, 1.5)
  property color accent: Color.accent
  property string fontFamily: Style.font.family

  signal clicked()
  signal entered()

  readonly property bool hasArt: !!book.cover && art.status !== Image.Error
  readonly property bool unreadable: !!book.error

  // Covers are never cropped: each keeps its own proportions inside the
  // cell and stands on the cell's bottom edge, like books on a shelf. The
  // size comes from the library entry so nothing moves when the image loads.
  readonly property real proportion: {
    var w = Number(book.coverW) || art.implicitWidth
    var h = Number(book.coverH) || art.implicitHeight
    return w > 0 && h > 0 ? w / h : 2 / 3
  }
  readonly property int shownWidth: hasArt ? Math.min(coverWidth, Math.round(coverHeight * proportion)) : coverWidth
  readonly property int shownHeight: hasArt ? Math.min(coverHeight, Math.round(coverWidth / proportion)) : coverHeight

  function bounded(shown, natural) {
    var wanted = Math.ceil(shown * Screen.devicePixelRatio)
    return natural > 0 ? Math.min(wanted, natural) : wanted
  }

  Item {
    id: frame
    y: tile.coverHeight - tile.shownHeight
    width: tile.shownWidth
    height: tile.shownHeight

    Rectangle {
      anchors.fill: parent
      color: Util.alpha(tile.fg, 0.05)
    }

    Image {
      id: art
      anchors.fill: parent
      visible: tile.hasArt && status === Image.Ready
      source: tile.book.cover ? Reader.pathToFileUrl(tile.book.cover) : ""
      asynchronous: true
      // Decoded at the size drawn, in physical pixels, so memory is bounded
      // however large the file is - but never above the file's own size,
      // which would decode an enlarged copy.
      sourceSize.width: tile.bounded(tile.coverWidth, tile.book.coverW)
      sourceSize.height: tile.bounded(tile.coverHeight, tile.book.coverH)
      fillMode: Image.PreserveAspectCrop
      opacity: tile.unreadable ? 0.4 : 1
    }

    // Drawn in place of art the book does not have.
    Column {
      visible: !art.visible
      anchors.fill: parent
      anchors.margins: Style.space(12)
      spacing: Style.space(8)
      opacity: tile.unreadable ? 0.5 : 1

      Rectangle {
        width: Style.space(18)
        height: 2
        color: tile.accent
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: tile.book.title || ""
        color: tile.fg
        font.family: tile.fontFamily
        font.pixelSize: Style.font.bodySmall
        font.bold: true
        wrapMode: Text.Wrap
        maximumLineCount: 7
        elide: Text.ElideRight
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: tile.book.author || ""
        color: tile.dim
        font.family: tile.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
        maximumLineCount: 3
        elide: Text.ElideRight
      }
    }

    // Books Reader lists but hands to another application say so.
    Rectangle {
      visible: tile.book.external === true
      anchors.top: parent.top
      anchors.right: parent.right
      anchors.margins: Style.space(6)
      width: tag.implicitWidth + Style.space(8)
      height: tag.implicitHeight + Style.space(4)
      color: Util.alpha(Color.background, 0.82)

      Text {
        id: tag
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: String(tile.book.format || "").toUpperCase()
        color: tile.fg
        font.family: tile.fontFamily
        font.pixelSize: Style.font.caption
      }
    }

    // The outline goes over the picture so its edge stays crisp at a
    // fractional display scale.
    Rectangle {
      anchors.fill: parent
      color: "transparent"
      border.width: tile.selected ? 2 : 1
      border.color: tile.selected ? tile.accent : Util.alpha(tile.fg, 0.14)
    }
  }

  // How far through the book the reader is: a bar under the cover, empty
  // for a book not yet begun. No figure; the bar says enough.
  Rectangle {
    id: bar
    objectName: "progressBar"
    visible: tile.book.external !== true && !tile.unreadable
    y: tile.coverHeight + Style.space(6)
    width: tile.shownWidth
    height: 3
    radius: 1.5
    color: Util.alpha(tile.fg, 0.14)

    Rectangle {
      objectName: "progressFill"
      width: Math.round(parent.width * Math.max(0, Math.min(1, tile.progress)))
      height: parent.height
      radius: parent.radius
      color: tile.accent
    }
  }

  Text {
    id: title
    y: bar.y + bar.height + Style.space(6)
    width: tile.coverWidth
    textFormat: Text.PlainText
    text: tile.book.title || ""
    color: tile.selected ? tile.accent : tile.fg
    font.family: tile.fontFamily
    font.pixelSize: Style.font.caption
    wrapMode: Text.Wrap
    maximumLineCount: 2
    elide: Text.ElideRight
    lineHeight: 1.15
  }

  Text {
    anchors.top: title.bottom
    anchors.topMargin: Style.space(2)
    width: tile.coverWidth
    textFormat: Text.PlainText
    text: tile.book.author || ""
    color: tile.dim
    font.family: tile.fontFamily
    font.pixelSize: Style.font.caption
    elide: Text.ElideRight
  }

  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onPositionChanged: tile.entered()
    onClicked: tile.clicked()
  }
}
