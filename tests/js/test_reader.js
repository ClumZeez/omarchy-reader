#!/usr/bin/env node
"use strict";

// Tests for Reader.js. The file is a QML JavaScript library, so its
// `.pragma` line is stripped and the rest evaluated in a sandbox.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function load(name) {
  const src = fs.readFileSync(path.join(__dirname, "..", "..", name), "utf8")
    .split("\n")
    .filter((l) => !l.trim().startsWith(".pragma") && !l.trim().startsWith(".import"))
    .join("\n");
  const sandbox = { console };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: name });
  return sandbox;
}

const F = load("Reader.js");
let pass = 0, fail = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("PASS " + name); }
  else { fail++; console.log("FAIL " + name); }
}
function eq(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { pass++; console.log("PASS " + name); }
  else { fail++; console.log("FAIL " + name + "\n   got  " + g + "\n   want " + w); }
}

// ---- paths
eq("file url to path", F.fileUrlToPath("file:///home/a%20b/c%23d.epub"), "/home/a b/c#d.epub");
eq("path to file url escapes # and space", F.pathToFileUrl("/home/a b/c#d?.epub"), "file:///home/a%20b/c%23d%3F.epub");
eq("path round trip", F.fileUrlToPath(F.pathToFileUrl("/x/é [1] {2}.epub")), "/x/é [1] {2}.epub");
eq("expand home", F.expandHome("~/Books", "/home/u"), "/home/u/Books");
eq("expand bare tilde", F.expandHome("~", "/home/u"), "/home/u");
eq("expand leaves absolute", F.expandHome("/data/books", "/home/u"), "/data/books");

// ---- settings
eq("a new state has every setting at its default", [F.emptyState().paged, F.emptyState().look], [false, "auto"]);
eq("settings survive a save and a load",
   (function() { var s = F.parseState(F.serializeState({ paged: true, look: "night", books: {} }));
                 return [s.paged, s.look]; })(), [true, "night"]);
eq("settings a reader would not understand fall back",
   (function() { var s = F.parseState('{"paged":"yes","look":"sepia"}'); return [s.paged, s.look]; })(), [false, "auto"]);
eq("a state from before the settings loads with the defaults",
   (function() { var s = F.parseState('{"version":1,"current":"k","view":"reader","fontPx":18,"books":{"k":{"b":3,"f":0.5}}}');
                 return [s.current, s.fontPx, s.paged, s.look, s.books.k.b]; })(), ["k", 18, false, "auto", 3]);
eq("a setting that is no longer one is dropped on the next save",
   Object.keys(JSON.parse(F.serializeState(F.parseState('{"paged":true,"gone":"x","other":1.5}')))).sort(),
   ["books", "current", "fontPx", "look", "paged", "version", "view"]);
eq("saving a position keeps the settings",
   (function() { var s = F.parseState('{"paged":true,"look":"day"}');
                 var n = F.withPosition(s, "k", { b: 1, f: 0, path: "/a", title: "A" });
                 return [n.paged, n.look]; })(), [true, "day"]);
eq("looks go round", [F.nextOf(F.LOOKS, "auto"), F.nextOf(F.LOOKS, "day"), F.nextOf(F.LOOKS, "night"), F.nextOf(F.LOOKS, "?")],
   ["day", "night", "auto", "auto"]);

// ---- state
eq("empty state on garbage", F.parseState("{broken"), F.emptyState());
eq("empty state on array", F.parseState("[1,2]"), F.emptyState());
eq("empty state on empty", F.parseState(""), F.emptyState());
(() => {
  const s = F.parseState(JSON.stringify({
    version: 1, current: "k1", view: "reader", fontPx: 15,
    books: { k1: { b: 12, f: 0.5, p: 0.25, x: "Hello", c: "1", t: 100, path: "/a.epub", title: "A" },
             bad: 7, k2: { b: -4, f: 9, p: "x" } }
  }));
  eq("state current", s.current, "k1");
  eq("state view", s.view, "reader");
  eq("state font", s.fontPx, 15);
  eq("state position kept", s.books.k1, { b: 12, f: 0.5, p: 0.25, x: F.mark("Hello"), c: "1", t: 100, path: "/a.epub", title: "A" });
  ok("words kept by an earlier version are gone once the state is read", JSON.stringify(s).indexOf("Hello") === -1
     && F.serializeState(s).indexOf("Hello") === -1);
  eq("and a fingerprint stays as it is", F.parseState(F.serializeState(s)).books.k1.x, F.mark("Hello"));
  ok("state drops non-object book", !("bad" in s.books));
  eq("state clamps wild values", s.books.k2, { b: 0, f: 1, p: 0, x: "", c: "", t: 0, path: "", title: "" });
  eq("state serialises and reparses", F.parseState(F.serializeState(s)), s);
})();
eq("which state files are worth setting aside",
   ["", "  \n", "{}", '{"books":{}}', "[]", "7", "null", '{"books":', "garbage"].map(F.stateIsReadable),
   [true, true, true, true, false, false, false, false, false]);
