# Reader

A minimal ebook reader for the Omarchy bar. A library of your ebooks, a table of contents that just works, and it remembers where you were so you can pick up where you last left off.

It can take on your Omarchy theme or for better readability on some themes, there's a separate day/night mode in the plugin as well as font scaling options.


![The library, a book and its contents](preview.png)

Plugin id: `clumzeez.reader`.

## Install

```sh
omarchy plugin add https://github.com/ClumZeez/omarchy-reader.git --enable
```

Move it with `omarchy bar move clumzeez.reader --after <widget-id>`.

Reader needs nothing beyond what Omarchy already ships: the reader is plain
QML and the book converter is Python's standard library.

To open or close Reader from the keyboard, add a line to
`~/.config/hypr/bindings.lua`, with a chord nothing else uses

```lua
o.bind("SUPER + CTRL + G", "Reader", "omarchy-shell shell toggle clumzeez.reader")
```

To take over a chord that is already bound, put `hl.unbind("…")` with the
same chord on the line before.

## Your books

Reader looks in `~/Books`, `~/Documents/Books`, `~/Documents/EPUB`,
`~/Documents/Ebooks` and `~/Calibre Library`, including their subfolders.
To use another folder:

```sh
omarchy bar set clumzeez.reader folder ~/path/to/books
```

Books are read where they are and never modified. Your place in each book
follows the book's content, not its file name: rename the file, move it to
another folder or replace its cover and Reader still opens it where you
stopped. Any jump — a chapter from the contents, a link, the start or the
end — can be undone with `Backspace`, so a slip of the finger never loses
your place.

To open a book that is not in the library:

```sh
omarchy-shell reader open ~/Downloads/some-book.epub
```

## Formats

| Format | |
|---|---|
| EPUB 2 and 3 (`.epub`, `.kepub.epub`) | read in Reader |
| Kindle (`.mobi`, `.azw`, `.azw3`, `.prc`) | read in Reader |
| FictionBook (`.fb2`, `.fb2.zip`, `.fbz`) | read in Reader |
| Plain text and single web pages (`.txt`, `.html`) | read in Reader |
| Comic archives (`.cbz`) | read in Reader, a page per picture |
| PDF and DjVu | listed in the library, opened in your default viewer |

Books with DRM cannot be opened by any reader but the vendor's; Reader says
so instead of showing garbage. Font obfuscation, which many publishers use,
is not DRM and is fine.

Reader shows a book's words, pictures and structure in your shell's own
typeface and colours. It keeps what carries meaning — emphasis, headings,
verse, quotations, lists, tables, notes and their links — and drops the
publisher's fonts, colours and page furniture.

## Where things are kept

| | |
|---|---|
| Your place in each book, and the settings | `~/.local/state/omarchy/settings/reader.json` |
| Covers and converted books (safe to delete) | `~/.cache/omarchy-reader/` |

Nothing is ever written next to your books or inside the plugin. Should the
first file ever become unreadable, Reader sets it aside as
`reader.json.damaged` instead of writing over it.

## Update and remove

```sh
omarchy plugin update clumzeez.reader
omarchy plugin remove clumzeez.reader
```

After an update, restart the shell (`omarchy restart shell`) for the new
version to take effect; until then the popout says so instead of opening.
Removing the plugin leaves the locations above in place; delete them if you
want a clean slate.

## How it works

`Service.qml` runs once per shell and owns everything shared: the saved
state, the library, the open book. `BarWidget.qml` runs once per monitor and
is only a view onto it. Books are converted by `bin/reader`, a Python program
using only the standard library, into a flat list of blocks (paragraph,
heading, picture, …) that a `ListView` draws natively — no web engine. The
conversion happens in a separate process, so a large or broken book can
never stall the bar.

That process works within fixed bounds: two gigabytes of memory, a gigabyte
of pictures a book, 48 MB of converted text. A file built to exhaust the
machine is refused with a plain sentence; no real book comes near any of
them. The cache as a whole is kept under 1.5 GB by dropping the books opened
longest ago, which are simply converted again when next opened.

A reading position is the block at the top of the view, how far through
that block you are, and the block's opening words, so it survives changes to
the text size, the panel width and the converter itself.

Text is drawn by read-only text edits, the one text item that can say which
character is under a point; that is what selecting rests on. Their lines are
a fixed whole number of pixels apart, which is what lets a page be cut
between two of them.

## Licence

MIT. See `LICENSE` and `NOTICE.md`.
