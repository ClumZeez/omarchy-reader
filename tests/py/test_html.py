import support  # noqa: F401  (sets up the import path)

import posixpath
import time
import unittest
from unittest import mock

from reader import html
from reader.blocks import TAG_BUDGET, BookBuilder
from reader.errors import ReaderError
from reader.html import convert_document

NBSP = "\u00a0"


class Book:
    """A book's resources held in memory."""

    def __init__(self, files=None, images=None):
        self.files = files or {}
        self.images = images or {}
        self.asked = []

    def _path(self, href, base):
        href = href.partition("#")[0]
        return posixpath.normpath(posixpath.join(posixpath.dirname(base), href))

    def stylesheet(self, href, base):
        self.asked.append((href, base))
        return self.files.get(self._path(href, base))

    def image(self, href, base):
        size = self.images.get(self._path(href, base))
        if size is None:
            return None
        return {"src": "/cache/" + self._path(href, base), "w": size[0], "h": size[1],
                "al": 1 if len(size) > 2 else 0}

    def document(self, href, base):
        path = self._path(href, base) if href else base
        return path if path in self.files else None


def page(body, css=""):
    style = "<style>%s</style>" % css if css else ""
    return ("<?xml version='1.0' encoding='utf-8'?>\n"
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            "<head><title>T</title>%s</head><body>%s</body></html>" % (style, body))


def build(markup, book=None, name="text/ch1.xhtml"):
    builder = BookBuilder()
    convert_document(builder, markup, name, book or Book({name: markup}))
    builder.finish()
    return builder


def blocks(body, css="", book=None):
    """Blocks of a body fragment, without the chapter space every first block gets."""
    found = build(page(body, css), book).blocks
    if found[0].get("s") == 2:
        del found[0]["s"]
    return found


def texts(body, css="", book=None):
    return [block.get("t") for block in blocks(body, css, book)]


class BlockTests(unittest.TestCase):
    def test_paragraphs(self):
        self.assertEqual(blocks("<p>One.</p>\n<p>Two.</p>"),
                         [{"k": "p", "t": "One."}, {"k": "p", "t": "Two."}])

    def test_headings(self):
        found = blocks("<h1>One</h1><h2>Two <em>b</em></h2><h6>Six</h6>")
        self.assertEqual(found, [{"k": "h", "l": 1, "t": "One"},
                                 {"k": "h", "l": 2, "t": "Two <i>b</i>", "f": 1},
                                 {"k": "h", "l": 6, "t": "Six"}])

    def test_headings_are_recorded_for_the_contents(self):
        builder = build(page("<p>x</p><h2>A <i>title</i></h2><div><h3>Sub</h3></div>"))
        self.assertEqual(builder.headings, [(1, 2, "A title"), (2, 3, "Sub")])

    def test_heading_wrapping_blocks(self):
        self.assertEqual(blocks("<h2><div>Inner</div><span>title</span></h2>"),
                         [{"k": "h", "l": 2, "t": "Inner"}, {"k": "h", "l": 2, "t": "title"}])

    def test_text_in_containers_becomes_paragraphs(self):
        self.assertEqual(texts("loose <div>in div<p>para</p>tail</div><section>more</section> end"),
                         ["loose", "in div", "para", "tail", "more", "end"])

    def test_inline_elements_do_not_break_paragraphs(self):
        self.assertEqual(texts("<p>a <span>b</span> <a>c</a> <unknown>d</unknown> e</p>"), ["a b c d e"])

    def test_empty_blocks_are_dropped(self):
        self.assertEqual(texts("<p></p><div> </div><p>x</p><p>\n</p><h2></h2>"), ["x"])

    def test_horizontal_rule(self):
        self.assertEqual([block["k"] for block in blocks("<p>a</p><hr/><hr/><p>b</p>")],
                         ["p", "hr", "p"])

    def test_preformatted(self):
        found = blocks("<pre>\ndef f(x):\n\treturn  x &lt; <b>1</b>\n\n</pre>")
        self.assertEqual(found, [{"k": "pre", "t": "def f(x):\n    return  x < 1"}])

    def test_pre_with_breaks_and_blocks(self):
        self.assertEqual(texts("<pre>one<br/>two<div>three</div></pre>"), ["one\ntwo\nthree"])

    def test_line_breaks_stay_inside_the_block(self):
        self.assertEqual(texts("<p>one<br/>two<br>three</p>"), ["one\ntwo\nthree"])

    def test_breaks_at_block_edges_are_trimmed(self):
        self.assertEqual(texts("<p><br/>one<br/></p><p>two</p>"), ["one", "two"])

    def test_double_break_is_a_paragraph_break(self):
        self.assertEqual(texts("<div>one<br/><br/>two<br/> <br/> <br/>three</div>"),
                         ["one", "two", "three"])

    def test_double_break_keeps_inline_style(self):
        self.assertEqual(texts("<p><i>one<br/><br/>two</i></p>"), ["<i>one</i>", "<i>two</i>"])

    def test_blockquote(self):
        found = blocks("<blockquote><p>Quoted.</p><blockquote>Deeper</blockquote></blockquote><p>x</p>")
        self.assertEqual(found, [{"k": "p", "t": "Quoted.", "i": 1, "q": 1},
                                 {"k": "p", "t": "Deeper", "i": 2, "q": 1},
                                 {"k": "p", "t": "x"}])

    def test_figure_caption(self):
        book = Book(images={"text/a.png": (300, 200)})
        found = blocks('<figure><img src="a.png" alt="A plan"/><figcaption>Fig. 1</figcaption></figure>',
                       book=book)
        self.assertEqual(found, [
            {"k": "img", "src": "/cache/text/a.png", "w": 300, "h": 200, "alt": "A plan"},
            {"k": "p", "t": "Fig. 1", "a": "c", "z": -1}])

    def test_definition_list(self):
        found = blocks("<dl><dt>Term</dt><dd>Meaning</dd><dt>Other<dd>Second</dl>")
        self.assertEqual(found, [{"k": "p", "t": "Term"}, {"k": "p", "t": "Meaning", "i": 1},
                                 {"k": "p", "t": "Other"}, {"k": "p", "t": "Second", "i": 1}])

    def test_quotation_marks(self):
        self.assertEqual(texts("<p>He said <q>go <q>now</q></q>.</p>"), ["He said “go ‘now’”."])

    def test_ruby(self):
        self.assertEqual(texts("<p><ruby>漢<rp>(</rp><rt>かん</rt><rp>)</rp>字<rt>じ</rt></ruby>!</p>"),
                         ["漢(かん)字(じ)!"])

    def test_math_falls_back_to_text(self):
        self.assertEqual(texts('<p>So <math alttext="x squared"><msup><mi>x</mi><mn>2</mn></msup></math>.</p>'),
                         ["So x squared."])
        self.assertEqual(texts("<p>So <m:math><mi>x</mi> <mo>=</mo> <mn>2</mn>"
                               "<annotation>x = 2 in TeX</annotation></m:math>.</p>"), ["So x = 2."])

    def test_math_layouts_that_mean_something_are_spelled_out(self):
        self.assertEqual(texts("<p>The odds are <math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>, and "
                               "<math><msqrt><mn>16</mn></msqrt><mo>=</mo><mn>4</mn></math>.</p>"),
                         ["The odds are 1/2, and √16=4."])
        self.assertEqual(texts("<p><math><mfrac><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow>\n"
                               "<mn>2</mn></mfrac></math></p>"), ["(a+b)/2"])
        self.assertEqual(texts("<p><math><msup><mi>x</mi><mn>2</mn></msup><mo>+</mo>"
                               "<msub><mi>a</mi><mrow><mi>i</mi><mi>j</mi></mrow></msub></math></p>"),
                         ["x^2+a_ij"])
        self.assertEqual(texts("<p><math><mroot><mn>8</mn><mn>3</mn></mroot><msqrt><mi>x</mi><mo>+</mo>"
                               "<mn>1</mn></msqrt><msubsup><mi>x</mi><mn>1</mn><mn>2</mn></msubsup>"
                               "</math></p>"), ["8^(1/3)√(x+1)x_1^2"])
        self.assertEqual(texts("<p><math><semantics><mfrac><mn>1</mn><mn>2</mn></mfrac><annotation-xml>"
                               "<mfrac><mn>3</mn><mn>4</mn></mfrac></annotation-xml></semantics></math></p>"),
                         ["1/2"])
        self.assertEqual(texts("<p><math><mfrac><mn>1</mn></mfrac></math> and <math><mfrac><mn>1</mn>"
                               "<mn>2</mn></math> end</p>"), ["1 and 1/2 end"])

    def test_math_picture_is_used_when_nothing_describes_it(self):
        book = Book(images={"text/eq.png": (90, 40), "text/half.png": (14, 20)})
        math = '<math altimg="%s"%s><mfrac><mn>1</mn><mn>2</mn></mfrac></math>'
        found = blocks("<p>The odds are %s, he said.</p>" % math % ("eq.png", ""), book=book)
        self.assertEqual([(block["k"], block.get("t"), block.get("alt")) for block in found],
                         [("p", "The odds are", None), ("img", None, "1/2"), ("p", ", he said.", None)])
        self.assertEqual(texts("<p>The odds are %s, he said.</p>" % math % ("half.png", ""), book=book),
                         ["The odds are 1/2, he said."])
        self.assertEqual(texts("<p>The odds are %s.</p>" % math % ("eq.png", ' alttext="one half"'),
                               book=book), ["The odds are one half."])
        self.assertEqual(texts("<p>The odds are %s.</p>" % math % ("gone.png", ""), book=book),
                         ["The odds are 1/2."])

    def test_kindle_frameset_is_not_an_html_frameset(self):
        self.assertEqual(texts("<mbp:frameset><p>a</p><b>b</b><p>c</p></mbp:frameset><p>after</p>"),
                         ["a", "<b>b</b>", "c", "after"])
        self.assertEqual(texts("<frameset><p>no</p></frameset><p>after</p>"), ["after"])

    def test_dropped_elements(self):
        body = ("<p>a<script>var x = '<p>no</p>';</script><iframe>no</iframe><video>no</video>"
                "<select><option>no</option></select><button>no</button><template>no</template>b</p>")
        self.assertEqual(texts(body), ["ab"])

    def test_forms_keep_their_text(self):
        self.assertEqual(texts("<form><p>Name: <input type='text'/></p></form>"), ["Name:"])

    def test_epub_switch_takes_the_default(self):
        body = "<epub:switch><epub:case>fancy</epub:case><epub:default><p>plain</p></epub:default></epub:switch>"
        self.assertEqual(texts(body), ["plain"])

    def test_document_title_is_kept_for_the_contents(self):
        builder = build("<html><head><title> My  Chapter </title></head><body><p>x</p></body></html>")
        self.assertEqual(builder.section_records(), [("text/ch1.xhtml", 0, "My Chapter")])
        self.assertEqual(builder.blocks, [{"k": "p", "t": "x", "s": 2}])

    def test_section_is_begun_once(self):
        builder = BookBuilder()
        builder.begin_section("a")
        convert_document(builder, "<p>one</p>", "a", Book())
        convert_document(builder, "<p>two</p>", "b", Book())
        self.assertEqual(builder.sections, [0, 1])


