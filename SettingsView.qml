import QtQuick
import qs.Commons
import qs.Ui
import "Reader.js" as Reader

// The settings: how the reader behaves and looks, the folder the books are
// in, and where more can be had. One cursor is shared by keyboard and mouse,
// as in the contents; below the last row it rests on the link to the
// keyboard shortcuts, in the corner.
//
//   up / down      choose a row
//   Enter, Space   switch it, step to its next value, or open it
//   left / right   change its value
Item {
  id: view

  property var service: null
  property int fontPx: Style.font.subtitle
  property color fg: Color.foreground
  property color dim: Qt.darker(fg, 1.5)
  property color accent: Color.accent
  property string fontFamily: Style.font.family

  property int cursor: 1
  readonly property var rows: build()
  readonly property int count: rows.length
  // The cursor is on the link in the corner rather than on a row.
  readonly property bool onKeys: cursor >= count && count > 0

  // Something was opened outside the popout, which should get out of its way.
  signal handedOff()
  signal keysRequested()

  function build() {
    var s = view.service
    if (!s) return []
    var out = []
    out.push({ kind: "head", label: "Reading" })
    out.push({ id: "paged", kind: "switch", label: "Turn pages", note: "A page at a time, rather than scrolling",
               on: s.paged })
    out.push({ id: "look", kind: "choice", label: "Appearance", note: "",
               options: ["Auto", "Day", "Night"], at: Math.max(0, Reader.LOOKS.indexOf(s.look)) })
    out.push({ id: "size", kind: "step", label: "Text size", note: "", value: view.fontPx + " px" })

    out.push({ kind: "head", label: "Library" })
    out.push({ id: "folder", kind: "act", label: "Open book folder",
               note: Reader.tildePath(s.libraryFolder, s.home), icon: 0xF0770 })

    out.push({ kind: "head", label: "Free books" })
    for (var i = 0; i < Reader.FREE_LIBRARIES.length; i++) {
      var site = Reader.FREE_LIBRARIES[i]
      out.push({ id: "site", kind: "act", label: site.name, note: site.note, url: site.url, icon: 0xF03CC })
    }
    return out
  }

  function reset() {
    cursor = 1
    list.positionViewAtBeginning()
  }

  function moveTo(row) {
    if (count === 0) return
    cursor = Math.max(0, Math.min(count, row))
    if (cursor < count) list.positionViewAtIndex(cursor, ListView.Contain)
  }

  // Up and down step over the section titles.
  function moveCursor(delta) {
    var step = delta > 0 ? 1 : -1
    var next = cursor
    for (var left = Math.abs(delta); left > 0; left--) {
      var probe = next + step
      while (probe >= 0 && probe < count && rows[probe].kind === "head") probe += step
      // One past the last row is the link in the corner.
      if (probe < 0 || probe > count) break
      next = probe
    }
    moveTo(next)
  }

  function moveToEdge(last) {
    if (last) {
      moveTo(count)
      return
    }
    var probe = 0
    while (probe < count && rows[probe].kind === "head") probe++
    moveTo(probe)
  }

  function adjustRow(index, direction) {
    var row = rows[index]
    if (!row || !service) return
    if (row.id === "paged") service.setSetting("paged", direction > 0)
    else if (row.id === "look") service.setSetting("look", Reader.LOOKS[Math.max(0, Math.min(Reader.LOOKS.length - 1, row.at + direction))])
    else if (row.id === "size") service.setFontPx(view.fontPx + direction)
  }

  function activateRow(index) {
    if (index >= count && count > 0) {
      cursor = count
      keysRequested()
      return
    }
    var row = rows[index]
    if (!row || !service || row.kind === "head") return
    cursor = index
    if (row.id === "paged") service.togglePaged()
    else if (row.id === "look") service.cycleLook()
    else if (row.id === "size") service.setFontPx(view.fontPx + 1)
    else if (row.id === "folder") {
      if (service.openLibraryFolder()) handedOff()
    } else if (row.id === "site") {
      if (service.openSite(row.url)) handedOff()
    }
  }

  function adjust(direction) { adjustRow(cursor, direction) }
  function activate() { activateRow(cursor) }

  ListView {
    id: list
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.bottom: shortcuts.top
    anchors.bottomMargin: Style.space(6)
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height
    // The rows keep their places while what they say changes, so a switch
    // slides rather than being drawn afresh.
    model: view.count

    delegate: Item {
      id: slot

      required property int index
      readonly property var modelData: view.rows[index] || ({ kind: "head", label: "" })

      readonly property bool head: modelData.kind === "head"

      width: ListView.view.width
      height: head ? Style.space(index === 0 ? 26 : 40) : Math.max(Style.space(40), labels.implicitHeight + Style.space(16))

      PanelSectionHeader {
        visible: slot.head
        x: Style.space(10)
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(6)
        text: slot.modelData.label
        foreground: view.fg
        fontFamily: view.fontFamily
      }

      CursorSurface {
        id: row
        visible: !slot.head
        anchors.fill: parent
        hasCursor: slot.index === view.cursor
        foreground: view.fg
        accent: view.accent

        Column {
          id: labels
          x: Style.space(12)
          width: parent.width - x - control.width - Style.space(24)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(2)

          Text {
            width: parent.width
            textFormat: Text.PlainText
            text: slot.modelData.label || ""
            color: view.fg
            font.family: view.fontFamily
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
          }

          Text {
            width: parent.width
            visible: text !== ""
            textFormat: Text.PlainText
            text: slot.modelData.note || ""
            color: view.dim
            font.family: view.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideMiddle
          }
        }

        MouseArea {
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onPositionChanged: view.cursor = slot.index
          onClicked: view.activateRow(slot.index)
        }

        // What the row is set to, at its right end.
        Row {
          id: control
          anchors.right: parent.right
          anchors.rightMargin: Style.space(12)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(10)

          ToggleSwitch {
            visible: slot.modelData.kind === "switch"
            anchors.verticalCenter: parent.verticalCenter
            interactive: false
            checked: slot.modelData.on === true
            foreground: view.fg
            accent: view.accent
          }

          Row {
            visible: slot.modelData.kind === "choice"
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Repeater {
              model: slot.modelData.kind === "choice" ? slot.modelData.options : []

              Rectangle {
                id: option

                required property string modelData
                required property int index

                readonly property bool chosen: index === slot.modelData.at

                width: name.implicitWidth + Style.space(18)
                height: name.implicitHeight + Style.space(8)
                radius: Style.cornerRadius
                color: chosen ? Util.alpha(view.accent, 0.18) : "transparent"
                border.width: 1
                border.color: chosen ? Util.alpha(view.accent, 0.7) : "transparent"

                Text {
                  id: name
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: option.modelData
                  color: option.chosen ? view.fg : view.dim
                  font.family: view.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: {
                    view.cursor = slot.index
                    view.adjustRow(slot.index, option.index - slot.modelData.at)
                  }
                }
              }
            }
          }

          Row {
            visible: slot.modelData.kind === "step" || slot.modelData.kind === "show"
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(4)

            Text {
              visible: slot.modelData.kind === "step"
              width: Style.space(22)
              horizontalAlignment: Text.AlignHCenter
              textFormat: Text.PlainText
              text: "‹"
              color: view.dim
              font.family: view.fontFamily
              font.pixelSize: Style.font.subtitle

              MouseArea {
                anchors.fill: parent
                anchors.margins: -Style.space(6)
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  view.cursor = slot.index
                  view.adjustRow(slot.index, -1)
                }
              }
            }

            Text {
              anchors.verticalCenter: parent.verticalCenter
              width: Math.max(Style.space(64), implicitWidth)
              horizontalAlignment: Text.AlignHCenter
              textFormat: Text.PlainText
              text: slot.modelData.value || ""
              color: view.fg
              font.family: view.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Text {
              visible: slot.modelData.kind === "step"
              width: Style.space(22)
              horizontalAlignment: Text.AlignHCenter
              textFormat: Text.PlainText
              text: "›"
              color: view.dim
              font.family: view.fontFamily
              font.pixelSize: Style.font.subtitle

              MouseArea {
                anchors.fill: parent
                anchors.margins: -Style.space(6)
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  view.cursor = slot.index
                  view.adjustRow(slot.index, 1)
                }
              }
            }
          }

          Text {
            visible: slot.modelData.kind === "act" && !!slot.modelData.icon
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: slot.modelData.icon ? String.fromCodePoint(slot.modelData.icon) : ""
            color: view.dim
            font.family: view.fontFamily
            font.pixelSize: Style.font.body
          }
        }
      }
    }
  }

  // Every key, behind a link in the corner.
  CursorSurface {
    id: shortcuts
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    width: shortcutsLabel.implicitWidth + Style.space(24)
    height: shortcutsLabel.implicitHeight + Style.space(14)
    hasCursor: view.onKeys
    foreground: view.fg
    accent: view.accent

    Text {
      id: shortcutsLabel
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: "Keyboard Shortcuts"
      color: view.onKeys ? view.fg : view.dim
      font.family: view.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onPositionChanged: view.cursor = view.count
      onClicked: view.activateRow(view.count)
    }
  }
}
