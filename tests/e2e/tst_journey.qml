import QtQuick
import QtTest
import Quickshell
import ReaderHarness
import "../../Reader.js" as Reader

// End to end: the real backend, real files (make_books.py) and the real
// interface, across what are separate shell sessions. tests/run.sh runs it
// once per phase over the same throwaway HOME:
//   phase=read     find the books, read one, leave
//   phase=resume   the book has since been moved and renamed: it is found
//                  again by its content and opened at the same place
//   phase=edition  the file has been replaced by another edition of the same
//                  title: the place follows the words
//   phase=gone     the book has been deleted: the library, without complaint
//   phase=damaged  the state file is unreadable: it is set aside, not lost
Item {
  id: root
  width: 1280
  height: 900

  readonly property string plugin: Quickshell.opt("plugin", "")
  readonly property string folder: Quickshell.opt("folder", "")
  readonly property string phase: Quickshell.opt("phase", "read")
  readonly property string shots: Quickshell.opt("shots", "")
  readonly property string statePath: Quickshell.env("XDG_STATE_HOME") + "/omarchy/settings/reader.json"

  BarHost {
    id: host
    anchors.fill: parent
    pluginDir: root.plugin
    settings: ({ folder: root.folder })
  }

  // What the service has written, read back from disk.
  function savedState() {
    var xhr = new XMLHttpRequest()
    xhr.open("GET", "file://" + statePath, false)
    xhr.send()
    return JSON.parse(xhr.responseText)
  }

  TestCase {
    name: "Journey"
    when: windowShown

    readonly property var service: host.service
    readonly property var widget: host.widget

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

    function type(text) {
      for (var i = 0; i < text.length; i++) keyClick(text[i])
    }

    function test_journey() {
      if (root.phase === "read") read()
      else if (root.phase === "resume") resume()
      else if (root.phase === "edition") edition()
      else if (root.phase === "gone") gone()
      else if (root.phase === "damaged") damaged()
      else fail("unknown phase " + root.phase)
    }

    function initTestCase() {
      verify(root.plugin !== "" && root.folder !== "", "pass plugin=<repository root> folder=<books>")
      tryVerify(function() { return host.service !== null && host.widget !== null }, 5000)
      tryVerify(function() { return host.service.storeReady }, 5000)
    }

    function read() {
      widget.open()
      tryVerify(function() { return service.scanned && service.books.length === 4 }, 20000)
      compare(service.libraryError, "")
      compare(service.view, "library")
      var titles = service.books.map(function(b) { return b.title }).join(" | ")
      compare(titles, "Locked Away | The Long Walk | manual | Short Notes")
      var walk = service.books[1]
      compare(walk.author, "Ann Walker")
      verify(walk.cover !== "" && walk.coverW === 120 && walk.coverH === 180)
      verify(service.books[2].external)
      shot("e1-library")

      type("/")
      type("long")
      keyClick(Qt.Key_Return)
      tryVerify(function() { return service.hasBook }, 20000)
      compare(service.bookTitle, "The Long Walk")
      compare(service.bookAuthor, "Ann Walker")
      compare(service.toc.length, 15)
      compare(service.posBlock, 0)
      // Emphasis that exists only in the stylesheet, and a note reference.
      var styled = service.blocks[2]
      compare(styled.f, 1)
      verify(styled.t.indexOf("<i>emphasis</i>") !== -1, styled.t)
      verify(styled.t.indexOf("<b>bold</b>") !== -1, styled.t)
      verify(/<a href="b:\d+">¹<\/a>/.test(styled.t), styled.t)

      type("t")
      type("/")
      type("chapter 7")
      keyClick(Qt.Key_Return)
      tryVerify(function() { return service.chapterTitle === "Chapter 7" }, 3000)
      for (var i = 0; i < 6; i++) keyClick(Qt.Key_J)
      tryVerify(function() { return service.posBlock > service.toc[8].b }, 3000)
      wait(1200)
      shot("e2-reading")
      var saved = root.savedState()
      compare(saved.current, walk.key)
      compare(saved.books[walk.key].b, service.posBlock)
      compare(saved.books[walk.key].path, walk.path)

      // The note link, and back.
      var here = service.posBlock
      service.followLink(/href="(b:\d+)"/.exec(service.blocks[service.toc[8].b + 2].t)[1])
      tryVerify(function() { return service.chapterTitle === "Notes" }, 3000)
      keyClick(Qt.Key_Backspace)
      tryVerify(function() { return service.posBlock === here }, 3000)

      // A protected book says so; a PDF goes to the system's viewer.
      type("b")
      tryVerify(function() { return service.view === "library" }, 3000)
      type("/")
      type("locked")
      keyClick(Qt.Key_Return)
      tryVerify(function() { return service.openError !== "" }, 20000)
      verify(service.openError.indexOf("DRM") !== -1, service.openError)
      type("b")
      type("/")
      type("manual")
      keyClick(Qt.Key_Return)
      tryVerify(function() { return Quickshell.execLog.length === 1 }, 3000)
      compare(Quickshell.execLog[0][0], "xdg-open")
      tryVerify(function() { return !widget.opened }, 3000)

      // Back to the book, then leave: this is the place to come back to.
      widget.open()
      tryVerify(function() { return widget.opened && service.view === "library" }, 3000)
      wait(200)
      type("/")
      type("long")
      keyClick(Qt.Key_Return)
      tryVerify(function() { return service.hasBook && service.bookTitle === "The Long Walk" }, 20000)
      wait(300)
      compare(service.posBlock, here)
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !widget.opened }, 3000)
      wait(600)
      saved = root.savedState()
      compare(saved.current, walk.key)
      compare(saved.view, "reader")
      compare(saved.books[walk.key].b, here)
    }

    function resume() {
      var saved = root.savedState()
      var place = saved.books[saved.current]
      verify(place.b > 0)
      compare(service.view, "reader")
      compare(service.blockCount, 0)

      widget.open()
      tryVerify(function() { return service.hasBook }, 30000)
      compare(service.bookTitle, "The Long Walk")
      compare(service.bookKey, saved.current)
      verify(service.bookPath !== place.path, "the book was moved between the phases")
      verify(service.bookPath.indexOf("/elsewhere/") !== -1, service.bookPath)
      compare(service.view, "reader")
      compare(service.openError, "")
      wait(400)
      compare(service.posBlock, place.b)
      fuzzyCompare(service.posFraction, place.f, 0.05)
      compare(service.chapterTitle, "Chapter 7")
      shot("e3-resumed")
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !widget.opened }, 3000)
      wait(600)
      // The new path is what is remembered from now on.
      compare(root.savedState().books[saved.current].path, service.bookPath)
    }

    function edition() {
      var saved = root.savedState()
      var oldKey = saved.current
      var place = saved.books[oldKey]

      widget.open()
      tryVerify(function() { return service.hasBook }, 30000)
      verify(service.bookKey !== oldKey, "the file was replaced between the phases")
      compare(service.bookPath, place.path)
      compare(service.toc[0].t, "Preface")
      wait(400)
      // Every block has moved down by the preface; the place is where the
      // same words are now.
      verify(service.posBlock > place.b)
      compare(Reader.snippet(service.blocks[service.posBlock]), place.x)
      compare(service.chapterTitle, "Chapter 7")
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !widget.opened }, 3000)
      wait(600)
      var after = root.savedState()
      compare(after.current, service.bookKey)
      compare(after.books[service.bookKey].b, service.posBlock)
      verify(!(oldKey in after.books), "the superseded place is not kept as a second book")
    }

    function gone() {
      var saved = root.savedState()
      widget.open()
      tryVerify(function() { return service.scanned && !service.opening && service.view === "library" }, 30000)
      compare(service.openError, "")
      compare(service.libraryError, "")
      compare(service.books.length, 3)
      verify(!service.hasBook)
      // The place itself is kept: the book may come back.
      verify(root.savedState().books[saved.current].b > 0)
      shot("e4-gone")
    }

    function damaged() {
      var raw = ""
      tryVerify(function() {
        var xhr = new XMLHttpRequest()
        xhr.open("GET", "file://" + root.statePath + ".damaged", false)
        xhr.send()
        raw = xhr.responseText
        return raw !== ""
      }, 5000)
      compare(raw, "{ \"books\": { \"cut off in the mid")
      compare(service.view, "library")
      widget.open()
      tryVerify(function() { return service.scanned && service.books.length === 3 }, 30000)
      compare(service.libraryError, "")
    }
  }
}