class ListTests(unittest.TestCase):
    def test_unordered(self):
        self.assertEqual(blocks("<ul><li>a</li><li>b</li></ul>"),
                         [{"k": "li", "m": "•", "t": "a"}, {"k": "li", "m": "•", "t": "b"}])

    def test_ordered(self):
        found = blocks("<ol><li>a</li><li>b</li><li value='7'>c</li><li>d</li></ol>")
        self.assertEqual([block["m"] for block in found], ["1.", "2.", "7.", "8."])

    def test_start_and_type(self):
        self.assertEqual([block["m"] for block in blocks("<ol start='3' type='a'><li>x</li><li>y</li></ol>")],
                         ["c.", "d."])
        self.assertEqual([block["m"] for block in blocks("<ol type='I' start='9'><li>x</li><li>y</li></ol>")],
                         ["IX.", "X."])
        self.assertEqual([block["m"] for block in blocks("<ol type='i'><li>x</li></ol>")], ["i."])
        self.assertEqual([block["m"] for block in blocks("<ol start='27' type='A'><li>x</li></ol>")], ["AA."])
        self.assertEqual([block["m"] for block in blocks("<ol reversed start='2'><li>x</li><li>y</li></ol>")],
                         ["2.", "1."])

    def test_reversed_counts_down_from_the_number_of_items(self):
        body = "<ol reversed='reversed'><li>Bronze</li><li>Silver</li><li>Gold</li></ol>"
        self.assertEqual([block["m"] for block in blocks(body)], ["3.", "2.", "1."])
        body = ("<p>Two\nlines</p>\n<p>before</p>  <OL type='a' REVERSED>\n<li>x<ul><li>p</li><li>q</li></ul>"
                "<ol reversed><li>r</li><li>s</li></ol></li>\n<LI>y<li value='7'>z</li><li>w</ol>"
                "<ol><li>next</li></ol>")
        self.assertEqual([(block["t"], block["m"]) for block in blocks(body)[2:]],
                         [("x", "d."), ("p", "◦"), ("q", "◦"), ("r", "2."), ("s", "1."), ("y", "c."),
                          ("z", "g."), ("w", "f."), ("next", "1.")])
        self.assertEqual([block["m"] for block in blocks("<ol reversed><li>a<li>b")], ["2.", "1."])
        self.assertEqual([block["m"] for block in blocks("<ul reversed><li>a</li></ul>")], ["•"])

    def test_reversed_list_too_long_to_count_is_not_numbered(self):
        body = "<ol reversed><li>a</li><li>%s</li><li>c</li></ol><ol><li>d</li></ol>" % ("b " * 40)
        with mock.patch.object(html, "ITEM_SCAN", 60):
            found = blocks(body)
        self.assertEqual([block["m"] for block in found], ["", "", "", "1."])

    def test_markers_drawn_by_generated_content(self):
        css = ('.lst-a > li { counter-increment: lst-ctn-a } ol.lst-a { list-style-type: none }'
               '.lst-a > li:before { content: "" counter(lst-ctn-a, decimal) ". " }'
               'ul.lst-b { list-style-type: none } .lst-b > li:before { content: "\\0025cf  " }'
               'ul.bare { list-style-type: none } ul.bare > li:before { content: "" }'
               'ol.own > li::before { content: counter(x, upper-roman) }')
        body = ('<ol class="lst-a" start="4"><li>Preheat.</li><li>Mix.</li></ol>'
                '<ul class="lst-b"><li>flour</li></ul><ul class="bare"><li>plain</li></ul>'
                '<ol class="own"><li>numbered by the list</li></ol>')
        self.assertEqual([block["m"] for block in blocks(body, css)], ["4.", "5.", "•", "", "1."])

    def test_list_style_type(self):
        css = ".none { list-style-type: none } .roman { list-style: upper-roman } li.sq { list-style-type: square }"
        self.assertEqual([block["m"] for block in blocks("<ul class='none'><li>x</li></ul>", css)], [""])
        self.assertEqual([block["m"] for block in blocks("<ol class='roman'><li>x</li><li>y</li></ol>", css)],
                         ["I.", "II."])
        self.assertEqual([block["m"] for block in blocks("<ul><li class='sq'>x</li><li>y</li></ul>", css)],
                         ["▪", "•"])
        found = blocks("<ul><li style='list-style: decimal-leading-zero'>x</li></ul>")
        self.assertEqual([block["m"] for block in found], ["01."])

    def test_nested(self):
        found = blocks("<ul><li>a<ol><li>b<ul><li>c</li></ul></li><li>d</li></ol></li><li>e</li></ul>")
        self.assertEqual([(block["t"], block["m"], block.get("i")) for block in found],
                         [("a", "•", None), ("b", "1.", 1), ("c", "▪", 2), ("d", "2.", 1), ("e", "•", None)])

    def test_nested_bullets_change_shape(self):
        found = blocks("<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>")
        self.assertEqual([block["m"] for block in found], ["•", "◦", "▪"])

    def test_item_with_paragraphs(self):
        found = blocks("<ol><li><p>first</p><p>second</p></li><li><p>next</p></li></ol>")
        self.assertEqual([(block["k"], block["m"], block["t"]) for block in found],
                         [("li", "1.", "first"), ("li", "", "second"), ("li", "2.", "next")])

    def test_unclosed_items(self):
        self.assertEqual([block["t"] for block in blocks("<ul><li>a<li>b<li>c</ul><p>after</p>")],
                         ["a", "b", "c", "after"])

    def test_item_outside_a_list(self):
        self.assertEqual(blocks("<li>stray</li>"), [{"k": "li", "m": "•", "t": "stray"}])

    def test_list_margins_do_not_indent(self):
        css = "ul { margin-left: 3em; padding-left: 2em } li { margin-left: 2em }"
        self.assertEqual(blocks("<ul><li>a</li></ul>", css), [{"k": "li", "m": "•", "t": "a"}])


