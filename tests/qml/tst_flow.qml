import QtQuick
import QtTest
import Quickshell
import ReaderHarness

// The whole plugin, hosted the way the shell hosts it, driven by keys
// against the stand-in backend: library -> open -> read -> contents ->
// close -> reopen at the same place.
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

  TestCase {
    name: "Flow"
    when: windowShown

    readonly property var service: host.service
    readonly property var widget: host.widget

    // Grabs just the popout card. grabToImage is used because TestCase's
    // grabImage crops in the wrong units at a fractional scale.
    function shot(name) {
      if (!root.shots) return
      wait(250)
      var done = false
      var target = host.card ? host.card : root
      target.grabToImage(function(result) {
        result.saveToFile(root.shots + "/" + name + ".png")
        done = true
      })
      tryVerify(function() { return done }, 5000)
    }

    function type(text) {
      for (var i = 0; i < text.length; i++) keyClick(text[i])
    }

    function initTestCase() {
      verify(root.plugin !== "", "pass plugin=<repository root>")
      tryVerify(function() { return host.service !== null }, 5000)
      host.service.backendCommand = ["python3", "-B", root.plugin + "/tests/qml/fake_reader.py"]
      tryVerify(function() { return host.widget !== null }, 5000)
      tryVerify(function() { return host.service.storeReady }, 5000)
    }

    function test_1_library_opens_first() {
      compare(service.view, "library")
      widget.open()
      tryVerify(function() { return widget.opened })
      tryVerify(function() { return service.books.length === 9 }, 8000)
      compare(service.libraryError, "")
      shot("01-library")
    }

    function test_2_cursor_and_search() {
      var content = host.card
      keyClick(Qt.Key_Right)
      keyClick(Qt.Key_Down)
      shot("02-library-cursor")
      type("/")
      type("odys")
      wait(100)
      shot("03-library-search")
      keyClick(Qt.Key_Return)
      tryVerify(function() { return service.hasBook }, 8000)
      compare(service.bookTitle, "The Odyssey")
      compare(service.view, "reader")
      compare(service.posBlock, 0)
      shot("04-reader-start")
    }

    function test_3_reading_moves_the_position() {
      for (var i = 0; i < 6; i++) keyClick(Qt.Key_J)
      keyClick(Qt.Key_Space)
      tryVerify(function() { return service.posBlock > 0 }, 3000)
      var before = service.posBlock
      keyClick(Qt.Key_Space, Qt.ShiftModifier)
      tryVerify(function() { return service.posBlock < before }, 3000)
      // ] steps to the next contents entry at any depth, [ back again.
      keyClick("]")
      tryVerify(function() { return service.chapterTitle === "A section inside" }, 3000)
      keyClick("]")
      tryVerify(function() { return service.chapterTitle === "Chapter 2" }, 3000)
      shot("05-reader-chapter-2")
      keyClick("[")
      tryVerify(function() { return service.chapterTitle === "A section inside" }, 3000)
      keyClick("]")
      tryVerify(function() { return service.chapterTitle === "Chapter 2" }, 3000)
    }

    function test_4_contents() {
      type("t")
      wait(200)
      shot("06-contents")
      // The list is long, so branches start folded except the current one.
      keyClick(Qt.Key_J)
      keyClick(Qt.Key_J)
      keyClick(Qt.Key_Return)
      tryVerify(function() { return service.chapterTitle.indexOf("Chapter 4") === 0 }, 3000)
      shot("07-reader-after-jump")

      // Filtering reaches into folded branches; Escape clears, then leaves.
      type("t")
      type("/")
      type("chapter 37")
      wait(100)
      shot("07b-contents-filtered")
      keyClick(Qt.Key_Return)
      tryVerify(function() { return service.chapterTitle === "Chapter 37" }, 3000)
      type("t")
      type("/")
      type("zzz")
      keyClick(Qt.Key_Escape)
      keyClick(Qt.Key_Escape)
      verify(widget.opened)
      type("t")
      wait(100)
      compare(service.chapterTitle, "Chapter 37")
      verify(widget.opened)
    }

    function test_5_position_survives_close_and_reload() {
      for (var i = 0; i < 9; i++) keyClick(Qt.Key_J)
      wait(400)
      var block = service.posBlock
      var fraction = service.posFraction
      verify(block > 0)
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !widget.opened })
      wait(400)

      // What a plugin reload does: widgets rebuilt, kept service reused.
      var previous = host.widget
      var kept = host.service
      host.reloadPlugins()
      tryVerify(function() { return host.widget !== null && host.widget !== previous }, 5000)
      compare(host.service, kept)
      host.widget.open()
      tryVerify(function() { return host.widget.opened })
      wait(500)
      compare(host.service.posBlock, block)
      fuzzyCompare(host.service.posFraction, fraction, 0.2)
      shot("08-reader-restored")
    }

    function test_6_back_to_library_shows_progress() {
      type("b")
      tryVerify(function() { return service.view === "library" })
      wait(300)
      compare(service.books[0].title, "The Odyssey")
      verify(service.progressOf(service.books[0].key) > 0)
      shot("09-library-with-progress")
    }

    function test_7_unreadable_and_external_books() {
      type("/")
      type("locked")
      keyClick(Qt.Key_Return)
      tryVerify(function() { return service.openError !== "" }, 8000)
      verify(service.openError.indexOf("DRM") !== -1)
      shot("10-reader-error")
      type("b")
      tryVerify(function() { return service.view === "library" })
      type("/")
      type("manual")
      keyClick(Qt.Key_Return)
      tryVerify(function() { return Quickshell.execLog.length === 1 }, 3000)
      compare(Quickshell.execLog[0][0], "xdg-open")
      compare(service.view, "library")
    }
  }
}