ok("unknown view falls back to library", F.parseState('{"view":"toc"}').view === "library");
ok("silly font size ignored", F.parseState('{"fontPx":900}').fontPx === 0);
(() => {
  const a = F.emptyState();
  const b = F.withPosition(a, "k", { b: 3, f: 0.2, p: 0.1, x: "x", c: "1", t: 5 });
  ok("withPosition returns a new object", a !== b && !("k" in a.books));
  eq("withPosition stores", b.books.k.b, 3);
  const c = F.withoutBook(Object.assign(b, { current: "k" }), "k");
  ok("withoutBook removes and clears current", !("k" in c.books) && c.current === "");
})();
(() => {
  // A new conversion of the same title at the same path has another key.
  const state = { books: {
    old: { b: 40, f: 0.5, p: 0.4, x: "The road", c: "1", t: 10, path: "/b/walk.epub", title: "The Long Walk" },
    older: { b: 2, f: 0, p: 0.1, x: "x", c: "1", t: 5, path: "/b/walk.epub", title: "the long walk " },
    other: { b: 7, f: 0, p: 0.2, x: "y", c: "1", t: 99, path: "/b/else.epub", title: "Else" }
  } };
  eq("place by key", F.positionFor(state, "other", "/b/walk.epub", "The Long Walk").b, 7);
  eq("place by path and title when the key is new", F.positionFor(state, "new", "/b/walk.epub", "The Long Walk").b, 40);
  eq("another book at that path is not given the place", F.positionFor(state, "new", "/b/walk.epub", "Something Else"), null);
  eq("no path, no fallback", F.positionFor(state, "new", "", "The Long Walk"), null);
  eq("unknown everything", F.positionFor(state, "new", "/b/nowhere.epub", "The Long Walk"), null);
  eq("no state", F.positionFor(null, "new", "/b/walk.epub", "The Long Walk"), null);
  const next = F.withPosition(state, "new", { b: 41, f: 0, p: 0.41, x: "The road", c: "1", t: 20, path: "/b/walk.epub", title: "The Long Walk" });
  eq("the superseded places go when the new key is saved", Object.keys(next.books).sort(), ["new", "other"]);
  const moved = F.withPosition(state, "old", { b: 41, f: 0, p: 0.41, x: "", c: "1", t: 20, path: "/c/walk.epub", title: "The Long Walk" });
  eq("a moved book keeps its key and takes nothing with it", Object.keys(moved.books).sort(), ["old", "older", "other"]);
})();

// ---- text
eq("plain text of plain block", F.plainText({ k: "p", t: "a <b> & c" }), "a <b> & c");
eq("plain text of styled block", F.plainText({ k: "p", f: 1, t: "a <i>b</i> &amp; c &lt;d&gt;<br>e<a href=\"b:1\">¹</a>" }), "a b & c <d>\ne¹");
eq("plain text decodes space entities", F.plainText({ k: "p", f: 1, t: "a&#160;b&#8239;c" }), "a\u00a0b\u202fc");
eq("snippet is the same however spaces and soft hyphens are written",
  F.snippet({ k: "p", f: 1, t: "&#160;&#160;In\u00addeed&#160;so" }), F.snippet({ k: "p", t: "Indeed so" }));
eq("snippet collapses space and truncates", F.snippet({ t: "  one   two\nthree " + "x".repeat(80) }).length, 48);
eq("snippet of image block", F.snippet({ k: "img", src: "/a.png" }), "");