class InlineTests(unittest.TestCase):
    def test_tags(self):
        body = ("<p><i>i</i> <em>em</em> <b>b</b> <strong>s</strong> <u>u</u> <s>s</s> <del>d</del> "
                "<cite>c</cite> <ins>n</ins></p>")
        self.assertEqual(texts(body), ["<i>i em</i> <b>b s</b> <u>u</u> <s>s d</s> <i>c</i> <u>n</u>"])

    def test_class_only_emphasis(self):
        css = ".calibre3 { font-style: italic } .bold { font-weight: bold } .u { text-decoration: underline }"
        body = ('<div class="calibre1">A <span class="calibre3">class</span> '
                '<span class="bold">only</span> <span class="u">book</span>.</div>')
        self.assertEqual(texts(body, css), ["A <i>class</i> <b>only</b> <u>book</u>."])

    def test_block_level_emphasis_is_inherited(self):
        self.assertEqual(texts('<div class="it"><p>All <span class="up">of</span> it</p></div>',
                               ".it { font-style: italic } .up { font-style: normal }"),
                         ["<i>All </i>of<i> it</i>"])

    def test_headings_are_not_marked_bold(self):
        self.assertEqual(blocks("<h1><b>Title</b> <i>here</i></h1>"),
                         [{"k": "h", "l": 1, "t": "Title <i>here</i>", "f": 1}])

    def test_superscript_and_subscript(self):
        self.assertEqual(texts("<p>E = mc<sup>2</sup>, H<sub>2</sub>O, 1<sup>st</sup></p>"),
                         ["E = mc², H₂O, 1st"])
        self.assertEqual(texts('<p>x<span class="s">3</span></p>', ".s { vertical-align: super }"), ["x³"])

    def test_raised_small_text_is_superscript(self):
        css = ".ref { vertical-align: text-top; font-size: 0.6em; font-weight: bold }"
        self.assertEqual(texts('<p><span class="ref">12</span>In the beginning</p>', css),
                         ["<b>¹²</b>In the beginning"])

    def test_text_transform(self):
        css = ".u { text-transform: uppercase } .l { text-transform: lowercase } .c { text-transform: capitalize }"
        self.assertEqual(texts('<p class="u">Straße <i>one</i></p>', css), ["STRASSE <i>ONE</i>"])
        self.assertEqual(texts('<p class="l">LOUD Noise</p>', css), ["loud noise"])
        self.assertEqual(texts('<p class="c">the don’t-<i>stop</i> o<b>n</b>e</p>', css),
                         ["The Don’t-<i>Stop</i> O<b>n</b>e"])
        self.assertEqual(texts('<p class="c">hy&shy;phen hy&shy;<i>phen</i></p>', css),
                         ["Hy\u00adphen Hy\u00ad<i>phen</i>"])

    def test_small_caps_are_upper_case(self):
        self.assertEqual(texts('<p>By <span class="sc">John Smith</span></p>', ".sc { font-variant: small-caps }"),
                         ["By JOHN SMITH"])

    def test_entities(self):
        self.assertEqual(texts("<p>&lt;tag&gt; &amp; &nbsp;&mdash; &#233; &#x2014; &copy;</p>"),
                         ["<tag> & " + NBSP + "— é — ©"])

    def test_c1_numeric_references(self):
        self.assertEqual(texts("<p>&#145;quoted&#146; &#151; &#133;</p>"), ["‘quoted’ — …"])

    def test_unknown_entities_stay_as_text(self):
        self.assertEqual(texts("<p>AT&T &bogus; a & b</p>"), ["AT&T &bogus; a & b"])

    def test_markup_is_escaped(self):
        self.assertEqual(texts("<p><i>a &lt; b</i> &amp; c &gt; d</p>"), ["<i>a &lt; b</i> &amp; c &gt; d"])

    def test_whitespace(self):
        self.assertEqual(texts("<p>  one\n\t two <i> three </i> four  </p>"), ["one two <i>three </i>four"])
        self.assertEqual(texts("<p>a</p>\n   \n<p>b</p>"), ["a", "b"])

    def test_invisible_characters(self):
        self.assertEqual(texts("<p>ze&#x200B;ro&#xFEFF; " + NBSP + "x</p>"), ["zero " + NBSP + "x"])

    def test_soft_hyphens_are_kept_in_text(self):
        self.assertEqual(texts("<p>hy&shy;phen</p><p><i>hy\u00adphen</i> x</p><pre>so&shy;ft</pre>"),
                         ["hy\u00adphen", "<i>hy\u00adphen</i> x", "so\u00adft"])

    def test_soft_hyphens_are_removed_from_names(self):
        book = Book(images={"text/big.jpg": (600, 800)})
        markup = page('<h2>Chap&shy;ter</h2><img src="big.jpg" alt="A&shy; map"/>').replace(
            "<title>T</title>", "<title>Ti&shy;tle</title>")
        builder = build(markup, book)
        self.assertEqual(builder.blocks[0]["t"], "Chap\u00adter")
        self.assertEqual(builder.headings, [(0, 2, "Chapter")])
        self.assertEqual(builder.section_records(), [("text/ch1.xhtml", 0, "Title")])
        self.assertEqual(builder.blocks[1]["alt"], "A map")

    def test_a_soft_hyphen_alone_is_not_a_paragraph(self):
        self.assertEqual(texts("<p>a</p><p>&shy;</p><p>&shy;&nbsp;</p><p>b</p>"), ["a", "b"])

    def test_no_break_spaces(self):
        self.assertEqual(texts("<p>&nbsp;a&nbsp;b 5&#8239;%</p><p>&nbsp;a&nbsp;<i>b</i> 5&#x202F;%</p>"),
                         [NBSP + "a" + NBSP + "b 5\u202f%", "&#160;a&#160;<i>b</i> 5&#8239;%"])

    def test_fixed_width_spaces_collapse_like_ordinary_ones(self):
        self.assertEqual(texts("<p>a&#8201;&#8212;&#8201;b &emsp; c</p><p>&ensp;<i>a</i>&thinsp; b</p>"),
                         ["a \u2014 b c", "<i>a</i> b"])

    def test_white_space_pre_on_a_paragraph(self):
        found = blocks('<p class="code">if x:\n    <i>go</i>  now</p>', ".code { white-space: pre-wrap }")
        self.assertEqual(found, [{"k": "p", "f": 1, "t": "if x:<br>&#160;&#160;&#160;&#160;<i>go</i>&#160; now"}])
        found = blocks('<p class="code">if x:\n    go  now</p>', ".code { white-space: pre-wrap }")
        self.assertEqual(found, [{"k": "p", "t": "if x:\n" + NBSP * 4 + "go" + NBSP + " now"}])

    def test_white_space_pre_line(self):
        self.assertEqual(texts('<p class="v">one   two\n  three</p>', ".v { white-space: pre-line }"),
                         ["one two\nthree"])


