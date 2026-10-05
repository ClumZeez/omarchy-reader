import QtQuick
import qs.Commons
import "Reader.js" as Reader

// The book: a list of blocks, one delegate each, built only for what is on
// screen. The model is just the block count; delegates look their block up
// in the service's shared array, so a 30 000-block book costs nothing to
// attach.
//
// A ListView with delegates of unknown height only estimates its content
// height, and its origin drifts, so nothing here trusts contentY as an
// absolute coordinate: the reader's place is "block index + fraction of
// that block", and the ends of the book are found from its first and last
// blocks.
//
// The book either scrolls or is turned a page at a time. A page starts and
// ends between two lines: the list is shown through a frame that is cut off
// under the last whole line, and turning puts the next line at the top.
//
// The mouse selects text (which copies it) rather than dragging the page;
// the wheel scrolls or turns.
Item {
  id: view

  property var service: null
  property int fontPx: Style.font.subtitle
  property color fg: Color.foreground
  property color dim: Qt.darker(fg, 1.5)
  property color accent: Color.accent
  property string fontFamily: Style.font.family
  property bool lightPage: false
  readonly property bool paged: service ? service.paged === true : false
  readonly property bool busy: service ? service.opening : false
  readonly property string problem: service ? service.openError : ""
  readonly property int lineStep: Math.round(fontPx * 1.6)

  // ---- selection: where a drag began, and what is selected, in reading
  // order ({ sb, so, eb, eo }: blocks and offsets into their text).
  property var anchor: null
  property var range: null
  readonly property bool hasSelection: range !== null

  // ---- pages: where the page on show ends, in the list's coordinates, and
  // the tops of the pages turned forward from, so turning back is exact.
  property real pageBottom: 0
  property var turned: []

  function clearSelection() {
    anchor = null
    range = null
  }

  function copySelection() {
    if (range && service) service.copyText(Reader.selectionText(service.blocks, range))
  }

  // ---- geometry helpers, all in the list's content coordinates

  function blockAt(y) {
    var index = list.indexAt(list.width / 2, y)
    return index >= 0 ? list.itemAtIndex(index) : null
  }

  function clampY(y) {
    var first = list.itemAtIndex(0)
    var low = first ? first.y : list.originY
    var high = list.originY + list.contentHeight - list.height
    return Math.max(low, Math.min(y, Math.max(low, high)))
  }

  function atBookStart() {
    var first = list.itemAtIndex(0)
    return first !== null && list.contentY <= first.y + first.gap + 0.5
  }

  function bookEndsBy(y) {
    var last = list.itemAtIndex(list.count - 1)
    return last !== null && last.y + last.height <= y + 0.5
  }

  // Where the page that starts at `top` ends: under the last line that fits
  // whole. Something taller than a page by itself is shown as far as it goes.
  function pageEnd(top) {
    var limit = top + list.height
    var index = list.indexAt(list.width / 2, limit - 1)
    var item = index >= 0 ? list.itemAtIndex(index) : null
    if (!item) return limit
    var cut = aboveHeadings(item.y + item.cutAtOrBefore(limit - item.y), top)
    return cut > top + 0.5 ? cut : limit
  }

  // A heading goes with what it heads. A cut that would leave one as the
  // last thing on a page is moved above it, and above any heading over
  // that; but never to the top of the page, which would leave it empty.
  function aboveHeadings(cut, top) {
    var kept = cut
    for (var index = list.indexAt(list.width / 2, cut - 0.5); index >= 0; index--) {
      var heading = list.itemAtIndex(index)
      if (!heading || heading.kind !== "h" || Math.abs(heading.y + heading.height - kept) > 0.5) break
      if (heading.y + heading.gap <= top + 0.5) break
      kept = heading.y
    }
    return kept
  }

  // A page does not start with the space above a block.
  function topFrom(cut) {
    var item = blockAt(cut)
    if (!item) return cut
    return cut - item.y < item.gap + 0.5 ? item.y + item.gap : cut
  }

  // Where the page before one starting at `top` has to end.
  function cutFrom(top) {
    var item = blockAt(top)
    if (!item) return top
    return top - item.y < item.gap + 0.5 ? item.y : top
  }

  function lineTopAtOrBefore(y) {
    var item = blockAt(y)
    return item ? item.y + item.topAtOrBefore(y - item.y) : y
  }

  function lineTopAtOrAfter(y) {
    var index = list.indexAt(list.width / 2, y)
    var item = index >= 0 ? list.itemAtIndex(index) : null
    if (!item) return y
    var top = item.topAtOrAfter(y - item.y)
    if (top < item.height) return item.y + top
    var next = list.itemAtIndex(index + 1)
    return next ? next.y + next.gap : item.y + item.height
  }

  function measure() {
    pageBottom = paged ? pageEnd(list.contentY) : list.contentY + list.height
  }

  // Puts `y` at the top without the position being reported half-way.
  function place(y) {
    list.restoring = true
    list.contentY = clampY(y)
    list.restoring = false
  }

  // The same going up the book, where the list only guesses how far up it
  // goes until the first block exists: go, and stop at the first block if
  // that turns out to have been passed.
  function reach(y) {
    list.restoring = true
    list.contentY = y
    var first = list.itemAtIndex(0)
    if (first && list.contentY < first.y) list.contentY = first.y
    list.restoring = false
  }

  // Puts the service's saved place at the top of the view. One pass is
  // enough: once positioned on the block its delegate exists.
  function restore() {
    if (!service || list.count === 0 || list.width <= 0) return
    var index = Math.max(0, Math.min(list.count - 1, service.posBlock))
    var fraction = service.posFraction
    var atEnd = index === list.count - 1 && fraction >= 1
    glide.stop()
    list.restoring = true
    if (atEnd && !paged) {
      // The end of the book: the last block's end at the bottom edge.
      list.positionViewAtEnd()
    } else {
      list.positionViewAtIndex(index, ListView.Beginning)
      var item = list.itemAtIndex(index)
      if (item && atEnd) {
        // The page that ends with the book.
        list.contentY = clampY(item.y + item.height - list.height)
        list.contentY = clampY(lineTopAtOrAfter(list.contentY))
      } else if (item) {
        var y = item.y + item.gap + Math.round(fraction * Math.max(0, item.height - item.gap))
        list.contentY = clampY(y)
        // A page starts with a whole line: the one the place is in.
        if (paged) list.contentY = clampY(lineTopAtOrBefore(list.contentY + 0.5))
      }
    }
    list.restoring = false
    measure()
  }

  // A relayout keeps the pixel offset, not the fraction through the block,
  // so the remembered place has to be put back - and only once the layout
  // has stopped moving. Delegates take their new heights a frame or more
  // after whatever changed them (a text size, a width, a window shown
  // again); a place set before that ends up a paragraph away and would then
  // be saved. So it is set, and set again until two passes agree.
  function reanchor() {
    turned = []
    if (!service || list.count === 0 || list.width <= 0) return
    list.restoring = true
    settle.passes = 0
    settle.restart()
  }

  // The reader moving the text themselves outranks putting it back.
  function yieldToReader() {
    if (!settle.running) return
    settle.stop()
    list.restoring = false
  }

  function scrollBy(pixels) {
    if (list.count === 0 || pixels === 0) return
    yieldToReader()
    if ((pixels > 0 && list.atYEnd) || (pixels < 0 && list.atYBeginning)) return
    var from = glide.running ? glide.to : list.contentY
    list.scrollDirection = pixels > 0 ? 1 : -1
    glide.stop()
    glide.from = list.contentY
    glide.to = from + pixels
    glide.start()
  }

  function scrollLines(lines) {
    scrollBy(lines * lineStep)
  }

  // A page on or back: turned when the book is in pages, scrolled by nearly
  // a screen when it is not.
  function page(direction) {
    if (paged) turn(direction)
    else scrollBy(direction * Math.max(lineStep * 2, list.height - lineStep * 2))
  }

  function turn(direction) {
    if (list.count === 0 || direction === 0) return
    yieldToReader()
    glide.stop()
    var top = list.contentY
    var index = list.indexAt(list.width / 2, top)
    var from = index >= 0 ? list.itemAtIndex(index) : null
    var here = { b: index, off: from ? top - from.y : 0 }
    if (direction > 0) {
      // On from the end of what is shown, which after a turn back can be
      // less than the page would hold.
      var end = pageBottom > top + 0.5 && pageBottom <= top + list.height + 0.5 ? pageBottom : pageEnd(top)
      if (bookEndsBy(end)) return
      if (from) turned = turned.concat([here]).slice(-400)
      place(topFrom(end))
      measure()
    } else {
      if (atBookStart()) return
      var back = turned.length > 0 ? turned[turned.length - 1] : null
      turned = turned.slice(0, -1)
      var kept = null
      if (back && from && (back.b < here.b || (back.b === here.b && back.off < here.off - 0.5))) {
        // The very page this one was turned from; its block may have to be
        // brought back into being first.
        kept = list.itemAtIndex(back.b)
        if (!kept) {
          list.restoring = true
          list.positionViewAtIndex(back.b, ListView.Beginning)
          list.restoring = false
          kept = list.itemAtIndex(back.b)
        }
      }
      if (kept) {
        place(kept.y + back.off)
        measure()
      } else {
        // The page that ends where this one starts. Going there first
        // makes the blocks up there exist; then the first line that fits.
        var ends = cutFrom(top)
        reach(ends - list.height)
        ends = aboveHeadings(ends, ends - list.height)
        reach(ends - list.height)
        var first = lineTopAtOrAfter(list.contentY)
        if (first >= ends - 0.5) first = lineTopAtOrBefore(list.contentY)
        reach(first)
        pageBottom = Math.min(Math.max(ends, list.contentY + 1), pageEnd(list.contentY))
      }
    }
    flash.restart()
    list.track()
  }

  onVisibleChanged: if (visible) reanchor()
  onFontPxChanged: reanchor()
  onPagedChanged: reanchor()
  onHeightChanged: if (paged) reanchor()

  Timer {
    id: settle

    property int passes: 0

    interval: 30
    repeat: true
    onTriggered: {
      var before = list.contentY
      view.restore()
      passes++
      if ((passes >= 3 && Math.abs(list.contentY - before) < 1) || passes >= 10) stop()
      else list.restoring = true
    }
  }

  Connections {
    target: view.service
    function onPosRevisionChanged() {
      view.turned = []
      view.restore()
    }
    function onBookKeyChanged() { view.clearSelection() }
  }

  NumberAnimation {
    id: glide
    target: list
    property: "contentY"
    duration: 140
    easing.type: Easing.OutCubic
  }

  // What is shown of the list: all of it when the book scrolls; in pages,
  // down to the last whole line and no further.
  Item {
    id: frame
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.leftMargin: Style.space(10)
    anchors.rightMargin: Style.space(10)
    height: view.paged ? Math.max(0, Math.min(view.height, view.pageBottom - list.contentY)) : view.height
    visible: !view.busy && view.problem === ""
    clip: true

    ListView {
      id: list

      property bool restoring: false
      property int scrollDirection: 0

      // The block at the top edge and how far through it that edge is.
      function track() {
        var index = indexAt(width / 2, contentY)
        var item = index >= 0 ? itemAtIndex(index) : null
        if (!item || !view.service) return
        var ended = view.paged ? view.bookEndsBy(view.pageBottom) && !view.atBookStart() : atYEnd && !atYBeginning
        if (ended) {
          view.service.reportPosition(count - 1, 1)
          return
        }
        view.service.reportPosition(index, (contentY - item.y - item.gap) / Math.max(1, item.height - item.gap))
      }

      width: parent.width
      height: view.height
      spacing: 0
      boundsBehavior: Flickable.StopAtBounds
      pixelAligned: true
      cacheBuffer: Math.max(600, Math.round(height * 1.2))
      reuseItems: false
      highlightFollowsCurrentItem: false
      keyNavigationEnabled: false
      // Pages are turned, never dragged or flicked; and a mouse button
      // selects text in either layout, so it never drags the list.
      interactive: !view.paged
      acceptedButtons: Qt.NoButton
      model: view.service ? view.service.blockCount : 0

      // Room after the last block, so the page that holds the end of the
      // book can start on whichever line it does.
      footer: Item {
        width: 1
        height: view.paged ? list.height : 0
      }

      onContentYChanged: {
        if (restoring || count === 0) return
        // contentY is never clamped for us: stop an animated scroll at the
        // ends, or it would run on past the book.
        if (glide.running && ((scrollDirection > 0 && atYEnd) || (scrollDirection < 0 && atYBeginning))) {
          glide.stop()
          returnToBounds()
        }
        if (!view.paged) view.pageBottom = contentY + height
        if (view.visible) track()
      }

      onWidthChanged: view.reanchor()
      onMovementStarted: view.yieldToReader()

      delegate: BlockDelegate {
        required property int index

        width: ListView.view.width
        block: view.service && view.service.blocks[index] ? view.service.blocks[index] : ({})
        first: index === 0
        fontPx: view.fontPx
        maxPictureHeight: Math.max(200, Math.round(list.height * 0.85))
        fg: view.fg
        dim: view.dim
        accent: view.accent
        fontFamily: view.fontFamily
        lightPage: view.lightPage
        selection: Reader.sliceFor(view.range, index)
        selectionTint: Style.selectionFillFor(view.fg, view.accent)
      }
    }

    // A turned page arrives with the faintest blink, so the eye knows it is
    // a new one even when it looks much like the last.
    opacity: 1

    SequentialAnimation {
      id: flash
      PropertyAction { target: frame; property: "opacity"; value: 0.35 }
      NumberAnimation { target: frame; property: "opacity"; to: 1; duration: 130; easing.type: Easing.OutQuad }
    }
  }

  // The pointer over the text. A drag selects and copies; a double click
  // takes a word, a third the paragraph; a click follows a link or lets go
  // of a selection. The wheel turns pages when the book is in pages and is
  // left to the list when it scrolls.
  MouseArea {
    id: pointer

    property var pressHit: null
    property real pressX: 0
    property real pressY: 0
    property bool dragging: false
    // A word or paragraph was just taken with a double click: the release
    // that follows must not let go of it.
    property bool picked: false
    property double pickedAt: 0
    property int pickedBlock: -1
    property bool overLink: false
    property bool overText: false
    property real wheelSum: 0

    // { b, o, link, text } for a point in this area; null over nothing.
    function locate(x, y) {
      if (list.count === 0) return null
      var cy = Math.max(0, Math.min(height - 1, y)) + list.contentY
      var index = list.indexAt(list.width / 2, cy)
      if (index < 0) {
        // Under the last block: its very end.
        index = list.count - 1
        var last = list.itemAtIndex(index)
        if (!last || cy < last.y) return null
        return { b: index, o: last.textLength, link: "", text: last.textual }
      }
      var item = list.itemAtIndex(index)
      if (!item) return null
      var hit = item.hit(x, cy - item.y)
      return { b: index, o: hit.o, link: hit.link, text: hit.text }
    }

    function extendTo(x, y) {
      var hit = locate(x, y)
      if (hit && view.anchor) view.range = Reader.orderRange(view.anchor, { b: hit.b, o: hit.o })
    }

    anchors.fill: frame
    visible: frame.visible
    hoverEnabled: true
    acceptedButtons: Qt.LeftButton
    preventStealing: true
    cursorShape: overLink ? Qt.PointingHandCursor : (overText ? Qt.IBeamCursor : Qt.ArrowCursor)

    onPressed: function(mouse) {
      var hit = locate(mouse.x, mouse.y)
      pressHit = hit
      pressX = mouse.x
      pressY = mouse.y
      dragging = false
      if (picked && hit && hit.b === pickedBlock && Date.now() - pickedAt < 500) {
        // The third click of three: the whole paragraph.
        var whole = list.itemAtIndex(hit.b)
        if (whole && whole.textual) {
          view.range = { sb: hit.b, so: 0, eb: hit.b, eo: whole.textLength }
          view.copySelection()
          pickedAt = 0
          return
        }
      }
      picked = false
      view.anchor = hit ? { b: hit.b, o: hit.o } : null
    }

    onPositionChanged: function(mouse) {
      if (!pressed) {
        var under = locate(mouse.x, mouse.y)
        overLink = under !== null && under.link !== ""
        overText = under !== null && under.text
        return
      }
      if (picked) return
      if (!dragging && Math.abs(mouse.x - pressX) + Math.abs(mouse.y - pressY) > 3) dragging = true
      if (!dragging) return
      extendTo(mouse.x, mouse.y)
      // Past the top or bottom edge the text follows, when it scrolls.
      if (!view.paged && (mouse.y < 0 || mouse.y > height)) edge.start()
      else edge.stop()
    }

    onReleased: function(mouse) {
      edge.stop()
      if (dragging) {
        dragging = false
        view.copySelection()
      } else if (!picked) {
        if (pressHit && pressHit.link !== "" && view.service) view.service.followLink(pressHit.link)
        view.clearSelection()
      }
    }

    onCanceled: {
      edge.stop()
      dragging = false
    }

    onDoubleClicked: function(mouse) {
      var hit = locate(mouse.x, mouse.y)
      var item = hit ? list.itemAtIndex(hit.b) : null
      if (!item || !item.textual) return
      var word = item.wordAt(hit.o)
      if (word.to <= word.from) return
      view.anchor = { b: hit.b, o: word.from }
      view.range = { sb: hit.b, so: word.from, eb: hit.b, eo: word.to }
      picked = true
      pickedAt = Date.now()
      pickedBlock = hit.b
      view.copySelection()
    }

    onWheel: function(wheel) {
      if (!view.paged) {
        wheel.accepted = false
        return
      }
      wheel.accepted = true
      // A wheel sends whole notches; a touchpad sends a stream of small
      // moves, and one swipe - however long its tail - is one page.
      var smooth = wheel.pixelDelta.y !== 0 || Math.abs(wheel.angleDelta.y) % 120 !== 0
      if (lull.running) {
        if (smooth) lull.restart()
        return
      }
      wheelSum += wheel.angleDelta.y !== 0 ? wheel.angleDelta.y : wheel.pixelDelta.y * 3
      if (Math.abs(wheelSum) < 120) return
      view.turn(wheelSum < 0 ? 1 : -1)
      wheelSum = 0
      lull.interval = smooth ? 300 : 40
      lull.restart()
    }

    Timer {
      id: lull
      onTriggered: pointer.wheelSum = 0
    }

    // A drag held past an edge scrolls the text under it.
    Timer {
      id: edge
      interval: 40
      repeat: true
      onTriggered: {
        if (!pointer.pressed || !pointer.dragging) {
          stop()
          return
        }
        var step = pointer.mouseY < 0 ? -view.lineStep : (pointer.mouseY > pointer.height ? view.lineStep : 0)
        if (step === 0 || (step > 0 && list.atYEnd) || (step < 0 && list.atYBeginning)) return
        list.contentY = view.clampY(list.contentY + step)
        pointer.extendTo(pointer.mouseX, pointer.mouseY)
      }
    }
  }

  Column {
    visible: view.busy || view.problem !== ""
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
      lineHeight: 1.4
      text: view.problem !== "" ? view.problem : "Opening…"
    }

    Text {
      width: parent.width
      visible: view.problem !== ""
      textFormat: Text.PlainText
      horizontalAlignment: Text.AlignHCenter
      color: view.dim
      font.family: view.fontFamily
      font.pixelSize: Style.font.bodySmall
      text: "b  library"
    }
  }
}