// ---- book maths
const book = {
  conv: "1",
  blocks: [
    { k: "h", l: 1, t: "One" },                 // 0  w=3
    { k: "p", t: "a".repeat(100) },             // 1  w=100
    { k: "img", src: "/i.png", w: 10, h: 10 },  // 2  w=400
    { k: "p", f: 1, t: "<i>b</i>" + "b".repeat(89) }, // 3 w=97
    { k: "hr" },                                // 4  w=1
    { k: "h", l: 1, t: "Two" },                 // 5  w=3
    { k: "p", t: "c".repeat(396) }              // 6  w=396
  ],
  sections: [0, 5],
  toc: [
    { t: "One", d: 0, b: 0 },
    { t: "One point five", d: 1, b: 3 },
    { t: "Two", d: 0, b: 5 }
  ]
};
const prep = F.prepareBook(book);
eq("total weight", prep.total, 1000);
eq("offsets", prep.offsets, [0, 3, 103, 503, 600, 601, 604, 1000]);
eq("progress at start", F.progressAt(prep, 0, 0), 0);
eq("progress mid block", F.progressAt(prep, 1, 0.5), 0.053);
eq("progress at end", F.progressAt(prep, 6, 1), 1);
eq("progress clamps index", F.progressAt(prep, 99, 0), 0.604);
eq("block at progress 0", F.blockAtProgress(prep, 0), { b: 0, f: 0 });
eq("block at progress mid", F.blockAtProgress(prep, 0.303), { b: 2, f: 0.5 });
eq("block at progress 1", F.blockAtProgress(prep, 1), { b: 6, f: 1 });
(() => {
  let worst = 0;
  for (let b = 0; b < prep.count; b++) {
    for (const f of [0, 0.25, 0.5, 0.9]) {
      const back = F.blockAtProgress(prep, F.progressAt(prep, b, f));
      worst = Math.max(worst, Math.abs(F.progressAt(prep, back.b, back.f) - F.progressAt(prep, b, f)));
    }
  }
  ok("progress/block round trip is stable", worst < 1e-9);
})();
eq("empty book prepares", F.prepareBook({}).total, 0);
eq("empty book progress", F.progressAt(F.prepareBook({}), 0, 0), 0);
eq("empty book relocate", F.relocate(F.prepareBook(null), { b: 3 }), { b: 0, f: 0 });

// ---- position round trip and re-anchoring
(() => {
  const saved = F.makePosition(prep, 3, 0.4, 1234, "/a.epub", "A");
  eq("what is saved of the words is their fingerprint", saved.x, F.mark("b" + "b".repeat(47)));
  ok("a fingerprint holds none of the words", /^#[0-9a-f]{14}$/.test(saved.x) && F.mark("") === ""
     && F.mark("one thing") !== F.mark("another") && F.mark("one thing") === F.mark("one thing"));
  ok("no words of the book are in what is written", (function() {
    const state = F.withPosition(F.emptyState(), "k", F.makePosition(prep, 3, 0.4, 1234, "/a.epub", "A"));
    const text = F.serializeState(state);
    return prep.blocks.every((block) => typeof block.t !== "string" || block.t.length < 8 || text.indexOf(block.t.substring(0, 8)) === -1);
  })());
  eq("saved conv", saved.c, "1");
  eq("same conversion restores exactly", F.relocate(prep, saved), { b: 3, f: 0.4 });

  // The converter changed and split the first paragraph in two: every
  // later block index shifts by one.
  const shifted = JSON.parse(JSON.stringify(book));
  shifted.conv = "2";
  shifted.blocks.splice(1, 1, { k: "p", t: "a".repeat(50) }, { k: "p", t: "a".repeat(50) });
  const prep2 = F.prepareBook(shifted);
  eq("reconverted book re-anchors by text", F.relocate(prep2, saved), { b: 4, f: 0.4 });

  // Same converter version but the block no longer matches (edited file).
  const edited = JSON.parse(JSON.stringify(book));
  edited.blocks.unshift({ k: "p", t: "A new preface paragraph." });
  eq("stale index with matching conv still re-anchors", F.relocate(F.prepareBook(edited), saved), { b: 4, f: 0.4 });

  // Nothing matches: fall back to the saved percentage.
  const rewritten = { conv: "3", blocks: [{ k: "p", t: "z".repeat(500) }, { k: "p", t: "y".repeat(500) }] };
  eq("unmatched text falls back to percentage", F.relocate(F.prepareBook(rewritten), saved).b, 1);

  // A position saved before snippets existed.
  eq("bare index is honoured", F.relocate(prep, { b: 5, f: 0, c: "1" }), { b: 5, f: 0 });
  eq("index past the end clamps", F.relocate(prep, { b: 500, f: 0.5, c: "1" }), { b: 6, f: 0.5 });
})();
(() => {
  // Repeated short lines: pick the occurrence nearest the saved percentage.
  const blocks = [];
  for (let i = 0; i < 200; i++) blocks.push({ k: "p", t: i % 10 === 0 ? "“Yes.”" : "filler text number " + i });
  const p1 = F.prepareBook({ conv: "1", blocks });
  const saved = F.makePosition(p1, 120, 0, 1, "", "");
  const p2 = F.prepareBook({ conv: "2", blocks: [{ k: "p", t: "inserted" }].concat(blocks) });
  eq("duplicate snippets resolve to the nearest", F.relocate(p2, saved), { b: 121, f: 0 });
})();
(() => {
  // A very long book: the words are looked for near where they should be,
  // not from one end of the book to the other.
  const blocks = [];
  for (let i = 0; i < 9000; i++) blocks.push({ k: "p", t: "paragraph number " + i });
  const p1 = F.prepareBook({ conv: "1", blocks });
  const near = blocks.slice(0, 4000).concat([{ k: "p", t: "a few" }, { k: "p", t: "new ones" }], blocks.slice(4000));
  eq("moved a little: found", F.relocate(F.prepareBook({ conv: "2", blocks: near }), F.makePosition(p1, 6000, 0.25, 1, "", "")), { b: 6002, f: 0.25 });
  const far = blocks.slice(4000).concat(blocks.slice(0, 4000));
  const where = F.relocate(F.prepareBook({ conv: "2", blocks: far }), F.makePosition(p1, 100, 0, 1, "", ""));
  ok("moved across the book: the same way through instead", where.b > 50 && where.b < 150);
})();

