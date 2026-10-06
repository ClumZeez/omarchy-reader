import QtQuick
import qs.Commons
import qs.Ui
import "Reader.js" as Reader

// Bar chip and popout. The bar builds one of these per monitor, so it stays
// a thin view: everything shared lives in Service.qml. The root is a Panel
// because the bar identifies a popout by the item mounted in its slot.
//
//   left click    open / close
//   right click   open on the library
Panel {
  id: root

  moduleName: "clumzeez.reader"
  manageIpc: false

  readonly property string pluginId: "clumzeez.reader"
  readonly property var service: bar && bar.shell && typeof bar.shell.serviceFor === "function"
    ? bar.shell.serviceFor(pluginId) : null
  // A service is kept loaded across a plugin update, so right after one this
  // widget can be newer than the service it finds. Nothing is built on a
  // service that does not say it has what this widget needs.
  readonly property bool serviceFits: service !== null && service.apiVersion === 5
  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // The content is built on first open and then kept, so reopening is
  // instant and nothing is torn down under the closing fade.
  property bool everOpened: false

  function pushSettings() {
    if (serviceFits) service.configure(setting("folder", ""), setting("fontSize", 0))
  }

  function focusKeys() {
    keys.forceActiveFocus()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // The bar hands a new widget `bar` before `settings`; pushing at once
  // would tell the service the folder is unset and start a scan of the
  // wrong place.
  onServiceChanged: greet.restart()
  onSettingsChanged: pushSettings()

  // A tick after the service appears (or goes). A timer rather than a
  // delayed call: it is gone with the widget, where a delayed call would
  // still run.
  Timer {
    id: greet
    interval: 0
    onTriggered: {
      root.pushSettings()
      // A popout opened before the service existed has not been announced.
      if (root.opened && root.serviceFits) root.service.activate()
    }
  }

  // The bar's flag is shared: a widget rebuilt while its popout is open must
  // not leave it set.
  Component.onDestruction: if (opened && bar) bar.setCenterHoverRevealSuppressed(false)

  // Reacting to the state rather than overriding open()/close(): the base
  // type calls its own close() when another popout takes over.
  onOpenedChanged: {
    if (opened) {
      everOpened = true
      pushSettings()
      if (serviceFits) service.activate()
      if (content.item) content.item.shown()
      Qt.callLater(function() {
        if (root.opened && root.bar) root.bar.setCenterHoverRevealSuppressed(true)
      })
    } else {
      if (bar) bar.setCenterHoverRevealSuppressed(false)
      if (content.item) content.item.hidden()
      if (serviceFits) service.commit()
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: String.fromCodePoint(0xF00BE)
    tooltipText: {
      if (!root.serviceFits || !root.service.currentTitle) return "Reader"
      return root.service.currentTitle + " · " + Reader.percentLabel(root.service.currentProgress)
    }

    onPressed: function(b) {
      if (b === Qt.LeftButton) {
        root.toggle()
      } else if (b === Qt.RightButton) {
        if (root.serviceFits) root.service.showLibrary()
        if (!root.opened) root.open()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keys
    centerOnBar: true

    contentWidth: panel.fittedContentWidth(Style.space(652))
    // As tall as the screen allows under the bar; 0 until the screen is known.
    contentHeight: panel.availableCardHeight > 0
      ? Math.round(panel.availableCardHeight)
      : panel.cappedContentHeight(Style.space(720))

    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      blocked: content.item ? content.item.editing : false

      // Escape leaves one thing at a time - a selection, the key sheet, the
      // settings, the contents - and closes the popout when nothing is left.
      onCloseRequested: if (!content.item || !content.item.back()) root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) { if (content.item) content.item.move(dx, dy) }
      onReturnRequested: if (content.item) content.item.activate()
      onTextKey: function(text) { if (content.item) content.item.typed(text) }

      // Runs after the catcher's own handler; adds the keys it leaves alone
      // (paging, Home/End, Backspace) and tells Space from Shift+Space.
      Keys.onPressed: function(event) {
        if (blocked || !content.item) return
        if (content.item.pressed(event)) event.accepted = true
      }

      Loader {
        id: content
        anchors.fill: parent
        active: root.everOpened && root.serviceFits
        source: "PanelContent.qml"
        onLoaded: {
          item.host = root
          item.bleed = Qt.binding(function() { return panel.padding })
          item.shown()
        }
      }

      Text {
        anchors.centerIn: parent
        width: parent.width - Style.space(80)
        visible: !root.serviceFits
        textFormat: Text.PlainText
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        lineHeight: 1.5
        text: root.service === null
          ? "Reader is still starting."
          : "Reader was updated.\nRestart the shell to finish:  omarchy restart shell"
        color: Qt.darker(root.fg, 1.5)
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }
    }
  }
}
