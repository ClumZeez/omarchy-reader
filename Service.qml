import QtQuick
import Quickshell
import Quickshell.Io
import "Reader.js" as Reader

// One instance per shell. Bar widgets exist once per monitor, so everything
// that must not be duplicated lives here: the saved state, the library, the
// backend processes, the open book and the reading position. Widgets are
// views onto this object and reach it with bar.shell.serviceFor(pluginId).
Item {
  id: root

  // Injected by the shell after creation; null in Component.onCompleted.
  property var shell: null
  property var manifest: null

  readonly property string pluginId: "clumzeez.reader"
  readonly property string pluginDir: Reader.fileUrlToPath(Qt.resolvedUrl(".")).replace(/\/$/, "")
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state")
    + "/omarchy/settings/reader.json"
  readonly property string cachePath: (Quickshell.env("XDG_CACHE_HOME") || home + "/.cache") + "/omarchy-reader"

  // What the widgets expect of this object. A service outlives a plugin
  // update (it is kept loaded), so a widget from a newer Reader may find one
  // from an older; it checks this before relying on anything.
  readonly property int apiVersion: 4

  // Where the backend lives. Tests point this at a stand-in.
  property var backendCommand: ["python3", "-B", pluginDir + "/bin/reader"]
  // The ways out of the reader: the desktop's opener and the clipboard.
  // Tests point both elsewhere.
  property var openCommand: ["xdg-open"]
  property var copyCommand: ["wl-copy"]
  // A first scan reads every book once, which for thousands of books takes
  // minutes. Should even this run out, the backend has recorded how far it
  // got and the next scan carries on from there.
  property int scanTimeoutMs: 600000

  // ---- settings pushed by the bar widget (its shell.json entry)
  property string folder: ""
  property int configuredFontPx: 0

  // ---- saved state
  property var store: Reader.emptyState()
  property bool storeReady: false
  property int loadAttempts: 0

  // ---- library
  property var books: []
  property var libraryDirs: []
  property bool scanning: false
  property bool scanned: false
  property bool scanFailed: false
  property string libraryError: ""
  property bool indexRequested: false
  property double lastScan: 0

  // ---- the open book. `blocks` is shared by every reader view; views use
  // blockCount as their model and index into it.
  property var blocks: []
  property int blockCount: 0
  property var prep: null
  property string bookKey: ""
  property string bookPath: ""
  property string bookTitle: ""
  property string bookAuthor: ""
  property int bookRevision: 0
  property bool opening: false
  property string openingTitle: ""
  property string openError: ""
  property string wantedKey: ""
  // The book being picked up where it was left, and the same book once its
  // file turned out to be gone from the remembered path.
  property string resumeKey: ""
  property string strayKey: ""

  // ---- where the reader is. posRevision changes only when something other
  // than scrolling moved the position, telling views to reposition.
  property int posBlock: 0
  property real posFraction: 0
  property int posRevision: 0
  // Places jumped away from, newest last, and the place the latest jump
  // landed on until the reader moves off it.
  property var trail: []
  property var landed: null

  // What a panel shows when it opens: "library" or "reader".
  property string view: "library"
  property string pendingOpenPath: ""
  property bool activatePending: false

  readonly property int fontPx: store.fontPx > 0 ? store.fontPx : configuredFontPx
  // ---- how the reader behaves and looks (the settings view)
  readonly property bool paged: store.paged === true
  readonly property string look: store.look || "auto"
  // A word for the open panel to show for a moment ("Copied").
  property string notice: ""
  property int noticeRevision: 0
  // What was last put on the clipboard.
  property string copied: ""
  readonly property real progress: prep ? Reader.progressAt(prep, posBlock, posFraction) : 0
  readonly property int tocIndex: prep ? Reader.tocIndexAt(prep, posBlock) : -1
  readonly property string chapterTitle: tocIndex >= 0 ? String(prep.toc[tocIndex].t || "") : ""
  readonly property var toc: prep ? prep.toc : []
  readonly property bool hasBook: blockCount > 0
  // What the bar chip says about the book in hand, loaded or not: after a
  // restart or an idle release only its saved place is known.
  readonly property var currentPlace: Reader.bookPosition(store, store.current)
  readonly property string currentTitle: hasBook ? bookTitle : (currentPlace ? String(currentPlace.title || "") : "")
  readonly property real currentProgress: hasBook ? progress : (currentPlace ? Number(currentPlace.p) || 0 : 0)
  readonly property bool canGoBack: trail.length > 0

  function now() { return Math.floor(Date.now() / 1000) }

  // ---------------------------------------------------------------- state

  function adoptStore(text) {
    if (!Reader.stateIsReadable(text)) {
      damagedFile.path = statePath + ".damaged"
      damagedFile.setText(text)
    }
    store = Reader.parseState(text)
    view = store.view
    storeReady = true
    if (activatePending) activate()
  }

  function persist() {
    if (!storeReady) return
    stateFile.setText(Reader.serializeState(store))
  }

  // Folds the live position into the store and writes it out.
  function commit() {
    saveTimer.stop()
    if (!storeReady) return
    var next = hasBook && bookKey
      ? Reader.withPosition(store, bookKey, Reader.makePosition(prep, posBlock, posFraction, now(), bookPath, bookTitle))
      : Reader.parseState(Reader.serializeState(store))
    if (hasBook && bookKey) next.current = bookKey
    next.view = view
    store = next
    persist()
    if (hasBook) idleTimer.restart()
  }

  function panelOpen() {
    return shell && typeof shell.isPluginOpen === "function" ? shell.isPluginOpen(pluginId) === true : false
  }

  // The shell lives for days; a whole book should not stay in its memory
  // for a reader who has gone. With no popout open the blocks are let go.
  // The place is saved, and the next popout loads the book again exactly as
  // it does after a restart.
  function releaseIfIdle() {
    if (!hasBook || opening) return
    if (panelOpen()) {
      idleTimer.restart()
      return
    }
    commit()
    idleTimer.stop()
    bookFile.path = ""
    blockCount = 0
    blocks = []
    prep = null
    bookKey = ""
    bookPath = ""
    bookTitle = ""
    bookAuthor = ""
    trail = []
    landed = null
  }

  function configure(nextFolder, nextFontPx) {
    var f = String(nextFolder || "").trim()
    var px = Math.round(Number(nextFontPx) || 0)
    configuredFontPx = px >= 6 && px <= 64 ? px : 0
    if (f === folder) return
    folder = f
    if (scanned || scanning) scan()
  }

  function setFontPx(px) {
    var next = Reader.parseState(Reader.serializeState(store))
    next.fontPx = px > 0 ? Math.max(8, Math.min(40, Math.round(px))) : 0
    store = next
    persist()
  }

  // One setting changed. The state is read back through its own parser, so
  // a value it would not understand never reaches the file.
  function setSetting(name, value) {
    var next = Reader.parseState(Reader.serializeState(store))
    next[name] = value
    next = Reader.parseState(Reader.serializeState(next))
    if (next[name] === store[name]) return
    store = next
    persist()
  }

  function togglePaged() { setSetting("paged", !paged) }
  function cycleLook() { setSetting("look", Reader.nextOf(Reader.LOOKS, look)) }

  function note(text) {
    notice = String(text || "")
    noticeRevision++
  }

  function progressOf(key) { return Reader.bookProgress(store, key) }

  // ---------------------------------------------------------------- the desktop

  function copyText(text) {
    var clip = Reader.clipboardText(text)
    if (clip.text === "") return false
    copyJob.send(clip.text)
    copied = clip.text
    note(clip.shortened ? "Copied the first part" : "Copied")
    return true
  }

  readonly property string libraryFolder: Reader.libraryFolder(books, libraryDirs, folder, home)

  // The folder the books are in, in the file manager. One that is not there
  // yet (no book has ever been found) is made first: it is where the first
  // book goes.
  function openLibraryFolder() {
    var target = libraryFolder
    if (!target) return false
    if (libraryDirs.indexOf(target) >= 0) Quickshell.execDetached(openCommand.concat([target]))
    else Quickshell.execDetached(["sh", "-c", 'mkdir -p -- "$0" && exec "$@" "$0"', target].concat(openCommand))
    return true
  }

  function openSite(url) {
    if (!/^https:\/\//.test(String(url || ""))) return false
    Quickshell.execDetached(openCommand.concat([String(url)]))
    return true
  }

  // ---------------------------------------------------------------- library

  function adoptLibrary(text, fromCache) {
    var res = null
    try { res = JSON.parse(text) } catch (e) { res = null }
    if (!res || !Array.isArray(res.books)) {
      if (!fromCache) libraryError = res && res.error && res.error.message
        ? String(res.error.message) : "The library could not be read."
      return
    }
    if (!fromCache) libraryError = ""
    libraryDirs = Array.isArray(res.dirs) ? res.dirs : []
    // Reassigning the list rebuilds the grid, so only do it for a change.
    var sorted = Reader.sortBooks(res.books, store)
    if (JSON.stringify(sorted) !== JSON.stringify(books)) books = sorted
  }

  function scan() {
    scanning = true
    var argv = backendCommand.concat(["scan"])
    if (folder) argv = argv.concat(["--dir", Reader.expandHome(folder, home)])
    scanJob.run(argv)
  }

  function refreshLibrary() {
    indexRequested = true
    if (!scanning && Date.now() - lastScan > 5000) scan()
  }

  function showLibrary() {
    view = "library"
    commit()
    var sorted = Reader.sortBooks(books, store)
    if (JSON.stringify(sorted) !== JSON.stringify(books)) books = sorted
    refreshLibrary()
  }

  // Back to the book in hand; one that is not loaded (after a restart or an
  // idle release) is picked up where it was left.
  function showReader() {
    if (hasBook || opening) {
      view = "reader"
      return
    }
    var pos = Reader.bookPosition(store, store.current)
    if (!pos || !pos.path) return
    openBook(pos.path, pos.title)
    resumeKey = store.current
  }

  // ---------------------------------------------------------------- book

  function openBook(path, title) {
    if (!path) return
    view = "reader"
    commit()
    if (hasBook && path === bookPath) {
      openError = ""
      return
    }
    // The previous book is dropped at once so a reader never shows it under
    // the new book's name while the backend works. The count goes to zero
    // first: no delegate may be alive when the array is swapped.
    blockCount = 0
    blocks = []
    prep = null
    bookKey = ""
    bookPath = ""
    bookTitle = ""
    bookAuthor = ""
    trail = []
    landed = null
    wantedKey = ""
    resumeKey = ""
    strayKey = ""
    opening = true
    openError = ""
    openingTitle = title || ""
    openJob.run(backendCommand.concat(["open", "--", path]))
  }

  function openExternal(path) {
    if (path) Quickshell.execDetached(openCommand.concat([path]))
  }

  function failOpen(message) {
    opening = false
    openError = message || "This book could not be opened."
  }

  // The book last read is not where it was. A place is kept by the book's
  // content, not its file name, so a fresh scan may find it moved or renamed.
  function seekMoved() {
    strayKey = resumeKey
    resumeKey = ""
    scan()
  }

  function settleStray() {
    if (!strayKey) return
    var key = strayKey
    strayKey = ""
    for (var i = 0; i < books.length; i++) {
      var book = books[i]
      if (book.key === key && !book.error && !book.external) {
        // Found; but the reader may have gone to the library meanwhile.
        if (view === "reader") openBook(book.path, book.title)
        else opening = false
        return
      }
    }
    // Gone for good: the library is the useful place to be.
    opening = false
    view = "library"
  }

  function adoptBook(text) {
    var book = null
    try { book = JSON.parse(text) } catch (e) { book = null }
    if (!book || !Array.isArray(book.blocks) || book.blocks.length === 0) {
      failOpen("This book could not be opened.")
      return
    }
    // A book asked for earlier can finish loading after a later request;
    // only the one the last request resolved to is shown.
    if (String(book.key || "") !== wantedKey) return
    var prepared = Reader.prepareBook(book)
    var where = Reader.relocate(prepared, Reader.positionFor(store, String(book.key || ""),
                                                           String(book.path || ""), String(book.title || "")))
    // Through zero, so no delegate is alive while the array is swapped and a
    // book with the same number of blocks still resets the readers.
    blockCount = 0
    blocks = prepared.blocks
    prep = prepared
    blockCount = prepared.count
    bookKey = String(book.key || "")
    bookPath = String(book.path || "")
    bookTitle = String(book.title || "")
    bookAuthor = String(book.author || "")
    posBlock = where.b
    posFraction = where.f
    trail = []
    landed = null
    opening = false
    openError = ""
    bookRevision++
    posRevision++
    commit()
  }

  // ---------------------------------------------------------------- position

  // From the visible reader as it scrolls.
  function reportPosition(block, fraction) {
    if (!hasBook) return
    posBlock = Math.max(0, Math.min(blockCount - 1, Math.floor(block)))
    posFraction = Reader.clamp(fraction, 0, 1)
    if (landed && (posBlock !== landed.b || Math.abs(posFraction - landed.f) > 0.02)) landed = null
    saveTimer.restart()
  }

  // Remembers the place a jump is leaving so goBack() can return to it. A
  // run of jumps with no reading in between is one excursion, and only the
  // place it started from is worth keeping.
  function markDeparture() {
    if (landed) return
    var last = trail.length > 0 ? trail[trail.length - 1] : null
    if (last && last.b === posBlock && Math.abs(last.f - posFraction) <= 0.02) return
    trail = trail.concat([{ b: posBlock, f: posFraction }]).slice(-32)
  }

  function moveTo(block, fraction) {
    var target = Math.max(0, Math.min(blockCount - 1, Math.floor(block)))
    if (target !== posBlock || Math.abs(fraction - posFraction) > 0.02) markDeparture()
    posBlock = target
    posFraction = fraction
    landed = { b: posBlock, f: posFraction }
    posRevision++
    saveTimer.restart()
  }

  function jumpTo(block) {
    if (hasBook) moveTo(block, 0)
  }

  function jumpToEnd() {
    if (hasBook) moveTo(blockCount - 1, 1)
  }

  function goBack() {
    if (trail.length === 0) return false
    var last = trail[trail.length - 1]
    trail = trail.slice(0, -1)
    posBlock = last.b
    posFraction = last.f
    landed = null
    posRevision++
    saveTimer.restart()
    return true
  }

  function chapterStep(direction) {
    if (!hasBook) return
    var target = Reader.chapterTarget(prep, posBlock, direction)
    // No later chapter: staying put must not rewind to the top of the block.
    if (direction > 0 && target <= posBlock) return
    jumpTo(target)
  }

  function followLink(href) {
    var link = String(href || "")
    if (link.indexOf("b:") === 0) {
      var target = parseInt(link.substring(2), 10)
      if (isFinite(target)) jumpTo(target)
    } else if (/^(https?:|mailto:)/i.test(link)) {
      Quickshell.execDetached(openCommand.concat([link]))
    }
  }

  // ---------------------------------------------------------------- panel

  // A bar widget's popout has just opened.
  function activate() {
    if (!storeReady) {
      activatePending = true
      return
    }
    activatePending = false
    if (pendingOpenPath) {
      var requested = pendingOpenPath
      pendingOpenPath = ""
      openBook(requested, "")
    } else if (view === "reader" && !hasBook && !opening) {
      var pos = Reader.bookPosition(store, store.current)
      if (pos && pos.path) {
        openBook(pos.path, pos.title)
        resumeKey = store.current
      } else {
        view = "library"
      }
    }
    if (view === "library") refreshLibrary()
  }

  function summon() {
    return shell && typeof shell.summon === "function" ? shell.summon(pluginId, "{}") === true : false
  }

  // ---------------------------------------------------------------- plumbing

  FileView {
    id: stateFile
    path: root.statePath
    atomicWrites: true
    printErrors: false
    onLoaded: root.adoptStore(text())
    // A failed first read can be a start-up race rather than a missing file;
    // treating it as "no state yet" straight away would later overwrite every
    // saved position, so ask again before believing it.
    onLoadFailed: {
      if (root.storeReady) return
      root.loadAttempts++
      if (root.loadAttempts < 3) retryLoad.restart()
      else root.adoptStore("")
    }
  }

  Timer {
    id: retryLoad
    interval: 700
    onTriggered: stateFile.reload()
  }

  // Where a state file that could not be understood is kept, so the next
  // save does not destroy what a person might still recover by hand.
  FileView {
    id: damagedFile
    atomicWrites: true
    printErrors: false
  }

  Timer {
    id: saveTimer
    interval: 800
    onTriggered: root.commit()
  }

  // Restarted by every save, so it only runs out when nothing has happened
  // for this long.
  Timer {
    id: idleTimer
    interval: 20 * 60 * 1000
    onTriggered: root.releaseIfIdle()
  }

  // The previous scan's result, shown at once while a fresh scan runs. The
  // path stays empty (nothing is read) until a panel first needs the library.
  FileView {
    id: indexFile
    path: root.indexRequested ? root.cachePath + "/index.json" : ""
    printErrors: false
    onLoaded: if (!root.scanned || root.scanFailed) root.adoptLibrary(text(), true)
  }

  // Loads when its path is set; reload() covers a book converted again to
  // the same path.
  FileView {
    id: bookFile
    printErrors: false
    onLoaded: {
      var loaded = String(path)
      root.adoptBook(text())
      // Parsed; the view need not go on holding megabytes of text as well.
      Qt.callLater(function() { if (String(bookFile.path) === loaded) bookFile.path = "" })
    }
    onLoadFailed: root.failOpen("This book could not be opened.")
  }

  // Puts text on the clipboard. The text is written to the clipboard
  // program's standard input and never appears among its arguments: what a
  // program was started with can be read by every user of the computer, and
  // what a person selects in a book is theirs. The count of bytes is given
  // so that the program sees the end of the text whether or not the pipe is
  // closed behind it.
  Process {
    id: copyJob

    property string pending: ""
    property string waiting: ""

    function send(text) {
      if (running) {
        // The last copy has not finished; it is cut short for this one.
        waiting = text
        running = false
        return
      }
      pending = text
      stdinEnabled = true
      command = ["sh", "-c", 'head -c "$0" | "$@"', String(Reader.utf8Length(text))].concat(root.copyCommand)
      running = true
    }

    stdinEnabled: true
    onStarted: {
      write(pending)
      pending = ""
      shut.restart()
    }
    onExited: {
      shut.stop()
      if (waiting === "") return
      var next = waiting
      waiting = ""
      send(next)
    }
  }

  Timer {
    id: shut
    interval: 400
    onTriggered: copyJob.stdinEnabled = false
  }

  BackendJob {
    id: scanJob
    timeoutMs: root.scanTimeoutMs
    onFinished: function(ok, output) {
      if (queued) return
      root.scanning = false
      root.scanned = true
      root.lastScan = Date.now()
      root.scanFailed = !output
      if (output) {
        root.adoptLibrary(output, false)
      } else {
        root.libraryError = timedOut
          ? "Reading your books is taking a long time. It carries on the next time the library opens."
          : "Reader's helper did not run. Is python3 installed?"
        // What the scan recorded before it stopped is better than nothing.
        indexFile.reload()
      }
      root.settleStray()
    }
  }

  BackendJob {
    id: openJob
    timeoutMs: 120000
    onFinished: function(ok, output) {
      if (queued) return
      var res = null
      try { res = JSON.parse(output) } catch (e) { res = null }
      if (ok && res && res.ok === true && res.book) {
        root.resumeKey = ""
        root.wantedKey = String(res.key || "")
        if (bookFile.path === String(res.book)) bookFile.reload()
        else bookFile.path = String(res.book)
      } else if (res && res.error && res.error.code === "missing" && root.resumeKey) {
        root.seekMoved()
      } else if (res && res.error && res.error.message) {
        root.failOpen(String(res.error.message))
      } else if (timedOut) {
        root.failOpen("This book is taking too long to open.")
      } else {
        root.failOpen(output ? "This book could not be opened." : "Reader's helper did not run. Is python3 installed?")
      }
    }
  }

  IpcHandler {
    target: "reader"

    // Summoning an already open popout changes nothing a widget would
    // notice, so the request is also acted on here.
    function open(path: string): string {
      if (!path) return "no path"
      root.pendingOpenPath = Reader.expandHome(path, root.home)
      if (!root.summon()) {
        root.pendingOpenPath = ""
        return "Reader is not on the bar"
      }
      root.activate()
      return "ok"
    }

    function library(): string {
      // Before the popout opens: opening it resumes the book in hand unless
      // the library is already what was asked for.
      root.showLibrary()
      return root.summon() ? "ok" : "Reader is not on the bar"
    }

    function status(): string {
      return JSON.stringify({
        title: root.bookTitle,
        author: root.bookAuthor,
        chapter: root.chapterTitle,
        progress: Math.round(root.progress * 1000) / 1000,
        books: root.books.length
      })
    }
  }

  // Writes are asynchronous by default and would be lost with the object.
  Component.onDestruction: {
    if (!storeReady) return
    stateFile.blockWrites = true
    commit()
  }
}
