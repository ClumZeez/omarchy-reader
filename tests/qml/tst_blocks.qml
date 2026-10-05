import QtQuick
import QtTest
import Quickshell
import ReaderHarness

// Every kind of block in one chapter, and following links out and back.
Item {
  id: root
  width: 1280
  height: 1100

  readonly property string plugin: Quickshell.opt("plugin", "")
  readonly property string shots: Quickshell.opt("shots", "")

  BarHost {
    id: host
    anchors.fill: parent
    pluginDir: root.plugin
  }

  TestCase {
    name: "Blocks"
    when: windowShown

    readonly property var service: host.service

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

    function initTestCase() {
      verify(root.plugin !== "", "pass plugin=<repository root>")
      tryVerify(function() { return host.service !== null }, 5000)
      host.service.backendCommand = ["python3", "-B", root.plugin + "/tests/qml/fake_reader.py"]
      tryVerify(function() { return host.widget !== null && host.service.storeReady }, 5000)
      host.widget.open()
      tryVerify(function() { return service.books.length === 9 }, 8000)
      service.openBook(service.books.filter(function(b) { return b.title === "Meditations" })[0].path, "Meditations")
      tryVerify(function() { return service.hasBook }, 8000)
    }

    function test_1_every_block_kind_renders() {
      keyClick("]")
      tryVerify(function() { return service.chapterTitle === "A section inside" }, 3000)
      shot("20-blocks")
      var kinds = {}
      for (var i = 0; i < service.blockCount; i++) kinds[service.blocks[i].k] = true
      compare(Object.keys(kinds).sort().join(" "), "h hr img li p pre tbl")
    }

    function test_2_links_go_out_and_come_back() {
      // Stepping to the chapter above was a jump too: one place to return to.
      compare(service.trail.length, 1)
      for (var i = 0; i < 4; i++) keyClick(Qt.Key_J)
      tryVerify(function() { return service.landed === null }, 3000)
      wait(300)
      var here = service.posBlock
      var fraction = service.posFraction
      service.followLink("b:3")
      compare(service.posBlock, 3)
      compare(service.trail.length, 2)
      wait(200)
      keyClick(Qt.Key_Backspace)
      compare(service.posBlock, here)
      fuzzyCompare(service.posFraction, fraction, 0.001)
      compare(service.trail.length, 1)
      keyClick(Qt.Key_Backspace)
      compare(service.posBlock, 0)
      verify(!service.canGoBack)
      keyClick(Qt.Key_Backspace)
      compare(service.posBlock, 0)

      service.followLink("https://example.org/?a=1&b=2")
      compare(Quickshell.execLog.length, 1)
      compare(Quickshell.execLog[0][1], "https://example.org/?a=1&b=2")
      service.followLink("javascript:alert(1)")
      service.followLink("file:///etc/passwd")
      compare(Quickshell.execLog.length, 1)
    }

    // A slip of the finger must not lose the reader's place: whatever jumps,
    // Backspace returns to where the reading stopped.
    function test_3_any_jump_can_be_undone() {
      compare(service.trail.length, 0)
      for (var i = 0; i < 7; i++) keyClick(Qt.Key_J)
      tryVerify(function() { return service.posBlock > 0 }, 3000)
      wait(300)
      var block = service.posBlock
      var fraction = service.posFraction

      keyClick("G")
      tryVerify(function() { return service.posBlock === service.blockCount - 1 }, 3000)
      wait(200)
      shot("22-end-of-book")
      // Wandering on from there is the same excursion.
      keyClick("g")
      tryVerify(function() { return service.posBlock === 0 }, 3000)
      keyClick("]")
      keyClick("]")
      keyClick("t")
      keyClick(Qt.Key_J)
      keyClick(Qt.Key_Return)
      wait(200)
      compare(service.trail.length, 1)
      keyClick(Qt.Key_Backspace)
      compare(service.posBlock, block)
      fuzzyCompare(service.posFraction, fraction, 0.001)
      verify(!service.canGoBack)

      // Reading on from a place that was jumped to makes it a place of its own.
      keyClick("]")
      wait(200)
      var landed = service.posBlock
      for (var j = 0; j < 5; j++) keyClick(Qt.Key_J)
      tryVerify(function() { return service.landed === null }, 3000)
      wait(300)
      var onward = service.posBlock
      keyClick("g")
      compare(service.trail.length, 2)
      keyClick(Qt.Key_Backspace)
      compare(service.posBlock, onward)
      keyClick(Qt.Key_Backspace)
      compare(service.posBlock, block)
      verify(landed >= block)

      // Jumping to where one already is leaves nothing to undo.
      keyClick("g")
      keyClick(Qt.Key_Backspace)
      tryVerify(function() { return service.posBlock === block }, 3000)
      keyClick("g")
      for (var k = 0; k < 3; k++) keyClick(Qt.Key_J)
      tryVerify(function() { return service.landed === null }, 3000)
      keyClick("g")
      wait(200)
      var before = service.trail.length
      keyClick("g")
      compare(service.trail.length, before)
      while (service.canGoBack) keyClick(Qt.Key_Backspace)
      compare(service.posBlock, block)
    }

    // The block at the top edge of the reader and how far through it the
    // edge is: what is on screen, whatever the service believes.
    function readerList(item) {
      if (!item) return null
      if (item.restoring !== undefined && typeof item.track === "function") return item
      var kids = item.children || []
      for (var i = 0; i < kids.length; i++) {
        var hit = readerList(kids[i])
        if (hit) return hit
      }
      return null
    }

    function onScreen(list) {
      var index = list.indexAt(list.width / 2, list.contentY)
      var item = index >= 0 ? list.itemAtIndex(index) : null
      if (!item) return { b: -1, f: 0 }
      var f = (list.contentY - item.y - item.gap) / Math.max(1, item.height - item.gap)
      return { b: index, f: Math.max(0, Math.min(1, f)) }
    }

    // Changing the text size relays every block out a frame or two later; the
    // place has to be on screen once that is over, or the next scroll saves
    // wherever the relayout happened to leave the reader.
    function test_4_text_size_keeps_the_place_on_screen() {
      var list = readerList(host.card)
      verify(list !== null)
      for (var i = 0; i < 30; i++) {
        keyClick(Qt.Key_J)
        wait(15)
      }
      wait(600)
      var block = service.posBlock
      var fraction = service.posFraction
      verify(block > 3)
      compare(onScreen(list).b, block)
      var keys = ["+", "+", "+", "+", "-", "-", "-", "-"]
      for (var k = 0; k < keys.length; k++) {
        keyClick(keys[k])
        wait(450)
        var seen = onScreen(list)
        compare(seen.b, block)
        fuzzyCompare(seen.f, fraction, 0.06)
        compare(service.posBlock, block)
        fuzzyCompare(service.posFraction, fraction, 0.0001)
      }
      // One line further is one line further, not a paragraph away.
      keyClick(Qt.Key_J)
      wait(400)
      verify(service.posBlock - block <= 1)
      keyClick("0")
      wait(300)
    }

    // There is no chapter after the last: stepping forward stays put,
    // without rewinding to the top of the paragraph or leaving a trail.
    function test_5_no_chapter_after_the_last() {
      keyClick("G")
      tryVerify(function() { return service.posBlock === service.blockCount - 1 }, 3000)
      for (var i = 0; i < 4; i++) keyClick(Qt.Key_K)
      tryVerify(function() { return service.landed === null }, 3000)
      wait(400)
      var block = service.posBlock
      var fraction = service.posFraction
      var trail = service.trail.length
      keyClick("]")
      wait(300)
      compare(service.posBlock, block)
      fuzzyCompare(service.posFraction, fraction, 0.0001)
      compare(service.trail.length, trail)
      while (service.canGoBack) keyClick(Qt.Key_Backspace)
    }

    function test_6_text_size() {
      compare(service.fontPx, 0)
      keyClick("+")
      keyClick("+")
      verify(service.fontPx > 0)
      var bigger = service.fontPx
      shot("21-blocks-larger")
      keyClick("-")
      compare(service.fontPx, bigger - 1)
      keyClick("0")
      compare(service.fontPx, 0)
    }
  }
}
