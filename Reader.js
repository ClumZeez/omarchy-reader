.pragma library

// Pure logic for the Reader panel: saved-state handling, reading-position
// maths, the contents view model and library ordering. No QML types are
// touched here so the whole file runs under node (tests/js).

var STATE_VERSION = 1
// How the reader looks: the shell's own colours, or paper by day and by night.
var LOOKS = ["auto", "day", "night"]
var SNIPPET_LENGTH = 48
// How many blocks either side of where a place should be are searched for
// its words.
var RELOCATE_REACH = 3000
// The share of a table's width a column can claim, in characters of its
// longest cell.
var TABLE_MIN_WEIGHT = 4
var TABLE_MAX_WEIGHT = 36
// Books with a longer table of contents open with their branches folded.
var TOC_FOLD_THRESHOLD = 40

// ---------------------------------------------------------------- paths

function fileUrlToPath(url) {
  var s = String(url || "")
  if (s.indexOf("file://") === 0) {
    s = s.substring(7)
    if (s.charAt(0) !== "/") s = "/" + s
    try { s = decodeURIComponent(s) } catch (e) {}
  }
  return s
}

// file:// URL with each segment percent-encoded, so "#", "?" and spaces in
// a book's path survive Image.source.
function pathToFileUrl(path) {
  if (!path) return ""
  return "file://" + String(path).split("/").map(encodeURIComponent).join("/")
}

function expandHome(path, home) {
  var s = String(path || "").trim()
  if (s === "~") return String(home || "")
  if (s.indexOf("~/") === 0) return String(home || "") + s.substring(1)
  return s
}

// ---------------------------------------------------------------- state

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function clamp(value, min, max) {
  var n = Number(value)
  if (!isFinite(n)) return min
  return Math.max(min, Math.min(max, n))
}

function emptyState() {
  return { version: STATE_VERSION, current: "", view: "library", fontPx: 0,
           paged: false, look: "auto", books: {} }
}

// The settings a state may carry, each brought back to a value the reader
// understands; anything else becomes the default.
function cleanSettings(raw) {
  var src = isObject(raw) ? raw : {}
  return {
    paged: src.paged === true,
    look: LOOKS.indexOf(src.look) >= 0 ? src.look : "auto"
  }
}

// The value after `current` in `list`, going round; the first when `current`
// is not in it.
function nextOf(list, current) {
  var at = list.indexOf(current)
  return list[(at + 1) % list.length]
}

function cleanPosition(raw) {
  if (!isObject(raw)) return null
  var b = Number(raw.b)
  if (!isFinite(b) || b < 0) b = 0
  return {
    b: Math.floor(b),
    f: clamp(raw.f, 0, 1),
    p: clamp(raw.p, 0, 1),
    x: typeof raw.x === "string" ? mark(raw.x) : "",
    c: typeof raw.c === "string" ? raw.c : "",
    t: isFinite(Number(raw.t)) ? Math.max(0, Math.floor(Number(raw.t))) : 0,
    path: typeof raw.path === "string" ? raw.path : "",
    title: typeof raw.title === "string" ? raw.title : ""
  }
}

// Whether what was read from the state file is a state at all: nothing yet,
// or a JSON object. Anything else is worth setting aside before it is
// overwritten, since the file holds every saved place.
function stateIsReadable(text) {
  if (!text || !String(text).trim()) return true
  try { return isObject(JSON.parse(text)) } catch (e) { return false }
}

// Anything unreadable falls back to an empty state rather than throwing:
// a damaged state file must never stop a book from opening.
function parseState(text) {
  var state = emptyState()
  var data = null
  try { data = text ? JSON.parse(text) : null } catch (e) { data = null }
  if (!isObject(data)) return state
  if (typeof data.current === "string") state.current = data.current
  if (data.view === "reader" || data.view === "library") state.view = data.view
  var px = Number(data.fontPx)
  if (isFinite(px) && px >= 6 && px <= 64) state.fontPx = Math.round(px)
  var settings = cleanSettings(data)
  for (var name in settings) state[name] = settings[name]
  if (isObject(data.books)) {
    for (var key in data.books) {
      var pos = cleanPosition(data.books[key])
      if (pos) state.books[key] = pos
    }
  }
  return state
}

