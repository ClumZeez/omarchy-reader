import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// A fake screen with a top bar that hosts one plugin the way the Omarchy
// shell does:
//  - the plugin's service entry point is created once, parentless, and then
//    handed `omarchyPath`, `shell` and `manifest`;
//  - the bar widget is created afterwards and handed the same PluginBarApi
//    facade type the real bar gives third-party widgets, its id as
//    `moduleName` and its shell.json entry as `settings`;
//  - `bar.shell` is the real PluginShellApi type, scoped to this plugin.
// `widgets` > 1 mounts more copies, as extra monitors would.
Rectangle {
  id: host

  property string pluginDir: ""
  property string moduleName: "clumzeez.reader"
  property var settings: ({})
  property string section: "center"
  property int widgets: 1
  property string wallpaper: Quickshell.opt("wallpaper", "")

  // Marks this item as the surface popout panels attach to (see the
  // KeyboardPanel stand-in).
  readonly property bool readerScreen: true
  readonly property var manifest: readManifest()
  readonly property Item widget: slots.count > 0 && slots.itemAt(0) ? slots.itemAt(0).item : null
  readonly property alias api: barApi
  readonly property alias shellApi: shellApi
  property var service: null
  property bool serviceReady: false
  property bool widgetsActive: true
  // Popout panels that attached themselves to this screen, in order.
  property var popouts: []
  readonly property Item card: popouts.length > 0 ? popouts[0].card : null

  width: 2048
  height: 1152
  color: Qt.darker(Color.background, 1.35)

  function readManifest() {
    if (!pluginDir) return null
    var xhr = new XMLHttpRequest()
    try {
      xhr.open("GET", "file://" + pluginDir + "/manifest.json", false)
      xhr.send()
      return JSON.parse(xhr.responseText)
    } catch (e) {
      console.error("BarHost: cannot read manifest in " + pluginDir + ": " + e)
      return null
    }
  }

  function entryUrl(kind) {
    var points = manifest && manifest.entryPoints ? manifest.entryPoints : {}
    return points[kind] ? "file://" + pluginDir + "/" + points[kind] : ""
  }

  function widgetAt(index) {
    var slot = slots.itemAt(index)
    return slot ? slot.item : null
  }

  // What a write under the plugins directory does: every widget is torn
  // down and rebuilt. A keepLoaded service survives, any other is recreated.
  function reloadPlugins() {
    widgetsActive = false
    if (!(manifest && manifest.keepLoaded === true)) {
      destroyService()
      createService()
    }
    widgetsActive = true
  }

  function destroyService() {
    var old = service
    service = null
    serviceReady = false
    if (old) old.destroy()
  }

  function createService() {
    var url = entryUrl("service")
    if (!url) {
      serviceReady = true
      return
    }
    var comp = Qt.createComponent(url, Component.PreferSynchronous)
    if (comp.status !== Component.Ready) {
      console.error("BarHost: service plugin load failed: " + comp.errorString())
      serviceReady = true
      return
    }
    var inst = comp.createObject(null)
    if ("omarchyPath" in inst) inst.omarchyPath = Quickshell.env("OMARCHY_PATH")
    if ("shell" in inst) inst.shell = shellApi
    if ("manifest" in inst) inst.manifest = host.manifest
    service = inst
    serviceReady = true
  }

  Component.onCompleted: createService()
  Component.onDestruction: destroyService()

  Image {
    anchors.fill: parent
    visible: host.wallpaper !== ""
    source: host.wallpaper ? "file://" + host.wallpaper : ""
    fillMode: Image.PreserveAspectCrop
    asynchronous: false
  }

  PluginShellApi {
    id: shellApi
    pluginId: host.moduleName
    barConfig: {
      var entry = { id: host.moduleName }
      for (var key in host.settings) entry[key] = host.settings[key]
      var layout = { left: [], center: [], right: [] }
      layout[host.section] = [entry]
      return { position: "top", layout: layout }
    }

    property var summonLog: []

    function own(id) { return id === host.moduleName }

    _serviceLookup: function(id) { return own(id) ? host.service : null }
    _firstPartyServiceLookup: function(id) { return own(id) ? host.service : null }
    _summon: function(id, payload) {
      if (!own(id) || !host.widget) return false
      summonLog = summonLog.concat(["summon"])
      if (!host.widget.opened) host.widget.open()
      return true
    }
    _hide: function(id) {
      if (!own(id) || !host.widget) return false
      if (host.widget.opened) host.widget.close()
      return true
    }
    _toggle: function(id, payload) {
      if (!own(id) || !host.widget) return false
      if (host.widget.opened) host.widget.close()
      else host.widget.open()
      return true
    }
    _isOpen: function(id) {
      if (!own(id)) return false
      for (var i = 0; i < slots.count; i++) {
        var mounted = host.widgetAt(i)
        if (mounted && mounted.opened === true) return true
      }
      return false
    }
    // Replace semantics, as in the shell: the entry becomes {id, ...settings}.
    _updateSettings: function(id, settings) {
      if (!own(id)) return false
      var next = {}
      for (var key in settings) if (key !== "id") next[key] = settings[key]
      host.settings = next
      return true
    }
  }

  PluginBarApi {
    id: barApi
    pluginId: host.moduleName
    moduleName: host.moduleName
    shell: shellApi
    foreground: Color.bar.text
    barForeground: Color.bar.text
    background: Color.bar.background
    urgent: Color.bar.active
    fontFamily: Style.font.family
    position: "top"
    vertical: false
    barSize: Style.bar.sizeHorizontal

    property var shownTooltips: []
    property var popoutLog: []

    _showTooltip: function(target, text) { shownTooltips = shownTooltips.concat([text]) }
    _hideTooltip: function(target) {}
    _registerClickTarget: function(target) { clickTargets = clickTargets.concat([target]) }
    _unregisterClickTarget: function(target) {
      clickTargets = clickTargets.filter(function(t) { return t !== target })
    }
    // As the bar does: the popout that was active is closed before the
    // newcomer takes over.
    _requestPopout: function(owner) {
      var previous = activePopout
      if (previous && previous !== owner) {
        if (typeof previous.closeForPopoutSwitch === "function") previous.closeForPopoutSwitch()
        else if (typeof previous.close === "function") previous.close()
      }
      activePopout = owner
      popoutLog = popoutLog.concat(["request"])
    }
    _releasePopout: function(owner) {
      if (activePopout === owner) activePopout = null
      popoutLog = popoutLog.concat(["release"])
    }
    _switchPanelFrom: function(owner, direction) {
      popoutLog = popoutLog.concat(["switch:" + direction])
      return false
    }
    _targetBelongsToWindow: function(target, window) { return true }
    _moduleWidgets: function(id) {
      var out = []
      if (id !== host.moduleName) return out
      for (var i = 0; i < slots.count; i++) if (host.widgetAt(i)) out.push(host.widgetAt(i))
      return out
    }
    _run: function(command) { Quickshell.execDetached(["bash", "-lc", command]) }
    _setCenterHoverRevealSuppressed: function(value) { _centerHoverRevealSuppressed = value }
  }

  Rectangle {
    id: strip
    width: parent.width
    height: barApi.barSize
    color: Color.bar.background

    Text {
      anchors.left: parent.left
      anchors.leftMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      text: "1  2  3"
      color: Color.bar.text
      font.family: Style.font.family
      font.pixelSize: Style.font.body
    }

    Row {
      height: parent.height
      anchors.horizontalCenter: host.section === "center" ? parent.horizontalCenter : undefined
      anchors.right: host.section === "right" ? parent.right : undefined
      anchors.rightMargin: Style.space(12)
      x: host.section === "left" ? Style.space(90) : 0
      spacing: Style.space(4)

      Text {
        anchors.verticalCenter: parent.verticalCenter
        visible: host.section === "center"
        text: "Sunday 18:30"
        color: Color.bar.text
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        rightPadding: Style.space(8)
      }

      Repeater {
        id: slots
        model: host.serviceReady && host.widgetsActive ? host.widgets : 0

        Loader {
          anchors.verticalCenter: parent ? parent.verticalCenter : undefined
          source: host.entryUrl("barWidget")
          onLoaded: {
            if ("bar" in item) item.bar = barApi
            if ("moduleName" in item) item.moduleName = host.moduleName
            if ("settings" in item) item.settings = host.settings
          }
          onStatusChanged: if (status === Loader.Error) console.error("BarHost: widget failed to load: " + source)
        }
      }
    }
  }

  onSettingsChanged: {
    for (var i = 0; i < slots.count; i++) {
      var item = widgetAt(i)
      if (item && "settings" in item) item.settings = host.settings
    }
  }
}
