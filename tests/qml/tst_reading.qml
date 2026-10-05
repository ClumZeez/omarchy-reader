import QtQuick
import QtTest
import Quickshell
import ReaderHarness
import "../../Reader.js" as Reader

// Selecting text with the mouse, and the book turned a page at a time.
// What a selection copies is only recorded: nothing here reaches a
// clipboard.
Item {
  id: root
  width: 1280
  height: 900

  readonly property string plugin: Quickshell.opt("plugin", "")
  readonly property string shots: Quickshell.opt("shots", "")

  BarHost {
    id: host
    anchors.fill: parent
    pluginDir: root.plugin
  }

  function find(item, name) {
    if (!item) return null
    if (String(item).indexOf(name) === 0 && item.visible) return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
      var hit = find(kids[i], name)
      if (hit) return hit
    }
    return null
  }

  function findList(item) {
    if (!item) return null
    if (item.restoring !== undefined && typeof item.track === "function") return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
      var hit = findList(kids[i])
      if (hit) return hit
    }
    return null
  }

  TestCase {
    name: "Reading"
    when: windowShown

    readonly property var service: host.service
    readonly property var widget: host.widget
    property var content: null
    property var reader: null
    property var list: null

    function shot(name) {
      if (!root.shots) return
      wait(250)
      var done = false
      host.card.grabToImage(function(result) {
        result.saveToFile(root.shots + "/" + name + ".png")
        done = true
      })
      tryVerify(function() { return done }, 5000)
    }

    // A point inside a block, in the reader's coordinates.
    function pointIn(index, dx, dy) {
      var item = list.itemAtIndex(index)
      verify(item !== null, "block " + index + " is on screen")
      var origin = item.mapToItem(reader, 0, 0)
      return Qt.point(origin.x + dx, origin.y + dy)
    }

    function lastCopy() {
      var asked = Quickshell.execLog[Quickshell.execLog.length - 1]
      return asked && asked[0] === "wl-copy" ? asked[asked.length - 1] : null
    }

    // Whether `y` (the list's own coordinates) falls between two lines, or
    // between two blocks: never through a line of text or a picture.
    function cleanCut(y) {
      var index = list.indexAt(list.width / 2, y - 0.5)
      if (index < 0) return true
      var item = list.itemAtIndex(index)
      var inside = y - item.y
      if (Math.abs(inside - item.height) < 0.5 || inside < item.gap + 0.5) return true
      if (!item.textual) return item.kind === "tbl"
      // A heading is never cut at all.
      if (item.kind === "h") return false
      // What the text item says is drawn just above and just below: one
      // line ending at or before the edge, another starting at or after it.
      var above = item.boxAt(item.width / 2, inside - 2)
      var below = item.boxAt(item.width / 2, inside + 2)
      return above.bottom <= inside + 0.5 && below.top >= inside - 0.5 && below.top > above.top
    }

    // No page ends with a heading, unless the heading is all there is on it.
    function headingStranded() {
      var bottom = reader.pageBottom
      var index = list.indexAt(list.width / 2, bottom - 0.5)
      if (index < 0) return false
      var item = list.itemAtIndex(index)
      if (!item || item.kind !== "h") return false
      if (Math.abs(item.y + item.height - bottom) > 0.5) return false
      if (reader.bookEndsBy(bottom)) return false
      return item.y + item.gap > list.contentY + 0.5
    }

    // The top of the page as a block and a distance into it. The list's own
    // coordinates are not fit to remember: they shift when blocks far away
    // are let go and made again.
    function topPlace() {
      var index = list.indexAt(list.width / 2, list.contentY)
      var item = list.itemAtIndex(index)
      return index + ":" + Math.round(list.contentY - item.y)
    }

    function initTestCase() {
      verify(root.plugin !== "", "pass plugin=<repository root>")
      tryVerify(function() { return host.service !== null }, 5000)
      host.service.backendCommand = ["python3", "-B", root.plugin + "/tests/qml/fake_reader.py"]
      tryVerify(function() { return host.widget !== null && host.service.storeReady }, 5000)
      widget.open()
      tryVerify(function() { return service.books.length === 9 }, 8000)
      service.openBook(service.books.filter(function(b) { return b.title === "Meditations" })[0].path, "Meditations")
      tryVerify(function() { return service.hasBook }, 8000)
      content = root.find(host.popouts[0], "PanelContent")
      reader = root.find(host.popouts[0], "ReaderView")
      list = root.findList(reader)
      verify(content !== null && reader !== null && list !== null)
      wait(300)
    }

    // ---- selecting

    function test_01_a_drag_selects_and_copies() {
      var item = list.itemAtIndex(1)
      var from = pointIn(1, 60, item.textTop + item.lineStep * 0.5)
      var copies = Quickshell.execLog.length
      mouseDrag(reader, from.x, from.y, 220, 0, Qt.LeftButton, Qt.NoModifier, 20)
      verify(reader.hasSelection)
      compare([reader.range.sb, reader.range.eb], [1, 1])
      verify(reader.range.eo > reader.range.so)
      var plain = Reader.plainText(service.blocks[1])
      compare(service.copied, plain.substring(reader.range.so, reader.range.eo).trim())
      verify(service.copied.length > 5)
      compare(Quickshell.execLog.length, copies + 1)
      compare(lastCopy(), service.copied)
      compare(service.notice, "Copied")
      // Selecting is not reading on: the place has not moved.
      compare(service.posBlock, 0)
      shot("40-selection")
    }

    function test_02_a_drag_across_paragraphs_keeps_them_apart() {
      var first = list.itemAtIndex(1)
      var from = pointIn(1, 80, first.textTop + first.lineStep * 0.5)
      var to = pointIn(3, 200, list.itemAtIndex(3).textTop + list.itemAtIndex(3).lineStep * 0.5)
      mouseDrag(reader, from.x, from.y, to.x - from.x, to.y - from.y, Qt.LeftButton, Qt.NoModifier, 20)
      compare([reader.range.sb, reader.range.eb], [1, 3])
      var parts = service.copied.split("\n\n")
      compare(parts.length, 3)
      compare(parts[1], Reader.plainText(service.blocks[2]))
      verify(Reader.plainText(service.blocks[1]).indexOf(parts[0]) > 0)
      compare(Reader.plainText(service.blocks[3]).indexOf(parts[2]), 0)
      // Dragging upwards selects the same.
      var before = service.copied
      mouseDrag(reader, to.x, to.y, from.x - to.x, from.y - to.y, Qt.LeftButton, Qt.NoModifier, 20)
      compare(service.copied, before)
    }

    function test_03_a_click_lets_go_and_escape_does_before_it_closes() {
      verify(reader.hasSelection)
      var at = pointIn(2, 100, list.itemAtIndex(2).textTop + 4)
      mouseClick(reader, at.x, at.y)
      verify(!reader.hasSelection)
      mouseDrag(reader, at.x, at.y, 150, 0, Qt.LeftButton, Qt.NoModifier, 20)
      verify(reader.hasSelection)
      keyClick(Qt.Key_Escape)
      verify(!reader.hasSelection)
      verify(widget.opened)
    }

    function test_04_a_double_click_takes_a_word_and_a_third_the_paragraph() {
      var item = list.itemAtIndex(2)
      var at = pointIn(2, 130, item.textTop + item.lineStep * 1.5)
      mouseDoubleClickSequence(reader, at.x, at.y)
      verify(reader.hasSelection)
      compare([reader.range.sb, reader.range.eb], [2, 2])
      verify(/^[A-Za-z]+$/.test(service.copied), "a single word, got " + JSON.stringify(service.copied))
      var plain = Reader.plainText(service.blocks[2])
      compare(plain.substring(reader.range.so, reader.range.eo), service.copied)
      // The characters either side of it are not letters.
      verify(reader.range.so === 0 || !/[A-Za-z]/.test(plain[reader.range.so - 1]))
      verify(reader.range.eo === plain.length || !/[A-Za-z]/.test(plain[reader.range.eo]))
      mouseClick(reader, at.x, at.y)
      compare([reader.range.so, reader.range.eo], [0, plain.length])
      compare(service.copied, plain)
      wait(600)
      mouseClick(reader, at.x, at.y)
      verify(!reader.hasSelection)
    }

    function test_05_links_are_still_followed() {
      service.jumpTo(13)
      wait(300)
      var item = list.itemAtIndex(14)
      verify(item !== null)
      var inner = null
      var outer = null
      for (var y = item.textTop + 3; y < item.height && (!inner || !outer); y += item.lineStep) {
        for (var x = 2; x < item.width; x += 4) {
          var link = item.hit(x, y).link
          if (link === "b:3" && !inner) inner = Qt.point(x, y)
          if (link.indexOf("https://") === 0 && !outer) outer = Qt.point(x + 6, y)
        }
      }
      verify(inner !== null && outer !== null)
      var opened = Quickshell.execLog.length
      var out = pointIn(14, outer.x, outer.y)
      mouseClick(reader, out.x, out.y)
      compare(Quickshell.execLog.length, opened + 1)
      compare(Quickshell.execLog[opened], ["xdg-open", "https://example.org/?a=1&b=2"])
      var into = pointIn(14, inner.x, inner.y)
      mouseClick(reader, into.x, into.y)
      tryCompare(service, "posBlock", 3, 2000)
      verify(service.canGoBack)
      // Dragging over a link selects it rather than following it.
      service.jumpTo(13)
      wait(300)
      out = pointIn(14, outer.x, outer.y)
      opened = Quickshell.execLog.length
      mouseDrag(reader, out.x, out.y, 70, 0, Qt.LeftButton, Qt.NoModifier, 20)
      compare(service.posBlock, 13)
      verify(reader.hasSelection)
      compare(lastCopy(), service.copied)
      compare(Quickshell.execLog.length, opened + 1)
      keyClick(Qt.Key_Escape)
    }

    function test_06_what_is_selected_stays_selected_as_the_text_moves() {
      service.jumpTo(0)
      wait(300)
      var item = list.itemAtIndex(1)
      var from = pointIn(1, 60, item.textTop + item.lineStep * 0.5)
      mouseDrag(reader, from.x, from.y, 220, 0, Qt.LeftButton, Qt.NoModifier, 20)
      var range = JSON.stringify(reader.range)
      for (var i = 0; i < 6; i++) mouseWheel(reader, reader.width / 2, reader.height / 2, 0, -120)
      wait(300)
      keyClick("+")
      wait(500)
      compare(JSON.stringify(reader.range), range)
      keyClick("0")
      wait(400)
      keyClick(Qt.Key_Escape)
      verify(!reader.hasSelection)
    }

    // ---- pages

    function test_10_p_turns_the_book_into_pages() {
      service.jumpTo(0)
      wait(300)
      verify(!reader.paged)
      keyClick("p")
      verify(reader.paged)
      wait(500)
      compare(service.posBlock, 0)
      verify(cleanCut(list.contentY))
      verify(cleanCut(reader.pageBottom))
      verify(reader.pageBottom - list.contentY <= list.height + 0.5)
      verify(reader.pageBottom - list.contentY > list.height * 0.6)
      shot("41-page-one")
    }

    // Every line of the book is on exactly one page: each page starts where
    // the one before it ended, and neither edge runs through a line.
    function test_11_turning_through_the_whole_book() {
      var pages = 1
      var tops = [topPlace()]
      for (var guard = 0; guard < 400; guard++) {
        var top = list.contentY
        var bottom = reader.pageBottom
        keyClick(Qt.Key_Right)
        if (list.contentY === top) break
        pages++
        tops.push(topPlace())
        verify(cleanCut(list.contentY), "page " + pages + " starts between lines")
        verify(cleanCut(reader.pageBottom), "page " + pages + " ends between lines")
        compare(list.contentY, reader.topFrom(bottom))
        verify(reader.pageBottom > list.contentY)
        verify(reader.pageBottom - list.contentY <= list.height + 0.5)
        verify(!headingStranded(), "page " + pages + " does not end with a heading")
      }
      verify(pages > 8, "the book is several pages long, got " + pages)
      compare(service.posBlock, service.blockCount - 1)
      compare(service.progress, 1)
      verify(reader.bookEndsBy(reader.pageBottom))
      shot("42-last-page")

      // And back: the same pages in reverse, to the very start.
      for (var back = tops.length - 2; back >= 0; back--) {
        keyClick(Qt.Key_Left)
        compare(topPlace(), tops[back])
        verify(cleanCut(list.contentY))
        verify(cleanCut(reader.pageBottom))
      }
      compare(service.posBlock, 0)
      keyClick(Qt.Key_Left)
      compare(topPlace(), tops[0])
      verify(reader.atBookStart())
    }

    function test_12_a_page_reached_by_a_jump_turns_back_to_the_page_before() {
      keyClick("]")
      keyClick("]")
      wait(300)
      var top = list.contentY
      verify(cleanCut(top))
      var starts = reader.cutFrom(top)
      keyClick(Qt.Key_Left)
      verify(list.contentY < top)
      verify(cleanCut(list.contentY))
      // It ends where the page jumped to begins: nothing skipped, nothing twice.
      compare(reader.pageBottom, starts)
      keyClick(Qt.Key_Right)
      compare(list.contentY, top)
      while (service.canGoBack) keyClick(Qt.Key_Backspace)
    }

    function test_13_no_page_cuts_a_picture() {
      service.jumpTo(12)
      wait(300)
      for (var turns = 0; turns < 6; turns++) {
        for (var index = 17; index <= 18; index++) {
          var item = list.itemAtIndex(index)
          if (!item) continue
          var top = item.y + item.gap
          var bottom = item.y + item.height
          var shown = top < reader.pageBottom && bottom > list.contentY
          if (shown) verify(top >= list.contentY - 0.5 && bottom <= reader.pageBottom + 0.5, "picture " + index + " is whole")
        }
        keyClick(Qt.Key_Space)
      }
      while (service.canGoBack) keyClick(Qt.Key_Backspace)
    }

    function test_14_every_way_of_turning() {
      service.jumpTo(0)
      wait(300)
      var first = list.contentY
      keyClick(Qt.Key_Down)
      var second = list.contentY
      verify(second > first)
      keyClick(Qt.Key_Up)
      compare(list.contentY, first)
      keyClick(Qt.Key_L)
      compare(list.contentY, second)
      keyClick(Qt.Key_H)
      compare(list.contentY, first)
      keyClick(Qt.Key_PageDown)
      compare(list.contentY, second)
      keyClick(Qt.Key_PageUp)
      compare(list.contentY, first)
      keyClick(Qt.Key_Space)
      compare(list.contentY, second)
      keyClick(Qt.Key_Space, Qt.ShiftModifier)
      compare(list.contentY, first)
      // One notch of the wheel is one page.
      mouseWheel(reader, reader.width / 2, reader.height / 2, 0, -120)
      compare(list.contentY, second)
      wait(120)
      mouseWheel(reader, reader.width / 2, reader.height / 2, 0, 120)
      compare(list.contentY, first)
      wait(120)
      while (service.canGoBack) keyClick(Qt.Key_Backspace)
    }

    function test_15_text_size_keeps_the_place_and_the_lines_whole() {
      for (var i = 0; i < 3; i++) keyClick(Qt.Key_Right)
      wait(300)
      var block = service.posBlock
      verify(block > 0)
      var keys = ["+", "+", "-", "-"]
      for (var k = 0; k < keys.length; k++) {
        keyClick(keys[k])
        wait(500)
        compare(service.posBlock, block)
        verify(cleanCut(list.contentY), "after " + keys[k] + " the page starts between lines")
        verify(cleanCut(reader.pageBottom), "after " + keys[k] + " the page ends between lines")
      }
      keyClick("0")
      wait(400)
    }

    function test_16_the_place_is_kept_across_closing_and_scrolling_again() {
      var block = service.posBlock
      var fraction = service.posFraction
      widget.close()
      tryVerify(function() { return !widget.opened }, 3000)
      widget.open()
      tryVerify(function() { return widget.opened }, 3000)
      wait(500)
      compare(service.posBlock, block)
      fuzzyCompare(service.posFraction, fraction, 0.001)
      verify(cleanCut(list.contentY))
      keyClick("p")
      verify(!reader.paged)
      wait(500)
      compare(service.posBlock, block)
      // Scrolling again: the frame is the whole view and the wheel scrolls.
      compare(reader.pageBottom, list.contentY + list.height)
      var before = list.contentY
      for (var i = 0; i < 3; i++) mouseWheel(reader, reader.width / 2, reader.height / 2, 0, -120)
      tryVerify(function() { return list.contentY > before }, 2000)
    }
  }
}
