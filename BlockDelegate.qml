import QtQuick
import qs.Commons
import "Reader.js" as Reader

// One block of a book: a paragraph, heading, list item, picture, rule,
// table or preformatted passage. The space above a block is part of the
// block (`gap`), so the reader can place a block's first line exactly at
// the top of the view.
Item {
  id: cell

  property var block: ({})
  property bool first: false
  property int fontPx: 13
  property real maxPictureHeight: 600
  property color fg: Color.foreground
  property color dim: Qt.darker(fg, 1.5)
  property color accent: Color.accent
  property string fontFamily: Style.font.family

  // The part of this block inside the reader's selection: { from, to } with
  // to -1 for "to the end", or null.
  property var selection: null
  property color selectionTint: Util.alpha(accent, 0.3)
  // The page behind is already light: a picture needs no paper of its own.
  property bool lightPage: false

  readonly property string kind: block.k || "p"
  readonly property bool textual: kind === "p" || kind === "h" || kind === "li" || kind === "pre"
  readonly property real em: fontPx

  readonly property real sizeFactor: {
    if (kind === "h") return [1.45, 1.3, 1.15][Math.max(1, Math.min(3, block.l || 1)) - 1]
    if (block.z === -1) return 0.9
    if (block.z === 1) return 1.15
    if (block.z === 2) return 1.3
    return 1
  }

  // Space above: none for the first block or a continuation, more before a
  // heading, a scene break (s: 1) or a new chapter (s: 2).
  readonly property real gap: {
    if (first || block.c === 1) return 0
    if (block.s === 2) return Math.round(em * 3.2)
    if (block.s === 1) return Math.round(em * 2.2)
    if (kind === "h") return Math.round(em * 1.9)
    return Math.round(em * 0.85)
  }

  readonly property real level: Math.max(0, Math.min(6, block.i || 0))
  // Wide enough for the marker's own text: "471." needs more room than "•".
  readonly property real markerWidth: kind === "li"
    ? Math.round(em * Math.max(2.2, String(block.m || "").length * 0.62 + 0.2)) : 0
  readonly property real inset: kind === "li"
    ? Math.round(Math.max(0, level - 1) * em * 1.5) + markerWidth + Math.round(em * 0.6)
    : Math.round(level * em * 1.5)
  readonly property real padding: kind === "pre" ? Math.round(em * 0.7) : 0

  // Whole-pixel line heights keep every block an integer number of pixels
  // tall, which is what makes restoring a place exact.
  readonly property int textPx: Math.max(6, Math.round(fontPx * sizeFactor))
  readonly property int lineStep: Math.round(textPx * (kind === "h" ? 1.3 : (kind === "pre" ? 1.4 : 1.6)))

  readonly property real bodyHeight: {
    if (textual) return Math.ceil(body.implicitHeight) + padding * 2
    if (kind === "img") return picture.item ? picture.item.height : 0
    if (kind === "tbl") return table.item ? table.item.height : 0
    if (kind === "hr") return Math.round(em * 1.5)
    return 0
  }

  height: gap + bodyHeight

  // ---- where a page may be cut (the reader's pages)
  //
  // All in this block's own coordinates. Text can be cut between any two
  // lines; a picture or a rule only before or after; a table between rows.

  readonly property real textTop: gap + padding
  // Counted from the height, which is what is drawn: the text item's own
  // count of its lines is now and then short.
  readonly property int lines: textual ? Math.max(1, Math.round((bodyHeight - padding * 2) / lineStep)) : 0
  // A heading is never cut: its lines are closer set than its letters are
  // tall, and half a heading is no way to end a page.
  readonly property bool whole: !textual || kind === "h"
  readonly property int textLength: textual ? body.length : 1

  function rowEdges() {
    var edges = [gap]
    var grid = table.item
    if (!grid) return edges
    var y = gap
    for (var i = 0; i < grid.children.length; i++) {
      var child = grid.children[i]
      if (!child || child.rowLine !== true) continue
      y += child.height
      edges.push(y)
    }
    return edges
  }

  // The lowest place at or above `offset` where a page may end: 0 when not
  // even the first line fits above it, the block's height when all of it
  // does.
  function cutAtOrBefore(offset) {
    if (offset >= height) return height
    if (textual && !whole) {
      var fit = Math.floor((offset - textTop) / lineStep)
      // A last line is not left behind without what is under it.
      if (fit >= lines) fit = lines - 1
      return fit <= 0 ? 0 : textTop + fit * lineStep
    }
    if (kind === "tbl") {
      var edges = rowEdges()
      var best = 0
      for (var i = 1; i < edges.length; i++) if (edges[i] <= offset) best = edges[i]
      return best
    }
    return 0
  }

  // The lowest line top at or above `offset` (the block's first when the
  // offset is in the space above it): where a page showing that place
  // starts.
  function topAtOrBefore(offset) {
    if (textual && !whole) {
      var line = Math.max(0, Math.min(lines - 1, Math.floor((offset - textTop) / lineStep)))
      return line === 0 ? gap : textTop + line * lineStep
    }
    if (kind === "tbl") {
      var edges = rowEdges()
      var best = gap
      for (var i = 0; i < edges.length - 1; i++) if (edges[i] <= offset) best = edges[i]
      return best
    }
    return gap
  }

  // The highest line top at or below `offset`; the block's height when
  // there is none, meaning the page starts with the next block.
  function topAtOrAfter(offset) {
    if (offset <= gap) return gap
    if (textual && !whole) {
      var line = Math.ceil((offset - textTop) / lineStep)
      return line >= lines ? height : textTop + Math.max(1, line) * lineStep
    }
    if (kind === "tbl") {
      var edges = rowEdges()
      for (var i = 0; i < edges.length - 1; i++) if (edges[i] >= offset) return edges[i]
    }
    return height
  }

  // ---- what is under a point (selecting, links)

  function linkUnder(item, x, y) {
    for (var hops = 0; item && hops < 8; hops++) {
      var child = item.childAt(x, y)
      if (!child) return ""
      var p = item.mapToItem(child, x, y)
      if (typeof child.linkAt === "function") return child.linkAt(p.x, p.y) || ""
      item = child
      x = p.x
      y = p.y
    }
    return ""
  }

  // { o, link, text }: the offset into this block's text nearest the point,
  // the link there if any, and whether the block has text to select. One
  // without counts 0 above its middle and 1 below.
  function hit(x, y) {
    if (!textual) {
      return { o: y < gap + bodyHeight / 2 ? 0 : 1, text: false,
               link: kind === "tbl" && table.item ? linkUnder(cell, x, y) : "" }
    }
    var px = x - body.x
    var py = y - body.y
    var inside = px >= 0 && px <= body.width && py >= 0 && py <= body.height
    return { o: body.positionAt(px, py), text: true, link: inside ? (body.linkAt(px, py) || "") : "" }
  }

  // The top and bottom of the letters drawn at a point, as the text item
  // itself reports them: for checking that a page edge runs between lines.
  function boxAt(x, y) {
    if (!textual) return { top: gap, bottom: height }
    var r = body.positionToRectangle(body.positionAt(x - body.x, y - body.y))
    return { top: body.y + r.y, bottom: body.y + r.y + r.height }
  }

  function wordAt(offset) {
    if (!textual) return { from: 0, to: 1 }
    body.cursorPosition = Math.max(0, Math.min(body.length, offset))
    body.selectWord()
    var found = { from: body.selectionStart, to: body.selectionEnd }
    applySelection()
    return found
  }

  function applySelection() {
    if (!textual) return
    var from = 0
    var to = 0
    if (selection) {
      to = selection.to < 0 ? body.length : Math.min(body.length, selection.to)
      from = Math.min(selection.from, to)
    }
    // Selecting in rich text announces a change of text, which would bring
    // this function round again: only ever ask for what is not yet so.
    if (body.selectionStart === from && body.selectionEnd === to) return
    if (to > from) body.select(from, to)
    else body.deselect()
  }

  // What the text item is given. A new size or colour is new markup, and
  // new markup arrives unselected.
  readonly property string markup: textual ? Reader.richText(block, lineStep, accent) : ""

  onSelectionChanged: applySelection()
  onMarkupChanged: Qt.callLater(applySelection)

  Rectangle {
    visible: cell.kind === "pre"
    x: cell.inset
    y: cell.gap
    width: cell.width - x
    height: cell.bodyHeight
    color: Util.alpha(cell.fg, 0.05)
  }

  Rectangle {
    visible: cell.block.q === 1 && cell.textual
    x: Math.max(0, cell.inset - Math.round(cell.em * 0.9))
    y: cell.gap
    width: 2
    height: cell.bodyHeight
    color: Util.alpha(cell.fg, 0.25)
  }

  // A block inside a selection that has no text of its own.
  Rectangle {
    visible: !cell.textual && cell.selection !== null
    y: cell.gap
    width: cell.width
    height: cell.bodyHeight
    color: cell.selectionTint
    opacity: 0.5
  }

  Text {
    visible: cell.kind === "li" && !!cell.block.m
    x: cell.inset - cell.markerWidth - Math.round(cell.em * 0.6)
    y: cell.gap
    width: cell.markerWidth
    textFormat: Text.PlainText
    text: cell.block.m || ""
    color: cell.dim
    font.family: cell.fontFamily
    font.pixelSize: cell.textPx
    horizontalAlignment: Text.AlignRight
    lineHeightMode: Text.FixedHeight
    lineHeight: cell.lineStep
  }

  // A text edit, though nothing is ever edited: it is the one text item
  // that can say which character is under a point, which is what selecting
  // needs. The reader's own pointer handling sits above it, so it is never
  // clicked or focused itself. Its lines are a fixed whole number of pixels
  // apart, as the paragraph style in Reader.richText() says.
  TextEdit {
    id: body
    visible: cell.textual
    x: cell.inset + cell.padding
    y: cell.gap + cell.padding
    width: cell.width - cell.inset - cell.padding * 2
    readOnly: true
    selectByMouse: false
    selectByKeyboard: false
    activeFocusOnPress: false
    activeFocusOnTab: false
    persistentSelection: true
    cursorVisible: false
    textFormat: TextEdit.RichText
    text: cell.markup
    color: cell.fg
    selectionColor: cell.selectionTint
    selectedTextColor: cell.fg
    font.family: cell.fontFamily
    font.pixelSize: cell.textPx
    font.bold: cell.kind === "h"
    wrapMode: cell.kind === "pre" ? TextEdit.WrapAnywhere : TextEdit.Wrap
    // Left unset for ordinary blocks so right-to-left text aligns itself.
    horizontalAlignment: cell.block.a === "c" ? TextEdit.AlignHCenter
      : (cell.block.a === "r" ? TextEdit.AlignRight : undefined)
    Component.onCompleted: cell.applySelection()
  }

  Text {
    visible: cell.kind === "hr"
    y: cell.gap
    width: cell.width
    height: cell.bodyHeight
    textFormat: Text.PlainText
    text: "·  ·  ·"
    color: cell.dim
    font.family: cell.fontFamily
    font.pixelSize: cell.fontPx
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
  }

  Loader {
    id: picture
    active: cell.kind === "img"
    y: cell.gap
    width: cell.width

    sourceComponent: Item {
      // Pictures are shown at their own size when they fit, shrunk to the
      // column or to most of a page when they do not, never enlarged.
      readonly property real naturalWidth: cell.block.w > 0 ? cell.block.w : image.implicitWidth
      readonly property real naturalHeight: cell.block.h > 0 ? cell.block.h : image.implicitHeight
      readonly property real fit: naturalWidth > 0 && naturalHeight > 0
        ? Math.min(1, cell.width / naturalWidth, cell.maxPictureHeight / naturalHeight) : 1
      readonly property real shownWidth: naturalWidth > 0 ? Math.round(naturalWidth * fit) : Math.round(cell.width * 0.5)
      readonly property real shownHeight: naturalHeight > 0 ? Math.round(naturalHeight * fit) : Math.round(cell.width * 0.3)

      width: cell.width
      height: shownHeight

      // Line art is usually dark strokes on a transparent ground, which
      // vanishes on a dark panel; give transparent pictures paper to sit on.
      Rectangle {
        visible: cell.block.al === 1 && image.status === Image.Ready && !cell.lightPage
        anchors.fill: image
        color: "#ece9e2"
      }

      Image {
        id: image
        anchors.horizontalCenter: parent.horizontalCenter
        width: parent.shownWidth
        height: parent.shownHeight
        source: cell.block.src ? Reader.pathToFileUrl(cell.block.src) : ""
        asynchronous: true
        // Bounded by what is drawn and by the file's own size (a larger
        // request would decode an enlarged copy).
        sourceSize.width: cell.block.w > 0
          ? Math.min(cell.block.w, Math.ceil(cell.width * Screen.devicePixelRatio))
          : Math.ceil(cell.width * Screen.devicePixelRatio)
        sourceSize.height: cell.block.h > 0
          ? Math.min(cell.block.h, Math.ceil(cell.maxPictureHeight * Screen.devicePixelRatio))
          : Math.ceil(cell.maxPictureHeight * Screen.devicePixelRatio)
        fillMode: Image.PreserveAspectFit
      }

      Text {
        anchors.centerIn: parent
        visible: image.status === Image.Error && !!cell.block.alt
        width: parent.width
        textFormat: Text.PlainText
        text: cell.block.alt || ""
        color: cell.dim
        font.family: cell.fontFamily
        font.pixelSize: cell.fontPx
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
      }
    }
  }

  Loader {
    id: table
    active: cell.kind === "tbl"
    y: cell.gap
    width: cell.width

    sourceComponent: Column {
      id: grid

      readonly property var rows: Array.isArray(cell.block.rows) ? cell.block.rows : []
      readonly property int columns: {
        var n = 1
        for (var r = 0; r < rows.length; r++) n = Math.max(n, rows[r].length)
        return n
      }
      readonly property var widths: Reader.columnWidths(rows, columns, cell.width)

      width: cell.width

      Repeater {
        model: grid.rows

        Item {
          id: line

          required property var modelData
          required property int index
          // Tells the rows from whatever else the column holds, for cutting
          // a page between two of them.
          readonly property bool rowLine: true

          width: grid.width
          height: cells.implicitHeight + Math.round(cell.em * 0.7)

          Rectangle {
            width: parent.width
            height: 1
            color: Util.alpha(cell.fg, line.index === 0 ? 0.25 : 0.10)
          }

          Row {
            id: cells
            y: Math.round(cell.em * 0.35)

            Repeater {
              model: line.modelData

              Text {
                required property var modelData
                required property int index

                width: grid.widths[index] || 0
                rightPadding: Math.round(cell.em * 0.8)
                textFormat: Reader.styledTextIsSafe(modelData) ? Text.StyledText : Text.PlainText
                text: String(modelData || "")
                color: cell.fg
                linkColor: cell.accent
                font.family: cell.fontFamily
                font.pixelSize: Math.max(6, Math.round(cell.fontPx * 0.9))
                font.bold: line.index < (cell.block.hdr || 0)
                wrapMode: Text.Wrap
                lineHeightMode: Text.FixedHeight
                lineHeight: Math.round(font.pixelSize * 1.4)
              }
            }
          }
        }
      }
    }
  }
}