function serializeState(state) {
  var src = isObject(state) ? state : emptyState()
  var settings = cleanSettings(src)
  var out = {
    version: STATE_VERSION,
    current: typeof src.current === "string" ? src.current : "",
    view: src.view === "reader" ? "reader" : "library",
    fontPx: src.fontPx || 0,
    paged: settings.paged,
    look: settings.look,
    books: {}
  }
  var books = isObject(src.books) ? src.books : {}
  for (var key in books) {
    var pos = cleanPosition(books[key])
    if (pos) out.books[key] = pos
  }
  return JSON.stringify(out)
}

function bookPosition(state, key) {
  if (!isObject(state) || !isObject(state.books) || !key) return null
  return state.books[key] || null
}

function sameTitle(a, b) {
  return String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase()
}

// The saved place for a book that is being opened. A place is kept under
// the book's content key; when the file at the same path has been replaced
// by another edition or conversion of the same title its key is new, and
// the place kept for that path is used instead (relocate() re-anchors it).
function positionFor(state, key, path, title) {
  var direct = bookPosition(state, key)
  if (direct || !path || !isObject(state) || !isObject(state.books)) return direct
  var best = null
  for (var other in state.books) {
    var pos = state.books[other]
    if (!isObject(pos) || pos.path !== path || !sameTitle(pos.title, title)) continue
    if (!best || (Number(pos.t) || 0) > (Number(best.t) || 0)) best = pos
  }
  return best
}

// Returns a new state object: bindings on the old one must see a change.
// Places kept for the same path and title under another key are what
// positionFor() fell back on; they are superseded, not a second book.
function withPosition(state, key, pos) {
  var next = parseState(serializeState(state))
  if (!key) return next
  var clean = cleanPosition(pos)
  if (!clean) return next
  if (clean.path) {
    for (var other in next.books) {
      var old = next.books[other]
      if (other !== key && old.path === clean.path && sameTitle(old.title, clean.title)) {
        delete next.books[other]
      }
    }
  }
  next.books[key] = clean
  return next
}

function withoutBook(state, key) {
  var next = parseState(serializeState(state))
  delete next.books[key]
  if (next.current === key) next.current = ""
  return next
}

// ---------------------------------------------------------------- text