class StyleTests(unittest.TestCase):
    def test_alignment(self):
        css = ".c { text-align: center } .r { text-align: right } .j { text-align: justify }"
        found = blocks('<p class="c">c</p><p class="r">r</p><p class="j">j</p><p align="center">a</p>'
                       '<center>old</center><div class="c"><p>in</p><p class="j">out</p></div>', css)
        self.assertEqual([block.get("a") for block in found], ["c", "r", None, "c", "c", "c", None])

    def test_hidden_content(self):
        css = ".gone { display: none } .ghost { visibility: hidden }"
        body = ('<p>a<span class="gone">no</span><span class="ghost">no</span><span hidden="">no</span>'
                '<span style="display:none">no</span>b</p><div class="gone"><p>no</p></div>'
                '<nav hidden="hidden"><ol><li>no</li></ol></nav>')
        self.assertEqual(texts(body, css), ["ab"])

    def test_ids_in_hidden_content_stay_addressable(self):
        builder = build(page('<p>a</p><div style="display:none"><p id="note">hidden</p></div><p>b</p>'
                             '<p><a href="#note">go</a></p>'))
        self.assertEqual(builder.anchor_index("text/ch1.xhtml", "note"), 1)
        self.assertEqual(builder.blocks[2]["t"], '<a href="b:1">go</a>')

    def test_page_number_markers(self):
        body = ('<p>before <span epub:type="pagebreak" id="p12" title="12">12</span>after'
                '<span role="doc-pagebreak" id="p13">13</span></p><p>next</p>')
        builder = build(page(body))
        self.assertEqual(builder.blocks[0]["t"], "before after")
        self.assertEqual(builder.anchor_index("text/ch1.xhtml", "p12"), 0)
        self.assertEqual(builder.anchor_index("text/ch1.xhtml", "p13"), 0)

    def test_font_size(self):
        css = ".s { font-size: 0.7em } .b { font-size: 1.3em } .h { font-size: 200% } body { font-size: 0.9em }"
        body = "<p>normal text that sets the baseline for the book</p>" * 3
        body += '<p class="s">small</p><p class="b">big</p><p class="h">huge</p><p><span class="b">spanned</span></p>'
        found = blocks(body, css)
        self.assertEqual([block.get("z") for block in found], [None, None, None, -1, 1, 2, 1])

    def test_indent(self):
        css = (".q { margin-left: 2em } .deep { margin-left: 3em; padding-left: 3em } .hang { margin-left: 2em;"
               " text-indent: -2em } .first { text-indent: 1.5em } .pct { margin-left: 10% }")
        found = blocks('<p class="q">a</p><p class="deep">b</p><p class="hang">c</p><p class="first">d</p>'
                       '<div class="q"><p class="q">e</p></div><p class="pct">f</p>', css)
        self.assertEqual([block.get("i") for block in found], [1, 2, None, None, 2, 1])

    def test_body_margin_is_ignored(self):
        self.assertEqual(blocks("<p>x</p>", "html { margin-left: 5em } body { padding-left: 5em }"),
                         [{"k": "p", "t": "x"}])

    def test_far_right_short_line_is_right_aligned(self):
        self.assertEqual(blocks('<p class="sig">Your uncle</p>', ".sig { margin-left: 60% }"),
                         [{"k": "p", "t": "Your uncle", "a": "r"}])

    def test_space_above(self):
        css = (".gap { margin-top: 2em } .pad { padding-top: 24pt } .small { margin-top: 1em } "
               ".after { margin-bottom: 3em }")
        body = ('<p>a</p><p class="gap">b</p><p class="pad">c</p><p class="small">d</p>'
                '<p class="after">e</p><p>f</p><p>g</p>' + "<p>x</p>" * 12)
        self.assertEqual([block.get("s") for block in blocks(body, css)[:7]],
                         [None, 1, 1, None, None, 1, None])

    def test_spacer_paragraphs(self):
        body = "<p>a</p><p>&nbsp;</p><p>b</p><p><br/></p><p><br/></p><p>c</p><div>" + NBSP + " </div><p>d</p>"
        body += "<p>x</p>" * 12
        found = blocks(body)
        self.assertEqual([(block["t"], block.get("s")) for block in found[:4]],
                         [("a", None), ("b", 1), ("c", 1), ("d", 1)])

    def test_page_breaks(self):
        css = ".before { page-break-before: always } .after { break-after: page }"
        body = '<p>a</p><p class="before">b</p><div class="after"><p>c</p></div><p>d</p><mbp:pagebreak/><p>e</p>'
        self.assertEqual([block.get("s") for block in blocks(body, css)], [None, 2, None, 2, 2])

    def test_display_block_spans_are_lines(self):
        css = "p span { display: block }"
        self.assertEqual(texts("<p><span>one</span><span>two</span> <span>three</span></p>", css),
                         ["one\ntwo\nthree"])

    def test_verse(self):
        css = ('[epub|type~="z3998:verse"] p > span { display: block; padding-left: 1em; text-indent: -1em }'
               '[epub|type~="z3998:verse"] p > span + br { display: none }'
               "p span.i1 { padding-left: 2em; text-indent: -1em } p span.i2 { padding-left: 3em }")
        body = ('<blockquote epub:type="z3998:verse"><p><span>The movement of the Tao</span><br/>'
                '<span class="i1">By <i>contraries</i> proceeds;</span><br/><span class="i2">And weakness</span>'
                "</p><p><span>Next stanza</span></p></blockquote>")
        found = blocks(body, css)
        self.assertEqual(found[0]["t"], "The movement of the Tao<br>&#160;&#160;"
                         "By <i>contraries</i> proceeds;<br>&#160;&#160;&#160;&#160;And weakness")
        self.assertEqual(found[1]["t"], "Next stanza")
        self.assertEqual(found[0]["q"], 1)

    def test_font_size_attribute_sets_the_size(self):
        body = ('<p align="center"><font size="7">CHAPTER I.</font></p><p>%s</p>'
                '<p><font size="1">A note.</font></p><p><font size="3">Plain.</font></p>' % ("Body. " * 30))
        self.assertEqual([block.get("z") for block in blocks(body)], [2, None, -1, None])

    def test_clipped_box_without_size_is_dropped(self):
        css = ".x { display: block; font-size: 2em; height: 0; overflow: hidden; width: 0 }"
        self.assertEqual(texts('<div class="x">x</div><p>shown</p><div style="overflow: hidden">kept</div>', css),
                         ["shown", "kept"])

    def test_media_conditions(self):
        css = ("@media handheld { .h { display: none } } @media (max-width: 300px) { .a { display: none } }"
               "@media (min-width: 50em) { .b { display: none } }"
               "@media all and (max-width: 400px) { .c { font-style: italic } }")
        body = '<p class="h">one</p><p class="a">two</p><p class="b">three</p><p class="c">four</p>'
        self.assertEqual(texts(body, css), ["one", "two", "three", "<i>four</i>"])

    def test_floated_drop_cap_stays_inline(self):
        css = ".drop { float: left; display: block; font-size: 3em }"
        self.assertEqual(blocks('<p><span class="drop">O</span>nce upon a time</p>', css),
                         [{"k": "p", "t": "Once upon a time"}])

    def test_display_inline_on_a_container(self):
        self.assertEqual(texts('<section>a <div class="i">b</div> c</section>',
                               ".i { display: inline }"), ["a b c"])

    def test_heading_like_paragraphs(self):
        css = ".t { font-weight: bold; text-align: center }"
        body = '<p class="t">Chapter One</p>' + "<p>Body text of the chapter.</p>" * 3
        builder = build(page(body, css))
        self.assertEqual(builder.blocks[0], {"k": "p", "t": "<b>Chapter One</b>", "f": 1, "a": "c", "s": 2})
        self.assertEqual(builder.headings, [(0, 7, "Chapter One")])

    def test_linked_and_imported_stylesheets(self):
        book = Book({"css/main.css": '@import "base.css"; .i { font-style: italic }',
                     "css/base.css": ".b { font-weight: bold } .i { font-style: normal }",
                     "css/print.css": "p { display: none }",
                     "css/alt.css": "p { display: none }"})
        markup = ('<html><head><link rel="stylesheet" type="text/css" href="../css/main.css"/>'
                  '<link rel="stylesheet" media="print" href="../css/print.css"/>'
                  '<link rel="alternate stylesheet" href="../css/alt.css"/>'
                  '<link rel="stylesheet" href="../css/missing.css"/></head>'
                  '<body><p><span class="i">i</span> <span class="b">b</span></p></body></html>')
        self.assertEqual(build(markup, book).blocks[0]["t"], "<i>i</i> <b>b</b>")
        self.assertIn(("base.css", "css/main.css"), book.asked)

    def test_xml_stylesheet_instruction(self):
        book = Book({"text/s.css": "p { font-style: italic }"})
        markup = '<?xml-stylesheet href="s.css" type="text/css"?><html><body><p>x</p></body></html>'
        self.assertEqual(build(markup, book).blocks[0]["t"], "<i>x</i>")

    def test_style_element_variants(self):
        markup = ('<html><head><style type="text/css"><![CDATA[ .a { font-style: italic } ]]></style>'
                  "<style><!-- .b { font-weight: bold } --></style>"
                  '<style media="print">p { display: none }</style>'
                  '<style type="text/x-other">p { display: none }</style></head>'
                  '<body><p><span class="a">a</span><span class="b">b</span></p></body></html>')
        self.assertEqual(build(markup).blocks[0]["t"], "<i>a</i><b>b</b>")

    def test_style_never_leaks_into_text(self):
        self.assertEqual(texts("<style>p > i { color: red }</style><p>x</p><script>if (a < b) { }</script>"), ["x"])


