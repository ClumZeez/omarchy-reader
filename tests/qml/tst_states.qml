import QtQuick
import QtTest
import Quickshell
import Quickshell.Io
import ReaderHarness

// The states around the happy path: no books, no helper, and two monitors
// sharing one service.
Item {
  id: root
  width: 1280
  height: 900

  readonly property string plugin: Quickshell.opt("plugin", "")
  readonly property string shots: Quickshell.opt("shots", "")
  readonly property string fake: plugin + "/tests/qml/fake_reader.py"

  BarHost {
    id: host
    anchors.fill: parent
    pluginDir: root.plugin
    widgets: 2
  }

  // How many times the stand-in backend has been run.
  FileView {
    id: log
    path: Quickshell.env("XDG_CACHE_HOME") + "/fake-reader.log"
    printErrors: false
  }

  TestCase {
    name: "States"
    when: windowShown

    readonly property var service: host.service

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

    function runs() {
      log.reload()
      wait(120)
      return log.text().split("\n").filter(function(line) { return line !== "" })
    }

    function initTestCase() {
      verify(root.plugin !== "", "pass plugin=<repository root>")
      tryVerify(function() { return host.service !== null }, 5000)
      tryVerify(function() { return host.widgetAt(1) !== null && host.service.storeReady }, 5000)
    }

    function test_1_nothing_runs_until_a_panel_opens() {
      service.backendCommand = ["python3", "-B", root.fake, "--empty"]
      wait(300)
      compare(runs().length, 0)
      compare(service.blockCount, 0)
    }

    function test_2_empty_library_explains_itself() {
      host.widgetAt(0).open()
      tryVerify(function() { return service.scanned }, 8000)
      compare(service.books.length, 0)
      compare(service.libraryError, "")
      shot("30-library-empty")
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !host.widgetAt(0).opened })
    }

    function test_3_a_helper_that_cannot_run_is_reported() {
      service.backendCommand = ["python3", "-B", root.fake, "--broken"]
      service.scan()
      tryVerify(function() { return !service.scanning }, 8000)
      verify(service.libraryError !== "")
      host.widgetAt(0).open()
      shot("31-library-error")
      keyClick(Qt.Key_Escape)
    }

    function test_4_two_monitors_share_one_service() {
      service.backendCommand = ["python3", "-B", root.fake]
      service.lastScan = 0
      var first = host.widgetAt(0)
      var second = host.widgetAt(1)
      verify(first !== second)
      compare(first.service, second.service)

      first.open()
      tryVerify(function() { return service.books.length === 9 }, 8000)
      var before = runs().length
      keyClick(Qt.Key_Return)
      tryVerify(function() { return service.hasBook }, 8000)
      compare(runs().length, before + 1)
      for (var i = 0; i < 12; i++) keyClick(Qt.Key_J)
      wait(400)
      var block = service.posBlock
      verify(block > 0)

      // Opening on the other monitor closes this popout and shows the same
      // book at the same place, without asking the backend again.
      second.open()
      tryVerify(function() { return second.opened && !first.opened }, 3000)
      wait(400)
      compare(service.posBlock, block)
      compare(runs().length, before + 1)
      compare(host.popouts.length, 2)
      shot("32-second-monitor")
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !second.opened })
    }

    // omarchy-shell reader open <path> / library / status
    function test_5_ipc() {
      var ipc = Quickshell.ipc("reader")
      verify(ipc !== null)
      var first = host.widgetAt(0)
      verify(!first.opened)
      var walden = service.books.filter(function(b) { return b.title.indexOf("Walden") === 0 })[0]

      compare(ipc.open(walden.path), "ok")
      tryVerify(function() { return first.opened }, 3000)
      tryVerify(function() { return service.bookTitle.indexOf("Walden") === 0 }, 8000)
      compare(JSON.parse(ipc.status()).title, service.bookTitle)

      // Asked again while the popout is already open.
      var essays = service.books.filter(function(b) { return b.title === "Essays" })[0]
      compare(ipc.open(essays.path), "ok")
      tryVerify(function() { return service.bookTitle === "Essays" }, 8000)
      verify(first.opened)

      compare(ipc.library(), "ok")
      compare(service.view, "library")
      compare(ipc.open(""), "no path")
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !first.opened })
    }

    // Two books asked for in quick succession: only the last one is shown.
    function test_6_last_request_wins() {
      var odyssey = service.books.filter(function(b) { return b.title === "The Odyssey" })[0]
      var meditations = service.books.filter(function(b) { return b.title === "Meditations" })[0]
      service.openBook(odyssey.path, odyssey.title)
      service.openBook(meditations.path, meditations.title)
      tryVerify(function() { return service.hasBook && !service.opening }, 8000)
      wait(400)
      compare(service.bookTitle, "Meditations")
      compare(service.openError, "")
    }

    // The shell lives for days: a book nobody has open is not kept in it.
    function test_7_a_book_nobody_is_reading_is_let_go() {
      var second = host.widgetAt(1)
      second.open()
      tryVerify(function() { return second.opened && service.hasBook }, 8000)
      for (var i = 0; i < 5; i++) keyClick(Qt.Key_J)
      tryVerify(function() { return service.posBlock > 0 }, 3000)
      wait(400)
      var block = service.posBlock
      var title = service.bookTitle

      // However long nothing happens, not while a popout is open.
      service.releaseIfIdle()
      verify(service.hasBook)

      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !second.opened })
      service.releaseIfIdle()
      verify(!service.hasBook)
      compare(service.blocks.length, 0)
      // The bar still knows which book and how far.
      compare(service.currentTitle, title)
      verify(service.currentProgress > 0)

      var before = runs().length
      host.widgetAt(0).open()
      tryVerify(function() { return service.hasBook }, 8000)
      compare(service.bookTitle, title)
      wait(300)
      compare(service.posBlock, block)
      compare(runs().length, before + 1)

      // Let go while the library was showing: "back to the book" still works.
      keyClick("b")
      tryVerify(function() { return service.view === "library" }, 3000)
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !host.widgetAt(0).opened })
      service.releaseIfIdle()
      verify(!service.hasBook)
      host.widgetAt(0).open()
      tryVerify(function() { return host.widgetAt(0).opened && service.view === "library" }, 3000)
      wait(200)
      keyClick("b")
      tryVerify(function() { return service.hasBook && service.view === "reader" }, 8000)
      compare(service.bookTitle, title)
      wait(300)
      compare(service.posBlock, block)
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !host.widgetAt(0).opened })
    }

    // The library chosen while a book is still on its way stays chosen.
    function test_8_a_book_that_arrives_does_not_take_the_view_back() {
      var odyssey = service.books.filter(function(b) { return b.title === "The Odyssey" })[0]
      service.openBook(odyssey.path, odyssey.title)
      service.showLibrary()
      tryVerify(function() { return service.hasBook && !service.opening }, 8000)
      compare(service.bookTitle, "The Odyssey")
      compare(service.view, "library")

      // `omarchy-shell reader library` with the book let go: the library,
      // and no book quietly loaded behind it.
      service.showReader()
      compare(service.view, "reader")
      service.releaseIfIdle()
      verify(!service.hasBook)
      compare(Quickshell.ipc("reader").library(), "ok")
      tryVerify(function() { return host.widgetAt(0).opened || host.widgetAt(1).opened }, 3000)
      wait(700)
      compare(service.view, "library")
      verify(!service.hasBook && !service.opening)
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !host.widgetAt(0).opened && !host.widgetAt(1).opened }, 3000)
    }

    // A rebuilt widget is handed `bar` before `settings`. Telling the
    // service then would announce an unset folder and scan the wrong place.
    function test_9_a_widget_rebuild_does_not_rescan() {
      host.settings = ({ folder: "~/Books" })
      wait(300)
      tryVerify(function() { return !service.scanning }, 8000)
      var scans = function() { return runs().filter(function(run) { return run === "scan" }).length }
      var before = scans()
      var first = host.widgetAt(0)
      first.open()
      tryVerify(function() { return first.opened }, 3000)
      tryVerify(function() { return host.api.centerHoverRevealSuppressed }, 3000)
      host.reloadPlugins()
      tryVerify(function() { return host.widgetAt(1) !== null && host.widgetAt(0) !== first }, 5000)
      wait(700)
      compare(scans(), before)
      // The popout went with its widget; the bar's shared flag must not stay set.
      verify(!host.api.centerHoverRevealSuppressed)
    }

    // A scan that runs out of time is not a missing python, and what the
    // library already held is still shown.
    function test_9b_a_scan_out_of_time_says_so() {
      var known = service.books.length
      verify(known > 0)
      service.backendCommand = ["python3", "-B", root.fake, "--slow"]
      service.scanTimeoutMs = 400
      service.scan()
      tryVerify(function() { return !service.scanning }, 8000)
      verify(service.libraryError.indexOf("taking a long time") !== -1, service.libraryError)
      compare(service.books.length, known)
      service.scanTimeoutMs = 600000
      service.backendCommand = ["python3", "-B", root.fake]
      service.scan()
      tryVerify(function() { return !service.scanning && service.libraryError === "" }, 8000)
    }
  }
}