eq("only text without pictures is drawn as styled text",
   ["a <b>b</b>", "", null, "<img src=\"http://x/y.png\">", "x < IMG src=y>", "<a href=\"b:1\">imgs</a>"].map(F.styledTextIsSafe),
   [true, true, true, false, false, true]);

// ---- tables
eq("columns share the width by what they hold",
   F.columnWidths([["Refuse shingles for roof sides", "4.00", ""], ["Boards", "$8.03½", "Mostly shanty boards."]], 3, 600),
   [315, 63, 222]);
eq("markup and entities do not count as content",
   F.columnWidths([["<a href=\"b:16\"><u>I.</u></a>", "Down the Rabbit&#160;Hole"]], 2, 280), [46, 234]);
eq("one long column cannot starve the others", F.columnWidths([["x".repeat(500), "ab"]], 2, 400), [360, 40]);
eq("no rows", F.columnWidths([], 2, 100), [50, 50]);
eq("rows shorter than the table", F.columnWidths([["abcdefgh"], ["a", "b", "c"]], 3, 160), [80, 40, 40]);
eq("nothing to share", F.columnWidths(null, 0, 0), [0]);

// ---- contents
eq("toc before first entry", F.tocIndexAt(F.prepareBook({ blocks: book.blocks, toc: [{ t: "X", d: 0, b: 3 }] }), 1), -1);
eq("toc at start", F.tocIndexAt(prep, 0), 0);
eq("toc inside nested", F.tocIndexAt(prep, 4), 1);
eq("toc at later chapter", F.tocIndexAt(prep, 6), 2);
eq("toc title", F.tocTitleAt(prep, 3), "One point five");
(() => {
  // A contents list that is not in reading order, with two entries on one block.
  const p = F.prepareBook({ blocks: book.blocks, toc: [
    { t: "Appendix", d: 0, b: 6 }, { t: "Part", d: 0, b: 1 }, { t: "Chapter", d: 1, b: 1 }, { t: "Broken", d: 0 }
  ] });
  eq("out-of-order toc uses reading order", F.tocIndexAt(p, 2), 2);
  eq("out-of-order toc later", F.tocIndexAt(p, 6), 0);
  eq("entries without a block are ignored", p.tocOrder.length, 3);
})();
eq("next chapter", F.chapterTarget(prep, 0, 1), 3);
eq("next chapter from middle", F.chapterTarget(prep, 4, 1), 5);
eq("next chapter at last stays", F.chapterTarget(prep, 6, 1), 6);
eq("prev chapter from middle goes to its start", F.chapterTarget(prep, 4, -1), 3);
eq("prev chapter from a start goes back one", F.chapterTarget(prep, 3, -1), 0);
eq("prev chapter at top", F.chapterTarget(prep, 0, -1), 0);
eq("chapter target without toc uses sections", F.chapterTarget(F.prepareBook({ blocks: book.blocks, sections: [0, 5] }), 1, 1), 5);

