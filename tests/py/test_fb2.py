import support

import base64
import os
import tempfile
import time
import unittest
import zipfile
from unittest import mock

from epubkit import jpeg, png
from reader import fb2, images
from reader.errors import ReaderError
from test_acceptance import check_block

NAMESPACES = ' xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink"'
INFO = ("<author><first-name>Иван</first-name><middle-name>Петрович</middle-name>"
        "<last-name>Тестов</last-name></author><author><nickname>второй</nickname></author>"
        "<book-title>Проверочная книга — «Фолио»</book-title><lang>RU</lang>")
RUSSIAN = ("В начале июля, в чрезвычайно жаркое время, под вечер, один молодой человек вышел из "
           "своей каморки на улицу и медленно, как бы в нерешимости, отправился к мосту.")
CHAPTERS = ("<section><title><p>One</p></title><p>alpha</p></section>"
            "<section><title><p>Two</p></title><p>beta</p></section>")


def binary(name, data, kind="image/png"):
    return '<binary id="%s" content-type="%s">%s</binary>' % (name, kind, base64.b64encode(data).decode())


def document(body=CHAPTERS, *, info=INFO, more="", declared="utf-8", namespaces=NAMESPACES,
             description=None):
    """A FictionBook as text; `more` is what follows the main body (bodies, binaries)."""
    if description is None:
        description = ("<description><title-info>%s</title-info><document-info><author>"
                       "<nickname>somebody else</nickname></author></document-info></description>" % info)
    return ('<?xml version="1.0" encoding="%s"?>\n<FictionBook%s>%s<body>%s</body>%s</FictionBook>'
            % (declared, namespaces, description, body, more))


def plain(block):
    return block.get("t", block["k"])


