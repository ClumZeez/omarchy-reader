import QtQuick
import qs.Commons
import qs.Ui
import "Reader.js" as Reader

// What the popout shows: a header, a search field where one applies, and
// one of four views. Built once per bar widget on first open; all state
// that matters lives in the service.
//
// Keys, the same letters throughout (see Reader.keySheet for all of them):
//   b library / book    s settings    p pages / scrolling    d day / night
//   ? every key         Esc one step back, then close
Item {
  id: root

  property var host: null
  // How far the card's padding lets a painted page reach past this item.
  property real bleed: 0
  readonly property var service: host ? host.service : null

  // ---- colours: the shell's own, or paper by day and by night
  readonly property string look: service ? service.look : "auto"
  readonly property bool papered: look === "day" || look === "night"
  readonly property color paper: look === "day" ? "#f4efe4" : (look === "night" ? "#0d0d0e" : "transparent")
  readonly property color fg: look === "day" ? "#2a2520" : (look === "night" ? "#b9b2a5" : (host ? host.fg : Color.foreground))
  readonly property color dim: look === "day" ? "#857c70" : (look === "night" ? "#6e6960" : Qt.darker(fg, 1.5))
  readonly property color accent: look === "day" ? "#a2501a" : (look === "night" ? "#c7a56f" : Color.accent)
  readonly property string fontFamily: host ? host.fontFamily : Style.font.family
  readonly property int fontPx: service && service.fontPx > 0 ? service.fontPx : Style.font.subtitle

  // The contents and the settings are detours, so they are local to this
  // panel; library versus reader is the service's call.
  property bool contentsOpen: false
  property bool settingsOpen: false
  property bool keysOpen: false
  // A word in passing ("Copied", "Pages"), shown for a moment where the
  // header's second line is.
  property string noticed: ""
  readonly property string view: settingsOpen ? "settings"
    : (!service || service.view === "library" ? "library" : (contentsOpen ? "contents" : "reader"))
  readonly property bool editing: search.activeFocus

  // ---- called by the bar widget

  function shown() {
    contentsOpen = false
    settingsOpen = false
    keysOpen = false
    search.text = ""
    library.reset()
    reader.reanchor()
  }

  function hidden() {
    keysOpen = false
    reader.clearSelection()
  }

  // Escape: leaves the innermost thing that is open. False when there was
  // nothing to leave, and the popout itself should close.
  function back() {
    if (keysOpen) keysOpen = false
    else if (view === "reader" && reader.hasSelection) reader.clearSelection()
    else if (settingsOpen) closeSettings()
    else if (contentsOpen) closeContents()
    else return false
    return true
  }

  function move(dx, dy) {
    if (keysOpen) {
      keysOpen = false
      return
    }
    if (view === "reader") {
      // Left and right are pages whichever way the book is laid out; up and
      // down are lines when it scrolls and pages when it does not.
      if (dx !== 0) reader.page(dx)
      if (dy !== 0) {
        if (reader.paged) reader.page(dy)
        else reader.scrollLines(dy * 3)
      }
    } else if (view === "library") {
      library.moveCursor(dx, dy)
    } else if (view === "contents") {
      if (dy !== 0) contents.moveCursor(dy)
      if (dx > 0) contents.unfold()
      if (dx < 0) contents.fold()
    } else {
      if (dy !== 0) settings.moveCursor(dy)
      if (dx !== 0) settings.adjust(dx)
    }
  }

  function activate() {
    if (keysOpen) keysOpen = false
    else if (view === "library") library.openSelected()
    else if (view === "contents") contents.jump()
    else if (view === "settings") settings.activate()
  }

  function typed(text) {
    if (!service) return
    if (text === "?") {
      keysOpen = !keysOpen
      return
    }
    if (keysOpen) {
      keysOpen = false
      return
    }
    // g and G differ; every other letter works in capitals too.
    var key = text === "G" ? "G" : text.toLowerCase()

    if (key === "s") {
      if (settingsOpen) closeSettings()
      else openSettings()
    } else if (key === "d") {
      service.cycleLook()
      service.note(service.look === "day" ? "Day" : (service.look === "night" ? "Night" : "The shell's colours"))
    } else if (key === "p") {
      service.togglePaged()
      service.note(service.paged ? "Pages" : "Scrolling")
    } else if (key === "b") {
      settingsOpen = false
      if (service.view === "library") service.showReader()
      else service.showLibrary()
    } else if (view === "reader") {
      if (key === "t" || key === "c") openContents()
      else if (key === "[") service.chapterStep(-1)
      else if (key === "]") service.chapterStep(1)
      else if (key === "g") service.jumpTo(0)
      else if (key === "G") service.jumpToEnd()
      else if (key === "+" || key === "=") service.setFontPx(fontPx + 1)
      else if (key === "-") service.setFontPx(fontPx - 1)
      else if (key === "0") service.setFontPx(0)
    } else if (view === "library") {
      if (key === "/") search.forceActiveFocus()
      else if (key === "r") service.scan()
      else if (key === "o") openFolder()
    } else if (view === "contents") {
      if (key === "/") search.forceActiveFocus()
      else if (key === "t" || key === "c") closeContents()
    } else {
      if (key === "+" || key === "=") service.setFontPx(fontPx + 1)
      else if (key === "-") service.setFontPx(fontPx - 1)
      else if (key === "0") service.setFontPx(0)
      else if (key === "o") openFolder()
    }
  }

  // Keys the kit's catcher leaves alone. Returns true when handled.
  function pressed(event) {
    var key = event.key
    var back = (event.modifiers & Qt.ShiftModifier) !== 0
    if (keysOpen) {
      // Any key puts the sheet away; the catcher has dealt with the rest.
      if (key === Qt.Key_Space || key === Qt.Key_Backspace) keysOpen = false
      return key === Qt.Key_Space || key === Qt.Key_Backspace
    }
    if (key === Qt.Key_F1) {
      keysOpen = true
      return true
    }
    if (view === "reader") {
      if (key === Qt.Key_Space) reader.page(back ? -1 : 1)
      else if (key === Qt.Key_PageDown) reader.page(1)
      else if (key === Qt.Key_PageUp) reader.page(-1)
      else if (key === Qt.Key_Home) service.jumpTo(0)
      else if (key === Qt.Key_End) service.jumpToEnd()
      else if (key === Qt.Key_Backspace) service.goBack()
      else return false
      return true
    }
    if (view === "library") {
      if (key === Qt.Key_Space) library.openSelected()
      else if (key === Qt.Key_PageDown) library.movePage(1)
      else if (key === Qt.Key_PageUp) library.movePage(-1)
      else if (key === Qt.Key_Home) library.moveTo(0)
      else if (key === Qt.Key_End) library.moveTo(library.count - 1)
      else if (key === Qt.Key_Backspace) service.showReader()
      else return false
      return true
    }
    if (view === "contents") {
      if (key === Qt.Key_Space) contents.jump()
      else if (key === Qt.Key_PageDown) contents.moveCursor(10)
      else if (key === Qt.Key_PageUp) contents.moveCursor(-10)
      else if (key === Qt.Key_Home) contents.moveTo(0)
      else if (key === Qt.Key_End) contents.moveTo(contents.count - 1)
      else if (key === Qt.Key_Backspace) closeContents()
      else return false
      return true
    }
    if (key === Qt.Key_Space) settings.activate()
    else if (key === Qt.Key_PageDown) settings.moveCursor(6)
    else if (key === Qt.Key_PageUp) settings.moveCursor(-6)
    else if (key === Qt.Key_Home) settings.moveToEdge(false)
    else if (key === Qt.Key_End) settings.moveToEdge(true)
    else if (key === Qt.Key_Backspace) closeSettings()
    else return false
    return true
  }

  // ---- view changes

  function openContents() {
    if (!service || !service.hasBook) return
    search.text = ""
    contents.reset()
    contentsOpen = true
  }

  function closeContents() {
    contentsOpen = false
    leaveSearch()
  }

  function openSettings() {
    if (!service) return
    if (search.activeFocus) leaveSearch()
    settings.reset()
    settingsOpen = true
  }

  function closeSettings() {
    settingsOpen = false
  }

  function openFolder() {
    if (service && service.openLibraryFolder() && host) host.close()
  }

  function leaveSearch() {
    if (host) host.focusKeys()
  }

  onViewChanged: {
    search.text = ""
    if (search.activeFocus) leaveSearch()
    if (view === "library") {
      // The contents were a detour from a book that has now been left.
      contentsOpen = false
      library.reset()
    }
  }

  // Another book arriving (from the library, or asked for from outside)
  // opens on its text, not on its contents or on the settings.
  Connections {
    target: root.service
    function onBookKeyChanged() {
      root.contentsOpen = false
      root.settingsOpen = false
    }
    function onNoticeRevisionChanged() {
      root.noticed = root.service.notice
      if (root.noticed !== "") noticeTimer.restart()
    }
  }

  Timer {
    id: noticeTimer
    interval: 1600
    onTriggered: root.noticed = ""
  }

  // ---- the page everything sits on by day and by night

  Rectangle {
    visible: root.papered
    anchors.fill: parent
    anchors.margins: -root.bleed
    radius: Math.max(0, Style.cornerRadius - 1)
    color: root.paper
  }

  // ---- header

  Item {
    id: header
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    height: Style.space(34)

    Button {
      id: leftButton
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(28)
      implicitHeight: Style.space(28)
      horizontalPadding: 0
      verticalPadding: 0
      visible: root.keysOpen || root.view !== "library"
        || (root.service && (root.service.opening || root.service.currentTitle !== ""))
      // An open book from the library; the shelves from a book; an arrow
      // back from the settings.
      iconText: String.fromCodePoint(root.keysOpen || root.view === "settings" ? 0xF004D
        : (root.view === "library" ? 0xF00BE : 0xF125F))
      foreground: root.dim
      accent: root.accent
      fontFamily: root.fontFamily
      tooltipText: root.keysOpen || root.view === "settings" ? "Back"
        : (root.view === "library" ? "Back to the book" : "Library")
      onClicked: {
        if (!root.service) return
        if (root.keysOpen) root.keysOpen = false
        else if (root.view === "settings") root.closeSettings()
        else if (root.view === "library") root.service.showReader()
        else root.service.showLibrary()
      }
    }

    Column {
      anchors.centerIn: parent
      width: parent.width - Style.space(100)
      spacing: Style.space(2)

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: {
          if (root.keysOpen) return "Keys"
          if (root.view === "settings") return "Settings"
          if (root.view === "library") return "Library"
          if (root.view === "contents") return "Contents"
          if (!root.service) return ""
          if (root.service.opening) return root.service.openingTitle || "Opening"
          return root.service.chapterTitle || root.service.bookTitle
        }
        color: root.fg
        font.family: root.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignHCenter
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: {
          if (!root.service) return ""
          if (root.noticed !== "") return root.noticed
          if (root.keysOpen || root.view === "settings") return "Reader"
          if (root.view === "library") {
            if (root.service.books.length === 0) return root.service.scanning ? "Looking for books" : ""
            return root.service.books.length === 1 ? "1 book" : root.service.books.length + " books"
          }
          if (root.view === "contents") return root.service.bookTitle
          if (!root.service.hasBook) return ""
          return root.service.bookTitle + " · " + Reader.percentLabel(root.service.progress)
        }
        color: root.noticed !== "" ? root.accent : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignHCenter
      }
    }

    // The title doubles as the way into the contents.
    MouseArea {
      anchors.centerIn: parent
      width: parent.width - Style.space(100)
      height: parent.height
      enabled: !root.keysOpen && (root.view === "reader" || root.view === "contents")
        && root.service && root.service.hasBook
      cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: root.contentsOpen ? root.closeContents() : root.openContents()
    }

    Button {
      id: backButton
      anchors.right: rightButton.left
      anchors.rightMargin: Style.space(2)
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(28)
      implicitHeight: Style.space(28)
      horizontalPadding: 0
      verticalPadding: 0
      visible: root.view === "reader" && root.service && root.service.canGoBack
      iconText: String.fromCodePoint(0xF0311)
      foreground: root.accent
      accent: root.accent
      fontFamily: root.fontFamily
      tooltipText: "Back to where you were"
      onClicked: root.service.goBack()
    }

    // The contents from a book; the settings from the library.
    Button {
      id: rightButton
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(28)
      implicitHeight: Style.space(28)
      horizontalPadding: 0
      verticalPadding: 0
      readonly property bool forSettings: root.view === "library" || root.view === "settings"
      visible: forSettings || (root.service && root.service.hasBook)
      iconText: String.fromCodePoint(forSettings ? 0xF0493 : 0xF0279)
      foreground: root.view === "contents" || root.view === "settings" ? root.accent : root.dim
      accent: root.accent
      fontFamily: root.fontFamily
      tooltipText: forSettings ? "Settings" : "Contents"
      onClicked: {
        if (root.view === "settings") root.closeSettings()
        else if (root.view === "library") root.openSettings()
        else if (root.contentsOpen) root.closeContents()
        else root.openContents()
      }
    }
  }

  // ---- search (library and contents)

  TextField {
    id: search
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: header.bottom
    anchors.topMargin: Style.space(8)
    visible: root.view === "library" || root.view === "contents"
    height: visible ? implicitHeight : 0
    placeholderText: root.view === "contents" ? "/  filter" : "/  search"
    foreground: root.fg
    accent: root.accent
    font.family: root.fontFamily

    // While the field has focus the key catcher stands aside, so the keys a
    // text field ignores are handled here.
    Keys.onPressed: function(event) {
      if (event.key === Qt.Key_Escape) {
        if (text !== "") text = ""
        else root.leaveSearch()
        event.accepted = true
      } else if (event.key === Qt.Key_Down || event.key === Qt.Key_Up) {
        root.leaveSearch()
        root.move(0, event.key === Qt.Key_Down ? 1 : -1)
        event.accepted = true
      } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
        root.leaveSearch()
        root.activate()
        event.accepted = true
      } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
        root.leaveSearch()
        event.accepted = true
      }
    }
  }

  // ---- views

  Item {
    id: body
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: search.visible ? search.bottom : header.bottom
    anchors.topMargin: Style.space(12)
    anchors.bottom: progressTrack.top
    anchors.bottomMargin: Style.space(10)

    LibraryView {
      id: library
      anchors.fill: parent
      visible: root.view === "library" && !root.keysOpen
      service: root.service
      filter: root.view === "library" ? search.text : ""
      fg: root.fg
      dim: root.dim
      accent: root.accent
      fontFamily: root.fontFamily
      // The viewer's window is what the reader wants to see next.
      onHandedOff: {
        search.text = ""
        if (root.host) root.host.close()
      }
    }

    ReaderView {
      id: reader
      anchors.fill: parent
      visible: root.view === "reader" && !root.keysOpen
      service: root.service
      fontPx: root.fontPx
      fg: root.fg
      dim: root.dim
      accent: root.accent
      fontFamily: root.fontFamily
      lightPage: root.look === "day"
    }

    TocView {
      id: contents
      anchors.fill: parent
      visible: root.view === "contents" && !root.keysOpen
      service: root.service
      filter: root.view === "contents" ? search.text : ""
      fg: root.fg
      dim: root.dim
      accent: root.accent
      fontFamily: root.fontFamily
      onJumped: root.closeContents()
    }

    SettingsView {
      id: settings
      anchors.fill: parent
      visible: root.view === "settings" && !root.keysOpen
      service: root.service
      fontPx: root.fontPx
      fg: root.fg
      dim: root.dim
      accent: root.accent
      fontFamily: root.fontFamily
      onHandedOff: if (root.host) root.host.close()
      onKeysRequested: root.keysOpen = true
    }

    KeySheet {
      anchors.fill: parent
      visible: root.keysOpen
      fg: root.fg
      dim: root.dim
      accent: root.accent
      fontFamily: root.fontFamily
      onDismissed: root.keysOpen = false
    }
  }

  // ---- progress through the book

  Rectangle {
    id: progressTrack
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    height: 2
    visible: (root.view === "reader" || root.view === "contents") && root.service && root.service.hasBook
    color: Util.alpha(root.fg, 0.10)

    Rectangle {
      width: parent.width * (root.service ? root.service.progress : 0)
      height: parent.height
      color: root.accent
    }
  }
}