const toc = [
  { t: "Front", d: 0, b: 0 },
  { t: "Part One", d: 0, b: 1 },
  { t: "Chapter 1", d: 1, b: 2 },
  { t: "Section 1.1", d: 2, b: 3 },
  { t: "Chapter 2", d: 1, b: 4 },
  { t: "Part Two", d: 0, b: 5 },
  { t: "Chapter 3", d: 1, b: 6 },
  { t: "Back", d: 0, b: 7 }
];
eq("toc parent", [F.tocParent(toc, 0), F.tocParent(toc, 3), F.tocParent(toc, 4), F.tocParent(toc, 6)], [-1, 2, 1, 5]);
eq("toc children", [F.tocHasChildren(toc, 1), F.tocHasChildren(toc, 3), F.tocHasChildren(toc, 7)], [true, false, false]);
eq("rows unfolded", F.tocRows(toc, {}, "", 3).map((r) => r.index), [0, 1, 2, 3, 4, 5, 6, 7]);
eq("rows with one branch folded", F.tocRows(toc, { 1: true }, "", 3).map((r) => r.index), [0, 1, 5, 6, 7]);
eq("rows with nested fold", F.tocRows(toc, { 2: true }, "", 3).map((r) => r.index), [0, 1, 2, 4, 5, 6, 7]);
eq("row flags", (() => { const r = F.tocRows(toc, { 5: true }, "", 2)[2]; return [r.kids, r.open, r.cur, r.d]; })(), [true, true, true, 1]);
eq("folded row flags", (() => { const r = F.tocRows(toc, { 5: true }, "", 2)[5]; return [r.index, r.kids, r.open]; })(), [5, true, false]);
eq("filter flattens with ancestor path", F.tocRows(toc, { 1: true }, "chap 1", -1).map((r) => [r.index, r.d, r.path]), [[2, 0, "Part One"]]);
eq("filter is case-insensitive and multi-term", F.tocRows(toc, {}, "SECTION 1.1", -1).map((r) => r.path), ["Part One › Chapter 1"]);
eq("row of toc index", F.rowOfTocIndex(F.tocRows(toc, { 1: true }, "", 0), 5), 2);
eq("row of hidden toc index", F.rowOfTocIndex(F.tocRows(toc, { 1: true }, "", 0), 3), -1);
eq("short toc starts unfolded", F.tocDefaultCollapsed(toc, 3), {});
(() => {
  const big = [];
  for (let part = 0; part < 12; part++) {
    big.push({ t: "Book " + part, d: 0, b: part * 10 });
    for (let ch = 0; ch < 5; ch++) big.push({ t: "Chapter " + ch, d: 1, b: part * 10 + ch + 1 });
  }
  const current = 6 * 3 + 2; // a chapter inside the fourth book
  const collapsed = F.tocDefaultCollapsed(big, current);
  ok("long toc folds other branches", collapsed[0] === true && collapsed[6] === true);
  ok("long toc keeps the current branch open", !(18 in collapsed));
  const rows = F.tocRows(big, collapsed, "", current);
  eq("long toc row count", rows.length, 12 + 5);
  ok("current entry is visible", F.rowOfTocIndex(rows, current) >= 0);
})();