class Fb2Case(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = scratch.name
        self.out = os.path.join(self.dir, "out")

    def write(self, content, name="book.fb2", encoding="utf-8"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(content if isinstance(content, bytes) else content.encode(encoding))
        return path

    def convert(self, *args, **options):
        return self.checked(fb2.convert(self.write(document(*args, **options)), self.out))

    def checked(self, book):
        total = len(book.blocks)
        for block in book.blocks:
            self.assertEqual(check_block(block, total)[0], set(), block)
        return book

    def texts(self, book):
        return [plain(block) for block in book.blocks]

    def toc(self, book):
        return [(entry["t"], entry["d"], plain(book.blocks[entry["b"]])) for entry in book.toc]

    def refusal(self, call):
        with self.assertRaises(ReaderError) as caught:
            call()
        return caught.exception.code


class MetaTest(Fb2Case):
    def meta(self, *args, **options):
        return fb2.read_meta(self.write(document(*args, **options)))

    def test_title_authors_and_language_come_from_title_info(self):
        meta = self.meta()
        self.assertEqual((meta.title, meta.authors, meta.language, meta.cover, meta.error),
                         ("Проверочная книга — «Фолио»", ["Иван Петрович Тестов", "второй"], "ru", None, ""))
        book = self.convert()
        self.assertEqual((book.title, book.author, book.language),
                         (meta.title, "Иван Петрович Тестов, второй", "ru"))

    def test_title_falls_back_to_the_publisher_s_then_the_file_name(self):
        published = ("<description><title-info><lang>en</lang></title-info><publish-info>"
                     "<book-name>Printed Name</book-name></publish-info></description>")
        self.assertEqual(self.meta(description=published).title, "Printed Name")
        path = self.write(document(description=""), "Some_Book.fb2")
        self.assertEqual((fb2.read_meta(path).title, fb2.read_meta(path).authors), ("Some Book", []))
        self.assertEqual(fb2.convert(path, self.out).title, "Some Book")

    def test_cover_is_the_coverpage_binary_however_its_base64_is_written(self):
        picture = png(60, 90)
        text = base64.b64encode(picture).decode()
        spellings = (text, "\n".join(text[at:at + 30] for at in range(0, len(text), 30)),
                     "  " + text.rstrip("=") + "\r\n", text[:40] + "\n!!not-base64!!\n" + text[40:])
        for spelling in spellings:
            with self.subTest(spelling=spelling[:20]):
                more = '<binary content-type="image/jpeg" id="Cover.PNG">%s</binary>' % spelling
                info = INFO + '<coverpage><image l:href="#cover.png"/></coverpage>'
                self.assertEqual(self.meta(info=info, more=binary("other", jpeg(9, 9)) + more).cover, picture)

    def test_a_cover_that_is_not_there_is_no_cover(self):
        info = INFO + '<coverpage><image l:href="#absent.png"/></coverpage>'
        self.assertIsNone(self.meta(info=info, more=binary("cover.png", png(60, 90))).cover)
        self.assertIsNone(self.meta(info=info + "<coverpage/>").cover)

    def test_read_meta_does_not_convert_the_book(self):
        path = self.write(document("<section>" + "<p>word</p>" * 2000 + "</section><p>cut off <emphasis>"))
        with mock.patch.object(fb2, "_Writer", side_effect=AssertionError("converted")), \
                mock.patch.object(fb2, "parse_xml", wraps=fb2.parse_xml) as parse:
            self.assertEqual(fb2.read_meta(path).title, "Проверочная книга — «Фолио»")
        self.assertNotIn("<body", parse.call_args.args[0])


class EncodingTest(Fb2Case):
    def test_declared_and_undeclared_encodings(self):
        body = "<section><title><p>Глава</p></title><p>%s</p></section>" % RUSSIAN
        cases = (("windows-1251", "cp1251"), ("KOI8-R", "koi8-r"), ("UTF-16", "utf-16"),
                 ("utf-8", "utf-8-sig"), ("utf-8", "cp1251"), ("windows-1251", "koi8-r"),
                 ("ISO-8859-1", "cp1251"), ("x-unknown", "utf-8"))
        for declared, actual in cases:
            with self.subTest(declared=declared, actual=actual):
                info = INFO.replace(" — «Фолио»", "")
                path = self.write(document(body, info=info, declared=declared), encoding=actual)
                meta = fb2.read_meta(path)
                self.assertEqual((meta.title, meta.authors[0]), ("Проверочная книга", "Иван Петрович Тестов"))
                self.assertEqual(self.texts(fb2.convert(path, self.out)), ["Глава", RUSSIAN])

    def test_namespaces_are_optional_and_prefixes_arbitrary(self):
        body = ('<section id="a"><title><p>One</p></title><p><a xlink:href="#b">to two</a>, '
                '<a href="#b">bare</a>, <q:a q:href="#b">odd</q:a></p></section>'
                '<section id="b"><title><p>Two</p></title><p>beta</p></section>')
        for namespaces in ("", ' xmlns:xlink="http://www.w3.org/1999/xlink"'):
            with self.subTest(namespaces=namespaces):
                book = self.convert(body, namespaces=namespaces)
                self.assertEqual(book.blocks[1]["t"], '<a href="b:2">to two</a>, <a href="b:2">bare</a>, '
                                                      '<a href="b:2">odd</a>')
                self.assertEqual(book.title, "Проверочная книга — «Фолио»")

    def test_malformed_xml_is_recovered(self):
        body = ("<section><title><p>One</p></title><p>AT&T and &nbsp; and \x0c and <emphasis>open.</p>"
                "<p>Unclosed<p>Next <b>b</b><br/>line</p><P>UPPER</P></section>"
                "<section><title><p>Two</p></title><p>beta</p>")
        path = self.write(document(body).replace("</body>", "").replace("</FictionBook>", ""))
        book = self.checked(fb2.convert(path, self.out))
        self.assertEqual(self.texts(book), ["One", "AT&amp;T and &#160; and and <i>open.</i>", "Unclosed",
                                            "Next <b>b</b><br>line", "UPPER", "Two", "beta"])
        self.assertEqual(fb2.read_meta(path).title, "Проверочная книга — «Фолио»")


class StructureTest(Fb2Case):
    def test_nested_sections_become_headings_and_the_contents_tree(self):
        body = ("<title><p>The Book</p><p>a subtitle</p></title><epigraph><p>Motto.</p></epigraph>"
                "<section><title><p>Part One</p></title>"
                "<section><title><p>Chapter 1</p><empty-line/><p>Start</p></title><p>a</p>"
                "<section><title><p>Scene</p></title><p>b</p><section><p>untitled</p>"
                "<section><title><p>Deep</p></title><p>c</p></section></section></section></section>"
                "<section><title><p>Chapter 2</p></title><p>d</p></section></section>"
                "<section><title><p>Part Two</p></title><p>e</p></section>")
        book = self.convert(body)
        self.assertEqual([(block["l"], block["t"]) for block in book.blocks if block["k"] == "h"], [
            (1, "The Book\na subtitle"), (2, "Part One"), (3, "Chapter 1\nStart"), (4, "Scene"),
            (6, "Deep"), (3, "Chapter 2"), (2, "Part Two")])
        self.assertEqual(self.toc(book), [
            ("Part One", 0, "Part One"), ("Chapter 1 Start", 1, "Chapter 1\nStart"), ("Scene", 2, "Scene"),
            ("Deep", 3, "Deep"), ("Chapter 2", 1, "Chapter 2"), ("Part Two", 0, "Part Two")])
        self.assertEqual([plain(book.blocks[start]) for start in book.sections],
                         ["The Book\na subtitle", "Part One", "Part Two"])
        self.assertEqual([book.blocks[start].get("s") for start in book.sections], [2, 2, 2])

    def test_a_section_that_is_only_a_title_heads_the_next_one(self):
        body = ("<section><title><p>CHAPTER I</p></title></section>"
                "<section><title><p>THE SERGEANT</p></title><p>a</p></section>"
                "<section><title><p>CHAPTER II.</p></title><empty-line/></section>"
                "<section><title><p>THE GUIDE</p></title><p>b</p></section>"
                "<section><title><p>* * *</p></title><p>c</p></section>"
                "<section><title><p>END</p></title><p>d</p></section>")
        book = self.convert(body)
        self.assertEqual(self.toc(book), [("CHAPTER I. THE SERGEANT", 0, "CHAPTER I"),
                                          ("CHAPTER II. THE GUIDE", 0, "CHAPTER II."), ("END", 0, "END")])
        self.assertEqual(self.texts(book), ["CHAPTER I", "THE SERGEANT", "a", "CHAPTER II.", "THE GUIDE", "b",
                                            "hr", "c", "END", "d"])
        self.assertEqual(book.sections, [0, 3, 6, 8])

    def test_notes_come_after_the_text_under_their_titles(self):
        notes = ('<body name="notes"><title><p>Примечания</p></title>'
                 '<section id="n1"><title><p>1</p></title><p>First note.</p></section>'
                 '<section id="n2"><title><p>2</p></title><p>Second note.</p><p>More.</p></section></body>')
        extra = "<body><section><title><p>Afterword</p></title><p>late</p></section></body>"
        body = ('<section><title><p>One</p></title><p>See<a l:href="#n1" type="note">[1]</a> and'
                '<a l:href="#N2" type="note">[2]</a>.</p></section>'
                '<section><title><p>Two</p></title><p>beta</p></section>')
        book = self.convert(body, more=notes + extra)
        self.assertEqual(self.texts(book), [
            "One", 'See<a href="b:7">[1]</a> and<a href="b:9">[2]</a>.', "Two", "beta", "Afterword", "late",
            "Примечания", "<b>1</b>", "First note.", "<b>2</b>", "Second note.", "More."])
        self.assertEqual(self.toc(book)[-2:], [("Afterword", 0, "Afterword"), ("Примечания", 0, "Примечания")])
        self.assertEqual(book.sections, [0, 2, 4, 6])

    def test_an_untitled_notes_body_is_called_notes(self):
        notes = '<body name="Comments"><section id="n"><p>A note.</p></section></body>'
        book = self.convert('<section><title><p>One</p></title><p><a l:href="#n">*</a></p></section>',
                            more=notes)
        self.assertEqual(self.texts(book), ["One", '<a href="b:3">*</a>', "Notes", "A note."])
        self.assertEqual(self.toc(book), [("One", 0, "One"), ("Notes", 0, "Notes")])

    def test_links_reach_anything_with_an_id_and_dead_ones_are_dropped(self):
        body = ('<section><title><p>One</p></title><p id="p1">target</p><p>'
                '<a l:href="#p1">up</a>, <a l:href="#v2">verse</a>, <a l:href="#s2">section</a>, '
                '<a l:href="#nowhere">dead</a>, <a l:href="https://example.org/a?b=1&amp;c=2">out</a>, '
                '<a l:href="javascript:alert(1)">script</a>, <a l:href="other.fb2#x">file</a></p></section>'
                '<section id="s2"><title><p>Two</p></title><poem><stanza><v>one</v><v id="v2">two</v></stanza>'
                '</poem></section>')
        book = self.convert(body)
        self.assertEqual(book.blocks[2]["t"], '<a href="b:1">up</a>, <a href="b:4">verse</a>, '
                                              '<a href="b:3">section</a>, dead, '
                                              '<a href="https://example.org/a?b=1&c=2">out</a>, script, file')

    def test_a_book_without_sections_or_titles(self):
        book = self.convert("<p>Just words.</p><p>And more.</p>")
        self.assertEqual((self.texts(book), book.sections, book.toc), (["Just words.", "And more."], [0], []))


class BlocksTest(Fb2Case):
    def section(self, content, **options):
        book = self.convert("<section><title><p>T</p></title>%s</section>" % content, **options)
        return book.blocks[1:]

    def test_inline_markup(self):
        blocks = self.section(
            "<p>a <emphasis>i</emphasis> <strong>b</strong> <strikethrough>s</strikethrough> "
            "H<sub>2</sub>O E=mc<sup>2</sup> <code>x &lt; y</code> "
            "<style name=\"foreign\">st <emphasis>in</emphasis></style>, <strong><emphasis>bi</emphasis></strong></p>")
        self.assertEqual(blocks[0]["t"], "a <i>i</i> <b>b</b> <s>s</s> H₂O E=mc² x &lt; y st <i>in</i>, "
                                         "<b><i>bi</i></b>")

    def test_epigraph_cite_and_text_author(self):
        blocks = self.section("<epigraph><p>Motto.</p><text-author>Sage</text-author></epigraph>"
                              "<cite><p>Quoted.</p><text-author>Sender</text-author></cite><p>plain</p>")
        self.assertEqual(blocks, [
            {"k": "p", "t": "<i>Motto.</i>", "f": 1, "i": 1}, {"k": "p", "t": "<i>Sage</i>", "f": 1, "a": "r"},
            {"k": "p", "t": "Quoted.", "i": 1, "q": 1}, {"k": "p", "t": "Sender", "a": "r", "q": 1},
            {"k": "p", "t": "plain"}])

    def test_a_stanza_is_one_block_with_a_line_per_verse(self):
        blocks = self.section(
            "<poem><title><p>Song</p></title><stanza><v>First <emphasis>line</emphasis>,</v><v>second line.</v>"
            "</stanza><stanza><title><p>II</p></title><v>Third,</v><v>fourth.</v></stanza>"
            "<text-author>Poet</text-author><date>1900</date></poem><p>after</p>")
        self.assertEqual([(block["t"], block.get("a", ""), block.get("i", 0)) for block in blocks], [
            ("<b>Song</b>", "c", 0), ("First <i>line</i>,<br>second line.", "", 1), ("<b>II</b>", "c", 0),
            ("Third,\nfourth.", "", 1), ("Poet", "r", 0), ("1900", "r", 0), ("after", "", 0)])
        self.assertEqual(blocks[1].get("s"), 1)
        self.assertNotIn("s", blocks[-1])

    def test_empty_lines_subtitles_and_scene_breaks(self):
        blocks = self.section("<p>a</p><p>b</p><p>c</p><p>d</p><empty-line/><p>spaced</p><subtitle>Mid</subtitle>"
                              "<p>e</p><subtitle>* * *</subtitle><p>f</p><empty-line/><subtitle>***</subtitle><p>g</p>")
        self.assertEqual([(plain(block), block.get("s", 0)) for block in blocks], [
            ("a", 0), ("b", 0), ("c", 0), ("d", 0), ("spaced", 1), ("<b>Mid</b>", 0), ("e", 0), ("hr", 0),
            ("f", 0), ("hr", 0), ("g", 0)])
        self.assertEqual(blocks[5]["a"], "c")

    def test_annotation_inside_a_section_is_small_print(self):
        blocks = self.section("<annotation><p>Summary.</p></annotation>" + "<p>body text</p>" * 3)
        self.assertEqual((blocks[0]["t"], blocks[0].get("z")), ("Summary.", -1))

    def test_pictures_come_from_binaries_by_id(self):
        large, small = png(200, 120), png(120, 90)
        more = binary("Big.PNG", large, "image/jpeg") + binary("small", small) + binary("text", b"not a picture")
        blocks = self.section('<image l:href="#big.png" alt="Map"/><p>Before <image l:href="#small"/> after.</p>'
                              '<image l:href="#missing"/><image l:href="#text" title="Caption"/><image/>'
                              '<image l:href="#big.png"/>', more=more)
        self.assertEqual([(block["k"], block.get("w"), block.get("alt", plain(block))) for block in blocks], [
            ("img", 200, "Map"), ("p", None, "Before"), ("img", 120, ""), ("p", None, "after."),
            ("p", None, "Caption"), ("img", 200, "")])
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "img"))), ["0001.png", "0002.png"])
        with open(blocks[0]["src"], "rb") as handle:
            self.assertEqual(handle.read(), large)

    def test_the_cover_opens_the_book_unless_the_body_already_shows_a_picture(self):
        info = INFO + '<coverpage><image l:href="#c"/></coverpage>'
        more = binary("c", png(300, 400)) + binary("d", png(100, 100))
        book = self.convert(info=info, more=more)
        self.assertEqual([(block["k"], block.get("w")) for block in book.blocks[:2]], [("img", 300), ("h", None)])
        self.assertEqual(book.sections[:2], [0, 3])
        book = self.convert('<image l:href="#d"/>' + CHAPTERS, info=info, more=more)
        self.assertEqual([block["w"] for block in book.blocks if block["k"] == "img"], [100])

    def test_tables(self):
        blocks = self.section('<table><tr><th>A</th><th align="right">B</th></tr>'
                              '<tr><td colspan="2">wide <strong>cell</strong></td></tr><tr><td>1</td><td>2</td></tr></table>')
        self.assertEqual(blocks, [{"k": "tbl", "hdr": 1, "rows": [["<b>A</b>", "<b>B</b>"],
                                                                  ["wide <b>cell</b>", ""], ["1", "2"]]}])

    def test_unknown_elements_keep_their_text(self):
        blocks = self.section("<wrapper><p>inside</p></wrapper><odd>loose <strong>words</strong></odd>stray"
                              "<p>a <unknown>b</unknown> c</p>")
        self.assertEqual([block["t"] for block in blocks], ["inside", "loose <b>words</b>", "stray", "a b c"])

    def test_markup_in_the_text_stays_text(self):
        blocks = self.section("<p>&lt;script&gt;alert(1)&lt;/script&gt; &amp;amp; <![CDATA[<b>not bold</b>]]></p>"
                              '<image l:href="#&quot;&gt;&lt;img src=x&gt;"/>')
        self.assertEqual(blocks, [{"k": "p", "t": "<script>alert(1)</script> &amp; <b>not bold</b>"}])
        blocks = self.section("<p><emphasis>&lt;b&gt;</emphasis> &amp;</p>")
        self.assertEqual(blocks, [{"k": "p", "f": 1, "t": "<i>&lt;b&gt;</i> &amp;"}])


