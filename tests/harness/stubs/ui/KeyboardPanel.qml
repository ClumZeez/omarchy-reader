import QtQuick
import QtQuick.Window
import qs.Commons

// Test stand-in for qs.Ui.KeyboardPanel. The real one is a layer-shell
// PanelWindow; this is an item that reparents itself over the harness
// window and reproduces the same public API, card geometry, focus hand-off,
// outside-click dismissal and popout coordination calls. It is a FocusScope
// because the real panel is its own window, with its own focus: another
// popout coming or going must not disturb which item holds focus in here.
FocusScope {
  id: root

  required property Item anchorItem
  required property QtObject bar
  property var owner: null
  property int margin: Style.gapsOut
  property int padding: Style.spacing.popupPadding
  property int contentWidth: Style.space(280)
  property int contentHeight: Style.space(200)
  property var borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
  property bool centerOnBar: false
  property bool open: false
  property int gap: Style.gapsOut
  property bool popoutSwitching: false
  property bool popoutSwitchClosing: false
  property bool focusPrimed: true
  property Item focusTarget: null

  default property alias contentItem: contentHolder.children

  readonly property var coordinatorKey: owner || root
  readonly property string barPos: bar ? bar.position : "top"
  readonly property real screenW: parent ? parent.width : 0
  readonly property real screenH: parent ? parent.height : 0
  readonly property real barW: screenW
  readonly property real barH: bar ? bar.barSize : 0
  readonly property real anchorW: anchorItem ? anchorItem.width : 0
  readonly property real anchorH: anchorItem ? anchorItem.height : 0
  readonly property point anchorScreenPos: {
    if (!anchorItem || !parent) return Qt.point(0, 0)
    var w = root.screenW
    return anchorItem.mapToItem(parent, 0, 0)
  }
  readonly property real availableCardWidth: screenW > 0
    ? Math.max(120, screenW - ((barPos === "left" || barPos === "right") ? barW + gap + margin : margin * 2))
    : 0
  readonly property real availableCardHeight: screenH > 0
    ? Math.max(120, screenH - ((barPos === "top" || barPos === "bottom") ? barH + gap + margin : margin * 2))
    : 0
  readonly property real verticalContentInset: padding * 2 + Border.top(borderSpec) + Border.bottom(borderSpec)

  function close() {
    if (owner && "close" in owner) owner.close()
    else root.open = false
  }

  function beginFocusPrime() {}

  function fittedContentWidth(width, cap) {
    var desired = Math.max(1, Number(width) || 1)
    var maxWidth = root.availableCardWidth > 0 ? root.availableCardWidth : desired
    if (cap !== undefined && Number(cap) > 0) maxWidth = Math.min(maxWidth, Number(cap))
    return Math.round(Math.min(desired, maxWidth))
  }

  function fittedContentHeight(implicitHeight, cap) {
    var desired = Math.max(root.verticalContentInset, (Number(implicitHeight) || 0) + root.verticalContentInset)
    var maxHeight = root.availableCardHeight > 0 ? root.availableCardHeight : desired
    if (cap !== undefined && Number(cap) > 0) maxHeight = Math.min(maxHeight, Number(cap))
    return Math.round(Math.min(desired, maxHeight))
  }

  function cappedContentHeight(height) {
    var desired = Math.max(root.padding * 2, Number(height) || root.padding * 2)
    var maxHeight = root.availableCardHeight > 0 ? root.availableCardHeight : desired
    return Math.round(Math.min(desired, maxHeight))
  }

  readonly property point cardOrigin: {
    if (!anchorItem || !bar) return Qt.point(margin, margin)
    var x = 0, y = 0
    if (centerOnBar) {
      x = screenW / 2 - contentWidth / 2
      y = barH + gap
    } else {
      x = anchorScreenPos.x + anchorW / 2 - contentWidth / 2
      y = barH + gap
    }
    x = Math.max(margin, Math.min(x, screenW - contentWidth - margin))
    y = Math.max(margin, Math.min(y, screenH - contentHeight - margin))
    return Qt.point(Math.round(x), Math.round(y))
  }

  visible: open
  z: 1000

  function _takeFocus() {
    if (!root.open) return
    root.forceActiveFocus()
    if (root.focusTarget) root.focusTarget.forceActiveFocus()
  }

  onOpenChanged: {
    if (open) {
      Qt.callLater(_takeFocus)
      refocus.restart()
    }
    if (!bar) return
    if (open) {
      if (typeof bar.requestPopout === "function") bar.requestPopout(coordinatorKey)
    } else if (bar.activePopout === coordinatorKey && typeof bar.releasePopout === "function") {
      bar.releasePopout(coordinatorKey)
    }
  }

  // The real panel is its own window, so it shows regardless of where the
  // declaring item sits (canon keeps its Loader invisible). Move onto the
  // nearest fake screen - an ancestor of the anchor marked `readerScreen` -
  // or failing that the window itself.
  function _adopt() {
    var screen = null
    for (var it = root.anchorItem; it; it = it.parent) {
      if (it.readerScreen === true) { screen = it; break }
    }
    if (!screen) screen = root.Window.contentItem
    if (!screen || root.parent === screen) return
    root.parent = screen
    root.anchors.fill = screen
    // Lets a scenario find the popout (and grab just its card).
    if (Array.isArray(screen.popouts) && screen.popouts.indexOf(root) === -1)
      screen.popouts = screen.popouts.concat([root])
  }

  readonly property alias card: card

  onAnchorItemChanged: _adopt()
  Window.onWindowChanged: _adopt()
  Component.onCompleted: _adopt()
  Component.onDestruction: {
    var screen = root.parent
    if (screen && Array.isArray(screen.popouts))
      screen.popouts = screen.popouts.filter(function(p) { return p !== root })
  }

  // A popout torn down in the same window (a plugin reload) takes the
  // window's focus with it a moment later; claim it again once that settles.
  Timer {
    id: refocus
    interval: 30
    onTriggered: if (!root.activeFocus) root._takeFocus()
  }

  MouseArea {
    anchors.fill: parent
    enabled: root.open
    acceptedButtons: Qt.AllButtons
    onClicked: root.close()
  }

  Rectangle {
    id: card
    x: root.cardOrigin.x
    y: root.cardOrigin.y
    width: root.contentWidth
    height: root.contentHeight
    color: Color.popups.background
    border.color: Border.color(root.borderSpec)
    border.width: Border.uniformWidth(root.borderSpec)
    radius: Style.cornerRadius

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.AllButtons
    }

    Item {
      id: contentHolder
      anchors.fill: parent
      anchors.margins: root.padding + Border.uniformWidth(root.borderSpec)
    }
  }
}