// ---- library
(() => {
  const books = [
    { key: "a", title: "The Zebra", author: "Z", path: "/1" },
    { key: "b", title: "An Apple", author: "A", path: "/2" },
    { key: "c", title: "“Quoted”", author: "Q", path: "/3" },
    { key: "d", title: "middle", author: "M", path: "/4" }
  ];
  const state = { books: { d: { t: 50 }, a: { t: 90 } } };
  eq("recents first then title", F.sortBooks(books, state).map((b) => b.key), ["a", "d", "b", "c"]);
  eq("no state sorts by title ignoring articles", F.sortBooks(books, null).map((b) => b.key), ["b", "d", "c", "a"]);
  eq("sort does not mutate", books.map((b) => b.key), ["a", "b", "c", "d"]);
  eq("filter by title", F.filterBooks(books, "zeb").map((b) => b.key), ["a"]);
  eq("filter by author and title terms", F.filterBooks(books, "q quoted").map((b) => b.key), ["c"]);
  eq("empty filter keeps all", F.filterBooks(books, "  ").length, 4);
  eq("no match", F.filterBooks(books, "xyz").length, 0);
})();
eq("percent labels", [F.percentLabel(0), F.percentLabel(0.004), F.percentLabel(0.5), F.percentLabel(0.996), F.percentLabel(1)], ["0%", "1%", "50%", "99%", "100%"]);
eq("grid right", F.gridMove(0, 10, 4, 1, 0), 1);
eq("grid right at row end stays", F.gridMove(3, 10, 4, 1, 0), 3);
eq("grid left at row start stays", F.gridMove(4, 10, 4, -1, 0), 4);
eq("grid down", F.gridMove(1, 10, 4, 0, 1), 5);
eq("grid down into short last row lands on last", F.gridMove(7, 10, 4, 0, 1), 9);
eq("grid down from last row stays", F.gridMove(9, 10, 4, 0, 1), 9);
eq("grid up from first row stays", F.gridMove(2, 10, 4, 0, -1), 2);
eq("grid on empty", F.gridMove(0, 0, 4, 1, 0), -1);

// ---- the desktop
eq("text for the clipboard is plain", F.cleanCopy("one\u2028two so\u00adft no\u00a0break thin\u202fspace  \n\n\n\nnext "),
   "one\ntwo soft no break thin space\n\nnext");
eq("a short selection is copied whole", F.clipboardText("  hello  "), { text: "hello", shortened: false });
(function() {
  const long = ("word ".repeat(999) + "\n\n").repeat(40);
  const clip = F.clipboardText(long);
  ok("a very long selection is cut short and says so", clip.shortened && F.utf8Length(clip.text) <= F.CLIPBOARD_BYTES);
  ok("and is cut between paragraphs", F.cleanCopy(long).indexOf(clip.text + "\n\n") === 0);
  const wide = "\u{1F4D6}".repeat(40000);
  const cut = F.clipboardText(wide);
  ok("never through the middle of a character", cut.shortened && cut.text.length % 2 === 0 && F.utf8Length(cut.text) <= F.CLIPBOARD_BYTES);
})();
eq("utf-8 lengths", [F.utf8Length("abc"), F.utf8Length("é"), F.utf8Length("書"), F.utf8Length("\u{1F4D6}")], [3, 2, 3, 4]);
eq("the folder of most books", F.libraryFolder(
  [{ path: "/h/Documents/EPUB/a.epub" }, { path: "/h/Documents/EPUB/sub/b.epub" }, { path: "/h/Books/c.epub" }],
  ["/h/Books", "/h/Documents/EPUB"], "", "/h"), "/h/Documents/EPUB");
eq("a chosen folder wins", F.libraryFolder([{ path: "/x/a.epub" }], ["/x"], "~/Library", "/h"), "/h/Library");
eq("with nothing found, where a first book would go", F.libraryFolder([], [], "", "/h"), "/h/Books");
eq("a folder is not mistaken for its neighbour", F.libraryFolder(
  [{ path: "/h/Books2/a.epub" }, { path: "/h/Books2/b.epub" }, { path: "/h/Books/c.epub" }], ["/h/Books", "/h/Books2"], "", "/h"), "/h/Books2");
eq("home as a tilde", [F.tildePath("/h/Documents/EPUB", "/h"), F.tildePath("/h", "/h"), F.tildePath("/data", "/h")],
   ["~/Documents/EPUB", "~", "/data"]);
ok("every free library is an https address with a name", F.FREE_LIBRARIES.length === 3
   && F.FREE_LIBRARIES.every((site) => /^https:\/\/[a-z.]+\/$/.test(site.url) && site.name && site.note));