class ContainerTest(Fb2Case):
    def zipped(self, name, members):
        path = os.path.join(self.dir, name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for member, data in members:
                archive.writestr(member, data)
        return path

    def test_the_first_fb2_member_of_a_zip_is_the_book(self):
        path = self.zipped("book.fb2.zip", [("readme.txt", "x" * 9000), ("__MACOSX/._a.fb2", "junk"),
                                            ("folder/Книга.FB2", document()), ("z.fb2", document("<p>no</p>"))])
        self.assertEqual(fb2.read_meta(path).title, "Проверочная книга — «Фолио»")
        self.assertEqual(self.texts(fb2.convert(path, self.out)), ["One", "alpha", "Two", "beta"])

    def test_a_member_without_an_extension_is_taken_when_no_fb2_is_named(self):
        path = self.zipped("book.fbz", [("note", "tiny"), ("book", document())])
        self.assertEqual(fb2.read_meta(path).authors, ["Иван Петрович Тестов", "второй"])
        self.assertEqual(len(fb2.convert(path, self.out).blocks), 4)

    def test_damage_is_reported_as_corrupt_and_nothing_else(self):
        cases = {
            "empty": b"", "noise": os.urandom(3000), "words": b"no markup at all",
            "no body": document().replace("<body>", "<x>").replace("</body>", "</x>").encode(),
            "zip of nothing": b"PK\x03\x04" + b"\x00" * 40,
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                path = self.write(content)
                self.assertEqual(self.refusal(lambda: fb2.convert(path, self.out)), "corrupt")
                try:
                    fb2.read_meta(path)
                except ReaderError as error:
                    self.assertEqual(error.code, "corrupt")

    def test_a_missing_file_is_missing(self):
        path = os.path.join(self.dir, "gone.fb2")
        self.assertEqual(self.refusal(lambda: fb2.read_meta(path)), "missing")
        self.assertEqual(self.refusal(lambda: fb2.convert(path, self.out)), "missing")

    def test_a_truncated_book_gives_what_was_there(self):
        content = document("<section><title><p>One</p></title>" + "<p>word</p>" * 500 + "</section>")
        book = fb2.convert(self.write(content[:len(content) // 2]), self.out)
        self.assertGreater(len(book.blocks), 10)

    def test_hostile_shapes_stay_bounded(self):
        deep = "<section><title><p>t</p></title>" * 3000 + "<p>bottom</p>" + "</section>" * 3000
        inline = "<section><p>" + "<emphasis>" * 5000 + "deep" + "</emphasis>" * 5000 + "</p></section>"
        wide = "".join("<section><title><p>%d</p></title><p>x</p></section>" % n for n in range(4000))
        loops = "".join('<section id="s%d"><p><a l:href="#s%d">next</a></p></section>' % (n, (n + 1) % 500)
                        for n in range(500))
        started = time.perf_counter()
        for body in (deep, inline, wide, loops):
            book = self.checked(fb2.convert(self.write(document(body)), self.out))
            self.assertGreaterEqual(len(book.blocks), 1)
            for entry in book.toc:
                self.assertLess(entry["b"], len(book.blocks))
        self.assertLess(time.perf_counter() - started, 20.0)
        self.assertIn("bottom", " ".join(self.texts(fb2.convert(self.write(document(deep)), self.out))))


EXPECTED = {
    "gogol-nevsky-prospekt.fb2.zip": ("Невский проспект", "Николай Васильевич Гоголь", None),
    "pushkin-kapitanskaya-dochka.fb2.zip": ("Капитанская дочка", "Александр Сергеевич Пушкин", None),
    "tolstoy-sevastopol-v-dekabre.fb2.zip": ("Севастополь в декабре месяце", "Лев Николаевич Толстой", None),
    "synthetic-koi8r.fb2": ('Проверочная книга - "Фолио"', "Иван Петрович Тестов, второй_автор", (60, 90)),
    "synthetic-malformed.fb2": ("Broken & Battered FB2", "Molly Malformed", (60, 90)),
}
SYNTHETIC = ("Проверочная книга — «Фолио»", "Иван Петрович Тестов, второй_автор", (60, 90))


@unittest.skipUnless(os.path.isdir(support.sample("fb2")), "the FictionBook samples are not on this machine")
class SamplesTest(Fb2Case):
    def book(self, name):
        return self.checked(fb2.convert(support.sample("fb2", name), self.out))

    def test_every_sample_converts_and_agrees_with_its_metadata(self):
        names = sorted(os.listdir(support.sample("fb2")))
        self.assertTrue(names)
        for name in names:
            with self.subTest(sample=name), tempfile.TemporaryDirectory() as out:
                title, author, cover = EXPECTED.get(name, SYNTHETIC)
                started = time.perf_counter()
                book = self.checked(fb2.convert(support.sample("fb2", name), out))
                self.assertLess(time.perf_counter() - started, 2.0)
                meta = fb2.read_meta(support.sample("fb2", name))
                self.assertEqual((book.title, book.author), (title, author))
                self.assertEqual((meta.title, meta.authors), (book.title, book.authors))
                found = images.sniff(meta.cover) if meta.cover else None
                self.assertEqual(found and (found.width, found.height), cover)
                self.assertEqual(book.language, "en-us" if "malformed" in name else "ru")
                for entry in book.toc:
                    self.assertEqual(book.blocks[entry["b"]]["k"], "h", entry)

    def test_the_synthetic_book_reads_the_same_in_every_encoding_and_container(self):
        names = ("synthetic-cp1251.fb2", "synthetic-utf8-bom.fb2", "synthetic-utf16.fb2",
                 "synthetic-lying-decl.fb2", "synthetic.fb2.zip", "synthetic.fbz")
        books = []
        for name in names:
            with tempfile.TemporaryDirectory() as out:
                book = fb2.convert(support.sample("fb2", name), out)
                books.append([{key: value for key, value in block.items() if key != "src"}
                              for block in book.blocks])
                self.assertEqual(self.toc(book), [
                    ("Часть первая", 0, "Часть первая"), ("Глава 1 Начало", 1, "Глава 1\nНачало"),
                    ("Глава 2", 1, "Глава 2"), ("Раздел 2.1", 2, "Раздел 2.1"),
                    ("Часть вторая", 0, "Часть вторая"), ("Примечания", 0, "Примечания")], name)
        for name, blocks in zip(names, books):
            self.assertEqual(blocks, books[0], name)
        self.assertEqual(len(books[0]), 37)

    def test_the_synthetic_book_in_detail(self):
        book = self.book("synthetic-cp1251.fb2")
        texts = self.texts(book)
        self.assertEqual(texts[:4], ["img", "Проверочная книга\nподзаголовок в заголовке",
                                     "<i>Эпиграф всей книги.</i>", "<i>Автор эпиграфа</i>"])
        self.assertEqual(texts[7], "Первый абзац с <i>курсивом</i>, <b>жирным</b>, <s>зачёркнутым</s>, H₂O, E=mc², "
                                   "code() и style-элементом с <i>вложением</i>.")
        self.assertEqual(texts[8], 'Сноска<a href="b:32">[1]</a> и ещё одна<a href="b:34">[2]</a>, внешняя '
                                   '<a href="https://example.org/">ссылка</a>, внутренняя '
                                   '<a href="b:23">ссылка на главу 2</a>.')
        self.assertEqual(texts[16], "Первая строка стиха,\nВторая строка стиха.")
        self.assertEqual(texts[31:], ["Примечания", "<b>1</b>", "Текст первой сноски.", "<b>2</b>",
                                      "Текст второй сноски.", "Второй абзац сноски."])
        self.assertEqual(book.blocks[22], {"k": "tbl", "hdr": 1, "rows": [
            ["<b>Кол. А</b>", "<b>Кол. Б</b>"], ["объединённая", ""], ["1", "2"]]})
        self.assertEqual(book.sections, [0, 4, 28, 31])
        self.assertEqual([(block["w"], block["h"], block["alt"]) for block in book.blocks if block["k"] == "img"],
                         [(60, 90, ""), (40, 30, "картинка")])

    def test_the_malformed_book(self):
        book = self.book("synthetic-malformed.fb2")
        self.assertEqual(self.toc(book), [("One", 0, "One"), ("Two", 0, "Two"), ("Notes", 0, "Notes")])
        self.assertEqual(self.texts(book), [
            "img", "Broken & Battered", "One",
            "Bare ampersand: AT&amp;T, unknown entity &#160; here, control char [], unclosed <i>emphasis.</i>",
            "Unclosed paragraph before the next one",
            'HTML-isms: <b>b</b> <i>i</i> <u>u</u><br><a href="b:7">plain href</a> and <a href="b:12">*</a>',
            "Upper-case tag", "Two", "Image with wrong-case id:", "img", "and missing binary", "Notes",
            "A note in a body called comments."])

    def test_the_real_books(self):
        pushkin = self.book("pushkin-kapitanskaya-dochka.fb2.zip")
        titles = [entry["t"] for entry in pushkin.toc]
        self.assertEqual(len(titles), 17)
        self.assertEqual(titles[:3] + titles[-1:], ["КАПИТАНСКАЯ ДОЧКА", "ГЛАВА I. СЕРЖАНТ ГВАРДИИ",
                                                    "ГЛАВА II. ВОЖАТЫЙ", "ПРИМЕЧАНИЯ"])
        self.assertGreater(len(pushkin.blocks), 800)
        gogol = self.book("gogol-nevsky-prospekt.fb2.zip")
        self.assertTrue(gogol.blocks[3]["t"].startswith("Нет ничего лучше Невского проспекта"))
        self.assertGreater(len(gogol.blocks), 130)
        self.assertGreater(len(self.book("tolstoy-sevastopol-v-dekabre.fb2.zip").blocks), 60)


if __name__ == "__main__":
    unittest.main()