class LinkTests(unittest.TestCase):
    def test_internal_links(self):
        book = Book({"text/ch1.xhtml": "", "text/ch2.xhtml": "", "notes.xhtml": ""})
        builder = BookBuilder()
        one = page('<p id="top">See <a href="ch2.xhtml#n1">note</a>, <a href="#top">top</a>, '
                   '<a href="../notes.xhtml">notes</a> and <a href="ch2.xhtml">next</a>.</p>')
        two = page('<p>filler</p><p><a id="n1"></a>The note. <a href="ch1.xhtml#top">back</a></p>')
        convert_document(builder, one, "text/ch1.xhtml", book)
        convert_document(builder, two, "text/ch2.xhtml", book)
        convert_document(builder, page("<p>All notes</p>"), "notes.xhtml", book)
        builder.finish()
        self.assertEqual(builder.blocks[0]["t"],
                         'See <a href="b:2">note</a>, <a href="b:0">top</a>, '
                         '<a href="b:3">notes</a> and <a href="b:1">next</a>.')
        self.assertEqual(builder.blocks[2]["t"], 'The note. <a href="b:0">back</a>')

    def test_dead_links_are_unwrapped(self):
        found = blocks('<p><a href="gone.xhtml#x">gone</a> and <a href="../up/gone.xhtml">away</a></p>')
        self.assertEqual(found, [{"k": "p", "t": "gone and away"}])

    def test_missing_fragment_in_the_same_document_is_no_link(self):
        self.assertEqual(texts('<p id="yes">a</p><p><a href="ch1.xhtml#nope">x</a> <a href="#nope">y</a> '
                               '<a href="#yes">z</a> <a href="#n%C3%B6">w</a></p><p id="n&#246;">b</p>'),
                         ["a", 'x y <a href="b:0">z</a> <a href="b:2">w</a>', "b"])

    def test_missing_fragment_in_another_document_goes_to_its_start(self):
        book = Book({"text/ch1.xhtml": "", "text/ch2.xhtml": ""})
        builder = BookBuilder()
        convert_document(builder, page('<p><a href="ch2.xhtml#nope">x</a></p>'), "text/ch1.xhtml", book)
        convert_document(builder, page('<p>two</p><p><a href="#nope">y</a></p>'), "text/ch2.xhtml", book)
        builder.finish()
        self.assertEqual([block["t"] for block in builder.blocks], ['<a href="b:1">x</a>', "two", "y"])

    def test_external_links(self):
        body = ('<p><a href="http://example.org/a?b=1&amp;c=2">web</a> <a href="HTTPS://example.org">s</a> '
                '<a href="mailto:a@example.org">mail</a></p>')
        self.assertEqual(texts(body), ['<a href="http://example.org/a?b=1&c=2">web</a> '
                                       '<a href="HTTPS://example.org">s</a> '
                                       '<a href="mailto:a@example.org">mail</a>'])

    def test_href_keeps_its_spaces_encoded(self):
        self.assertEqual(texts('<p><a href=" http://x/a b?q=1&amp;r=2\n#f ">x</a></p>'),
                         ['<a href="http://x/a%20b?q=1&r=2#f">x</a>'])

    def test_external_link_to_nothing_is_dropped(self):
        body = '<p><a href="http:">a</a> <a href="https://">b</a> <a href="mailto: ">c</a> <a href="#">d</a></p>'
        self.assertEqual(texts(body), ["a b c d"])

    def test_other_schemes_are_dropped(self):
        body = ('<p><a href="javascript:alert(1)">js</a> <a href="data:text/html,x">data</a> '
                '<a href="file:///etc/passwd">file</a> <a href="kindle:pos:fid:0:off:0">k</a> <a href="">e</a></p>')
        self.assertEqual(blocks(body), [{"k": "p", "t": "js data file k e"}])

    def test_href_is_escaped(self):
        self.assertEqual(texts('<p><a href=\'http://x/"&gt;&lt;b&gt;&apos;\'>x</a></p>'),
                         ['<a href="http://x/%22%3E%3Cb%3E%27">x</a>'])

    def test_link_with_emphasis_and_note_reference(self):
        book = Book({"text/ch1.xhtml": ""})
        body = ('<p>Text<a href="#n1" epub:type="noteref"><sup>1</sup></a> and <i><a href="#n1">it</a></i>.</p>'
                '<aside epub:type="footnote" id="n1"><p>The note.</p></aside>')
        found = blocks(body, book=book)
        self.assertEqual(found[0]["t"], 'Text<a href="b:1">¹</a> and <a href="b:1"><i>it</i></a>.')
        self.assertEqual(found[1]["t"], "The note.")

    def test_anchors(self):
        body = ('<p>zero</p><a id="a1"/><a name="a2"></a><p>one <span id="mid">x</span></p>'
                '<h2 id="h">two</h2><div id="wrap"><p>three</p></div><p>four<br id="br"/></p><p id="end"></p>')
        builder = build(page(body))
        index = {name: builder.anchor_index("text/ch1.xhtml", name)
                 for name in ("a1", "a2", "mid", "h", "wrap", "br", "end")}
        self.assertEqual(index, {"a1": 1, "a2": 1, "mid": 1, "h": 2, "wrap": 3, "br": 4, "end": 4})

    def test_nested_links_do_not_nest(self):
        found = texts('<p><a href="http://a/">one <a href="http://b/">two</a> three</a></p>')
        self.assertEqual(found, ['<a href="http://a/">one </a><a href="http://b/">two</a> three'])


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.book = Book(images={"text/big.jpg": (600, 800), "text/icon.png": (16, 16),
                                 "text/cap.png": (100, 234), "text/word.png": (141, 18),
                                 "images/cover.jpeg": (1200, 1600), "text/unknown.svg": (0, 0)})

    def test_block_image(self):
        self.assertEqual(blocks('<div><img src="big.jpg" alt="  A  map "/></div>', book=self.book),
                         [{"k": "img", "src": "/cache/text/big.jpg", "w": 600, "h": 800, "alt": "A map"}])

    def test_image_inside_a_paragraph_splits_it(self):
        found = blocks('<p>before <img src="big.jpg" alt="image"/> after</p>', book=self.book)
        self.assertEqual([(block["k"], block.get("t"), block.get("alt")) for block in found],
                         [("p", "before", None), ("img", None, ""), ("p", "after", None)])

    def test_text_after_a_picture_in_a_sentence_is_a_continuation(self):
        body = ('<p>one</p><p>The word <img src="word.png" alt=""/> <img src="word.png"/> means reason,<br/>'
                '<br/>he wrote.</p><p>Next <img src="big.jpg"/></p><p>paragraph.</p>'
                '<p><img src="big.jpg"/> Caption.</p><ul><li>item <img src="word.png"/> goes on</li></ul>')
        found = blocks(body, book=self.book)
        self.assertEqual([(block["k"], block.get("t"), block.get("c")) for block in found],
                         [("p", "one", None), ("p", "The word", None), ("img", None, None),
                          ("img", None, None), ("p", "means reason,", 1), ("p", "he wrote.", None),
                          ("p", "Next", None), ("img", None, None), ("p", "paragraph.", None),
                          ("img", None, None), ("p", "Caption.", None), ("li", "item", None),
                          ("img", None, None), ("li", "goes on", 1)])
        self.assertEqual([block.get("m") for block in found[-3:]], ["•", None, ""])

    def test_drop_cap_picture_of_any_size_becomes_its_letter(self):
        self.assertEqual(blocks('<p><span>“<img alt="I" src="cap.png"/></span> HAVE been thinking,” he said.</p>'
                                '<p><img alt="T" src="cap.png"/>HE end.</p>', book=self.book),
                         [{"k": "p", "t": "“I HAVE been thinking,” he said."}, {"k": "p", "t": "THE end."}])
        found = blocks('<p><img alt="I" src="cap.png"/></p><p>Vitamin <img alt="A" src="cap.png"/> helps.</p>'
                       '<p><img alt="&amp;" src="cap.png"/> so on</p>', book=self.book)
        self.assertEqual([(block["k"], block.get("t"), block.get("alt")) for block in found],
                         [("img", None, "I"), ("p", "Vitamin", None), ("img", None, "A"), ("p", "helps.", None),
                          ("img", None, "&"), ("p", "so on", None)])

    def test_filler_alt_text_is_not_written_into_the_sentence(self):
        body = ('<p>a large red X<img alt="art" src="icon.png"/> and the old sign, '
                '<img alt="inline" src="icon.png"/>. <img alt="Icon 2" src="icon.png"/>'
                '<img alt="decoration" src="gone.png"/><img alt="Spacer" src="icon.png"/></p>')
        self.assertEqual(texts(body, book=self.book), ["a large red X and the old sign, ."])

    def test_alt_text_is_parted_from_the_words_beside_it(self):
        body = ('<p>4 out of 5 marks<img src="icon.png" alt="Very good"/><b>Anna</b>, and '
                '<img src="icon.png" alt="Enter"/> twice (<img src="icon.png" alt="Tab"/>), '
                'then<img src="gone.png" alt="Escape"/>once<img src="icon.png" alt="Up"/>'
                '<img src="icon.png" alt="Down"/>.</p>')
        self.assertEqual(texts(body, book=self.book),
                         ["4 out of 5 marks Very good <b>Anna</b>, and Enter twice (Tab), "
                          "then Escape once Up Down."])

    def test_missing_image_keeps_meaningful_alt(self):
        self.assertEqual(texts('<p>a</p><img src="gone.png" alt="The lost map"/><p>b</p>', book=self.book),
                         ["a", "The lost map", "b"])
        self.assertEqual(texts('<p>a</p><img src="gone.png" alt="image001.png"/><img alt="Image 3"/>'
                               '<img src="gone.png"/><p>b</p>', book=self.book), ["a", "b"])

    def test_small_image_in_text_is_replaced_by_its_alt(self):
        self.assertEqual(blocks('<p>Press <img src="icon.png" alt="Enter"/> twice <img src="icon.png" alt=""/>.</p>',
                                book=self.book), [{"k": "p", "t": "Press Enter twice ."}])

    def test_small_image_alone_is_kept(self):
        found = blocks('<p>a</p><p> <img src="icon.png" alt="ornament"/> </p><p>b</p>', book=self.book)
        self.assertEqual([block["k"] for block in found], ["p", "img", "p"])
        self.assertEqual(found[1]["w"], 16)

    def test_unknown_size_is_a_block(self):
        self.assertEqual([block["k"] for block in blocks('<p>a <img src="unknown.svg"/> b</p>', book=self.book)],
                         ["p", "img", "p"])

    def test_svg_wrapping_one_image(self):
        body = ('<div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                'viewBox="0 0 1200 1600"><image width="1200" height="1600" '
                'xlink:href="../images/cover.jpeg"/></svg></div>')
        self.assertEqual(blocks(body, book=self.book),
                         [{"k": "img", "src": "/cache/images/cover.jpeg", "w": 1200, "h": 1600, "alt": ""}])

    def test_prefixed_svg(self):
        body = '<svg:svg><svg:title>ignored</svg:title><svg:image href="big.jpg"/></svg:svg>'
        self.assertEqual([block["k"] for block in blocks(body, book=self.book)], ["img"])

    def test_other_svg_falls_back_to_its_text(self):
        body = ("<p>Logo: <svg><title>metadata</title><style>text { fill: red }</style><rect/>"
                "<text>ACME</text><text><tspan>Books</tspan></text></svg>!</p>")
        self.assertEqual(texts(body, book=self.book), ["Logo: ACME Books!"])

    def test_svg_without_content_is_dropped(self):
        self.assertEqual(texts('<p>a</p><svg><path d="M0 0"/><image xlink:href="gone.png"/></svg><p>b</p>',
                               book=self.book), ["a", "b"])

    def test_object_image(self):
        found = blocks('<object data="big.jpg"><p>fallback</p></object><object data="x.swf"><p>shown</p></object>',
                       book=self.book)
        self.assertEqual([(block["k"], block.get("t")) for block in found], [("img", None), ("p", "shown")])

    def test_transparency_is_carried(self):
        book = Book(images={"text/line.png": (600, 800, "alpha"), "text/dot.png": (16, 16, "alpha"),
                            "text/big.jpg": (600, 800)})
        body = ('<img src="line.png"/><img src="big.jpg"/><p><img src="dot.png"/></p>'
                '<svg><image href="line.png"/></svg><object data="line.png"></object>')
        self.assertEqual([(block["src"][-8:], block.get("al")) for block in blocks(body, book=book)],
                         [("line.png", 1), ("/big.jpg", None), ("/dot.png", 1), ("line.png", 1),
                          ("line.png", 1)])

    def test_image_anchor(self):
        builder = build(page('<p>a</p><p>b <img id="fig" src="big.jpg"/></p>'), self.book)
        self.assertEqual(builder.anchor_index("text/ch1.xhtml", "fig"), 2)


