import QtQuick
import QtTest
import Quickshell
import ReaderHarness

// The same journey by mouse: click a cover, scroll, open the contents from
// the title, click a chapter, click the library button, click away.
Item {
  id: root
  width: 1280
  height: 900

  readonly property string plugin: Quickshell.opt("plugin", "")

  BarHost {
    id: host
    anchors.fill: parent
    pluginDir: root.plugin
  }

  // First descendant whose QML type name starts with `name`, depth first.
  function find(item, name, skip) {
    var left = { n: skip || 0 }
    return findFrom(item, name, left)
  }

  function findFrom(item, name, left) {
    if (!item) return null
    if (String(item).indexOf(name) === 0 && item.visible) {
      if (left.n === 0) return item
      left.n--
    }
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
      var hit = findFrom(kids[i], name, left)
      if (hit) return hit
    }
    // A view's delegates live under its contentItem.
    if (item.contentItem && item.contentItem !== item) {
      kids = item.contentItem.children || []
      for (var j = 0; j < kids.length; j++) {
        var inner = findFrom(kids[j], name, left)
        if (inner) return inner
      }
    }
    return null
  }

  TestCase {
    name: "Pointer"
    when: windowShown

    readonly property var service: host.service
    property var panel: null

    function initTestCase() {
      verify(root.plugin !== "", "pass plugin=<repository root>")
      tryVerify(function() { return host.service !== null }, 5000)
      host.service.backendCommand = ["python3", "-B", root.plugin + "/tests/qml/fake_reader.py"]
      tryVerify(function() { return host.widget !== null && host.service.storeReady }, 5000)
    }

    function test_1_the_bar_icon_opens_the_library() {
      mouseClick(host.widget)
      tryVerify(function() { return host.widget.opened }, 3000)
      tryVerify(function() { return service.books.length === 9 }, 8000)
      panel = host.popouts[0]
      verify(panel !== null)
    }

    function test_2_a_cover_opens_its_book() {
      wait(200)
      var tile = root.find(panel, "CoverTile", 5)
      verify(tile !== null)
      var title = tile.book.title
      mouseMove(tile, 20, 20)
      mouseClick(tile, 20, 20)
      tryVerify(function() { return service.hasBook }, 8000)
      compare(service.bookTitle, title)
      compare(service.view, "reader")
    }

    function test_3_the_wheel_scrolls_and_moves_the_place() {
      wait(200)
      var reader = root.find(panel, "ReaderView")
      verify(reader !== null)
      compare(service.posBlock, 0)
      for (var i = 0; i < 8; i++) mouseWheel(reader, reader.width / 2, reader.height / 2, 0, -120)
      tryVerify(function() { return service.posBlock > 0 }, 3000)
    }

    function test_4_the_title_opens_the_contents_and_a_row_jumps() {
      var content = root.find(panel, "PanelContent")
      verify(content !== null)
      compare(content.view, "reader")
      // The title sits in the middle of the header, a little below the top.
      mouseClick(content, content.width / 2, 16)
      tryCompare(content, "view", "contents", 2000)
      wait(200)
      var row = root.find(panel, "CursorSurface", 3)
      verify(row !== null)
      var target = row.modelData.b
      mouseClick(row, row.width / 2, row.height / 2)
      tryCompare(content, "view", "reader", 2000)
      compare(service.posBlock, target)
    }

    function test_5_the_library_button_and_clicking_away() {
      var content = root.find(panel, "PanelContent")
      // From the contents to the library and into another book: its text,
      // not its contents.
      mouseClick(content, content.width / 2, 16)
      tryCompare(content, "view", "contents", 2000)
      mouseClick(content, 14, 16)
      tryCompare(service, "view", "library", 2000)
      wait(200)
      var other = root.find(panel, "CoverTile", 2)
      verify(other !== null && other.book.title !== service.bookTitle)
      mouseClick(other, 20, 20)
      tryVerify(function() { return service.hasBook && service.bookTitle === other.book.title }, 8000)
      compare(content.view, "reader")
      wait(200)
      var reader = root.find(panel, "ReaderView")
      for (var i = 0; i < 8; i++) mouseWheel(reader, reader.width / 2, reader.height / 2, 0, -120)
      tryVerify(function() { return service.posBlock > 0 }, 3000)
      // The library button is at the left end of the header.
      mouseClick(content, 14, 16)
      tryCompare(service, "view", "library", 2000)
      verify(host.widget.opened)
      // Outside the card: the popout closes, and the place was saved.
      mouseClick(root, 20, root.height - 20)
      tryVerify(function() { return !host.widget.opened }, 3000)
      verify(service.progressOf(service.bookKey) > 0)
    }

    function test_6_right_click_goes_to_the_library() {
      service.showReader()
      compare(service.view, "reader")
      mouseClick(host.widget, host.widget.width / 2, host.widget.height / 2, Qt.RightButton)
      tryVerify(function() { return host.widget.opened }, 3000)
      compare(service.view, "library")
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !host.widget.opened }, 3000)
    }
  }
}