// ---- selection
eq("a range is put in reading order", F.orderRange({ b: 5, o: 2 }, { b: 3, o: 9 }), { sb: 3, so: 9, eb: 5, eo: 2 });
eq("backwards within a block", F.orderRange({ b: 3, o: 9 }, { b: 3, o: 4 }), { sb: 3, so: 4, eb: 3, eo: 9 });
eq("nothing selected", F.orderRange({ b: 3, o: 9 }, { b: 3, o: 9 }), null);
(function() {
  const range = { sb: 3, so: 9, eb: 5, eo: 2 };
  eq("a block's part of a selection", [F.sliceFor(range, 2), F.sliceFor(range, 3), F.sliceFor(range, 4), F.sliceFor(range, 5), F.sliceFor(range, 6)],
     [null, { from: 9, to: -1 }, { from: 0, to: -1 }, { from: 0, to: 2 }, null]);
  eq("a selection ending at a block's start leaves it out", F.sliceFor({ sb: 1, so: 0, eb: 2, eo: 0 }, 2), null);
  const blocks = [
    { k: "p", t: "First paragraph here." },
    { k: "img", src: "/x.png", w: 10, h: 10 },
    { k: "p", f: 1, t: "A <i>styled</i> one &amp; more<br>second line" },
    { k: "tbl", rows: [["a", "<b>b</b>"], ["c", "d"]] },
    { k: "li", m: "1.", t: "An item" },
    { k: "hr" },
    { k: "p", t: "Last." }
  ];
  eq("words across blocks, a blank line between paragraphs",
     F.selectionText(blocks, { sb: 0, so: 6, eb: 2, eo: 8 }), "paragraph here.\n\nA styled");
  eq("a line break inside a block is a newline", F.selectionText(blocks, { sb: 2, so: 0, eb: 2, eo: 99 }), "A styled one & more\nsecond line");
  eq("a table's cells, a list item without its marker, no rule",
     F.selectionText(blocks, { sb: 2, so: 20, eb: 6, eo: 5 }), "second line\n\na\tb\nc\td\n\nAn item\n\nLast.");
  eq("a table at the edge counts only when covered",
     [F.selectionText(blocks, { sb: 3, so: 1, eb: 4, eo: 2 }), F.selectionText(blocks, { sb: 2, so: 20, eb: 3, eo: 0 })],
     ["An", "second line"]);
  eq("nothing from nothing", [F.selectionText(blocks, null), F.selectionText(blocks, { sb: 1, so: 0, eb: 1, eo: 1 })], ["", ""]);
})();

// ---- rich text
ok("the converter's own tags are safe", F.markupIsSafe('a <b>b</b> <i>i</i> <u>u</u> <s>s</s> <a href="b:3">x</a><br>y &amp; z'));
ok("a picture is not", !F.markupIsSafe('<img src="http://example.org/x.png">'));
ok("nor a tag with anything extra", !F.markupIsSafe('<a href="b:1" onclick="x">y</a>') && !F.markupIsSafe("<style>p{}</style>")
   && !F.markupIsSafe("<B>x</B>") && !F.markupIsSafe("<br/>") && !F.markupIsSafe('<a href="a"b">'));
ok("nor a tag that never closes", !F.markupIsSafe("fine until <img src=x"));
eq("styled text keeps its tags and its links take the reader's colour",
   F.richText({ k: "p", f: 1, t: 'see <a href="b:3">there</a>' }, 26, "#aa5500"),
   '<p style="margin:0; white-space:pre-wrap; line-height:26px">see <a style="color:#aa5500" href="b:3">there</a></p>');
eq("plain text is escaped, its line breaks kept",
   F.richText({ k: "p", t: "1 < 2 & 3 > 2\nnext" }, 20.4, "#000"),
   '<p style="margin:0; white-space:pre-wrap; line-height:20px">1 &lt; 2 &amp; 3 &gt; 2<br>next</p>');
eq("text with a tag the converter never writes is shown as it stands",
   F.richText({ k: "p", f: 1, t: 'x <img src="y"> z' }, 20, "#000"),
   '<p style="margin:0; white-space:pre-wrap; line-height:20px">x &lt;img src="y"&gt; z</p>');
eq("preformatted text is never taken for markup",
   F.richText({ k: "pre", f: 1, t: "if (a < b) <b>" }, 20, "#000"),
   '<p style="margin:0; white-space:pre-wrap; line-height:20px">if (a &lt; b) &lt;b&gt;</p>');

console.log(fail === 0 ? `\n${pass} passed` : `\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