class TableTests(unittest.TestCase):
    def test_data_table(self):
        body = ("<table><caption>Scores</caption><thead><tr><th>Name</th><th>Score</th></tr></thead>"
                "<tbody><tr><td>Ann &amp; Bo</td><td><i>12</i></td></tr>"
                "<tr><td>Cy<br/>Dee</td><td>7</td></tr></tbody></table>")
        self.assertEqual(blocks(body), [
            {"k": "p", "t": "Scores", "a": "c", "z": -1},
            {"k": "tbl", "hdr": 1, "rows": [["<b>Name</b>", "<b>Score</b>"], ["Ann &amp; Bo", "<i>12</i>"],
                                            ["Cy<br>Dee", "7"]]}])

    def test_cells_follow_the_markup_rules(self):
        body = ('<table><tr><td>a&nbsp;b</td><td><i>c</i>&#8239;d</td></tr>'
                '<tr><td>1 &lt; 2</td><td><a href="http://x/?a=1&amp;b=2">so&shy;ft</a></td></tr></table>')
        self.assertEqual(blocks(body), [{"k": "tbl", "rows": [
            ["a&#160;b", "<i>c</i>&#8239;d"],
            ["1 &lt; 2", '<a href="http://x/?a=1&b=2">so\u00adft</a>']]}])

    def test_header_row_without_thead(self):
        found = blocks("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
        self.assertEqual(found[0]["hdr"], 1)

    def test_spans_keep_columns_aligned(self):
        body = ("<table><tr><td rowspan='2'>a</td><td colspan='2'>b</td></tr>"
                "<tr><td>c</td><td>d</td></tr><tr><td>e</td></tr></table>")
        self.assertEqual(blocks(body)[0]["rows"], [["a", "b", ""], ["", "c", "d"], ["e", "", ""]])

    def test_implied_cells_and_rows(self):
        self.assertEqual(blocks("<table><tr><td>a<td>b<tr><td>c<td>d</table><p>x</p>")[0]["rows"],
                         [["a", "b"], ["c", "d"]])

    def test_single_column_table_is_unwrapped(self):
        self.assertEqual(blocks("<table><tr><td>one</td></tr><tr><td><p>two</p><p>three</p></td></tr></table>"),
                         [{"k": "p", "t": "one"}, {"k": "p", "t": "two"}, {"k": "p", "t": "three"}])

    def test_layout_table_is_unwrapped(self):
        long = "A long paragraph of running text. " * 12
        body = "<table><tr><td><h2>Left</h2><p>%s</p></td><td><p>Right</p></td></tr></table>" % long
        found = blocks(body)
        self.assertEqual([block["k"] for block in found], ["h", "p", "p"])
        self.assertEqual(found[2]["t"], "Right")

    def test_table_with_one_filled_column_is_unwrapped(self):
        body = ("<table><tr><td> </td><td>quote one</td></tr>"
                "<tr><td></td><td>quote two</td></tr></table>")
        self.assertEqual(texts(body), ["quote one", "quote two"])

    def test_marker_table_becomes_a_list(self):
        long = "The text of the note, which runs on for long enough not to be a table cell. " * 5
        body = ("<table><tr><td>1.</td><td><p>%s</p><p>More.</p></td></tr>"
                "<tr><td>[2]</td><td>Short</td></tr></table>" % long)
        found = blocks(body)
        self.assertEqual([(block["k"], block.get("m")) for block in found],
                         [("li", "1."), ("p", None), ("li", "[2]")])

    def test_nested_tables_are_flattened(self):
        body = ("<table><tr><td>outer</td><td><table><tr><td>a</td><td>b</td></tr></table></td></tr></table>")
        found = blocks(body)
        self.assertEqual([block["k"] for block in found], ["p", "tbl"])
        self.assertEqual(found[1]["rows"], [["a", "b"]])

    def test_anchors_and_links_in_cells(self):
        builder = build(page('<p>a</p><table><tr><td id="c1">x</td><td><a href="#c1">go</a></td></tr></table>'
                             '<table><tr><td id="c2"><p>one</p></td></tr></table>'))
        self.assertEqual(builder.blocks[1]["rows"], [["x", '<a href="b:1">go</a>']])
        self.assertEqual(builder.anchor_index("text/ch1.xhtml", "c2"), 2)

    def test_headings_in_layout_tables_are_recorded(self):
        builder = build(page("<table><tr><td><h2>Inside</h2></td></tr></table>"))
        self.assertEqual(builder.headings, [(0, 2, "Inside")])

    def test_stray_table_parts(self):
        self.assertEqual(texts("<tr><td>a</td><td>b</td></tr><p>c</p>"), ["a", "b", "c"])

    def test_text_outside_cells(self):
        self.assertEqual(texts("<table>loose<tr><td>a</td><td>b</td></tr></table>")[0], "loose")

    def test_unclosed_table(self):
        self.assertEqual(blocks("<table><tr><td>a</td><td>b")[0]["rows"], [["a", "b"]])


class MalformedTests(unittest.TestCase):
    def test_xhtml_empty_elements(self):
        self.assertEqual(texts('<p>a<span/>b<a id="x"/>c</p><div/><p/>d<p>e</p><b/>f'), ["abc", "d", "e", "f"])

    def test_void_elements_need_no_end_tag(self):
        self.assertEqual(texts("<p>a<br>b<wbr>c<img>d<hr>e</p>"), ["a\nbcd", None, "e"])

    def test_p_closes_p(self):
        self.assertEqual(texts("<p>one<p>two<p>three"), ["one", "two", "three"])

    def test_block_start_closes_p(self):
        found = blocks('<p class="i">one<div>two</div>three<ul><li>four</ul>', ".i { font-style: italic }")
        self.assertEqual([block["t"] for block in found], ["<i>one</i>", "two", "three", "four"])

    def test_stray_end_tags(self):
        self.assertEqual(texts("</div></p><p>a</span></b> b</p></p></table><p>c</p></html>trailing"),
                         ["a b", "c", "trailing"])

    def test_end_tag_br(self):
        self.assertEqual(texts("<p>a</br>b</p>"), ["a\nb"])

    def test_unclosed_elements_at_end(self):
        self.assertEqual(texts("<div><p><i>a <b>b"), ["<i>a <b>b</b></i>"])

    def test_unclosed_inline_does_not_leak_past_its_block(self):
        self.assertEqual(texts("<p><i>a</p><p>b</p>"), ["<i>a</i>", "b"])

    def test_misnested_inline(self):
        self.assertEqual(texts("<p><b>a<i>b</b>c</i>d</p>"), ["<b>a<i>b</i></b>cd"])

    def test_second_html_and_body(self):
        markup = ("<html><body><p>one</p></body></html>"
                  "<html><head><title>x</title></head><body class='b'><p>two</p></body></html>")
        self.assertEqual([block["t"] for block in build(markup).blocks], ["one", "two"])

    def test_missing_head_end(self):
        markup = "<html><head><title>T</title><style>p{font-style:italic}</style><body><p>x</p></body></html>"
        builder = build(markup)
        self.assertEqual(builder.blocks[0]["t"], "<i>x</i>")
        self.assertEqual(builder.section_records()[0][2], "T")

    def test_no_body(self):
        markup = "<html><head><title>T</title><meta charset='utf-8'><p>straight in</p>"
        self.assertEqual([block["t"] for block in build(markup).blocks], ["straight in"])

    def test_fragment(self):
        self.assertEqual([block["t"] for block in build("just <i>text</i>").blocks], ["just <i>text</i>"])

    def test_unclosed_title(self):
        builder = build("<html><head><title>Lost<body><p>found</p></body></html>")
        self.assertEqual([block["t"] for block in builder.blocks], ["found"])
        self.assertEqual(builder.section_records()[0][2], "Lost")
        self.assertEqual([block["t"] for block in build("<title>T<p>found</p>").blocks], ["found"])

    def test_self_closed_raw_text_elements(self):
        markup = ('<html><head><title/><script src="a.js"/><style/></head><body><p>a</p><script/>'
                  "<textarea/><iframe/><p>b</p></body></html>")
        self.assertEqual([block["t"] for block in build(markup).blocks], ["a", "b"])

    def test_unterminated_comment(self):
        self.assertEqual(texts("<p>one</p><!-- oops <p>two</p><p>three</p>"), ["one", "two", "three"])
        self.assertEqual(texts("<p>one</p><!-- fine --><p>two</p><!-- oops"), ["one", "two"])

    def test_comments_and_instructions_are_dropped(self):
        self.assertEqual(texts("<p>a<!-- hidden --><?pi data?>b<!---->c<!--->d</p>"), ["abcd"])

    def test_cdata(self):
        self.assertEqual(texts("<p>a <![CDATA[<b> & raw]]> b</p>"), ["a <b> & raw b"])
        self.assertEqual(texts("<p>a <![CDATA[ never closed</p><p>next</p>"), ["a never closed", "next"])

    def test_unterminated_script_and_style(self):
        self.assertEqual(texts("<p>one</p><style>p { color: red }<p>two</p>"), ["one", "two"])
        self.assertEqual(texts("<p>one</p><script>var a = 1;<p>two</p>"), ["one", "two"])

    def test_plaintext(self):
        self.assertEqual(blocks("<p>a</p><plaintext>x <b>y"), [{"k": "p", "t": "a"}, {"k": "pre", "t": "x y"}])

    def test_doctype_and_internal_entities(self):
        markup = ('<?xml version="1.0"?>\n<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "x.dtd" [\n'
                  '<!ENTITY pub "Reader &amp; Sons">\n<!ENTITY nbsp "&#160;">\n<!ENTITY % skip "x">\n'
                  '<!ENTITY ext SYSTEM "file:///etc/passwd">\n]>\n'
                  "<html><body><p>&pub;&nbsp;&ext; &pub;</p></body></html>")
        self.assertEqual(build(markup).blocks[0]["t"], "Reader & Sons" + NBSP + "&ext; Reader & Sons")

    def test_doctype_subset_that_never_ends(self):
        markup = "<!DOCTYPE html [ <!ENTITY a 'b'> <p>kept</p><p>also &a;</p>"
        self.assertEqual([block["t"] for block in build(markup).blocks], ["kept", "also &a;"])

    def test_plain_doctype(self):
        self.assertEqual([block["t"] for block in build("<!DOCTYPE html>\n<p>x > y</p>").blocks], ["x > y"])
        subset = build("<!doctype html [ <!ENTITY a 'b'> ]><p>&a;</p>")
        self.assertEqual([block["t"] for block in subset.blocks], ["b"])

    def test_entity_expansion_is_bounded(self):
        markup = "<!DOCTYPE x [<!ENTITY a '%s'>]><p>%s</p>" % ("x" * 1000, "&a;" * 20000)
        started = time.perf_counter()
        builder = build(markup)
        self.assertLess(time.perf_counter() - started, 5)
        self.assertLess(sum(len(block["t"]) for block in builder.blocks), 6_000_000)

    def test_upper_case_markup(self):
        found = blocks('<P CLASS="i">a<BR>b <EM>c</EM></P>', ".i { text-align: center }")
        self.assertEqual(found, [{"k": "p", "t": "a<br>b <i>c</i>", "f": 1, "a": "c"}])

    def test_prefixed_html(self):
        self.assertEqual(texts("<h:p>a <h:i>b</h:i></h:p>"), ["a <i>b</i>"])

    def test_junk_attributes(self):
        self.assertEqual(texts('<p class="a" class="b" style=\'"\' "=""  id>x <img alt="a > b" src=""/> y</p>'),
                         ["x a > b y"])

    def test_bare_angle_brackets(self):
        self.assertEqual(texts("<p>a < b and c <3 then x << y</p>"), ["a < b and c <3 then x << y"])

    def test_control_characters_and_nul(self):
        self.assertEqual(texts("<p>a\x00b\x01c\x0cd\x7f</p>"), ["abc d"])

    def test_empty_input(self):
        for markup in ("", "   ", "<html></html>", "<html><head><title>x</title></head></html>", "<!-- -->"):
            builder = build(markup)
            self.assertEqual(len(builder.blocks), 1)
            self.assertEqual(builder.sections, [0])


class BoundsTests(unittest.TestCase):
    def timed(self, markup, limit=10.0):
        started = time.perf_counter()
        builder = build(markup)
        self.assertLess(time.perf_counter() - started, limit)
        return builder

    def test_deep_nesting_is_flattened(self):
        builder = self.timed("<div>" * 20000 + "deep" + "</div>" * 20000 + "<p>after</p>")
        self.assertEqual([block["t"] for block in builder.blocks], ["deep", "after"])

    def test_deep_inline_nesting(self):
        builder = self.timed("<p>" + "<i>" * 5000 + "x" + "</i>" * 5000 + " y</p><p>z</p>")
        self.assertEqual([block["t"] for block in builder.blocks], ["<i>x</i> y", "z"])

    def test_flattened_blocks_still_separate_paragraphs(self):
        builder = self.timed("<div>" * 300 + "<p>one</p><p>two</p>")
        self.assertEqual([block["t"] for block in builder.blocks], ["one", "two"])

    def test_many_siblings(self):
        builder = self.timed('<p class="a">x</p>' * 8000)
        self.assertEqual(len(builder.blocks), 8000)

    def test_many_inline_siblings(self):
        builder = self.timed("<p>" + "<span>x</span> <b>y</b> " * 6000 + "</p>")
        self.assertGreater(len(builder.blocks), 10)
        self.assertTrue(all(len(block["t"]) <= 2100 for block in builder.blocks))

    def test_one_huge_text_node(self):
        builder = self.timed("<p>" + "word " * 200000 + "</p>")
        self.assertEqual(sum(block["t"].count("word") for block in builder.blocks), 200000)
        self.assertTrue(all(len(block["t"]) <= 2000 for block in builder.blocks))
        self.assertTrue(all(block.get("c") == 1 for block in builder.blocks[1:]))

    def test_block_cap_degrades_gracefully(self):
        with mock.patch.object(html, "MAX_BLOCKS", 200):
            builder = self.timed("<p>x</p>" * 5000 + "<h2>y</h2>")
        self.assertLess(len(builder.blocks), 210)
        self.assertEqual(sum(block["t"].count("x") for block in builder.blocks), 5000)
        self.assertEqual(builder.blocks[-1]["t"][-3:], "x\ny")

    def test_hostile_fragments(self):
        for piece in ("<", "<!", "<!--", "<![CDATA[", "&", "&#", "<?", "<a ", "</", "<p", '<p class="',
                      "<table><tr><td>", "<svg>", "<q>", "<style>", "<title>", "<ul><li>"):
            self.timed(piece * 5000, 5)

    def test_repair_passes_are_linear(self):
        for piece in ("<style>", "<script>", "<script", "<!--", "<![CDATA[", "<!DOCTYPE x [",
                      '<!DOCTYPE x [<!ENTITY a "', "<style>url("):
            self.timed(piece * 60000, 5)

    def test_huge_attributes(self):
        markup = '<p class="%s" id="%s" style="%s">x</p>' % ("c " * 100000, "i" * 100000, "color:red;" * 50000)
        self.assertEqual([block["t"] for block in self.timed(markup).blocks], ["x"])

    def test_wide_and_long_tables(self):
        wide = "<table><tr>" + "<td>c</td>" * 500 + "</tr></table>"
        self.assertEqual({block["k"] for block in self.timed(wide).blocks}, {"p"})
        long = "<table>" + "<tr><td>a</td><td>b</td></tr>" * 5000 + "</table>"
        found = self.timed(long).blocks
        self.assertEqual({block["k"] for block in found}, {"tbl"})
        self.assertEqual(sum(len(block["rows"]) for block in found), 5000)
        self.assertTrue(all(len(block["rows"]) <= 60 for block in found))


class WorkBoundTests(unittest.TestCase):
    def test_a_book_has_a_budget_of_tags(self):
        builder = BookBuilder()
        builder.tags_left = 40
        convert_document(builder, "<p>one</p>" * 10, "a.xhtml", Book())
        self.assertEqual((len(builder.blocks), builder.tags_left), (10, 20))
        # Past it the book is refused, not quietly cut short, and before any
        # of the work is done.
        with mock.patch.object(html, "_Converter", side_effect=AssertionError("worked through")):
            with self.assertRaises(ReaderError) as refused:
                convert_document(builder, "<p>" + "<i/>" * 100 + "two</p>", "b.xhtml", Book())
        self.assertEqual((refused.exception.code, refused.exception.message),
                         ("corrupt", "This book is too large to open."))
        with self.assertRaises(ReaderError):
            convert_document(builder, "<p>later</p>", "c.xhtml", Book())

    def test_an_ordinary_book_spends_almost_nothing(self):
        builder = BookBuilder()
        convert_document(builder, "<p>one</p><p>two</p>", "a.xhtml", Book())
        self.assertEqual(builder.tags_left, TAG_BUDGET - 4)


if __name__ == "__main__":
    unittest.main()
