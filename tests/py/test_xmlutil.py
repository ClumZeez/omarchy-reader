import support

import time
import unittest
from unittest import mock

from reader import xmlutil
from reader.errors import ReaderError
from reader.xmlutil import attr, children, descendants, local, parse_xml, text_of

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>The   Time
      Machine</dc:title>
    <dc:creator opf:role="aut" opf:file-as="Wells, H. G.">H. G. Wells</dc:creator>
    <meta property="dcterms:modified">2020-01-01T00:00:00Z</meta>
    <meta name="cover" content="cover-image"/>
  </metadata>
  <manifest>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="ch1"/><itemref idref="ch2"/></spine>
</package>
"""


class TestWellFormed(unittest.TestCase):
    def test_names_stay_as_written(self):
        root = parse_xml(OPF.encode("utf-8"))
        self.assertEqual(root.tag, "package")
        self.assertEqual(root.get("xmlns"), "http://www.idpf.org/2007/opf")
        metadata = children(root, "metadata")[0]
        self.assertEqual([child.tag for child in metadata], ["dc:title", "dc:creator", "meta", "meta"])
        self.assertEqual(metadata[1].get("opf:role"), "aut")

    def test_accepts_text(self):
        self.assertEqual(parse_xml("\ufeff" + OPF).tag, "package")

    def test_lookup_helpers(self):
        root = parse_xml(OPF)
        self.assertEqual(local("dc:title"), "title")
        self.assertEqual(local("{http://purl.org/dc/elements/1.1/}title"), "title")
        self.assertEqual(local("title"), "title")
        self.assertEqual(children(root, "item"), [])
        self.assertEqual(len(children(root, "MANIFEST")), 1)
        self.assertEqual([attr(item, "idref") for item in descendants(root, "itemref")], ["ch1", "ch2"])
        self.assertEqual(descendants(root, "package"), [])
        creator = descendants(root, "creator")[0]
        self.assertEqual(attr(creator, "role"), "aut")
        self.assertEqual(attr(creator, "FILE-AS"), "Wells, H. G.")
        self.assertEqual(attr(creator, "opf:role"), "aut")
        self.assertEqual(attr(creator, "missing"), "")
        self.assertEqual(attr(creator, "missing", "x"), "x")
        self.assertEqual(text_of(descendants(root, "title")[0]), "The Time Machine")
        self.assertEqual(text_of(descendants(root, "meta")[0]), "2020-01-01T00:00:00Z")

    def test_attr_ignores_namespace_declarations(self):
        root = parse_xml('<metadata xmlns:opf="http://www.idpf.org/2007/opf" xmlns="urn:x"/>')
        self.assertEqual(attr(root, "opf"), "")
        self.assertEqual(attr(root, "xmlns"), "urn:x")

    def test_text_of_includes_descendants_and_tails(self):
        root = parse_xml("<navLabel>\n <text>Chapter <i>One</i>\n</text> tail\u00a0</navLabel>")
        self.assertEqual(text_of(root), "Chapter One tail\u00a0")

    def test_cdata_and_comments(self):
        root = parse_xml("<a><!-- AT&T <b> --><![CDATA[x < y & z]]></a>")
        self.assertEqual(text_of(root), "x < y & z")
        self.assertEqual(len(root), 0)


class TestRepairsKeepingStrictPath(unittest.TestCase):
    """Damage that is repaired before parsing, so names keep their case."""

    def assert_title(self, markup, title, **options):
        root = parse_xml(markup, **options)
        self.assertEqual(root.tag, "Package")
        self.assertEqual(text_of(descendants(root, "title")[0]), title)

    def test_unbound_prefixes(self):
        root = parse_xml('<Package><dc:title epub:type="x" xlink:href="y">T</dc:title></Package>')
        self.assertEqual(root[0].tag, "dc:title")
        self.assertEqual(attr(root[0], "type"), "x")
        self.assertEqual(attr(root[0], "href"), "y")

    def test_junk_before_the_declaration(self):
        self.assert_title(b'\x00\n  junk <?xml version="1.0"?>\n<Package><title>T</title></Package>'[9:], "T")
        self.assert_title(b'\n\n  <?xml version="1.0"?><Package><title>T</title></Package>', "T")
        self.assert_title(b'junk text <Package><title>T</title></Package>', "T")

    def test_junk_after_the_root(self):
        self.assert_title("<Package><title>T</title></Package>\x00\x00 trailing <junk", "T")
        self.assert_title("<Package><title>T</title></Package><Package><title>U</title></Package>", "T")

    def test_undefined_and_html_entities(self):
        self.assert_title("<Package><title>A&nbsp;B &mdash; &eacute;&hellips; &bogus; &#146;</title></Package>",
                          "A\u00a0B — é… &bogus; ’")

    def test_stray_ampersands(self):
        self.assert_title('<Package><title id="a&b">AT&T & Tom &amp; Jerry &lt;3 &#38;</title></Package>',
                          "AT&T & Tom & Jerry <3 &")
        self.assertEqual(parse_xml('<a href="?x=1&y=2"/>').get("href"), "?x=1&y=2")

    def test_illegal_characters(self):
        self.assert_title("<Package><title>A\x01B\x0cC&#0;&#3;</title></Package>", "ABC")

    def test_wrong_encoding_declarations(self):
        self.assert_title('<?xml version="1.0" encoding="ISO-8859-1"?><Package><title>café 第</title></Package>'
                          .encode("utf-8"), "café 第")
        self.assert_title(b'<?xml version="1.0" encoding="UTF-8"?><Package><title>caf\xe9</title></Package>', "café")
        self.assert_title('<?xml version="1.0" encoding="gbk"?><Package><title>风雪夜归人</title></Package>'
                          .encode("gbk"), "风雪夜归人")
        self.assert_title("<Package><title>Буря</title></Package>".encode("cp1251"), "Буря", language="ru")
        self.assert_title('<?xml version="1.0" encoding="UTF-16"?><Package><title>é</title></Package>'
                          .encode("utf-16"), "é")

    def test_private_entities(self):
        self.assert_title('<!DOCTYPE Package [ <!ENTITY pub "Reader &amp; Sons"> <!ENTITY full \'&pub; Ltd\'>\n'
                          '<!ENTITY % param "ignored"> ]><Package><title>&full; &nope;</title></Package>',
                          "Reader & Sons Ltd &nope;")

    def test_doctype_is_never_loaded(self):
        self.assert_title('<!DOCTYPE Package PUBLIC "-//X//DTD X//EN" "http://example.invalid/x.dtd">'
                          "<Package><title>T&nbsp;U</title></Package>", "T\u00a0U")
        self.assert_title('<!DOCTYPE Package [ <!ENTITY xxe SYSTEM "file:///etc/hostname"> ]>'
                          "<Package><title>[&xxe;]</title></Package>", "[&xxe;]")

    def test_billion_laughs_is_inert(self):
        levels = ['<!ENTITY lol0 "' + "lol" * 20 + '">']
        levels += ['<!ENTITY lol%d "%s">' % (n, ("&lol%d;" % (n - 1)) * 10) for n in range(1, 12)]
        markup = "<!DOCTYPE Package [%s]><Package><title>%s</title></Package>" % ("".join(levels), "&lol11;" * 50)
        started = time.monotonic()
        root = parse_xml(markup)
        self.assertLess(time.monotonic() - started, 2)
        self.assertLess(len(text_of(root)), 2_000_000)

    def test_entity_expansion_is_capped(self):
        markup = '<!DOCTYPE Package [<!ENTITY big "%s">]><Package><title>%s</title></Package>' % (
            "x" * 4000, "&big;" * 100_000)
        self.assertLessEqual(len(text_of(parse_xml(markup))), 1024 * 1024)


class TestTolerantPath(unittest.TestCase):
    def test_unclosed_and_misnested_tags(self):
        root = parse_xml('<package><manifest><item id="a" href="a.xhtml"><item id="b" href="b.xhtml">'
                         '</manifest><spine><itemref idref="a"><itemref idref="b"></wrong></spine>')
        manifest = children(root, "manifest")[0]
        self.assertEqual([attr(item, "id") for item in children(manifest, "item")], ["a", "b"])
        self.assertEqual([attr(ref, "idref") for ref in children(children(root, "spine")[0], "itemref")], ["a", "b"])

    def test_unquoted_and_duplicate_attributes(self):
        root = parse_xml('<package><item id=a id=b href="x.xhtml" "=3></package')
        item = descendants(root, "item")[0]
        self.assertEqual((attr(item, "id"), attr(item, "href")), ("a", "x.xhtml"))
        self.assertEqual(sorted(item.attrib), ["href", "id"])

    def test_tag_case_is_kept_and_lookups_ignore_it(self):
        root = parse_xml('<Package><Metadata><DC:Title>T</dc:title><Meta NAME="cover" CONTENT="c"></Metadata>')
        self.assertEqual(root.tag, "Package")
        title = descendants(root, "title")[0]
        self.assertEqual((title.tag, text_of(title)), ("DC:Title", "T"))
        self.assertEqual(attr(descendants(root, "meta")[0], "Content"), "c")

    def test_meta_keeps_its_text(self):
        root = parse_xml('<package><metadata><meta property="dcterms:modified">2020</meta><meta name="a">'
                         "<dc:title>T</dc:title></metadata><br></package>")
        metas = descendants(root, "meta")
        self.assertEqual(text_of(metas[0]), "2020")
        self.assertEqual(len(metas[1]), 0)
        self.assertEqual(text_of(children(children(root, "metadata")[0], "title")[0]), "T")

    def test_implied_list_item_ends(self):
        root = parse_xml("<nav><ol><li><a href='1'>One</a><li><a href='2'>Two</a><ol><li><a href='3'>Three</a>"
                         "</ol><li><a href='4'>Four<br>IV</a></ol></nav>")
        top = children(children(root, "ol")[0], "li")
        self.assertEqual([text_of(children(item, "a")[0]) for item in top], ["One", "Two", "Four IV"])
        self.assertEqual(len(descendants(top[1], "li")), 1)

    def test_fictionbook_title_holds_paragraphs(self):
        root = parse_xml("<FictionBook><body><title><p>Part One</p><p>Dawn</title><section><p>Text</section>"
                         "<style>a<b>c</b></style><script>x</script>")
        title = descendants(root, "title")[0]
        self.assertEqual([text_of(p) for p in children(title, "p")], ["Part One", "Dawn"])
        self.assertEqual(text_of(descendants(root, "section")[0]), "Text")
        self.assertEqual(len(descendants(root, "b")), 1)

    def test_cdata_and_unterminated_comment(self):
        root = parse_xml("<a><b>one<![CDATA[ <two> ]]><c></b><!-- never closed <d>three</d>")
        self.assertEqual(text_of(root), "one <two> never closed three")
        self.assertEqual(len(descendants(root, "d")), 1)

    def test_content_after_a_stray_root_end_tag_is_kept(self):
        root = parse_xml("<package><metadata></package><manifest><item id='a'></manifest>")
        self.assertEqual(attr(descendants(root, "item")[0], "id"), "a")

    def test_garbage_tags_are_ignored(self):
        root = parse_xml("<a>c<d< e</a><1x>t</1x>")
        self.assertEqual(root.tag, "a")
        self.assertTrue(all(el.tag.isidentifier() for el in root.iter()))

    def test_never_raises_on_noise(self):
        samples = ["<", "<<<>>>", "<a", "<a b=", "</a>", "<!DOCTYPE", "<!DOCTYPE x [", "<?xml", "<!--", "<![CDATA[",
                   "<a><![CDATA[x", "< a>", "<a>" * 5000, "</a>" * 5000 + "<b/>", "<a " + "x" * 100_000,
                   "<a>" + "&" * 10_000 + "</a>", "\x00<\x00a\x00>"]
        for sample in samples:
            with self.subTest(sample[:16]):
                try:
                    parse_xml(sample)
                except ReaderError as error:
                    self.assertEqual(error.code, "corrupt")

    def test_nothing_element_like(self):
        for sample in (b"", b"plain text", "no markup & nothing", b"\xff\xfe", "< <"):
            with self.subTest(sample):
                with self.assertRaises(ReaderError) as caught:
                    parse_xml(sample)
                self.assertEqual(caught.exception.code, "corrupt")

    def test_bounded_on_pathological_input(self):
        deep = "<a>" * 50_000 + "x" + "</b>" * 50_000
        wide = "<r>" + "<e>" * 200_000
        text = "<r>" + "<![CDATA[x]]>y" * 100_000 + "<unclosed"
        started = time.monotonic()
        for markup in (deep, wide, text):
            root = parse_xml(markup)
            self.assertIn(root.tag, ("a", "r"))
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(len(descendants(parse_xml(deep), "a")), 49_999)


class TestBounds(unittest.TestCase):
    def test_a_file_of_nothing_but_tags_is_refused(self):
        with mock.patch.object(xmlutil, "MAX_TAGS", 1000):
            self.assertEqual(len(parse_xml(b"<a>" + b"<b/>" * 998 + b"</a>")), 998)
            for bomb in (b"<a>" + b"<b/>" * 1000 + b"</a>", "<a>" + "<b/>" * 1000 + "</a>"):
                with self.assertRaises(ReaderError) as raised:
                    parse_xml(bomb)
                self.assertEqual(raised.exception.code, "corrupt")


if __name__ == "__main__":
    unittest.main()