// Visible characters of a block. StyledText blocks carry tags, the three
// escapes and the numeric space entities the converter emits; everything
// else is literal.
function plainText(block) {
  if (!block) return ""
  var t = typeof block.t === "string" ? block.t : ""
  if (block.f !== 1) return t
  return t.replace(/<br\s*\/?>/g, "\n").replace(/<[^>]*>/g, "")
    .replace(/&#(\d+);/g, function(match, code) { return String.fromCodePoint(Number(code)) })
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&")
}

// The opening words of a block, as remembered with a saved position. Soft
// hyphens are dropped and every kind of space folded, so the same words
// still match if a later converter encodes them differently.
function snippet(block) {
  return plainText(block).replace(/\u00ad/g, "").replace(/\s+/g, " ").trim().substring(0, SNIPPET_LENGTH)
}

// What is kept of those words with a saved place: a fingerprint, never the
// words. A place saved by an earlier version, which did keep them, is turned
// into its fingerprint the first time it is read, so the words go from the
// file at the next save.
var MARK = /^#[0-9a-f]{14}$/

function mark(words) {
  var s = String(words || "")
  if (s === "" || MARK.test(s)) return s
  var h1 = 0xdeadbeef
  var h2 = 0x41c6ce57
  for (var i = 0; i < s.length; i++) {
    var c = s.charCodeAt(i)
    h1 = Math.imul(h1 ^ c, 2654435761)
    h2 = Math.imul(h2 ^ c, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  var high = ((h2 >>> 0) & 0xffffff).toString(16)
  var low = (h1 >>> 0).toString(16)
  return "#" + ("000000" + high).slice(-6) + ("00000000" + low).slice(-8)
}

// Weight of a block for progress. Pictures and rules count for something so
// an image-only book still advances.
function blockWeight(block) {
  if (!block) return 1
  if (typeof block.t === "string") return Math.max(1, block.t.length)
  if (Array.isArray(block.rows)) {
    var n = 1
    for (var r = 0; r < block.rows.length; r++)
      for (var c = 0; c < block.rows[r].length; c++) n += String(block.rows[r][c] || "").length
    return n
  }
  return block.k === "img" || block.k === "pg" ? 400 : 1
}

// ---------------------------------------------------------------- book

// Derives the lookup tables the reader needs. `offsets[i]` is the weight of
// everything before block i, with one extra entry holding the total.
function prepareBook(book) {
  var blocks = book && Array.isArray(book.blocks) ? book.blocks : []
  var offsets = new Array(blocks.length + 1)
  var total = 0
  for (var i = 0; i < blocks.length; i++) {
    offsets[i] = total
    total += blockWeight(blocks[i])
  }
  offsets[blocks.length] = total

  var toc = book && Array.isArray(book.toc) ? book.toc : []
  var order = []
  for (var j = 0; j < toc.length; j++) {
    var b = Number(toc[j] && toc[j].b)
    if (isFinite(b) && b >= 0) order.push(j)
  }
  // Reading order, not listing order: a contents page may list an appendix
  // first. Ties keep listing order so the deepest entry wins.
  order.sort(function(x, y) { return (toc[x].b - toc[y].b) || (x - y) })

  var sections = book && Array.isArray(book.sections) ? book.sections.slice() : []
  sections.sort(function(x, y) { return x - y })

  return {
    book: book || null,
    blocks: blocks,
    count: blocks.length,
    offsets: offsets,
    total: total,
    toc: toc,
    tocOrder: order,
    sections: sections,
    conv: book && typeof book.conv === "string" ? book.conv : ""
  }
}

function progressAt(prep, b, f) {
  if (!prep || prep.count === 0 || prep.total <= 0) return 0
  var i = Math.floor(clamp(b, 0, prep.count - 1))
  var within = (prep.offsets[i + 1] - prep.offsets[i]) * clamp(f, 0, 1)
  return clamp((prep.offsets[i] + within) / prep.total, 0, 1)
}

function blockAtProgress(prep, p) {
  if (!prep || prep.count === 0) return { b: 0, f: 0 }
  var target = clamp(p, 0, 1) * prep.total
  var lo = 0
  var hi = prep.count - 1
  while (lo < hi) {
    var mid = (lo + hi + 1) >> 1
    if (prep.offsets[mid] <= target) lo = mid
    else hi = mid - 1
  }
  var span = prep.offsets[lo + 1] - prep.offsets[lo]
  return { b: lo, f: span > 0 ? clamp((target - prep.offsets[lo]) / span, 0, 1) : 0 }
}

// Where to resume. A saved block index is only trusted when the book was
// produced by the same converter version and the block still starts with
// the remembered words; otherwise the words are searched for near where
// the saved percentage says they should be, and the percentage itself is
// the last resort.
function relocate(prep, saved) {
  if (!prep || prep.count === 0) return { b: 0, f: 0 }
  var pos = cleanPosition(saved)
  if (!pos) return { b: 0, f: 0 }
  var last = prep.count - 1

  if (pos.b <= last && pos.c === prep.conv) {
    if (!pos.x || mark(snippet(prep.blocks[pos.b])) === pos.x) return { b: pos.b, f: pos.f }
  }

  var guess = pos.p > 0 ? blockAtProgress(prep, pos.p).b : Math.min(pos.b, last)
  if (pos.x) {
    // The same words are near the same way through the book or nowhere;
    // looking further would only stall the shell on a very long book.
    var reach = Math.min(last, RELOCATE_REACH)
    for (var step = 0; step <= reach; step++) {
      var down = guess - step
      var up = guess + step
      if (down < 0 && up > last) break
      if (down >= 0 && mark(snippet(prep.blocks[down])) === pos.x) return { b: down, f: pos.f }
      if (step > 0 && up <= last && mark(snippet(prep.blocks[up])) === pos.x) return { b: up, f: pos.f }
    }
  }
  if (pos.p > 0) return blockAtProgress(prep, pos.p)
  return { b: Math.min(pos.b, last), f: pos.f }
}

function makePosition(prep, b, f, now, path, title) {
  var i = prep && prep.count > 0 ? Math.floor(clamp(b, 0, prep.count - 1)) : 0
  return {
    b: i,
    f: clamp(f, 0, 1),
    p: progressAt(prep, i, f),
    x: prep && prep.count > 0 ? mark(snippet(prep.blocks[i])) : "",
    c: prep ? prep.conv : "",
    t: Math.floor(Number(now) || 0),
    path: path || "",
    title: title || ""
  }
}

// Whether a string may be handed to a StyledText item. StyledText fetches
// the source of an <img>, also from the network; the converter never writes
// one, and this is what stands between a tampered cache and the shell
// making requests.
function styledTextIsSafe(text) {
  return !/<\s*img/i.test(String(text || ""))
}

// ---------------------------------------------------------------- tables

// Widths for a table's columns: each in proportion to its longest cell,
// within bounds, so a column of prices does not take a paragraph's room.
function columnWidths(rows, columns, total) {
  var count = Math.max(1, Math.floor(columns) || 1)
  var weights = []
  for (var c = 0; c < count; c++) weights.push(TABLE_MIN_WEIGHT)
  var list = Array.isArray(rows) ? rows : []
  for (var r = 0; r < list.length; r++) {
    var row = Array.isArray(list[r]) ? list[r] : []
    for (var k = 0; k < row.length && k < count; k++) {
      var length = plainText({ f: 1, t: String(row[k] || "") }).length
      if (length > weights[k]) weights[k] = Math.min(TABLE_MAX_WEIGHT, length)
    }
  }
  var sum = 0
  for (var i = 0; i < count; i++) sum += weights[i]
  var widths = []
  var used = 0
  for (var j = 0; j < count; j++) {
    var width = j === count - 1 ? Math.max(0, Math.floor(total) - used) : Math.floor(total * weights[j] / sum)
    widths.push(width)
    used += width
  }
  return widths
}

// ---------------------------------------------------------------- contents

// Index into book.toc of the entry the reader is inside: the last entry, in
// reading order, that starts at or before block `b`. -1 before the first.
function tocIndexAt(prep, b) {
  if (!prep || prep.tocOrder.length === 0) return -1
  var order = prep.tocOrder
  var toc = prep.toc
  var lo = 0
  var hi = order.length - 1
  if (toc[order[0]].b > b) return -1
  while (lo < hi) {
    var mid = (lo + hi + 1) >> 1
    if (toc[order[mid]].b <= b) lo = mid
    else hi = mid - 1
  }
  return order[lo]
}

function tocTitleAt(prep, b) {
  var i = tocIndexAt(prep, b)
  return i < 0 ? "" : String(prep.toc[i].t || "")
}

// The blocks that begin a page when the book is in pages: where each of the
// book's files starts, and what the two outer levels of its contents point
// at - the parts and chapters, and the sections where a book has parts. Not
// the very first block, which begins a page anyway; and not a block that
// follows a heading, so the title of a part stays over its first chapter.
function pageStarts(prep) {
  var starts = {}
  if (!prep || prep.count === 0) return starts
  var wanted = prep.sections.slice()
  for (var i = 0; i < prep.tocOrder.length; i++) {
    var entry = prep.toc[prep.tocOrder[i]]
    if (!(Number(entry.d) > 1)) wanted.push(entry.b)
  }
  for (var j = 0; j < wanted.length; j++) {
    var b = Math.floor(Number(wanted[j]))
    if (!(b > 0) || b >= prep.count) continue
    var before = prep.blocks[b - 1]
    if (before && before.k === "h") continue
    starts[b] = true
  }
  return starts
}

// Block to jump to for "next/previous chapter". Uses the contents when the
// book has one and falls back to its files otherwise. Stepping back from
// the middle of a chapter goes to that chapter's start first.
function chapterTarget(prep, b, direction) {
  if (!prep || prep.count === 0) return 0
  var starts = []
  var i
  if (prep.tocOrder.length > 0) {
    for (i = 0; i < prep.tocOrder.length; i++) starts.push(prep.toc[prep.tocOrder[i]].b)
  } else {
    starts = prep.sections.slice()
  }
  var unique = []
  for (i = 0; i < starts.length; i++) {
    var s = Math.floor(clamp(starts[i], 0, prep.count - 1))
    if (unique.length === 0 || unique[unique.length - 1] !== s) unique.push(s)
  }
  if (unique.length === 0) return direction > 0 ? prep.count - 1 : 0
  if (direction > 0) {
    for (i = 0; i < unique.length; i++) if (unique[i] > b) return unique[i]
    return b
  }
  for (i = unique.length - 1; i >= 0; i--) if (unique[i] < b) return unique[i]
  return 0
}

function tocHasChildren(toc, index) {
  return index + 1 < toc.length && toc[index + 1].d > toc[index].d
}

function tocParent(toc, index) {
  if (index < 0 || index >= toc.length) return -1
  var depth = toc[index].d
  for (var i = index - 1; i >= 0; i--) if (toc[i].d < depth) return i
  return -1
}

// Which branches start folded: none for a short contents; for a long one
// every branch except those leading to where the reader is.
function tocDefaultCollapsed(toc, currentIndex) {
  var collapsed = {}
  if (!Array.isArray(toc) || toc.length <= TOC_FOLD_THRESHOLD) return collapsed
  for (var i = 0; i < toc.length; i++) if (tocHasChildren(toc, i)) collapsed[i] = true
  for (var p = currentIndex; p >= 0; p = tocParent(toc, p)) delete collapsed[p]
  return collapsed
}

function matchesAll(haystack, needles) {
  for (var i = 0; i < needles.length; i++) if (haystack.indexOf(needles[i]) === -1) return false
  return true
}

function searchTerms(text) {
  var s = String(text || "").toLowerCase().trim()
  return s ? s.split(/\s+/) : []
}

// Rows to draw. With a filter the tree is flattened to its matches, each
// carrying its ancestors as `path`; without one, folded branches are hidden.
function tocRows(toc, collapsed, filter, currentIndex) {
  var rows = []
  if (!Array.isArray(toc)) return rows
  var terms = searchTerms(filter)
  var i
  if (terms.length > 0) {
    for (i = 0; i < toc.length; i++) {
      var title = String(toc[i].t || "")
      if (!matchesAll(title.toLowerCase(), terms)) continue
      var path = []
      for (var p = tocParent(toc, i); p >= 0; p = tocParent(toc, p)) path.unshift(String(toc[p].t || ""))
      rows.push({ index: i, t: title, d: 0, b: toc[i].b, kids: false, open: false,
                  cur: i === currentIndex, path: path.join(" › ") })
    }
    return rows
  }
  var hiddenBelow = -1
  for (i = 0; i < toc.length; i++) {
    var depth = toc[i].d
    if (hiddenBelow >= 0) {
      if (depth > hiddenBelow) continue
      hiddenBelow = -1
    }
    var kids = tocHasChildren(toc, i)
    var folded = kids && collapsed && collapsed[i] === true
    if (folded) hiddenBelow = depth
    rows.push({ index: i, t: String(toc[i].t || ""), d: depth, b: toc[i].b, kids: kids, open: kids && !folded,
                cur: i === currentIndex, path: "" })
  }
  return rows
}

function rowOfTocIndex(rows, tocIndex) {
  for (var i = 0; i < rows.length; i++) if (rows[i].index === tocIndex) return i
  return -1
}

// ---------------------------------------------------------------- library

function sortTitle(title) {
  return String(title || "").toLowerCase().replace(/^(the|a|an)\s+/, "").replace(/^[^a-z0-9]+/, "")
}

// Books being read come first, most recent first; the rest by title.
function sortBooks(books, state) {
  var list = Array.isArray(books) ? books.slice() : []
  var seen = isObject(state) && isObject(state.books) ? state.books : {}
  function stamp(book) {
    var pos = seen[book.key]
    return pos && pos.t ? pos.t : 0
  }
  list.sort(function(a, b) {
    var ta = stamp(a)
    var tb = stamp(b)
    if (ta !== tb) return tb - ta
    var sa = sortTitle(a.title)
    var sb = sortTitle(b.title)
    if (sa < sb) return -1
    if (sa > sb) return 1
    return String(a.path || "") < String(b.path || "") ? -1 : 1
  })
  return list
}

function filterBooks(books, text) {
  var list = Array.isArray(books) ? books : []
  var terms = searchTerms(text)
  if (terms.length === 0) return list
  return list.filter(function(book) {
    return matchesAll((String(book.title || "") + " " + String(book.author || "")).toLowerCase(), terms)
  })
}

function bookProgress(state, key) {
  var pos = bookPosition(state, key)
  return pos ? pos.p : 0
}

function percentLabel(p) {
  var n = clamp(p, 0, 1)
  if (n > 0 && n < 0.01) return "1%"
  // Only the true end reads as 100%.
  if (n < 1 && n > 0.99) return "99%"
  return Math.round(n * 100) + "%"
}

// Cursor movement over a grid laid out row-major with `columns` per row.
function gridMove(index, count, columns, dx, dy) {
  if (count <= 0) return -1
  var cols = Math.max(1, columns)
  var i = clamp(index, 0, count - 1)
  if (dx !== 0) {
    var col = i % cols
    if (dx < 0 && col > 0) i -= 1
    else if (dx > 0 && col < cols - 1 && i + 1 < count) i += 1
  }
  if (dy !== 0) {
    var next = i + dy * cols
    if (next >= 0 && next < count) i = next
    else if (dy > 0 && Math.floor(i / cols) < Math.floor((count - 1) / cols)) i = count - 1
  }
  return i
}

// ---------------------------------------------------------------- the desktop

// The most one program argument may carry is 128 KiB; a selection longer
// than this is copied in part, and said to be.
var CLIPBOARD_BYTES = 100000

// Where free books of good quality are to be had. Every one answered when
// this list was last checked.
var FREE_LIBRARIES = [
  { name: "Global Grey", url: "https://www.globalgreyebooks.com/", note: "Thousands of well-made editions" },
  { name: "Standard Ebooks", url: "https://standardebooks.org/", note: "Classics, carefully typeset and proofread" },
  { name: "Project Gutenberg", url: "https://www.gutenberg.org/", note: "The largest collection: over 75,000 books" }
]

function utf8Length(text) {
  var s = String(text || "")
  var n = 0
  for (var i = 0; i < s.length; i++) {
    var c = s.charCodeAt(i)
    if (c < 0x80) n += 1
    else if (c < 0x800) n += 2
    else if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) { n += 4; i++ }
    else n += 3
  }
  return n
}

// Text as it should land on a clipboard: the separators a text item uses
// between lines become newlines, soft hyphens go, and the special spaces a
// book is typeset with become ordinary ones.
function cleanCopy(text) {
  return String(text || "").replace(/[\u2028\u2029]/g, "\n").replace(/\u00ad/g, "")
    .replace(/[\u00a0\u202f]/g, " ").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim()
}

// What to hand the clipboard program, and whether it had to be cut short.
function clipboardText(text) {
  var clean = cleanCopy(text)
  if (utf8Length(clean) <= CLIPBOARD_BYTES) return { text: clean, shortened: false }
  var lo = 0
  var hi = clean.length
  while (lo < hi) {
    var mid = (lo + hi + 1) >> 1
    if (utf8Length(clean.substring(0, mid)) <= CLIPBOARD_BYTES) lo = mid
    else hi = mid - 1
  }
  // Never between the two halves of one character.
  var last = clean.charCodeAt(lo - 1)
  if (last >= 0xd800 && last <= 0xdbff) lo--
  var cut = clean.substring(0, lo)
  var paragraph = cut.lastIndexOf("\n\n")
  if (paragraph > cut.length / 2) cut = cut.substring(0, paragraph)
  return { text: cut.trim(), shortened: true }
}

// The folder a reader would call "my books": the one they chose, else the
// scanned folder that holds most of the library, else where a first book
// would go.
function libraryFolder(books, dirs, configured, home) {
  var chosen = expandHome(configured, home)
  if (chosen) return chosen
  var roots = Array.isArray(dirs) ? dirs : []
  var list = Array.isArray(books) ? books : []
  var best = ""
  var most = -1
  for (var r = 0; r < roots.length; r++) {
    var root = String(roots[r] || "").replace(/\/+$/, "")
    if (!root) continue
    var held = 0
    for (var b = 0; b < list.length; b++) {
      if (String(list[b].path || "").indexOf(root + "/") === 0) held++
    }
    if (held > most) {
      most = held
      best = root
    }
  }
  if (best) return best
  return home ? String(home) + "/Books" : ""
}

// A path as a person writes it: the home folder as "~".
function tildePath(path, home) {
  var s = String(path || "")
  var h = String(home || "")
  if (h && s === h) return "~"
  if (h && s.indexOf(h + "/") === 0) return "~" + s.substring(h.length)
  return s
}

// ---------------------------------------------------------------- keys

// Every key, for the sheet that "?" shows. The letters are also accepted
// in capitals, as in the shell's own panels; h j k l stand in for the
// arrows everywhere.
function keySheet() {
  return [
    { title: "Reading", keys: [
      ["← →", "Previous, next page"],
      ["Space", "Next page (Shift: back)"],
      ["↑ ↓", "Scroll; turn, in pages"],
      ["[ ]", "Previous, next chapter"],
      ["g G", "Start, end of the book"],
      ["⌫", "Back from a jump"],
      ["t", "Contents"],
      ["+ − 0", "Text size (0: the shell's)"]
    ] },
    { title: "Anywhere", keys: [
      ["b", "Library; back to the book"],
      ["s", "Settings"],
      ["p", "Pages or scrolling"],
      ["d", "Day, night, shell colours"],
      ["?", "These keys"],
      ["Esc", "Step back; then close"],
      ["Tab", "The next panel on the bar"]
    ] },
    { title: "Library", keys: [
      ["← → ↑ ↓", "Choose a book"],
      ["Enter", "Open it"],
      ["/", "Search"],
      ["r", "Look for new books"],
      ["o", "Open the book folder"]
    ] },
    { title: "Contents", keys: [
      ["↑ ↓", "Choose a chapter"],
      ["← →", "Fold, unfold"],
      ["Enter", "Go there"],
      ["/", "Filter"]
    ] },
    { title: "Mouse", keys: [
      ["Drag", "Select text; it is copied"],
      ["Double-click", "Select a word"],
      ["Wheel", "Scroll, or turn pages"]
    ] }
  ]
}

// ---------------------------------------------------------------- selection

// A selection runs from one place to another, each a block and an offset
// into that block's text. This puts the two in reading order; null when they
// are the same place.
function orderRange(a, b) {
  if (!a || !b) return null
  var forward = a.b < b.b || (a.b === b.b && a.o <= b.o)
  var start = forward ? a : b
  var end = forward ? b : a
  if (start.b === end.b && start.o === end.o) return null
  return { sb: start.b, so: start.o, eb: end.b, eo: end.o }
}

// The part of a block inside a selection: { from, to } with to -1 for "to
// the end", or null when the block is outside it.
function sliceFor(range, index) {
  if (!range || index < range.sb || index > range.eb) return null
  var from = index === range.sb ? range.so : 0
  var to = index === range.eb ? range.eo : -1
  if (to >= 0 && to <= from) return null
  return { from: from, to: to }
}

// The words a block puts into a selection: its text, a table's cells row by
// row, nothing for a picture or a rule. List markers are not part of it.
function copyableText(block) {
  if (!block) return ""
  if (typeof block.t === "string") return plainText(block)
  if (!Array.isArray(block.rows)) return ""
  var lines = []
  for (var r = 0; r < block.rows.length; r++) {
    var cells = []
    for (var c = 0; c < block.rows[r].length; c++) cells.push(plainText({ f: 1, t: String(block.rows[r][c] || "") }))
    lines.push(cells.join("\t"))
  }
  return lines.join("\n")
}

// The text of a selection, paragraphs parted by a blank line. Offsets into
// a block without text of its own (a table, a picture) are 0 for "before
// it" and 1 for "after it".
function selectionText(blocks, range) {
  if (!range || !Array.isArray(blocks)) return ""
  var parts = []
  for (var b = Math.max(0, range.sb); b <= range.eb && b < blocks.length; b++) {
    var block = blocks[b]
    var text = copyableText(block)
    if (block && typeof block.t === "string") {
      var from = b === range.sb ? range.so : 0
      var to = b === range.eb ? range.eo : text.length
      text = text.substring(from, to)
    } else if ((b === range.sb && range.so > 0) || (b === range.eb && range.eo < 1)) {
      text = ""
    }
    if (text.replace(/[\s\u00a0\u2028\u2029]/g, "") !== "") parts.push(text)
  }
  return cleanCopy(parts.join("\n\n"))
}

// ---------------------------------------------------------------- rich text

var SAFE_TAG = /^<(?:\/?[bius]|br|\/a|a href="[^"<>]*")>$/

// Whether every tag in a block's text is one the converter writes. Rich
// text would fetch the source of an <img> and obey a <style>; anything
// outside the few tags above means the text is shown as it stands instead.
function markupIsSafe(text) {
  var tags = String(text || "").match(/<[^>]*>?/g) || []
  for (var i = 0; i < tags.length; i++) if (!SAFE_TAG.test(tags[i])) return false
  return true
}

function escapeMarkup(text) {
  return String(text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>")
}

// A block's text for a rich text item: one paragraph with lines exactly
// `lineStep` pixels apart, its spaces kept as written, so that the place of
// every character is the same as in plainText().
function richText(block, lineStep, linkColor) {
  var t = block && typeof block.t === "string" ? block.t : ""
  var styled = block && block.f === 1 && block.k !== "pre" && markupIsSafe(t)
  var inner = styled
    ? t.replace(/<a href=/g, '<a style="color:' + String(linkColor || "") + '" href=')
    : escapeMarkup(t)
  return '<p style="margin:0; white-space:pre-wrap; line-height:' + Math.round(lineStep) + 'px">' + inner + "</p>"
}
