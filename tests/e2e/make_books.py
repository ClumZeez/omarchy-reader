#!/usr/bin/env python3
"""Writes the small library the end-to-end scenario reads: make_books.py DIR.

Real files for the real backend: an EPUB 2 with nested contents, a cover and
emphasis that exists only in its stylesheet; an EPUB 3 with a nav document
and no cover; an encrypted book; and a PDF.

`make_books.py --second-edition FILE` writes the first book again with a
preface ahead of its text: the same title, other content.
"""

import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py"))

import epubkit  # noqa: E402

SENTENCE = ("The road went on, as roads do, past the mill and the three poplars, and nobody "
            "who walked it that morning thought to ask where it meant to end. ")
HEAD = '<link rel="stylesheet" type="text/css" href="style.css"/>'
STYLE = ".em { font-style: italic } .strong { font-weight: bold } .note { vertical-align: super; font-size: 0.7em }"


def chapter(number):
    body = ['<h1 id="top">Chapter %d</h1>' % number]
    for paragraph in range(1, 31):
        text = "Chapter %d, paragraph %d. " % (number, paragraph) + SENTENCE * (1 + paragraph % 3)
        if paragraph == 2:
            text += ('A word with <span class="em">emphasis</span>, one in <span class="strong">bold</span>'
                     ', and a note<a href="notes.xhtml#n%d"><span class="note">%d</span></a>.' % (number, number))
        body.append("<p>%s</p>" % text)
    return epubkit.xhtml("".join(body), "Chapter %d" % number, HEAD)


def long_walk(directory, name="The Long Walk.epub", preface=False):
    docs = [("ch%02d.xhtml" % number, chapter(number)) for number in range(1, 13)]
    if preface:
        words = "".join("<p>Preface, paragraph %d. %s</p>" % (n, SENTENCE) for n in range(1, 10))
        docs.insert(0, ("preface.xhtml", epubkit.xhtml("<h1>Preface</h1>" + words, "Preface", HEAD)))
    notes = "".join('<p id="n%d">%d. A note to chapter %d. <a href="ch%02d.xhtml#top">Back</a></p>'
                    % (number, number, number, number) for number in range(1, 13))
    docs.append(("notes.xhtml", epubkit.xhtml("<h1>Notes</h1>" + notes, "Notes", HEAD)))
    points = [
        ("Part One", "ch01.xhtml", [("Chapter %d" % n, "ch%02d.xhtml" % n) for n in range(1, 7)]),
        ("Part Two", "ch07.xhtml", [("Chapter %d" % n, "ch%02d.xhtml" % n) for n in range(7, 13)]),
        ("Notes", "notes.xhtml"),
    ]
    if preface:
        points.insert(0, ("Preface", "preface.xhtml"))
    return epubkit.make(
        directory, name, title="The Long Walk", creators=("Walker, Ann",),
        docs=docs, raw_docs=True, ncx_points=points,
        metadata_extra='<meta name="cover" content="cover-image"/>',
        manifest_extra='<item id="cover-image" href="cover.png" media-type="image/png"/>'
                       '<item id="css" href="style.css" media-type="text/css"/>',
        files={"cover.png": epubkit.png(120, 180, 90), "style.css": STYLE})


def short_notes(directory):
    docs = (("a.xhtml", "<h1>First Note</h1><p>Short.</p>"),
            ("b.xhtml", "<h1>Second Note</h1><p>Shorter.</p>"),
            ("c.xhtml", "<h1>Third Note</h1><p>Shortest.</p>"))
    nav = epubkit.nav('<ol><li><a href="a.xhtml">First Note</a></li><li><a href="b.xhtml">Second Note</a></li>'
                      '<li><a href="c.xhtml">Third Note</a></li></ol>')
    return epubkit.make(directory, "short-notes.epub", title="Short Notes", creators=("B. Brief",),
                        version="3.0", docs=docs, ncx_points=False, nav_text=nav)


def locked(directory):
    # Declared encrypted and really unreadable: a declaration alone is not
    # believed, since books freed of their protection often keep it.
    scrambled = bytes((index * 197 + 13) % 256 for index in range(900))
    protection = epubkit.encryption((epubkit.AES, "OEBPS/ch1.xhtml"), (epubkit.AES, "OEBPS/ch2.xhtml"))
    return epubkit.make(directory, "locked.epub", title="Locked Away", creators=("Some Publisher",),
                        docs=(("ch1.xhtml", scrambled), ("ch2.xhtml", scrambled[::-1])),
                        root_files={"META-INF/encryption.xml": protection})


def main(argv):
    if len(argv) == 3 and argv[1] == "--second-edition":
        long_walk(os.path.dirname(argv[2]), os.path.basename(argv[2]), preface=True)
        return 0
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 64
    directory = argv[1]
    os.makedirs(directory, exist_ok=True)
    long_walk(directory)
    short_notes(directory)
    locked(directory)
    with open(os.path.join(directory, "manual.pdf"), "wb") as handle:
        handle.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
