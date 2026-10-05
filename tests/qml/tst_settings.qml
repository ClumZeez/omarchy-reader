import QtQuick
import QtTest
import Quickshell
import ReaderHarness

// The settings, the key sheet and the ways out to the desktop, by keyboard
// and by mouse. Nothing here runs a program: what would be opened or copied
// is only recorded.
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
    name: "Settings"
    when: windowShown

    readonly property var service: host.service
    readonly property var widget: host.widget
    property var content: null
    property var settings: null

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

    function rowOf(id, skip) {
      var left = skip || 0
      for (var i = 0; i < settings.rows.length; i++) {
        if (settings.rows[i].id !== id) continue
        if (left === 0) return i
        left--
      }
      return -1
    }

    function goTo(id, skip) {
      var target = rowOf(id, skip)
      verify(target >= 0, "a row called " + id)
      settings.moveToEdge(false)
      for (var guard = 0; settings.cursor !== target && guard < 40; guard++) keyClick(Qt.Key_Down)
      compare(settings.cursor, target)
    }

    function initTestCase() {
      verify(root.plugin !== "", "pass plugin=<repository root>")
      tryVerify(function() { return host.service !== null }, 5000)
      host.service.backendCommand = ["python3", "-B", root.plugin + "/tests/qml/fake_reader.py"]
      tryVerify(function() { return host.widget !== null && host.service.storeReady }, 5000)
      widget.open()
      tryVerify(function() { return service.books.length === 9 }, 8000)
      content = root.find(host.popouts[0], "PanelContent")
      verify(content !== null)
    }

    function test_1_s_opens_the_settings_and_the_cursor_skips_titles() {
      compare(content.view, "library")
      keyClick("s")
      compare(content.view, "settings")
      settings = root.find(host.popouts[0], "SettingsView")
      verify(settings !== null)
      compare(settings.rows[0].kind, "head")
      compare(settings.rows[settings.cursor].id, "paged")
      keyClick(Qt.Key_Up)
      compare(settings.rows[settings.cursor].id, "paged")
      keyClick(Qt.Key_Down)
      compare(settings.rows[settings.cursor].id, "look")
      keyClick(Qt.Key_J)
      compare(settings.rows[settings.cursor].id, "size")
      keyClick(Qt.Key_Down)
      verify(settings.rows[settings.cursor].kind !== "head")
      // Below the last row the cursor rests on the link in the corner.
      keyClick(Qt.Key_End)
      verify(settings.onKeys)
      keyClick(Qt.Key_Up)
      compare(settings.rows[settings.cursor].label, "Project Gutenberg")
      keyClick(Qt.Key_Down)
      verify(settings.onKeys)
      keyClick(Qt.Key_Down)
      verify(settings.onKeys)
      // No row carries a key beside it.
      for (var r = 0; r < settings.rows.length; r++) verify(settings.rows[r].hint === undefined)
      compare(settings.rows.filter(function(row) { return row.id === "site" }).length, 3)
      compare(settings.rows.filter(function(row) { return row.kind === "head" }).map(function(row) { return row.label }),
              ["Reading", "Library", "Free books"])
      keyClick(Qt.Key_Home)
      compare(settings.rows[settings.cursor].id, "paged")
      shot("30-settings")
    }

    function test_2_rows_change_what_they_say() {
      compare(service.paged, false)
      keyClick(Qt.Key_Return)
      compare(service.paged, true)
      keyClick(Qt.Key_Space)
      compare(service.paged, false)
      keyClick(Qt.Key_Right)
      compare(service.paged, true)
      keyClick(Qt.Key_Left)
      compare(service.paged, false)

      goTo("look")
      compare(service.look, "auto")
      keyClick(Qt.Key_Right)
      compare(service.look, "day")
      shot("31-settings-day")
      keyClick(Qt.Key_Right)
      compare(service.look, "night")
      shot("32-settings-night")
      keyClick(Qt.Key_Right)
      compare(service.look, "night")
      keyClick(Qt.Key_Return)
      compare(service.look, "auto")

      goTo("size")
      var size = content.fontPx
      keyClick(Qt.Key_Right)
      compare(content.fontPx, size + 1)
      keyClick(Qt.Key_Left)
      keyClick(Qt.Key_Left)
      compare(content.fontPx, size - 1)
      keyClick("0")
      compare(service.fontPx, 0)
    }

    function test_3_what_was_set_is_what_is_saved() {
      service.setSetting("paged", true)
      service.setSetting("look", "night")
      service.setSetting("look", "sepia")
      compare(service.look, "auto")
      service.setSetting("look", "night")
      var saved = JSON.parse(JSON.stringify(service.store))
      compare([saved.paged, saved.look], [true, "night"])
      compare(Object.keys(saved).sort(), ["books", "current", "fontPx", "look", "paged", "version", "view"])
      // A place saved afterwards keeps them.
      service.commit()
      compare([service.store.paged, service.store.look], [true, "night"])
      service.setSetting("paged", false)
      service.setSetting("look", "auto")
    }

    function test_4_the_letters_work_from_anywhere() {
      keyClick("d")
      compare(service.look, "day")
      keyClick("D")
      compare(service.look, "night")
      keyClick("d")
      compare(service.look, "auto")
      keyClick("p")
      compare(service.paged, true)
      keyClick("P")
      compare(service.paged, false)
      keyClick("s")
      compare(content.view, "library")
      keyClick("p")
      compare(service.paged, true)
      keyClick("p")
      compare(service.paged, false)
    }

    function test_5_escape_leaves_one_thing_at_a_time() {
      keyClick("s")
      compare(content.view, "settings")
      // The link in the corner, by keyboard and by mouse.
      keyClick(Qt.Key_End)
      keyClick(Qt.Key_Return)
      verify(content.keysOpen)
      keyClick(Qt.Key_Escape)
      verify(!content.keysOpen)
      mouseClick(settings, settings.width - 30, settings.height - 12)
      verify(content.keysOpen)
      keyClick(Qt.Key_Escape)
      keyClick("?")
      verify(content.keysOpen)
      shot("33-keys")
      keyClick(Qt.Key_Escape)
      verify(!content.keysOpen)
      compare(content.view, "settings")
      verify(widget.opened)
      keyClick(Qt.Key_Escape)
      compare(content.view, "library")
      verify(widget.opened)
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !widget.opened }, 3000)
      widget.open()
      tryVerify(function() { return widget.opened }, 3000)
      // Any key puts the sheet away without doing what it otherwise does.
      keyClick("?")
      verify(content.keysOpen)
      keyClick("s")
      verify(!content.keysOpen)
      compare(content.view, "library")
      keyClick(Qt.Key_F1)
      verify(content.keysOpen)
      keyClick(Qt.Key_Space)
      verify(!content.keysOpen)
      verify(!service.hasBook)
    }

    function test_6_the_folder_and_the_free_libraries_open_outside() {
      var before = Quickshell.execLog.length
      keyClick("s")
      goTo("folder")
      verify(settings.rows[settings.cursor].note !== "")
      keyClick(Qt.Key_Return)
      compare(Quickshell.execLog.length, before + 1)
      var asked = Quickshell.execLog[before]
      compare(asked[asked.length - 1], service.libraryFolder)
      verify(service.libraryFolder !== "")
      // The file manager is what the reader wants to see next.
      tryVerify(function() { return !widget.opened }, 3000)

      widget.open()
      tryVerify(function() { return widget.opened }, 3000)
      compare(content.view, "library")
      keyClick("s")
      goTo("site", 0)
      compare(settings.rows[settings.cursor].label, "Global Grey")
      keyClick(Qt.Key_Return)
      compare(Quickshell.execLog.length, before + 2)
      compare(Quickshell.execLog[before + 1], ["xdg-open", "https://www.globalgreyebooks.com/"])
      tryVerify(function() { return !widget.opened }, 3000)
      verify(!service.openSite("file:///etc/passwd"))
      verify(!service.openSite("javascript:alert(1)"))
      compare(Quickshell.execLog.length, before + 2)

      // o from the library does the same as the row.
      widget.open()
      tryVerify(function() { return widget.opened }, 3000)
      keyClick("o")
      compare(Quickshell.execLog.length, before + 3)
      tryVerify(function() { return !widget.opened }, 3000)
    }

    function test_7_the_gear_and_the_rows_by_mouse() {
      widget.open()
      tryVerify(function() { return widget.opened }, 3000)
      compare(content.view, "library")
      // The gear is at the right end of the header.
      mouseClick(content, content.width - 14, 16)
      tryCompare(content, "view", "settings", 2000)
      wait(100)
      var first = root.find(settings, "CursorSurface", 0)
      verify(first !== null)
      compare(service.paged, false)
      mouseMove(first, first.width / 2, first.height / 2)
      mouseClick(first, 40, first.height / 2)
      compare(service.paged, true)
      mouseClick(first, 40, first.height / 2)
      compare(service.paged, false)
      // The arrow at the left end goes back.
      mouseClick(content, 14, 16)
      tryCompare(content, "view", "library", 2000)
    }

    // After an update the widgets are new and the service, kept loaded, is
    // still the one from before, until the shell is restarted. Nothing is
    // built on it; the popout says what to do.
    function test_9_a_service_from_before_an_update_is_left_alone() {
      var real = host.service
      var old = Qt.createQmlObject(
        'import QtQuick; QtObject { property string view: "library"; property var books: []; property int asked: 0;'
        + ' function configure(a, b) { asked++ } function activate() { asked++ } function showLibrary() { asked++ }'
        + ' function commit() { asked++ } }', root)
      widget.close()
      tryVerify(function() { return !widget.opened }, 3000)
      host.service = old
      tryCompare(widget, "serviceFits", false, 2000)
      widget.open()
      tryVerify(function() { return widget.opened }, 3000)
      wait(200)
      verify(root.find(host.popouts[0], "PanelContent") === null)
      var said = root.find(host.popouts[0], "QQuickText")
      verify(said !== null && said.text.indexOf("omarchy restart shell") > 0, said ? said.text : "nothing shown")
      var keys = ["s", "p", "d", "b", "?", "]"]
      for (var i = 0; i < keys.length; i++) keyClick(keys[i])
      keyClick(Qt.Key_Right)
      keyClick(Qt.Key_Space)
      verify(widget.opened)
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !widget.opened }, 3000)
      mouseClick(widget, widget.width / 2, widget.height / 2, Qt.RightButton)
      tryVerify(function() { return widget.opened }, 3000)
      keyClick(Qt.Key_Escape)
      tryVerify(function() { return !widget.opened }, 3000)
      compare(old.asked, 0)

      host.service = real
      tryCompare(widget, "serviceFits", true, 2000)
      widget.open()
      tryVerify(function() { return widget.opened }, 3000)
      tryVerify(function() { return root.find(host.popouts[0], "PanelContent") !== null }, 3000)
      old.destroy()
    }

    function test_8_a_book_opening_leaves_the_settings() {
      keyClick("s")
      compare(content.view, "settings")
      service.openBook(service.books[0].path, service.books[0].title)
      tryVerify(function() { return service.hasBook }, 8000)
      compare(content.view, "reader")
      keyClick("s")
      compare(content.view, "settings")
      keyClick(Qt.Key_Backspace)
      compare(content.view, "reader")
      keyClick("b")
      compare(content.view, "library")
      keyClick("b")
      compare(content.view, "reader")
    }
  }
}
