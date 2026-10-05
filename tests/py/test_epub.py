import support

import functools
import os
import re
import struct
import tempfile
import time
import unittest
import zipfile
import zlib
from unittest import mock

import epubkit
from epubkit import AES, jpeg, nav, png, xhtml
from reader import epub, images, pictures
from reader.archive import Archive
from reader.errors import ReaderError

DRM_SENTENCE = "This book is protected by DRM and can't be opened."

SVG_PAGE = ('<div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="100%%" height="100%%" viewBox="0 0 1200 1600"><image width="1200" height="1600" '
            'xlink:href="%s"/></svg></div>')


def clear_png(width, height):
    """A PNG of black ink on a fully transparent ground, as pull-quotes are sometimes drawn."""
    rows = (b"\x00" + b"\x00\x00\x00\x00" * width) * height

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def plain(block):
    text = block.get("t", "")
    if block.get("f"):
        text = re.sub(r"<[^>]*>", "", text.replace("<br>", " "))
        text = text.replace("&#160;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text


class EpubCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = scratch.name
        self.out = os.path.join(self.dir, "out")

    def make(self, **options):
        return epubkit.make(self.dir, **options)

    def convert(self, **options):
        return epub.convert(self.make(**options), self.out)

    def meta(self, **options):
        return epub.read_meta(self.make(**options))

    def texts(self, book):
        return [plain(block) for block in book.blocks if "t" in block]

    def toc(self, book):
        return [(entry["t"], entry["d"], plain(book.blocks[entry["b"]]) or book.blocks[entry["b"]]["k"])
                for entry in book.toc]

    def cover_size(self, **options):
        cover = self.meta(**options).cover
        if cover is None:
            return None
        found = images.sniff(cover)
        return found.width, found.height


class ContainerTest(EpubCase):
    def test_plain_book(self):
        book = self.convert()
        self.assertEqual(self.texts(book), ["One", "alpha", "Two", "beta"])
        self.assertEqual(book.sections, [0, 2])
        self.assertEqual(book.toc, [{"t": "One", "d": 0, "b": 0}, {"t": "Two", "d": 0, "b": 2}])
        self.assertEqual((book.title, book.author, book.language), ("T", "A B", "en"))

    def test_mimetype_is_never_needed(self):
        self.assertEqual(len(self.convert(mimetype=False).blocks), 4)
        path = self.make(mimetype=False, root_files={"zz/mimetype": "application/x-wrong\r\n"})
        self.assertEqual(len(epub.convert(path, self.out).blocks), 4)

    def test_rootfile_spellings(self):
        for rootfiles in (
            '<rootfile full-path="missing/content.opf" media-type="application/oebps-package+xml"/>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>',
            '<rootfile full-path="/oebps/Content.OPF"/>',
            '<rootfile full-path="OEBPS\\content.opf"/>',
            '<rootfile full-path="OEBPS/content%2Eopf"/>',
        ):
            with self.subTest(rootfiles=rootfiles):
                self.assertEqual(self.meta(container=rootfiles, title="Found").title, "Found")

    def test_percent_encoded_package_path(self):
        meta = self.meta(opf_path="OEBPS/my book.opf", title="Spaced",
                         container='<rootfile full-path="OEBPS/my%20book.opf"/>')
        self.assertEqual(meta.title, "Spaced")

    def test_package_preferred_over_other_rootfiles(self):
        rootfiles = ('<rootfile full-path="OEBPS/other.xml" media-type="application/xml"/>'
                     '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>')
        meta = self.meta(container=rootfiles, title="Package", files={"other.xml": "<other/>"})
        self.assertEqual(meta.title, "Package")

    def test_no_container_finds_the_package(self):
        junk = {"__MACOSX/OEBPS/._content.opf": b"\x00\x05\x16\x07junk", ".hidden.opf": "<package/>"}
        book = self.convert(container=False, title="Loose", root_files=junk)
        self.assertEqual(book.title, "Loose")
        self.assertEqual(len(book.blocks), 4)

    def test_broken_container_falls_back_to_any_package(self):
        path = self.make(title="Loose", root_files={"META-INF/container.xml": "no markup here"})
        self.assertEqual(epub.read_meta(path).title, "Loose")

    def test_shallowest_package_wins_without_a_container(self):
        deep = self.make(title="Deep")
        with zipfile.ZipFile(deep) as archive:
            other = archive.read("OEBPS/content.opf").replace(b"Deep", b"Shallow")
        path = self.make(container=False, title="Deep", opf_path="a/b/content.opf",
                         root_files={"a/top.opf": other, "a/ch1.xhtml": xhtml("<p>x</p>"),
                                     "a/ch2.xhtml": xhtml("<p>y</p>")})
        self.assertEqual(epub.read_meta(path).title, "Shallow")

    def test_everything_inside_one_folder(self):
        book = self.convert(prefix="My Book", title="Nested")
        self.assertEqual(book.title, "Nested")
        self.assertEqual(self.texts(book), ["One", "alpha", "Two", "beta"])
        self.assertEqual(len(book.toc), 2)

    def test_no_package_at_all(self):
        members = {"ch10.html": "<p>ten</p>", "ch2.html": "<p>two</p>", "ch1.html": "<p>one</p>",
                   "__MACOSX/._ch1.html": "junk", "notes.txt": "x"}
        path = epubkit.write_zip(os.path.join(self.dir, "Some_Book - A Author.epub"), members)
        book = epub.convert(path, self.out)
        self.assertEqual(self.texts(book), ["one", "two", "ten"])
        self.assertEqual(book.title, "Some Book - A Author")
        self.assertEqual(epub.read_meta(path).authors, [])

    def test_nothing_readable_is_corrupt(self):
        path = epubkit.write_zip(os.path.join(self.dir, "empty.epub"), {"readme.txt": "nothing"})
        for function in (epub.read_meta, lambda p: epub.convert(p, self.out)):
            with self.assertRaises(ReaderError) as caught:
                function(path)
            self.assertEqual(caught.exception.code, "corrupt")
        self.assertIsNone(epub.content_key(path))

    def test_not_a_zip(self):
        path = os.path.join(self.dir, "x.epub")
        with open(path, "wb") as handle:
            handle.write(b"this is not an archive at all")
        with self.assertRaises(ReaderError) as caught:
            epub.read_meta(path)
        self.assertEqual(caught.exception.code, "corrupt")
        self.assertIsNone(epub.content_key(path))

    def test_package_with_byte_order_mark_and_junk(self):
        good = self.make()
        with zipfile.ZipFile(good) as archive:
            opf = archive.read("OEBPS/content.opf")
        path = self.make(name="bom.epub", opf=b"\xef\xbb\xbf\n" + opf)
        self.assertEqual(len(epub.convert(path, self.out).blocks), 4)


class MetadataTest(EpubCase):
    def test_main_title_when_marked(self):
        metadata = ('<dc:title id="t1">A Subtitle</dc:title><dc:title id="t2">The Real One</dc:title>'
                    '<meta refines="#t2" property="title-type">main</meta>'
                    '<meta refines="#t1" property="title-type">subtitle</meta>')
        self.assertEqual(self.meta(metadata=metadata, version="3.0").title, "The Real One")

    def test_first_title_that_says_something(self):
        metadata = "<dc:title>  </dc:title><dc:title/><dc:title>Real\n   Title</dc:title><dc:title>Sub</dc:title>"
        self.assertEqual(self.meta(metadata=metadata).title, "Real Title")

    def test_soft_hyphens_are_not_part_of_a_name(self):
        metadata = "<dc:title>Donau&#173;dampf&#173;schiff</dc:title><dc:creator>Mül&#173;ler, Eva</dc:creator>"
        meta = self.meta(metadata=metadata)
        self.assertEqual((meta.title, meta.authors), ("Donaudampfschiff", ["Müller, Eva"]))
        docs = (("ch1.xhtml", "<h1>Ers&#173;tes</h1><p>Schiff&#173;fahrt</p>"), epubkit.DEFAULT_DOCS[1],
                ("ch3.xhtml", "<h1>Three</h1><p>c</p>"))
        book = self.convert(metadata=metadata, docs=docs,
                            ncx_points=[("Ers&#173;tes", "ch1.xhtml"), ("Two", "ch2.xhtml"), ("Three", "ch3.xhtml")])
        self.assertEqual((book.title, book.author), ("Donaudampfschiff", "Eva Müller"))
        self.assertEqual(book.toc[0]["t"], "Erstes")
        self.assertEqual([block["t"] for block in book.blocks[:2]], ["Ers\u00adtes", "Schiff\u00adfahrt"])

    def test_title_from_the_file_name(self):
        meta = epub.read_meta(self.make(name="Some_Book  - A Author.epub", metadata="<dc:title> </dc:title>"))
        self.assertEqual(meta.title, "Some Book - A Author")
        self.assertEqual(meta.authors, [])

    def test_entities_in_metadata(self):
        meta = self.meta(metadata="<dc:title>Tom &amp; Jerry&nbsp;II</dc:title><dc:creator>Caf&eacute; X</dc:creator>")
        self.assertEqual(meta.title, "Tom & Jerry II")
        self.assertEqual(meta.authors, ["Café X"])

    def test_old_package_vocabulary(self):
        opf = ('\n<?xml version="1.0"?><package unique-identifier="id"><metadata>'
               '<dc-metadata xmlns:dc="http://purl.org/dc/elements/1.0/"><dc:Title>T &amp; U</dc:Title>'
               '<dc:Creator role="aut" file-as="B, A">A B</dc:Creator><dc:Language>en_US</dc:Language>'
               '</dc-metadata></metadata><manifest><item id="c1" href="ch1.xhtml" media-type="text/x-oeb1-document">'
               '</manifest><spine><itemref idref="c1"></spine></package>')
        meta = self.meta(opf=opf)
        self.assertEqual((meta.title, meta.authors, meta.language), ("T & U", ["A B"], "en-us"))

    def test_undeclared_prefixes(self):
        opf = ('<package version="2.0"><metadata><dc:title>Loose</dc:title>'
               '<dc:creator opf:role="aut">Ann Author</dc:creator></metadata><manifest>'
               '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
               '<spine><itemref idref="c1"/></spine></package>')
        meta = self.meta(opf=opf)
        self.assertEqual((meta.title, meta.authors), ("Loose", ["Ann Author"]))

    def test_authors_by_role(self):
        metadata = ('<dc:title>T</dc:title><dc:creator opf:role="ill">Ivy Illustrator</dc:creator>'
                    '<dc:creator opf:role="aut">Ann Author</dc:creator><dc:creator>No Role</dc:creator>'
                    '<dc:creator opf:role="pbl">Pub</dc:creator><dc:creator opf:role="aut">Ann Author</dc:creator>')
        self.assertEqual(self.meta(metadata=metadata).authors, ["Ann Author", "No Role"])

    def test_all_creators_when_none_is_an_author(self):
        metadata = ('<dc:title>T</dc:title><dc:creator opf:role="edt">Ed Itor</dc:creator>'
                    '<dc:creator opf:role="trl">Trans Lator</dc:creator>')
        self.assertEqual(self.meta(metadata=metadata).authors, ["Ed Itor", "Trans Lator"])

    def test_refined_roles(self):
        metadata = ('<dc:title>T</dc:title><dc:creator id="a">Ed Itor</dc:creator><dc:creator id="b">Ann Author</dc:creator>'
                    '<meta refines="#a" property="role" scheme="marc:relators">edt</meta>'
                    '<meta refines="#b" property="role" scheme="marc:relators">aut</meta>')
        self.assertEqual(self.meta(metadata=metadata, version="3.0").authors, ["Ann Author"])

    def test_several_names_in_one_creator(self):
        metadata = "<dc:title>T</dc:title><dc:creator>Neil Gaiman &amp; Terry Pratchett; Third Person</dc:creator>"
        self.assertEqual(self.meta(metadata=metadata).authors, ["Neil Gaiman", "Terry Pratchett", "Third Person"])

    def test_author_line_of_the_book(self):
        book = self.convert(creators=("Wells, H. G.", "Carroll, Lewis"))
        self.assertEqual(book.author, "H. G. Wells, Lewis Carroll")
        self.assertEqual(book.authors, ["Wells, H. G.", "Carroll, Lewis"])

    def test_language(self):
        self.assertEqual(self.meta(language="en_GB").language, "en-gb")
        self.assertEqual(self.meta(language=None).language, "")
        self.assertEqual(self.meta(language="UND").language, "")


class ManifestTest(EpubCase):
    def body(self, manifest, files, **options):
        return self.convert(manifest=manifest, files=files, docs=(), ncx_points=False, **options)

    def test_href_spellings(self):
        files = {"Text/Chapter 1.xhtml": xhtml("<p>one</p>"), "Text/b.xhtml": xhtml("<p>two</p>"),
                 "c.xhtml": xhtml("<p>three</p>"), "Text/D.XHTML": xhtml("<p>four</p>"),
                 "e.xhtml": xhtml("<p>five</p>"), "100%.xhtml": xhtml("<p>six</p>")}
        manifest = ('<item id="c1" href="Text/Chapter%201.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="c2" href="Text\\b.xhtml#top" media-type="application/xhtml+xml"/>'
                    '<item id="c3" href="/OEBPS/c.xhtml?v=2" media-type="application/xhtml+xml"/>'
                    '<item id="c4" href="text/d.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="c5" href="e.xhtml"/>'
                    '<item id="c6" href="100%.xhtml" media-type="application/xhtml+xml"/>')
        book = self.body(manifest, files, spine=["c1", "c2", "c3", "c4", "c5", "c6"])
        self.assertEqual(self.texts(book), ["one", "two", "three", "four", "five", "six"])

    def test_documents_above_the_package_folder(self):
        book = self.convert(opf_path="OPS/pkg/content.opf",
                            docs=(("../Text/ch1.xhtml", '<p>one <a href="ch2.xhtml#x">go</a></p>'),
                                  ("../Text/ch2.xhtml", '<p>pad</p><p id="x">two</p>')),
                            ncx_points=[("One", "../Text/ch1.xhtml"), ("Two", "../Text/ch2.xhtml#x")])
        self.assertEqual(self.texts(book), ["one go", "pad", "two"])
        self.assertIn('href="b:2"', book.blocks[0]["t"])
        self.assertEqual([entry["b"] for entry in book.toc], [0, 2])

    def test_duplicate_ids_first_wins(self):
        manifest = ('<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="c1" href="other.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>')
        book = self.convert(manifest=manifest, files={"other.xhtml": xhtml("<p>other</p>")},
                            spine=["c1", "c1", "c2"], ncx_points=False)
        self.assertEqual(self.texts(book), ["One", "alpha", "Two", "beta"])

    def test_media_type_is_inferred(self):
        files = {"page": xhtml("<p>typeless</p>"), "pic": png(80, 90), "blob": b"\x00\x01\x02binary",
                 "plain.html": xhtml("<p>mislabelled</p>")}
        manifest = ('<item id="a" href="page"/><item id="b" href="pic"/><item id="c" href="blob"/>'
                    '<item id="d" href="plain.html" media-type="text/plain"/>')
        book = self.body(manifest, files, spine=["a", "b", "c", "d"])
        self.assertEqual([block["k"] for block in book.blocks], ["p", "img", "p"])
        self.assertEqual(self.texts(book), ["typeless", "mislabelled"])


class SpineTest(EpubCase):
    def test_dangling_duplicate_and_junk_itemrefs(self):
        manifest_extra = ('<item id="pt" href="page-template.xpgt" media-type="application/vnd.adobe-page-template+xml"/>'
                          '<item id="css" href="style.css" media-type="text/css"/>'
                          '<item id="gone" href="gone.xhtml" media-type="application/xhtml+xml"/>')
        book = self.convert(spine=["ncx", "c1", "ghost", "pt", "css", "c1", "gone", "c2", "c1"],
                            manifest_extra=manifest_extra,
                            files={"page-template.xpgt": "<ade:template/>", "style.css": "p{}"})
        self.assertEqual(self.texts(book), ["One", "alpha", "Two", "beta"])
        self.assertEqual(book.sections, [0, 2])

    def test_a_document_without_blocks_adds_no_section(self):
        docs = (("ch1.xhtml", "<p>one</p>"), ("empty.xhtml", '<img src="gone.jpg" alt=""/>'), ("ch3.xhtml", "<p>three</p>"))
        book = self.convert(docs=docs)
        self.assertEqual(self.texts(book), ["one", "three"])
        self.assertEqual(book.sections, [0, 1])

    def test_image_and_svg_spine_items(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200"><rect width="9" height="9"/></svg>'
        manifest = ('<item id="p1" href="p1.jpg" media-type="image/jpeg"/>'
                    '<item id="p2" href="p2.svg" media-type="image/svg+xml"/>'
                    '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>')
        book = self.convert(manifest=manifest, spine=["p1", "p2", "c1"], ncx_points=False,
                            files={"p1.jpg": jpeg(600, 900), "p2.svg": svg})
        self.assertEqual([block["k"] for block in book.blocks], ["img", "img", "h", "p"])
        self.assertEqual((book.blocks[0]["w"], book.blocks[0]["h"]), (600, 900))
        self.assertTrue(book.blocks[0]["src"].endswith(".jpg"))
        self.assertTrue(book.blocks[1]["src"].endswith(".svg"))
        self.assertEqual(book.sections, [0, 1, 2])
        self.assertEqual(self.cover_size(manifest=manifest, spine=["p1", "p2", "c1"], ncx_points=False,
                                         files={"p1.jpg": jpeg(600, 900), "p2.svg": svg}), (600, 900))

    def test_non_linear_items_go_last_and_stay_reachable(self):
        docs = (("ch1.xhtml", '<p>one <a href="notes.xhtml#n1">1</a></p>'),
                ("notes.xhtml", '<p>filler</p><p id="n1">the note</p>'),
                ("ch2.xhtml", "<p>two</p>"), ("extra.xhtml", "<p>extra</p>"), ("more.xhtml", "<p>more</p>"))
        spine = ('<itemref idref="c1"/><itemref idref="c2" linear="no"/><itemref idref="c3"/>'
                 '<itemref idref="c4" linear="No"/><itemref idref="c5" linear="false"/>')
        book = self.convert(docs=docs, spine=spine,
                            ncx_points=[("One", "ch1.xhtml"), ("Two", "ch2.xhtml"), ("Notes", "notes.xhtml#n1")])
        self.assertEqual(self.texts(book), ["one 1", "two", "more", "filler", "the note", "extra"])
        self.assertIn('href="b:4"', book.blocks[0]["t"])
        self.assertEqual(book.toc[-1], {"t": "Notes", "d": 0, "b": 4})

    def test_non_linear_cover_page_stays_first(self):
        docs = (("cover.xhtml", '<img src="cover.jpg" alt="cover"/>'), ("ch1.xhtml", "<p>one</p>"))
        for guide in ('<reference type="cover" href="cover.xhtml"/>', ""):
            with self.subTest(guide=guide):
                book = self.convert(docs=docs, guide=guide, files={"cover.jpg": jpeg(600, 900)},
                                    spine='<itemref idref="c1" linear="no"/><itemref idref="c2"/>')
                self.assertEqual([block["k"] for block in book.blocks], ["img", "p"])

    def test_only_the_opening_item_is_kept_in_place_when_non_linear(self):
        docs = (("a.xhtml", "<p>a</p>"), ("b.xhtml", "<p>b</p>"), ("c.xhtml", "<p>c</p>"))
        book = self.convert(docs=docs, spine='<itemref idref="c1" linear="no"/>'
                                             '<itemref idref="c2" linear="no"/><itemref idref="c3"/>')
        self.assertEqual(self.texts(book), ["a", "c", "b"])

    def test_linked_document_outside_the_spine_follows_the_book(self):
        docs = (("ch1.xhtml", '<h1>One</h1><p>A claim.<a href="notes.xhtml#n1">1</a></p>'),
                ("ch2.xhtml", '<h1>Two</h1><p>beta <a href="nav.xhtml">contents</a> <a href="loose.xhtml">x</a></p>'))
        options = dict(
            docs=docs, nav_text=nav("<ol><li><a href='ch1.xhtml'>One</a></li></ol>"),
            manifest_extra='<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>'
                           '<item id="more" href="more.xhtml" media-type="application/xhtml+xml"/>'
                           '<item id="idle" href="idle.xhtml" media-type="application/xhtml+xml"/>',
            files={"notes.xhtml": xhtml('<h1>Notes</h1><p id="n1">1. The source. <a href="more.xhtml">More</a></p>'),
                   "more.xhtml": xhtml("<p>further reading</p>"), "idle.xhtml": xhtml("<p>never linked</p>"),
                   "loose.xhtml": xhtml("<p>not in the manifest</p>")},
            ncx_points=[("One", "ch1.xhtml"), ("Two", "ch2.xhtml"), ("Notes", "notes.xhtml")])
        book = self.convert(**options)
        self.assertEqual(self.texts(book), ["One", "A claim.1", "Two", "beta contents x",
                                            "Notes", "1. The source. More", "further reading"])
        self.assertIn('href="b:5"', book.blocks[1]["t"])
        self.assertIn('href="b:6"', book.blocks[5]["t"])
        self.assertEqual(book.toc[-1], {"t": "Notes", "d": 0, "b": 4})
        self.assertEqual(book.sections, [0, 2, 4, 6])
        path = os.path.join(self.dir, "book.epub")
        self.assertEqual(epub.content_key(path), epub.content_key(self.make(name="plain.epub", docs=docs)))

    def test_document_outside_the_spine_named_only_by_the_contents(self):
        book = self.convert(manifest_extra='<item id="end" href="end.xhtml" media-type="application/xhtml+xml"/>',
                            files={"end.xhtml": xhtml("<h1>Afterword</h1><p>omega</p>")},
                            ncx_points=[("One", "ch1.xhtml"), ("Two", "ch2.xhtml"), ("Afterword", "end.xhtml")])
        self.assertEqual(self.texts(book), ["One", "alpha", "Two", "beta", "Afterword", "omega"])
        self.assertEqual(book.toc[-1], {"t": "Afterword", "d": 0, "b": 4})

    def test_missing_spine_uses_the_manifest(self):
        book = self.convert(spine="", nav_text=nav("<ol><li><a href='ch1.xhtml'>One</a></li></ol>"))
        self.assertEqual(self.texts(book), ["One", "alpha", "Two", "beta"])

    def test_no_spine_no_manifest_uses_the_archive(self):
        opf = "<package><metadata><dc:title>Bare</dc:title></metadata></package>"
        book = self.convert(opf=opf, docs=(("b10.html", "<p>ten</p>"), ("b9.html", "<p>nine</p>")))
        self.assertEqual(book.title, "Bare")
        self.assertEqual(self.texts(book), ["nine", "ten"])

    def test_many_documents_share_one_stylesheet(self):
        count = 400
        head = '<link rel="stylesheet" type="text/css" href="../Styles/main.css"/>'
        docs = tuple(("Text/c%d.xhtml" % index,
                      xhtml('<p class="i">chapter %d</p><p><a href="c%d.xhtml">next</a></p>'
                            % (index, (index + 1) % count), head=head))
                     for index in range(count))
        path = self.make(docs=docs, raw_docs=True, files={"Styles/main.css": ".i{font-style:italic}"},
                         ncx_points=[("C%d" % index, "Text/c%d.xhtml" % index) for index in range(count)])
        reads = []
        original = Archive.read

        def counting(archive, name, *args, **kwargs):
            reads.append(name)
            return original(archive, name, *args, **kwargs)

        started = time.perf_counter()
        with mock.patch.object(Archive, "read", counting):
            book = epub.convert(path, self.out)
        self.assertLess(time.perf_counter() - started, 5.0)
        self.assertEqual(reads.count("OEBPS/Styles/main.css"), 1)
        self.assertEqual(len(reads), len(set(reads)))
        self.assertEqual(len(book.sections), count)
        self.assertEqual(len(book.toc), count)
        self.assertEqual(book.blocks[0], {"k": "p", "f": 1, "t": "<i>chapter 0</i>", "s": 2})
        self.assertEqual(book.blocks[1]["t"], '<a href="b:2">next</a>')
        self.assertEqual(book.blocks[-1]["t"], '<a href="b:0">next</a>')


class ProtectionTest(EpubCase):
    def locked(self, *members, root_files=None, **options):
        declared = {"META-INF/encryption.xml": epubkit.encryption(*members)}
        return self.make(root_files=dict(declared, **(root_files or {})), **options)

    def test_font_obfuscation_is_not_protection(self):
        for algorithm in (epubkit.IDPF_OBFUSCATION, epubkit.ADOBE_OBFUSCATION):
            with self.subTest(algorithm=algorithm):
                path = self.locked((algorithm, "OEBPS/fonts/f.otf"), files={"fonts/f.otf": b"\x12\x34" * 40})
                self.assertEqual(epub.read_meta(path).error, "")
                self.assertEqual(len(epub.convert(path, self.out).blocks), 4)

    def test_encrypted_documents_are_refused(self):
        noise = bytes(range(256)) * 16
        path = self.locked((AES, "OEBPS/ch1.xhtml"), (AES, "OEBPS/ch2.xhtml"), title="Locked Book",
                           docs=(("ch1.xhtml", noise), ("ch2.xhtml", noise)),
                           manifest_extra='<item id="cover" href="cover.jpg" media-type="image/jpeg"/>',
                           metadata_extra='<meta name="cover" content="cover"/>',
                           files={"cover.jpg": jpeg(300, 400)},
                           root_files={"META-INF/rights.xml": "<adept:rights xmlns:adept='http://ns.adobe.com/adept'/>"})
        with self.assertRaises(ReaderError) as caught:
            epub.convert(path, self.out)
        self.assertEqual((caught.exception.code, caught.exception.message), ("drm", DRM_SENTENCE))
        meta = epub.read_meta(path)
        self.assertEqual((meta.title, meta.error), ("Locked Book", DRM_SENTENCE))
        self.assertIsNotNone(meta.cover)
        self.assertEqual(meta.key, epub.content_key(path))
        self.assertRegex(meta.key, r"^[0-9a-f]{20}$")

    def test_percent_encoded_cipher_reference(self):
        path = self.locked((AES, "OEBPS/my%20chapter.xhtml"), docs=(("my chapter.xhtml", b"\x9c\x01\xfe" * 500),),
                           ncx_points=False)
        with self.assertRaises(ReaderError) as caught:
            epub.convert(path, self.out)
        self.assertEqual(caught.exception.code, "drm")

    def test_encrypted_book_inside_a_folder(self):
        noise = bytes(range(256)) * 16
        path = self.make(prefix="Book", docs=(("ch1.xhtml", noise),), ncx_points=False,
                         root_files={"META-INF/encryption.xml": epubkit.encryption((AES, "OEBPS/ch1.xhtml"))})
        self.assertEqual(epub.read_meta(path).error, DRM_SENTENCE)

    def test_stale_declaration_over_readable_documents(self):
        path = self.locked((AES, "OEBPS/ch1.xhtml"), (AES, "OEBPS/ch2.xhtml"))
        self.assertEqual(epub.read_meta(path).error, "")
        self.assertEqual(len(epub.convert(path, self.out).blocks), 4)

    def test_encrypted_picture_is_only_missing(self):
        docs = (("ch1.xhtml", '<p>before</p><img src="pic.jpg" alt=""/><p>after</p>'),)
        path = self.locked((AES, "OEBPS/pic.jpg"), docs=docs, files={"pic.jpg": b"\x8a\x11\x93" * 300})
        book = epub.convert(path, self.out)
        self.assertEqual([block["k"] for block in book.blocks], ["p", "p"])

    def test_leftovers_are_not_protection(self):
        path = self.make(metadata_extra='<meta name="Adept.expected.resource" content="urn:uuid:1"/>'
                                        '<meta name="Adept.resource" content="urn:uuid:1"/>',
                         root_files={"META-INF/rights.xml": "<rights/>"},
                         files={"fonts/a.dat": b"\x67\xd5\x7e\x78" * 30})
        self.assertEqual(epub.read_meta(path).error, "")
        self.assertEqual(len(epub.convert(path, self.out).blocks), 4)


class CoverTest(EpubCase):
    def test_cover_image_property(self):
        extra = ('<item id="x" href="img/other.png" media-type="image/png"/>'
                 '<item id="ci" href="img/c.png" media-type="image/png" properties="svg cover-image"/>')
        size = self.cover_size(manifest_extra=extra, files={"img/c.png": png(100, 150), "img/other.png": png(90, 90)})
        self.assertEqual(size, (100, 150))

    def test_cover_meta_as_id(self):
        size = self.cover_size(manifest_extra='<item id="image_rsrc1.jpg" href="images/front.jpg" media-type="image/jpeg"/>',
                               metadata_extra='<meta name="cover" content="image_rsrc1.jpg"/>',
                               files={"images/front.jpg": jpeg(600, 900), "image_rsrc1.jpg": jpeg(70, 70)})
        self.assertEqual(size, (600, 900))

    def test_cover_meta_as_href(self):
        size = self.cover_size(manifest_extra='<item id="img1" href="images/front.jpg" media-type="image/jpeg"/>',
                               metadata_extra='<meta content="images/front.jpg" name="cover"/>',
                               files={"images/front.jpg": jpeg(600, 900)})
        self.assertEqual(size, (600, 900))

    def test_cover_meta_naming_a_page(self):
        size = self.cover_size(manifest_extra='<item id="coverpage" href="front.xhtml" media-type="application/xhtml+xml"/>',
                               metadata_extra='<meta name="cover" content="coverpage"/>',
                               files={"front.xhtml": xhtml('<p><img src="images/front.jpg"/></p>'),
                                      "images/front.jpg": jpeg(640, 960)})
        self.assertEqual(size, (640, 960))

    def test_guide_page_wrapping_the_image_in_svg(self):
        docs = (("titlepage.xhtml", SVG_PAGE % "images/front.jpeg"),) + epubkit.DEFAULT_DOCS
        options = dict(docs=docs, guide='<reference type="cover" title="Cover" href="titlepage.xhtml#top"/>',
                       files={"images/front.jpeg": jpeg(825, 1200)})
        self.assertEqual(self.cover_size(**options), (825, 1200))
        book = self.convert(**options)
        self.assertEqual(book.blocks[0]["k"], "img")
        self.assertEqual((book.blocks[0]["w"], book.blocks[0]["h"]), (825, 1200))
        self.assertEqual(self.texts(book), ["One", "alpha", "Two", "beta"])

    def test_guide_reference_with_wrong_case(self):
        size = self.cover_size(guide='<reference type="cover" href="Front.XHTML"/>',
                               files={"front.xhtml": xhtml('<object data="a.png"></object>'), "a.png": png(80, 120)})
        self.assertEqual(size, (80, 120))

    def test_undeclared_cover_found_by_name(self):
        page = ('<svg:svg xmlns:svg="http://www.w3.org/2000/svg" viewBox="0 0 1200 1600">'
                '<svg:image xlink:href="Images/Cover.jpg"/></svg:svg>')
        extra = ('<item id="logo" href="logo.jpg" media-type="image/jpeg"/>'
                 '<item id="my-cover-image" href="Images/Cover.jpg" media-type="image/jpeg"/>')
        size = self.cover_size(docs=(("cover.xml", page),) + epubkit.DEFAULT_DOCS, manifest_extra=extra,
                               guide='<reference type="other.ms-thumbimage-standard" href="MSRThumb.jpg"/>',
                               files={"logo.jpg": jpeg(200, 200), "Images/Cover.jpg": jpeg(825, 1200)})
        self.assertEqual(size, (825, 1200))

    def test_cover_is_a_word_not_a_run_of_letters(self):
        extra = ('<item id="i1" href="front.jpg" media-type="image/jpeg"/>'
                 '<item id="i2" href="back.jpg" media-type="image/jpeg"/>')
        files = {"front.jpg": jpeg(600, 900), "back.jpg": jpeg(601, 901)}
        for back in ("other.backcover", "back-cover", "back cover"):
            with self.subTest(back=back):
                guide = '<reference type="%s" href="back.jpg"/><reference type="cover" href="front.jpg"/>' % back
                self.assertEqual(self.cover_size(manifest_extra=extra, guide=guide, files=files), (600, 900))
        for kind in ("other.ms-coverimage-standard", "coverimagestandard", "other.cover", "frontcover"):
            with self.subTest(kind=kind):
                guide = '<reference type="%s" href="front.jpg"/>' % kind
                self.assertEqual(self.cover_size(manifest_extra=extra, guide=guide, files=files), (600, 900))
        docs = (("ch1.xhtml", "<p>%s</p><img src='page.jpg'/>" % ("word " * 80)),)
        for name in ("discover-more.jpg", "recovery.jpg", "back_cover.jpg", "backcover.jpg"):
            with self.subTest(name=name):
                size = self.cover_size(docs=docs, files={name: jpeg(601, 901), "page.jpg": jpeg(300, 400)},
                                       manifest_extra='<item id="i1" href="%s" media-type="image/jpeg"/>' % name)
                self.assertIsNone(size)
        for name in ("FrontCover.jpg", "ebook-cover.jpg", "9780000000000_cover.jpg", "CoverImage.jpg"):
            with self.subTest(name=name):
                size = self.cover_size(docs=docs, files={name: jpeg(601, 901), "page.jpg": jpeg(300, 400)},
                                       manifest_extra='<item id="i1" href="%s" media-type="image/jpeg"/>' % name)
                self.assertEqual(size, (601, 901))

    def test_named_cover_prefers_exact_then_largest(self):
        extra = ('<item id="a" href="cover_art_big.jpg" media-type="image/jpeg"/>'
                 '<item id="b" href="x/cover.png" media-type="image/png"/>')
        files = {"cover_art_big.jpg": jpeg(900, 900) + b"\x00" * 5000, "x/cover.png": png(100, 160)}
        self.assertEqual(self.cover_size(manifest_extra=extra, files=files), (100, 160))
        extra = ('<item id="a" href="cover_small.jpg" media-type="image/jpeg"/>'
                 '<item id="b" href="cover_large.jpg" media-type="image/jpeg"/>')
        files = {"cover_small.jpg": jpeg(100, 100), "cover_large.jpg": jpeg(900, 1200) + b"\x00" * 5000}
        self.assertEqual(self.cover_size(manifest_extra=extra, files=files), (900, 1200))

    def test_landmarks_cover(self):
        landmarks = nav('<ol><li><a epub:type="toc" href="nav.xhtml">Contents</a></li>'
                        '<li><a epub:type="cover" href="front.xhtml">Cover</a></li></ol>', "landmarks")
        size = self.cover_size(manifest_extra='<item id="f" href="front.xhtml" media-type="application/xhtml+xml"/>',
                               nav_text=landmarks, version="3.0",
                               files={"front.xhtml": xhtml('<img src="art/f.jpg" alt=""/>'), "art/f.jpg": jpeg(500, 700)})
        self.assertEqual(size, (500, 700))

    def test_first_page_that_is_only_a_picture(self):
        docs = (("front.xhtml", '<div><img src="art/f.jpg" alt=""/></div>'),) + epubkit.DEFAULT_DOCS
        self.assertEqual(self.cover_size(docs=docs, files={"art/f.jpg": jpeg(500, 700)}), (500, 700))

    def test_picture_in_a_page_of_text_is_not_a_cover(self):
        docs = (("ch1.xhtml", "<p>%s</p><img src='art/f.jpg' alt=''/>" % ("word " * 80)),)
        self.assertIsNone(self.cover_size(docs=docs, files={"art/f.jpg": jpeg(500, 700)}))

    def test_declared_cover_beats_the_first_picture(self):
        docs = (("titlepage.xhtml", '<img src="titlepage.png" alt=""/>'),) + epubkit.DEFAULT_DOCS
        extra = '<item id="cover.jpg" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>'
        size = self.cover_size(docs=docs, manifest_extra=extra, version="3.0",
                               files={"titlepage.png": png(140, 210), "images/cover.jpg": jpeg(1400, 2100)})
        self.assertEqual(size, (1400, 2100))

    def test_no_picture_no_cover(self):
        self.assertIsNone(self.meta().cover)

    def test_each_failed_candidate_falls_through(self):
        extra = ('<item id="prop" href="gone.jpg" media-type="image/jpeg" properties="cover-image"/>'
                 '<item id="tiny" href="tiny.png" media-type="image/png"/>'
                 '<item id="fake" href="cover.jpg" media-type="image/jpeg"/>'
                 '<item id="empty" href="cover-empty.jpg" media-type="image/jpeg"/>')
        docs = (("front.xhtml", '<img src="real.jpg" alt=""/>'),) + epubkit.DEFAULT_DOCS
        size = self.cover_size(docs=docs, manifest_extra=extra, metadata_extra='<meta name="cover" content="tiny"/>',
                               guide='<reference type="cover" href="nowhere.xhtml"/>',
                               files={"tiny.png": png(10, 10), "cover.jpg": b"not an image at all" * 20,
                                      "cover-empty.jpg": b"", "real.jpg": jpeg(400, 600)})
        self.assertEqual(size, (400, 600))

    def test_cover_meta_pointing_nowhere_then_guide_image(self):
        size = self.cover_size(metadata_extra='<meta name="cover" content="ghost"/>',
                               guide='<reference type="cover" href="images/front.jpg"/>',
                               files={"images/front.jpg": jpeg(300, 450)})
        self.assertEqual(size, (300, 450))

    def test_svg_file_wrapping_a_raster(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><image href="deep/real.png"/></svg>'
        extra = '<item id="c" href="art/wrap.svg" media-type="image/svg+xml" properties="cover-image"/>'
        size = self.cover_size(manifest_extra=extra, files={"art/wrap.svg": svg, "art/deep/real.png": png(120, 180)})
        self.assertEqual(size, (120, 180))

    def test_size_comes_from_the_bytes(self):
        docs = (("cover.xhtml", SVG_PAGE % "c.jpg"),)
        size = self.cover_size(docs=docs, guide='<reference type="cover" href="cover.xhtml"/>',
                               files={"c.jpg": jpeg(825, 1200)})
        self.assertEqual(size, (825, 1200))


class ContentsTest(EpubCase):
    DOCS = (("ch1.xhtml", '<h1>One</h1><p>alpha</p><h2 id="a">Part A</h2><p>x</p><a name="b"></a><p>after b</p>'),
            ("ch2.xhtml", '<h1 id="sec:1">Two</h1><p>beta</p>'),
            ("ch3.xhtml", "<h1>Three</h1><p>gamma</p>"))

    def test_ncx_nesting_and_fragments(self):
        points = [("One", "ch1.xhtml", [("A", "ch1.xhtml#a"), ("B", "ch1.xhtml#b", [("Deep", "ch2.xhtml")])]),
                  ("Three", "ch3.xhtml")]
        book = self.convert(docs=self.DOCS, ncx_points=points)
        self.assertEqual(self.toc(book), [("One", 0, "One"), ("A", 1, "Part A"), ("B", 1, "after b"),
                                          ("Deep", 2, "Two"), ("Three", 0, "Three")])

    def test_ncx_found_without_spine_attribute(self):
        for options in (dict(spine_attrs=""), dict(spine_attrs=' toc="missing"'),
                        dict(spine_attrs="", ncx_type="text/xml"),
                        dict(spine_attrs=' toc="toc.ncx"', ncx_id="toc.ncx", ncx_type="text/xml"),
                        dict(spine_attrs="", ncx_type="text/xml", ncx_href="contents.xml")):
            with self.subTest(options=options):
                if options.get("ncx_href"):
                    options["manifest_extra"] = ""
                    options["spine_attrs"] = ' toc="ncx"'
                book = self.convert(docs=self.DOCS, **options)
                self.assertEqual([entry["t"] for entry in book.toc], ["One", "Two", "Three"])

    def test_ncx_not_in_the_manifest(self):
        manifest = "".join('<item id="c%d" href="ch%d.xhtml" media-type="application/xhtml+xml"/>' % (n, n)
                           for n in (1, 2, 3))
        book = self.convert(docs=self.DOCS, manifest=manifest, spine_attrs="")
        self.assertEqual([entry["t"] for entry in book.toc], ["One", "Two", "Three"])

    def test_ncx_percent_encoded_targets(self):
        docs = (("Verne, Jules - Moon_split_000.htm", "<p>zero</p>"), ("Verne, Jules - Moon_split_001.htm", "<p>one</p>"),
                ("Verne, Jules - Moon_split_002.htm", '<p>pad</p><p id="s 1">two</p>'))
        points = [("Zero", "Verne%2c%20Jules%20-%20Moon_split_000.htm"),
                  ("One", "Verne%2C%20Jules%20-%20Moon_split_001.htm"),
                  ("Two", "Verne%2c%20Jules%20-%20Moon_split_002.htm#s%201")]
        book = self.convert(docs=docs, ncx_points=points)
        self.assertEqual(self.toc(book), [("Zero", 0, "zero"), ("One", 0, "one"), ("Two", 0, "two")])

    def test_ncx_fragment_needing_decoding(self):
        book = self.convert(docs=self.DOCS, ncx_points=[("One", "ch1.xhtml"), ("Two", "ch2.xhtml#sec%3A1"),
                                                        ("Three", "ch3.xhtml#nope")])
        self.assertEqual([entry["b"] for entry in book.toc], [0, 5, 7])

    def test_ncx_breakage(self):
        text = ('  <?xml version="1.0"?><NCX><NAVMAP>'
                '<NAVPOINT id="np"><NAVLABEL><TEXT>\n   Chapter   <b>1</b>\n </TEXT></NAVLABEL><CONTENT SRC="ch1.xhtml"/>'
                '<navPoint id="np"><navLabel><text>&lt;i&gt;Inner&lt;/i&gt; A</text></navLabel><content src="ch1.xhtml#a"/></navPoint>'
                "</NAVPOINT>"
                '<navPoint id="np"><navLabel><text/></navLabel><content src="ch2.xhtml"/></navPoint>'
                '<navPoint><navLabel><text>Gone</text></navLabel><content src="gone.xhtml"/></navPoint>'
                '<navPoint><navPoint><navLabel><text>Promoted</text></navLabel><content src="ch3.xhtml"/></navPoint></navPoint>'
                "</NAVMAP><pageList><pageTarget><navLabel><text>1</text></navLabel><content src='ch1.xhtml'/></pageTarget></pageList></NCX>")
        book = self.convert(docs=self.DOCS, ncx_text=text)
        self.assertEqual(self.toc(book), [("Chapter 1", 0, "One"), ("Inner A", 1, "Part A"), ("Two", 0, "Two"),
                                          ("Promoted", 0, "Three")])

    def test_ncx_group_without_target_opens_at_first_child(self):
        points = [("Part I", None, [("One", "ch1.xhtml"), ("Two", "ch2.xhtml")]), ("Three", "ch3.xhtml")]
        book = self.convert(docs=self.DOCS, ncx_points=points)
        self.assertEqual(self.toc(book), [("Part I", 0, "One"), ("One", 1, "One"), ("Two", 1, "Two"), ("Three", 0, "Three")])

    def test_ncx_in_another_folder(self):
        points = [("One", "../ch1.xhtml"), ("Two", "../ch2.xhtml"), ("Three", "ch3.xhtml")]
        book = self.convert(docs=self.DOCS, ncx_points=points, ncx_href="nav/toc.ncx")
        self.assertEqual([entry["b"] for entry in book.toc], [0, 5, 7])

    def test_ncx_order_is_kept_even_backwards(self):
        points = [("Three", "ch3.xhtml"), ("Two", "ch2.xhtml"), ("One", "ch1.xhtml"), ("*", "ch1.xhtml#a")]
        book = self.convert(docs=self.DOCS, ncx_points=points)
        self.assertEqual([(entry["t"], entry["b"]) for entry in book.toc],
                         [("Three", 7), ("Two", 5), ("One", 0), ("*", 2)])

    def test_nav_document(self):
        inner = ('<h2>Contents</h2><ol><li><a href="ch1.xhtml"><span>1</span> <em>One</em></a><ol>'
                 '<li><a href="ch1.xhtml#a">A</a></li></ol></li><li><a href="ch2.xhtml">Two</a></li>'
                 '<li><a href="ch3.xhtml"><img src="m.png" alt="Map"/></a></li></ol>')
        text = xhtml('<nav epub:type="landmarks"><ol><li><a href="ch3.xhtml">Start</a></li></ol></nav>'
                     '<nav epub:type="toc" hidden="">%s</nav>'
                     '<nav epub:type="page-list"><ol><li><a href="ch1.xhtml">1</a></li></ol></nav>' % inner)
        book = self.convert(docs=self.DOCS, nav_text=text, ncx_points=False, version="3.0",
                            nav_properties="scripted nav")
        self.assertEqual(self.toc(book), [("1 One", 0, "One"), ("A", 1, "Part A"), ("Two", 0, "Two"), ("Map", 0, "Three")])

    def test_nav_in_odd_shapes(self):
        inner = ('<h2>Contents</h2><div><ol><li><p><a href="ch1.xhtml">One</a></p><ol><li><span>Group</span>'
                 '<ul><li><a href="ch2.xhtml#sec:1">Two</a></li></ul></li></ol></li>'
                 '<li><a href="#">Dead</a></li><li><a href="ch3.xhtml">Three</a></li></ol></div>')
        book = self.convert(docs=self.DOCS, nav_text=nav(inner, "landmarks toc"), ncx_points=False)
        self.assertEqual(self.toc(book), [("One", 0, "One"), ("Group", 1, "Two"), ("Two", 2, "Two"), ("Three", 0, "Three")])

    def test_nav_without_lists(self):
        inner = '<p><a href="ch1.xhtml">One</a></p><p><a href="ch2.xhtml">Two</a></p><div><a href="ch3.xhtml">Three</a></div>'
        book = self.convert(docs=self.DOCS, nav_text=nav(inner), ncx_points=False)
        self.assertEqual(self.toc(book), [("One", 0, "One"), ("Two", 0, "Two"), ("Three", 0, "Three")])

    def test_nav_in_another_folder(self):
        for href in ("../ch%d.xhtml", "ch%d.xhtml"):
            with self.subTest(href=href):
                inner = "<ol>%s</ol>" % "".join('<li><a href="%s">N%d</a></li>' % (href % n, n) for n in (1, 2, 3))
                book = self.convert(docs=self.DOCS, nav_text=nav(inner), nav_href="nav/nav.xhtml", ncx_points=False)
                self.assertEqual([entry["b"] for entry in book.toc], [0, 5, 7])

    def test_nav_recognised_without_the_property(self):
        inner = "<ol>%s</ol>" % "".join('<li><a href="ch%d.xhtml">N%d</a></li>' % (n, n) for n in (3, 2, 1))
        for marker in ('epub:type="toc"', 'role="doc-toc"', 'id="toc"'):
            with self.subTest(marker=marker):
                docs = self.DOCS + (("contents.xhtml", "<nav %s>%s</nav>" % (marker, inner)),)
                book = self.convert(docs=docs, ncx_points=False)
                self.assertEqual([entry["t"] for entry in book.toc], ["N3", "N2", "N1"])

    def test_nav_of_a_chapter_is_not_the_contents(self):
        sections = ('<nav><ul><li><a href="#s1">The harbour</a></li><li><a href="#s2">The inn</a></li>'
                    '<li><a href="#s3">The letter</a></li></ul></nav>'
                    '<h2 id="s1">The harbour</h2><p>a</p><h2 id="s2">The inn</h2><p>b</p><h2 id="s3">The letter</h2><p>c</p>')
        strip = ('<nav class="chapnav"><a href="ch1.xhtml">First</a> | <a href="ch2.xhtml">Previous</a> | '
                 '<a href="ch3.xhtml">Next</a></nav>')
        for label, first, rest in (("sections", "<h1>One</h1>" + sections, ""), ("strip", strip + "<h1>One</h1>", strip)):
            docs = (("ch1.xhtml", first), ("ch2.xhtml", rest + "<h1>Two</h1><p>x</p>"),
                    ("ch3.xhtml", rest + "<h1>Three</h1><p>y</p>"))
            with self.subTest(label, ncx="full"):
                book = self.convert(docs=docs)
                self.assertEqual([entry["t"] for entry in book.toc], ["One", "Two", "Three"])
            for ncx_points in ([("Start", "ch1.xhtml"), ("End", "ch3.xhtml")], False):
                with self.subTest(label, ncx=ncx_points):
                    book = self.convert(docs=docs, ncx_points=ncx_points)
                    self.assertEqual([entry["t"] for entry in book.toc if entry["d"] == 0],
                                     ["One", "Two", "Three"])

    def test_untyped_nav_of_the_declared_nav_document(self):
        inner = "<ol>%s</ol>" % "".join('<li><a href="ch%d.xhtml">N%d</a></li>' % (n, n) for n in (1, 2, 3))
        book = self.convert(docs=self.DOCS, nav_text=xhtml("<nav>%s</nav>" % inner), ncx_points=False)
        self.assertEqual([entry["t"] for entry in book.toc], ["N1", "N2", "N3"])

    def test_ncx_preferred_to_an_undeclared_nav(self):
        inner = "<ol>%s</ol>" % "".join('<li><a href="ch%d.xhtml">N%d</a></li>' % (n, n) for n in (1, 2, 3))
        docs = self.DOCS + (("contents.xhtml", '<nav epub:type="toc">%s</nav>' % inner),)
        book = self.convert(docs=docs, ncx_points=[("One", "ch1.xhtml"), ("Two", "ch2.xhtml"), ("Three", "ch3.xhtml")])
        self.assertEqual([entry["t"] for entry in book.toc], ["One", "Two", "Three"])
        book = self.convert(docs=docs, ncx_points=[("One", "ch1.xhtml")])
        self.assertEqual([entry["t"] for entry in book.toc], ["N1", "N2", "N3"])

    def test_nav_outside_the_spine_without_the_property(self):
        inner = "<ol>%s</ol>" % "".join('<li><a href="ch%d.xhtml">N%d</a></li>' % (n, n) for n in (1, 2, 3))
        book = self.convert(docs=self.DOCS, ncx_points=False, files={"contents.xhtml": nav(inner)},
                            manifest_extra='<item id="n" href="contents.xhtml" media-type="application/xhtml+xml"/>')
        self.assertEqual([entry["t"] for entry in book.toc], ["N1", "N2", "N3"])

    def test_links_to_the_nav_page_itself(self):
        inner = ('<ol><li><a href="ch1.xhtml">One</a></li><li><a href="ch2.xhtml">Two</a></li>'
                 '<li><a href="#about">About</a></li></ol><p>pad</p><p id="about">About this list</p>')
        docs = self.DOCS[:2] + (("a.xhtml", '<p id="about">wrong one</p>'), ("contents.xhtml", '<nav epub:type="toc">%s</nav>' % inner))
        book = self.convert(docs=docs, ncx_points=False)
        self.assertEqual(self.toc(book)[-1], ("About", 0, "About this list"))

    def test_absurdly_deep_contents_do_not_crash(self):
        depth = 3000
        text = ('<ncx><navMap>' + '<navPoint><navLabel><text>L</text></navLabel><content src="ch1.xhtml"/>' * depth
                + "</navPoint>" * depth + "</navMap></ncx>")
        inner = "<ol><li><a href='ch2.xhtml'>x</a>" * depth + "</li></ol>" * depth
        book = self.convert(docs=self.DOCS, ncx_text=text, nav_text=nav(inner))
        self.assertGreaterEqual(len(book.toc), 3)
        for entry in book.toc:
            self.assertLess(entry["b"], len(book.blocks))

    def test_nav_preferred_when_both_are_good(self):
        inner = "<ol>%s</ol>" % "".join('<li><a href="ch%d.xhtml">Nav %d</a></li>' % (n, n) for n in (1, 2, 3))
        book = self.convert(docs=self.DOCS, nav_text=nav(inner))
        self.assertEqual([entry["t"] for entry in book.toc], ["Nav 1", "Nav 2", "Nav 3"])

    def test_thin_nav_loses_to_a_fuller_ncx(self):
        for inner in ('<ol><li><a href="ch1.xhtml">Only</a></li><li><a href="ch2.xhtml">Two of them</a></li></ol>',
                      "<ol><li><span>One</span></li><li>Two</li></ol>"):
            with self.subTest(inner=inner):
                book = self.convert(docs=self.DOCS, nav_text=nav(inner))
                self.assertEqual([entry["t"] for entry in book.toc], ["One", "Two", "Three"])

    def test_thin_ncx_loses_to_a_fuller_nav(self):
        inner = "<ol>%s</ol>" % "".join('<li><a href="ch%d.xhtml">Nav %d</a></li>' % (n, n) for n in (1, 2, 3))
        book = self.convert(docs=self.DOCS, nav_text=nav(inner), ncx_points=[("Start", "ch1.xhtml")])
        self.assertEqual(len(book.toc), 3)

    def test_guide_contents_page(self):
        page = ('<p><a href="ch1.xhtml">First</a></p><p><a href="ch2.xhtml#sec:1">Second</a> '
                '<a href="http://example.org/">elsewhere</a></p><p><a href="ch3.xhtml">Third</a></p>')
        docs = (("ch1.xhtml", "<p>alpha</p>"), ("ch2.xhtml", '<p id="sec:1">beta</p>'), ("ch3.xhtml", "<p>gamma</p>"),
                ("contents.xhtml", page))
        book = self.convert(docs=docs, ncx_points=[("Start", "ch1.xhtml")],
                            guide='<reference type="toc" title="Contents" href="contents.xhtml"/>')
        self.assertEqual([(entry["t"], entry["b"]) for entry in book.toc], [("First", 0), ("Second", 1), ("Third", 2)])

    def test_coarse_contents_are_refined_by_the_chapter_files(self):
        docs, points = [("toc.xhtml", xhtml("<p>Contents</p>", "Table of Contents"))], [("Table of Contents", "toc.xhtml")]
        for name, chapter, count in (("Genesis", "Genesis", 3), ("Obadiah", "Obadiah", 1), ("Psalms", "Psalm", 4)):
            points.append((name, "%s1.htm" % name))
            for number in range(1, count + 1):
                title = "%s %d KJV" % (chapter, number)
                docs.append(("%s%d.htm" % (name, number), xhtml("<p><b>The Heading</b></p><p>Verses.</p>", title)))
        book = self.convert(docs=docs, raw_docs=True, ncx_points=points, title="King James Bible")
        self.assertEqual([(entry["t"], entry["d"], entry["b"]) for entry in book.toc], [
            ("Table of Contents", 0, 0),
            ("Genesis", 0, 1), ("Genesis 1", 1, 1), ("Genesis 2", 1, 3), ("Genesis 3", 1, 5),
            ("Obadiah", 0, 7),
            ("Psalms", 0, 9), ("Psalm 1", 1, 9), ("Psalm 2", 1, 11), ("Psalm 3", 1, 13),
            ("Psalm 4", 1, 15)])

    def test_files_split_by_a_converter_add_nothing(self):
        docs = [("index_split_%03d.html" % number, xhtml("<p>Text.</p><h2>Later %d</h2><p>More.</p>" % number, "T"))
                for number in range(9)]
        points = [("One", docs[0][0]), ("Two", docs[3][0]), ("Three", docs[6][0])]
        book = self.convert(docs=docs, raw_docs=True, ncx_points=points)
        self.assertEqual([entry["t"] for entry in book.toc], ["One", "Two", "Three"])

    def test_unlisted_opening_gets_an_entry(self):
        body = "<p>Opening words.</p>" * 30 + "".join(
            '<h2 id="c%d">CHAPTER %d</h2>%s' % (number, number, "<p>Text.</p>" * 30) for number in (2, 3, 4))
        points = [("CHAPTER %d" % number, "ch1.xhtml#c%d" % number) for number in (2, 3, 4)]
        book = self.convert(docs=(("ch1.xhtml", body),), ncx_points=points, title="The Long Road")
        self.assertEqual([(entry["t"], entry["b"]) for entry in book.toc],
                         [("The Long Road", 0), ("CHAPTER 2", 30), ("CHAPTER 3", 61), ("CHAPTER 4", 92)])

    def test_junk_contents_are_replaced_by_headings(self):
        docs = tuple(("c%d.xhtml" % n, "<h1>Chapter %d</h1><p>text</p>" % n) for n in range(1, 11))
        for points in ([("Start", "c1.xhtml")], [], False):
            with self.subTest(points=points):
                book = self.convert(docs=docs, ncx_points=points)
                self.assertEqual([entry["t"] for entry in book.toc], ["Chapter %d" % n for n in range(1, 11)])
                self.assertEqual([entry["b"] for entry in book.toc], list(range(0, 20, 2)))

    def test_entries_pointing_at_missing_files_are_dropped(self):
        points = [("One", "ch1.xhtml"), ("Lost", "lost.xhtml"), ("Two", "ch2.xhtml"), ("Web", "http://example.org/x"),
                  ("Three", "ch3.xhtml")]
        book = self.convert(docs=self.DOCS, ncx_points=points)
        self.assertEqual([entry["t"] for entry in book.toc], ["One", "Two", "Three"])


class ResourcesTest(EpubCase):
    def test_pictures_stop_at_the_cap_and_the_book_still_opens(self):
        body = "".join('<p>text %d</p><img src="p%d.png" alt=""/>' % (number, number) for number in range(6))
        files = {"p%d.png" % number: png(40 + number, 40) for number in range(6)}
        for bound, kept in ((dict(count=3), 3), (dict(total=2 * len(png(40, 40)) + 10), 2),
                            (dict(each=len(png(40, 40)) - 1), 0)):
            with self.subTest(bound=bound):
                out = os.path.join(self.dir, "out-%d" % kept)
                with mock.patch.object(epub, "Pictures", functools.partial(pictures.Pictures, **bound)):
                    book = epub.convert(self.make(docs=(("c1.xhtml", body),), files=files), out)
                shown = [block for block in book.blocks if block["k"] == "img"]
                self.assertEqual(len(shown), kept)
                written = os.listdir(os.path.join(out, "img")) if kept else []
                self.assertEqual(sorted(written), ["%04d.png" % (number + 1) for number in range(kept)])
                self.assertEqual(self.texts(book), ["text %d" % number for number in range(6)])

    def test_a_picture_too_large_to_keep_is_not_read_to_the_end(self):
        seen = []
        real = Archive.read

        def read(archive, name, limit=None, **more):
            seen.append((name, limit))
            return real(archive, name, *(() if limit is None else (limit,)), **more)

        with mock.patch.object(Archive, "read", read):
            self.convert(docs=(("c1.xhtml", '<p>a</p><img src="p.png" alt=""/>'),), files={"p.png": png(40, 40)})
        self.assertIn(("OEBPS/p.png", pictures.MAX_PICTURE), seen)

    def test_pictures_are_written_once_each(self):
        docs = (("Text/ch1.xhtml", '<p>a</p><img src="../Images/my%20pic.png" alt="x"/><img src="../images/MY PIC.PNG"/>'
                                   '<img src="../Images/extra.jpg"/><img src="../Images/gone.png" alt=""/>'),
                ("Text/ch2.xhtml", '<img src="../Images/my pic.png"/><p>b</p><img src="../Images/broken.png"/>'))
        book = self.convert(docs=docs, files={"Images/my pic.png": png(200, 100), "Images/extra.jpg": jpeg(300, 150),
                                              "Images/broken.png": b"\x89PNG but not really"})
        pictures = [block for block in book.blocks if block["k"] == "img"]
        self.assertEqual([os.path.basename(block["src"]) for block in pictures],
                         ["0001.png", "0001.png", "0002.jpg", "0001.png"])
        self.assertEqual((pictures[0]["w"], pictures[0]["h"]), (200, 100))
        folder = os.path.join(self.out, "img")
        self.assertEqual(sorted(os.listdir(folder)), ["0001.png", "0002.jpg"])
        with open(os.path.join(folder, "0001.png"), "rb") as handle:
            self.assertEqual(handle.read(), png(200, 100))
        for block in pictures:
            self.assertTrue(os.path.isabs(block["src"]))

    def test_data_uri_picture(self):
        import base64
        uri = "data:image/png;base64," + base64.b64encode(png(90, 70)).decode()
        book = self.convert(docs=(("ch1.xhtml", '<p>a</p><img src="%s"/><img src="data:image/png;base64,!!"/>' % uri),))
        pictures = [block for block in book.blocks if block["k"] == "img"]
        self.assertEqual([(block["w"], block["h"]) for block in pictures], [(90, 70)])
        self.assertTrue(os.path.isfile(pictures[0]["src"]))

    def test_transparent_pictures_are_marked(self):
        import base64
        uri = "data:image/png;base64," + base64.b64encode(clear_png(80, 70)).decode()
        docs = (("ch1.xhtml", '<p>a</p><img src="quote.png" alt="A pull-quote"/><img src="photo.jpg"/>'
                              '<img src="solid.png"/><img src="%s"/>' % uri),
                ("quote.png", clear_png(300, 120)))
        book = self.convert(docs=docs, spine=["c1", "c2"], manifest=(
            '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c2" href="quote.png" media-type="image/png"/>'),
            files={"photo.jpg": jpeg(300, 150), "solid.png": png(200, 100)}, ncx_points=False)
        pictures = [block for block in book.blocks if block["k"] == "img"]
        self.assertEqual([(block["w"], block.get("al")) for block in pictures],
                         [(300, 1), (300, None), (200, None), (80, 1), (300, 1)])
        self.assertEqual(pictures[0], {"k": "img", "src": pictures[0]["src"], "w": 300, "h": 120,
                                       "alt": "A pull-quote", "al": 1})

    def test_stylesheet_through_parent_folders(self):
        head = '<link href="../../Styles/main.css" rel="stylesheet" type="text/css"/><link rel="stylesheet" href="../missing.css"/>'
        docs = (("Text/a/ch1.xhtml", xhtml('<p>plain <span class="it">ital</span> <span class="b">bold</span></p>', head=head)),)
        book = self.convert(docs=docs, raw_docs=True,
                            files={"Styles/main.css": b"\xef\xbb\xbf.it{font-style:italic}.b{font-weight:bold}"})
        self.assertEqual(book.blocks[0]["t"], "plain <i>ital</i> <b>bold</b>")

    def test_links(self):
        docs = (("Text/Chapter 1.xhtml", '<p id="top">one <a href="Chapter%202.xhtml#s%201">a</a> <a href="chapter 2.xhtml">b</a> '
                                         '<a href="https://example.org/?a=1&amp;b=&quot;2&quot; 3">c</a> <a href="nowhere.xhtml">d</a> '
                                         '<a href="Chapter 2.xhtml?ref=toc#s 1">e</a> <a href="#top">f</a></p>'),
                ("Text/Chapter 2.xhtml", '<p>pad</p><p id="s 1">two <a href="../Text/Chapter 1.xhtml">back</a></p>'))
        book = self.convert(docs=docs, ncx_points=False)
        self.assertEqual(book.blocks[0]["t"],
                         'one <a href="b:2">a</a> <a href="b:1">b</a> <a href="https://example.org/?a=1&b=%222%22%203">c</a> d '
                         '<a href="b:2">e</a> <a href="b:0">f</a>')
        self.assertEqual(book.blocks[2]["t"], 'two <a href="b:0">back</a>')

    def test_links_to_missing_fragments(self):
        docs = (("ch1.xhtml", '<p>one <a href="#gone">a</a> <a href="ch1.xhtml#gone">b</a> <a href="ch2.xhtml#gone">c</a></p>'),
                ("ch2.xhtml", '<p>two</p><p><a href="#gone">d</a> <a href="ch1.xhtml#gone">e</a></p>'))
        book = self.convert(docs=docs, ncx_points=[("One", "ch1.xhtml#gone"), ("Two", "ch2.xhtml#gone")])
        self.assertEqual([block["t"] for block in book.blocks],
                         ['one a b <a href="b:1">c</a>', "two", 'd <a href="b:0">e</a>'])
        self.assertEqual([entry["b"] for entry in book.toc], [0, 1])

    def test_document_in_a_legacy_encoding(self):
        raw = '<html><head><meta charset="windows-1252"/></head><body><p>café ’q’</p></body></html>'
        docs = (("a.xhtml", raw.encode("cp1252")),
                ("b.xhtml", ('<?xml version="1.0" encoding="iso-8859-1"?>' + raw).encode("utf-8")),
                ("c.xhtml", b"\xff\xfe" + raw.encode("utf-16-le")))
        book = self.convert(docs=docs, ncx_points=False)
        self.assertEqual(self.texts(book), ["café ’q’"] * 3)


class KeyTest(EpubCase):
    def test_key_shape(self):
        key = epub.content_key(self.make())
        self.assertRegex(key, r"^[0-9a-f]{20}$")

    def test_key_survives_metadata_cover_and_bookmark_edits(self):
        cover = dict(manifest_extra='<item id="cover" href="cover.jpg" media-type="image/jpeg"/>',
                     metadata_extra='<meta name="cover" content="cover"/>')
        first = epub.content_key(self.make(name="a.epub", files={"cover.jpg": jpeg(300, 400)}, **cover))
        second = epub.content_key(self.make(
            name="renamed.epub", title="Another Title", creators=("Someone Else",), language="fr",
            files={"cover.jpg": jpeg(900, 1200)}, root_files={"META-INF/calibre_bookmarks.txt": "encoding=json+base64:"},
            **cover))
        self.assertEqual(first, second)

    def cover_book(self, name, page="titlepage.xhtml", body=None, picture=jpeg(600, 900), guide=True, **options):
        docs = ((page, SVG_PAGE % "cover.jpg" if body is None else body),) + options.pop("docs", epubkit.DEFAULT_DOCS)
        return epub.content_key(self.make(
            name=name, docs=docs, files={"cover.jpg": picture},
            guide='<reference type="cover" href="%s"/>' % page if guide else "", **options))

    def test_key_survives_a_new_cover_page(self):
        first = self.cover_book("a.epub")
        self.assertEqual(first, self.cover_book("b.epub", picture=png(1200, 1800)))
        self.assertEqual(first, self.cover_book("c.epub", body='<div><img src="cover.jpg" alt="Cover"/></div>'))
        self.assertEqual(first, self.cover_book("d.epub", page="cover_page.xhtml", body="<p><img src='cover.jpg'/></p>"))
        self.assertEqual(first, self.cover_book("e.epub", picture=b"not a picture any more"))
        self.assertRegex(first, r"^[0-9a-f]{20}$")

    def test_key_with_a_cover_page_follows_the_chapters(self):
        first = self.cover_book("a.epub")
        edited = self.cover_book("b.epub", docs=(("ch1.xhtml", "<h1>One</h1><p>alpha!</p>"), epubkit.DEFAULT_DOCS[1]))
        self.assertNotEqual(first, edited)

    def test_key_is_that_of_the_book_without_its_cover_page(self):
        self.assertEqual(self.cover_book("a.epub"), epub.content_key(self.make(name="b.epub")))

    def test_undeclared_cover_page_is_known_by_its_shape(self):
        first = self.cover_book("a.epub", guide=False)
        self.assertEqual(first, self.cover_book("b.epub", guide=False, body='<img src="cover.jpg" alt=""/>'))
        self.assertEqual(first, self.cover_book("c.epub"))

    def test_landmarks_cover_page(self):
        landmarks = nav('<ol><li><a epub:type="cover" href="titlepage.xhtml">Cover</a></li></ol>', "landmarks")
        first = self.cover_book("a.epub", guide=False, body="<p>My Book</p>", nav_text=landmarks, version="3.0")
        second = self.cover_book("b.epub", guide=False, body="<p>My Book, again</p>", nav_text=landmarks, version="3.0")
        self.assertEqual(first, second)
        self.assertNotEqual(first, self.cover_book("c.epub", guide=False, body="<p>My Book, again</p>"))

    def test_non_linear_cover_page_is_left_out_of_the_key(self):
        landmarks = nav('<ol><li><a epub:type="cover" href="titlepage.xhtml">Cover</a></li></ol>', "landmarks")
        spine = '<itemref idref="c1" linear="no"/><itemref idref="c2"/><itemref idref="c3"/>'
        for label, options in (("landmark", dict(nav_text=landmarks, version="3.0")), ("shape", {})):
            with self.subTest(label):
                first = self.cover_book("a.epub", guide=False, spine=spine, **options)
                second = self.cover_book("b.epub", guide=False, spine=spine, **options,
                                         body='<div class="new"><img src="cover.jpg" alt="New"/></div>')
                self.assertEqual(first, second)
                self.assertEqual(first, epub.content_key(self.make(name="c.epub")))

    def test_picture_spine_item_first_is_the_cover(self):
        def key(name, picture):
            return epub.content_key(self.make(
                name=name, docs=(("cover.jpg", picture),) + epubkit.DEFAULT_DOCS, ncx_points=False,
                manifest='<item id="c1" href="cover.jpg" media-type="image/jpeg"/>'
                         '<item id="c2" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
                         '<item id="c3" href="ch2.xhtml" media-type="application/xhtml+xml"/>'))
        self.assertEqual(key("a.epub", jpeg(600, 900)), key("b.epub", jpeg(1200, 1800)))

    def test_a_page_of_text_is_never_the_cover_page(self):
        text = "<h1>One</h1><p>%s</p><img src='cover.jpg'/>"
        first = self.cover_book("a.epub", page="ch0.xhtml", body=text % ("word " * 80))
        second = self.cover_book("b.epub", page="ch0.xhtml", body=text % ("other " * 80))
        self.assertNotEqual(first, second)
        pictures = "<img src='cover.jpg'/><img src='cover.jpg'/>"
        first = self.cover_book("c.epub", guide=False, body=pictures)
        self.assertNotEqual(first, self.cover_book("d.epub", guide=False, body=pictures + "<p>x</p>"))

    def test_only_the_opening_page_is_left_out(self):
        docs = (epubkit.DEFAULT_DOCS[0], ("titlepage.xhtml", SVG_PAGE % "cover.jpg"), epubkit.DEFAULT_DOCS[1])
        other = (epubkit.DEFAULT_DOCS[0], ("titlepage.xhtml", "<img src='cover.jpg'/>"), epubkit.DEFAULT_DOCS[1])
        guide = '<reference type="cover" href="titlepage.xhtml"/>'
        self.assertNotEqual(epub.content_key(self.make(name="a.epub", docs=docs, guide=guide)),
                            epub.content_key(self.make(name="b.epub", docs=other, guide=guide)))

    def test_book_of_only_a_cover_page_still_has_a_key(self):
        first = self.cover_book("a.epub", docs=())
        self.assertRegex(first, r"^[0-9a-f]{20}$")
        self.assertNotEqual(first, self.cover_book("b.epub", docs=(), body="<img src='cover.jpg'/>"))
        self.assertEqual(epub.read_meta(os.path.join(self.dir, "a.epub")).key, first)

    def test_key_follows_the_text(self):
        first = epub.content_key(self.make(name="a.epub"))
        edited = epub.content_key(self.make(name="b.epub", docs=(("ch1.xhtml", "<h1>One</h1><p>alpha!</p>"),
                                                                    epubkit.DEFAULT_DOCS[1])))
        reordered = epub.content_key(self.make(name="c.epub", spine=["c2", "c1"]))
        self.assertEqual(len({first, edited, reordered}), 3)


if __name__ == "__main__":
    unittest.main()
