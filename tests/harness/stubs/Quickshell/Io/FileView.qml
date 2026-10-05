import QtQuick

// Test stand-in for Quickshell.Io.FileView backed by local-file XHR
// (needs QML_XHR_ALLOW_FILE_READ=1 / QML_XHR_ALLOW_FILE_WRITE=1).
//
// It mirrors the real type's loading rules (quickshell src/io/fileview.cpp),
// because getting them wrong is silent in the live shell:
//  - setting `path` loads only when `preload` is true;
//  - reload() re-runs that same rule, so with `preload: false` it loads
//    nothing by itself;
//  - text() on an unloaded file starts an asynchronous load and returns
//    what was there before;
//  - an empty path clears the content and loads nothing;
//  - setText() is a no-op when the text equals what is loaded or pending.
// Loads complete a tick later, never synchronously. `blockWrites` cannot be
// honoured here (local-file PUT is asynchronous only).
QtObject {
  id: root

  property string path: ""
  property bool preload: true
  property bool blockLoading: false
  property bool blockAllReads: false
  property bool blockWrites: false
  property bool printErrors: true
  property bool watchChanges: false
  property bool atomicWrites: true

  signal loaded()
  signal loadFailed(int error)
  signal saved()
  signal saveFailed(int error)
  signal fileChanged()

  property string _text: ""
  property bool _has: false
  property bool _prepared: false
  property bool _loading: false
  property int _generation: 0

  function _url() {
    return "file://" + path.split("/").map(encodeURIComponent).join("/")
  }

  function _read() {
    if (!path) return null
    var xhr = new XMLHttpRequest()
    try {
      xhr.open("GET", _url(), false)
      xhr.send()
    } catch (e) {
      return null
    }
    if (xhr.status !== 200 && xhr.status !== 0) return null
    // A missing file reads back as an empty body with status 0.
    if (xhr.status === 0 && xhr.responseText === "") return null
    return xhr.responseText
  }

  function _loadAsync() {
    if (_loading) return
    _loading = true
    var generation = _generation
    Qt.callLater(function() {
      root._loading = false
      if (generation !== root._generation) {
        // The path changed while this load was queued; the newer request
        // decides what happens next.
        if (root.preload && root.path) root._loadAsync()
        return
      }
      var body = root._read()
      root._prepared = true
      if (body === null) {
        root._has = false
        root._text = ""
        root.loadFailed(2)
        return
      }
      root._has = true
      root._text = body
      root.loaded()
    })
  }

  function _updatePath() {
    _generation++
    _prepared = false
    if (!path) {
      _has = false
      _text = ""
      return
    }
    // Decided a tick later: during object creation `path` may be assigned
    // before `preload`.
    var generation = _generation
    Qt.callLater(function() {
      if (generation === root._generation && root.preload && !root._prepared) root._loadAsync()
    })
  }

  function text() {
    if (!_prepared && path) _loadAsync()
    return _text
  }

  function data() { return text() }
  function waitForJob() {}
  function reload() { _updatePath() }

  // Local-file PUT only works asynchronously (a synchronous one truncates
  // the file and writes nothing).
  function setText(value) {
    if (!path) return
    var next = String(value)
    if (_has && next === _text) return
    _has = true
    _prepared = true
    _text = next
    var xhr = new XMLHttpRequest()
    xhr.onreadystatechange = function() {
      // The view may be gone by the time its last write lands.
      if (xhr.readyState === XMLHttpRequest.DONE && root) root.saved()
    }
    try {
      xhr.open("PUT", _url())
      xhr.send(next)
    } catch (e) {
      saveFailed(1)
    }
  }

  onPathChanged: _updatePath()

  property Timer _watch: Timer {
    interval: 250
    repeat: true
    running: root.watchChanges && root.path !== ""
    onTriggered: {
      var body = root._read()
      var has = body !== null
      if (has !== root._has || (has && body !== root._text)) root.fileChanged()
    }
  }
}
