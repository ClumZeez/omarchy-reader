import support  # noqa: F401  (sets up the import path)

import re
import time
import unittest

from reader import blocks
from reader.blocks import (BOLD, ITALIC, PARAGRAPH_LIMIT, STRIKE, SUB, SUPER, UNDERLINE,
                          BookBuilder, Inline, clean, escape, plain_text, split_text)

ALLOWED_TAG = re.compile(r'<(?:/?[bius]|/a|br|a href="[^"<>]*")>')


def render(*pieces):
    inline = Inline()
    for piece in pieces:
        if piece == "\n":
            inline.line_break()
        elif isinstance(piece, str):
            inline.text(piece)
        else:
            inline.text(*piece)
    return inline.render()


class MergeTests(unittest.TestCase):
    def test_runs_alike_become_one(self):
        items = [["a", 0, ""], ["b", 0, ""], ["c", 1, ""], ["d", 1, ""], ["e", 1, ""],
                 ["f", 0, "x"], ["g", 0, ""], ["h", 0, ""]]
        self.assertEqual(blocks._merge(items),
                         [["ab", 0, ""], ["cde", 1, ""], ["f", 0, "x"], ["gh", 0, ""]])
        self.assertEqual(blocks._merge([]), [])
        self.assertEqual(blocks._merge([["a", 2, ""]]), [["a", 2, ""]])

    def test_a_paragraph_of_line_breaks_costs_in_proportion(self):
        # Joined piece by piece this took a minute; it is a few kilobytes of zip.
        inline = Inline()
        for _ in range(300_000):
            inline.text("x")
            inline.line_break()
        started = time.monotonic()
        text, markup = inline.render()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((text.count("x"), text.count("\n"), markup), (300_000, 299_999, 0))


def balanced(markup):
    """True when every tag in `markup` is allowed, nested and closed."""
    stack = []
    for tag in re.findall(r"<[^>]*>", markup):
        if not ALLOWED_TAG.fullmatch(tag):
            return False
        if tag == "<br>":
            continue
        if tag[1] == "/":
            if not stack or stack.pop() != tag[2]:
                return False
        else:
            stack.append(tag[1])
    return not stack


class InlineTests(unittest.TestCase):
    def test_plain_text_needs_no_markup(self):
        self.assertEqual(render("Hello ", "world"), ("Hello world", 0))

    def test_plain_text_is_not_escaped(self):
        self.assertEqual(render("a < b & c > d"), ("a < b & c > d", 0))

    def test_markup_escapes_text(self):
        self.assertEqual(render("a < b & ", ("c > d", ITALIC)),
                         ("a &lt; b &amp; <i>c &gt; d</i>", 1))

    def test_whitespace_collapses_across_runs(self):
        self.assertEqual(render("  one \n\t two  ", ("  three ", BOLD), "  four  "),
                         ("one two <b>three </b>four", 1))

    def test_edges_are_trimmed(self):
        self.assertEqual(render("\n", "  text  ", "\n", "\n"), ("text", 0))

    def test_bare_until_something_other_than_opening_punctuation(self):
        inline = Inline()
        self.assertTrue(inline.bare)
        inline.text(" “")
        inline.text("(", ITALIC)
        self.assertTrue(inline.bare)
        inline.text("A")
        self.assertFalse(inline.bare)
        inline = Inline()
        inline.text("“")
        inline.line_break()
        self.assertFalse(inline.bare)

    def test_space_before_a_break_is_dropped(self):
        self.assertEqual(render("one  ", "\n", "  two"), ("one\ntwo", 0))

    def test_break_is_a_tag_in_markup(self):
        self.assertEqual(render(("one", ITALIC), "\n", "two"), ("<i>one</i><br>two", 1))

    def test_no_break_space_is_kept(self):
        self.assertEqual(render("\u00a0\u00a0a\u00a0 b"), ("\u00a0\u00a0a\u00a0 b", 0))

    def test_no_break_spaces_are_entities_in_markup(self):
        self.assertEqual(render("\u00a0\u00a0a\u00a0b 5\u202f%", (" c", ITALIC)),
                         ("&#160;&#160;a&#160;b 5&#8239;%<i> c</i>", 1))
        self.assertEqual(render(("one", BOLD), "\n", "\u00a0two"), ("<b>one</b><br>&#160;two", 1))

    def test_fixed_width_spaces_become_ordinary_ones(self):
        self.assertEqual(render("a\u2009b \u2003 c\u2002", ("\u200ad", ITALIC)),
                         ("a b c <i>d</i>", 1))
        self.assertEqual(render("\u2003a\u2003"), ("a", 0))

    def test_invisible_characters_are_removed(self):
        self.assertEqual(render("ze\u200bro b\ufeffom c\x01trl j\u2060oin"),
                         ("zero bom ctrl join", 0))

    def test_soft_hyphens_are_kept(self):
        self.assertEqual(render("so\u00adft"), ("so\u00adft", 0))
        self.assertEqual(render(("hy\u00adphen", ITALIC), " x"), ("<i>hy\u00adphen</i> x", 1))

    def test_soft_hyphens_alone_are_not_text(self):
        inline = Inline()
        inline.text("\u00ad \u00a0\u202f")
        self.assertFalse(inline.visible)

    def test_literal_c1_controls_read_as_windows_1252(self):
        self.assertEqual(clean("it\x92s \x93so\x94 \x81"), "it’s “so” ")

    def test_lone_surrogates_are_removed(self):
        self.assertEqual(clean("a\ud800b"), "ab")

    def test_adjacent_runs_with_one_style_merge(self):
        self.assertEqual(render(("a", ITALIC), ("b", ITALIC), ("c", ITALIC)), ("<i>abc</i>", 1))

    def test_space_between_styled_runs_joins_them(self):
        self.assertEqual(render(("a", ITALIC), " ", ("b", ITALIC)), ("<i>a b</i>", 1))

    def test_break_between_styled_runs_joins_them(self):
        self.assertEqual(render(("a", BOLD), "\n", ("b", BOLD)), ("<b>a<br>b</b>", 1))

    def test_entirely_bold_block_still_uses_tags(self):
        inline = Inline()
        inline.text("All bold", BOLD)
        self.assertEqual(inline.render(), ("<b>All bold</b>", 1))
        self.assertTrue(inline.bold)

    def test_partly_bold_block_is_not_emphatic(self):
        inline = Inline()
        inline.text("Some ", BOLD)
        inline.text("not")
        inline.render()
        self.assertFalse(inline.bold)

    def test_nesting_opens_the_longer_style_first(self):
        text, markup = render(("a", BOLD | ITALIC), ("b", ITALIC), ("c", ITALIC | UNDERLINE))
        self.assertEqual(text, "<i><b>a</b>b<u>c</u></i>")
        self.assertEqual(markup, 1)

    def test_overlapping_styles_stay_properly_nested(self):
        text, _ = render(("a", BOLD), ("b", BOLD | ITALIC), ("c", ITALIC), ("d", STRIKE))
        self.assertTrue(balanced(text), text)
        self.assertEqual(plain_text(text, 1), "abcd")
        self.assertEqual(text, "<b>a<i>b</i></b><i>c</i><s>d</s>")

    def test_links(self):
        text, _ = render("see ", ("this", 0, "http://example.org/?a=1&b=2"), " now")
        self.assertEqual(text, 'see <a href="http://example.org/?a=1&b=2">this</a> now')

    def test_href_is_percent_encoded_not_escaped(self):
        cases = {
            "http://e.org/?a=1&b=2&amp;c": "http://e.org/?a=1&b=2&amp;c",
            "http://e.org/a\"b'c": "http://e.org/a%22b%27c",
            "http://e.org/<a b>": "http://e.org/%3Ca%20b%3E",
            " http://e.org/a\tb\nc\x01d\x7fe\x85f ": "http://e.org/abcdef",
            "http://e.org/a\u00a0b\u2003c": "http://e.org/a%C2%A0b%E2%80%83c",
            "http://e.org/caf\u00e9": "http://e.org/caf\u00e9",
        }
        for href, written in cases.items():
            with self.subTest(href=href):
                self.assertEqual(render(("x", 0, href)), ('<a href="%s">x</a>' % written, 1))

    def test_href_with_nothing_left_is_no_link(self):
        self.assertEqual(render("a ", ("b", 0, " \x01\n")), ("a b", 0))
        self.assertEqual(render("a ", ("b", ITALIC, "\x02")), ("a <i>b</i>", 1))

    def test_link_spanning_styles_is_one_element(self):
        text, _ = render(("a", ITALIC, "http://x/"), ("b", 0, "http://x/"))
        self.assertEqual(text, '<a href="http://x/"><i>a</i>b</a>')

    def test_adjacent_links_stay_separate(self):
        text, _ = render(("a", 0, "http://x/"), ("b", 0, "http://y/"))
        self.assertEqual(text, '<a href="http://x/">a</a><a href="http://y/">b</a>')

    def test_superscript_maps_when_every_character_can(self):
        self.assertEqual(render("x", ("2", SUPER), " and note", ("12", SUPER)),
                         ("x² and note¹²", 0))
        self.assertEqual(render("x", ("n+1", SUPER)), ("xⁿ⁺¹", 0))

    def test_superscript_stays_inline_otherwise(self):
        self.assertEqual(render("M", ("lle", SUPER)), ("Mlle", 0))
        self.assertEqual(render("note", ("*", SUPER)), ("note*", 0))

    def test_superscript_group_is_judged_as_a_whole(self):
        self.assertEqual(render("x", ("1", SUPER), ("a", SUPER | ITALIC)), ("x1<i>a</i>", 1))

    def test_subscript(self):
        self.assertEqual(render("H", ("2", SUB), "O"), ("H₂O", 0))
        self.assertEqual(render("x", ("i", SUB)), ("xi", 0))

    def test_soft_break_never_makes_an_empty_line(self):
        inline = Inline()
        inline.line_break(soft=True)
        inline.text("one")
        inline.line_break(soft=True)
        inline.line_break(soft=True)
        inline.text("two")
        inline.line_break(soft=True)
        self.assertEqual(inline.render(), ("one\ntwo", 0))

    def test_hard_breaks_keep_an_empty_line_inside(self):
        self.assertEqual(render("one", "\n", "\n", "two"), ("one\n\ntwo", 0))

    def test_indent_applies_to_the_next_line_only(self):
        inline = Inline()
        inline.indent(2)
        inline.text("one")
        inline.line_break()
        inline.text("two")
        self.assertEqual(inline.render(), ("\u00a0\u00a0one\ntwo", 0))

    def test_indent_of_an_empty_line_is_forgotten(self):
        inline = Inline()
        inline.text("one")
        inline.line_break()
        inline.indent(4)
        inline.line_break(soft=True)
        inline.text("two")
        self.assertEqual(inline.render(), ("one\ntwo", 0))

    def test_preformatted_keeps_lines_and_spaces(self):
        inline = Inline()
        inline.preformatted("  if x:\n\treturn  1\n")
        self.assertEqual(inline.render(),
                         ("\u00a0\u00a0if x:\n\u00a0\u00a0\u00a0\u00a0return\u00a0 1", 0))

    def test_preformatted_lines_only(self):
        inline = Inline()
        inline.preformatted("one   two\n  three", keep_spaces=False)
        self.assertEqual(inline.render(), ("one two\nthree", 0))

    def test_visible_ignores_spaces(self):
        inline = Inline()
        inline.text("\u00a0 \u00a0")
        self.assertFalse(inline.visible)
        inline.text("x")
        self.assertTrue(inline.visible)

    def test_dominant_size(self):
        inline = Inline()
        inline.text("T", 0, "", 3.0)
        inline.text("he rest of the paragraph", 0, "", 1.0)
        self.assertEqual(inline.dominant_size(), 1.0)

    def test_empty(self):
        inline = Inline()
        inline.text("   ")
        inline.line_break()
        self.assertEqual(inline.render(), ("", 0))
        self.assertFalse(inline)


class HelperTests(unittest.TestCase):
    def test_escape(self):
        self.assertEqual(escape('<a href="x">&</a>'), '&lt;a href="x"&gt;&amp;&lt;/a&gt;')
        self.assertEqual(escape("a\u00a0b\u202fc &#160;"), "a&#160;b&#8239;c &amp;#160;")

    def test_plain_text(self):
        self.assertEqual(plain_text("one\n two"), "one two")
        self.assertEqual(plain_text('<i>T</i>ao, &amp; <a href="b:1">x</a><br>y &lt;', 1),
                         "Tao, & x y <")

    def test_plain_text_reads_entities_and_drops_soft_hyphens(self):
        self.assertEqual(plain_text("<b>a&#160;b</b>&#8239;c &amp;#160; so\u00adft", 1),
                         "a b c &#160; soft")
        self.assertEqual(plain_text("so\u00adft"), "soft")


class SplitTests(unittest.TestCase):
    def test_short_text_is_left_alone(self):
        self.assertEqual(split_text("short", 0), [("short", 0)])

    def test_prefers_a_line_break(self):
        text = "a" * 1500 + "\n" + "b. " * 600
        pieces = split_text(text, 0)
        self.assertEqual(pieces[0], ("a" * 1500, 0))
        self.assertTrue(all(len(piece) <= PARAGRAPH_LIMIT for piece, _ in pieces))

    def test_then_a_sentence_end(self):
        sentence = "This is one sentence of the paragraph. "
        pieces = split_text(sentence * 200, 0)
        self.assertGreater(len(pieces), 2)
        for piece, markup in pieces:
            self.assertLessEqual(len(piece), PARAGRAPH_LIMIT)
            self.assertTrue(piece.startswith("This is"))
            self.assertTrue(piece.endswith("paragraph."))
            self.assertEqual(markup, 0)
        self.assertEqual(" ".join(piece for piece, _ in pieces), (sentence * 200).strip())

    def test_then_a_space(self):
        pieces = split_text("word " * 2000, 0)
        for piece, _ in pieces:
            self.assertLessEqual(len(piece), PARAGRAPH_LIMIT)
            self.assertFalse(piece.startswith(" ") or piece.endswith(" "))
            self.assertEqual(set(piece.split()), {"word"})

    def test_unbroken_text_is_cut_hard(self):
        pieces = split_text("x" * 7000, 0)
        self.assertEqual([len(piece) for piece, _ in pieces], [2000, 2000, 2000, 1000])

    def test_limit_is_two_thousand_characters(self):
        self.assertEqual(PARAGRAPH_LIMIT, 2000)

    def test_markup_pieces_are_balanced(self):
        text = "<i>" + 'Some <b>bold</b> words &amp; <a href="b:3">a link</a> here. ' * 150 + "</i>"
        pieces = split_text(text, 1)
        self.assertGreater(len(pieces), 2)
        for piece, markup in pieces:
            self.assertEqual(markup, 1)
            self.assertTrue(balanced(piece), piece[-80:])
            self.assertTrue(piece.startswith("<i>Some") and piece.endswith("</i>"))
            self.assertIsNone(re.search(r"&(?!amp;|lt;|gt;|quot;)", piece))
        self.assertEqual(" ".join(plain_text(piece, 1) for piece, _ in pieces),
                         plain_text(text, 1))

    def test_never_cuts_inside_a_tag_or_entity(self):
        text = '<a href="http://example.org/a/very/long/address">x</a>&amp;' * 400
        for piece, markup in split_text(text, 1):
            self.assertTrue(balanced(piece))
            self.assertIsNone(re.search(r"&(?!amp;)", piece))
            self.assertNotRegex(piece, r"<[^>]*$")

    def test_an_entity_is_one_character(self):
        text = "<i>x</i>" + "&#160;" * 1990
        self.assertEqual(split_text(text, 1), [(text, 1)])
        self.assertEqual(split_text("&amp;" * 2000, 1), [("&amp;" * 2000, 1)])

    def test_never_cuts_inside_a_no_break_space(self):
        text = "<i>x</i> " + "word&#160;word&#8239;word " * 400
        pieces = split_text(text, 1)
        self.assertGreater(len(pieces), 2)
        for piece, markup in pieces:
            self.assertLessEqual(len(plain_text(piece, markup)), PARAGRAPH_LIMIT)
            self.assertNotRegex(piece, r"&(?!#160;|#8239;)|(?<!&)#|(?<!\d);")
        self.assertEqual(pieces[0][1], 1)
        # A piece that needs no tags is plain text, where the spaces are literal.
        self.assertEqual(pieces[1][1], 0)
        self.assertTrue(pieces[1][0].startswith("word\u00a0word\u202fword word"))
        self.assertEqual(" ".join(plain_text(piece, markup) for piece, markup in pieces),
                         plain_text(text, 1))

    def test_an_unbroken_run_of_entities_is_cut_between_them(self):
        pieces = split_text("<i>x</i>" + "&#160;y" * 3000, 1)
        self.assertGreater(len(pieces), 2)
        for piece, markup in pieces:
            self.assertLessEqual(len(plain_text(piece, markup).replace(" ", "")), PARAGRAPH_LIMIT)
            self.assertNotRegex(piece, r"&(?!#160;)|(?<!&)#|(?<!\d);")

    def test_never_cuts_at_a_no_break_space(self):
        text = "Good day to you all. " * 60 + "x" * 600 + " Mr.\u00a0Smith and Mrs.\u202fSmith " + "y" * 1300
        pieces = split_text(text, 0)
        self.assertEqual(len(pieces), 2)
        self.assertTrue(pieces[0][0].endswith("you all."))
        self.assertIn("Mr.\u00a0Smith and Mrs.\u202fSmith", pieces[1][0])
        pieces = split_text("z" * 1900 + " a\u00a0b\u00a0c\u00a0d " * 40, 0)
        self.assertFalse(any(piece[:1] in "\u00a0" or piece.endswith("\u00a0") for piece, _ in pieces))

    def test_a_cut_after_a_closing_tag_leaves_no_space_behind(self):
        # Unpunctuated prose (the last chapter of Ulysses): the only place to
        # cut is where the next run of words would no longer fit.
        text = "since the <i>City Arms</i> " + "hotel when he used to be " * 400
        pieces = split_text(text, 1)
        self.assertGreater(len(pieces), 2)
        self.assertTrue(pieces[0][0].startswith("since the <i>City Arms</i> hotel when"))
        for piece, markup in pieces:
            self.assertEqual(piece, piece.strip(" "))
            self.assertLessEqual(len(plain_text(piece, markup)), PARAGRAPH_LIMIT)
        # Each piece is filled before the next begins: no stub ahead of a full one.
        for piece, markup in pieces[:-1]:
            self.assertGreater(len(plain_text(piece, markup)), PARAGRAPH_LIMIT * 0.8)
        self.assertEqual(" ".join(plain_text(piece, markup) for piece, markup in pieces),
                         plain_text(text, 1))

    def test_no_break_indentation_survives_a_cut(self):
        text = "a" * 1500 + "\n\u00a0\u00a0" + "b " * 600
        self.assertTrue(split_text(text, 0)[1][0].startswith("\u00a0\u00a0b b"))
        text = "<i>" + "a" * 1500 + "</i><br>&#160;&#160;<i>b</i> " + "b " * 600
        self.assertTrue(split_text(text, 1)[1][0].startswith("&#160;&#160;<i>b</i> b"))

    def test_break_tag_is_consumed_by_the_cut(self):
        text = "<i>" + "a" * 1500 + "</i><br>" + "b " * 1000
        pieces = split_text(text, 1)
        self.assertEqual(pieces[0], ("<i>" + "a" * 1500 + "</i>", 1))
        self.assertEqual(pieces[1][1], 0)
        self.assertFalse(pieces[1][0].startswith("<br>"))

    def test_plain_piece_of_markup_becomes_plain(self):
        text = "<b>Title</b> " + "plain &amp; simple. " * 300
        pieces = split_text(text, 1)
        self.assertEqual(pieces[0][1], 1)
        self.assertEqual(pieces[-1][1], 0)
        self.assertIn("plain & simple.", pieces[-1][0])


class BuilderTests(unittest.TestCase):
    def test_add_returns_indexes(self):
        builder = BookBuilder()
        self.assertEqual(builder.add({"k": "p", "t": "one"}), 0)
        self.assertEqual(builder.add({"k": "hr"}), 1)
        self.assertEqual(builder.add({"k": "p", "t": "two"}), 2)

    def test_empty_blocks_are_not_stored(self):
        builder = BookBuilder()
        self.assertEqual(builder.add({"k": "p", "t": ""}), 0)
        self.assertEqual(builder.add({"k": "p", "t": " \u00a0 "}), 0)
        self.assertEqual(builder.add({"k": "pre", "t": "\n\n"}), 0)
        self.assertEqual(builder.add({"k": "img", "src": "", "w": 1, "h": 1, "alt": ""}), 0)
        self.assertEqual(builder.add({"k": "tbl", "rows": []}), 0)
        self.assertEqual(builder.add({"k": "bogus"}), 0)
        self.assertEqual(builder.blocks, [])

    def test_at_least_one_block(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.finish()
        self.assertEqual(len(builder.blocks), 1)
        self.assertEqual(builder.blocks[0]["k"], "p")
        self.assertTrue(builder.blocks[0]["t"])
        self.assertEqual(builder.sections, [0])

    def test_sections(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "t": "one"})
        builder.add({"k": "p", "t": "two"})
        builder.begin_section("empty")
        builder.begin_section("b")
        builder.add({"k": "p", "t": "three"})
        builder.finish()
        self.assertEqual(builder.sections, [0, 2])
        self.assertEqual(builder.anchor_index("a"), 0)
        self.assertEqual(builder.anchor_index("empty"), 2)
        self.assertEqual(builder.anchor_index("b"), 2)
        self.assertIsNone(builder.anchor_index("missing"))
        self.assertEqual([name for name, _, _ in builder.section_records()], ["a", "b"])

    def test_sections_start_at_zero_without_begin_section(self):
        builder = BookBuilder()
        builder.add({"k": "p", "t": "one"})
        self.assertEqual(builder.sections, [0])

    def test_repeating_the_current_section_is_harmless(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "t": "one"})
        builder.begin_section("a")
        builder.add({"k": "p", "t": "two"})
        self.assertEqual(builder.sections, [0])

    def test_section_start_gets_chapter_space(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "t": "one", "s": 1})
        builder.add({"k": "p", "t": "two"})
        builder.begin_section("b")
        builder.add({"k": "img", "src": "/x.png", "w": 1, "h": 1, "alt": ""})
        builder.finish()
        self.assertEqual(builder.blocks[0]["s"], 2)
        self.assertNotIn("s", builder.blocks[1])
        self.assertNotIn("s", builder.blocks[2])

    def test_anchor_names_the_next_block(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "t": "one"})
        builder.anchor("x")
        builder.add({"k": "p", "t": ""})
        builder.add({"k": "p", "t": "two"})
        builder.anchor("end")
        builder.finish()
        self.assertEqual(builder.anchor_index("a", "x"), 1)
        self.assertEqual(builder.anchor_index("a", "end"), 1)
        self.assertIsNone(builder.anchor_index("a", "nope"))
        self.assertIsNone(builder.anchor_index("b", "x"))

    def test_first_duplicate_anchor_wins(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.anchor("x")
        builder.add({"k": "p", "t": "one"})
        builder.anchor("x")
        builder.add({"k": "p", "t": "two"})
        self.assertEqual(builder.anchor_index("a", "x"), 0)

    def test_anchor_lookup_is_lenient(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "t": "one"})
        builder.anchor("Chapter 2")
        builder.add({"k": "p", "t": "two"})
        self.assertEqual(builder.anchor_index("a", "Chapter%202"), 1)
        self.assertEqual(builder.anchor_index("a", "chapter 2"), 1)

    def test_anchor_across_sections(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "t": "one"})
        builder.anchor("tail")
        builder.begin_section("b")
        builder.add({"k": "p", "t": "two"})
        self.assertEqual(builder.anchor_index("a", "tail"), 1)

    def test_links_resolve_at_finish(self):
        builder = BookBuilder()
        builder.begin_section("a")
        forward = builder.link("b", "note")
        builder.add({"k": "p", "f": 1, "t": '<a href="%s">note</a>' % forward})
        builder.begin_section("b")
        builder.add({"k": "p", "t": "filler"})
        builder.anchor("note")
        builder.add({"k": "p", "f": 1, "t": '<a href="%s">back</a> text' % builder.link("a", "")})
        builder.finish()
        self.assertEqual(builder.blocks[0]["t"], '<a href="b:2">note</a>')
        self.assertEqual(builder.blocks[2]["t"], '<a href="b:0">back</a> text')

    def test_missing_fragment_in_another_document_goes_to_its_start(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "f": 1, "t": '<a href="%s">x</a>' % builder.link("b", "gone")})
        builder.begin_section("b")
        builder.add({"k": "p", "t": "two"})
        builder.finish()
        self.assertEqual(builder.blocks[0]["t"], '<a href="b:1">x</a>')

    def test_missing_fragment_in_the_same_document_is_a_dead_link(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "t": "one"})
        builder.begin_section("b")
        builder.anchor("here")
        builder.add({"k": "p", "f": 1, "t": '<a href="%s">x</a> <a href="%s">y</a> <a href="%s">z</a>'
                     % (builder.link("b", "gone"), builder.link("b", "here"), builder.link("b", ""))})
        builder.finish()
        self.assertEqual(builder.blocks[1]["t"], 'x <a href="b:1">y</a> <a href="b:1">z</a>')

    def test_the_same_target_is_judged_from_where_the_link_is(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "f": 1, "t": '<a href="%s">x</a>' % builder.link("b", "gone")})
        builder.begin_section("b")
        builder.add({"k": "p", "f": 1, "t": '<a href="%s">y</a>' % builder.link("b", "gone")})
        builder.finish()
        self.assertEqual(builder.blocks[0]["t"], '<a href="b:1">x</a>')
        self.assertEqual(builder.blocks[1], {"k": "p", "t": "y", "s": 2})

    def test_resolved_markup_without_tags_keeps_its_spaces(self):
        builder = BookBuilder()
        builder.begin_section("a")
        dead = builder.link("nowhere", "")
        builder.add({"k": "p", "f": 1, "t": '&#160;a&#8239;b <a href="%s">c</a>' % dead})
        builder.add({"k": "tbl", "rows": [['x&#160;<a href="%s">y</a>' % dead, "z"]]})
        builder.finish()
        self.assertEqual(builder.blocks[0], {"k": "p", "t": "\u00a0a\u202fb c", "s": 2})
        self.assertEqual(builder.blocks[1]["rows"], [["x&#160;y", "z"]])

    def test_dead_link_is_unwrapped(self):
        builder = BookBuilder()
        builder.begin_section("a")
        dead = builder.link("nowhere", "x")
        builder.add({"k": "p", "f": 1,
                     "t": 'a <a href="%s"><i>dead</i></a> and <a href="http://x/">live</a>' % dead})
        builder.add({"k": "p", "f": 1, "t": 'only &amp; <a href="%s">dead</a><br>here' % dead})
        builder.finish()
        self.assertEqual(builder.blocks[0]["t"], 'a <i>dead</i> and <a href="http://x/">live</a>')
        self.assertEqual(builder.blocks[1], {"k": "p", "t": "only & dead\nhere"})

    def test_links_in_table_cells(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "tbl", "rows": [['<a href="%s">x</a>' % builder.link("a", ""),
                                           '<a href="%s">y</a>' % builder.link("zz", "")]]})
        builder.finish()
        self.assertEqual(builder.blocks[0]["rows"], [['<a href="b:0">x</a>', "y"]])

    def test_placeholder_is_opaque_and_stable(self):
        builder = BookBuilder()
        self.assertEqual(builder.link("a", "x"), builder.link("a", "x"))
        self.assertNotEqual(builder.link("a", "x"), builder.link("a", "y"))
        self.assertNotIn('"', builder.link("a", "x"))

    def test_headings_are_recorded(self):
        builder = BookBuilder()
        builder.add({"k": "p", "t": "intro"})
        builder.add({"k": "h", "l": 2, "f": 1, "t": "The <i>Second</i><br>Part"})
        builder.add({"k": "h", "l": 9, "t": "odd"})
        self.assertEqual(builder.headings, [(1, 2, "The Second Part"), (2, 6, "odd")])
        self.assertEqual(builder.blocks[2]["l"], 6)

    def test_long_paragraph_is_split_into_continuations(self):
        builder = BookBuilder()
        builder.anchor("x")
        first = builder.add({"k": "p", "a": "c", "i": 1, "q": 1, "s": 1, "t": "Sentence here. " * 500})
        after = builder.add({"k": "p", "t": "next"})
        self.assertEqual(first, 0)
        self.assertGreater(after, 2)
        self.assertEqual(builder.anchor_index("", "x"), 0)
        head, *rest = builder.blocks[:after]
        self.assertEqual(head["s"], 1)
        self.assertNotIn("c", head)
        for block in rest:
            self.assertEqual((block["k"], block["c"], block["a"], block["i"], block["q"]),
                             ("p", 1, "c", 1, 1))
            self.assertNotIn("s", block)
        for block in builder.blocks[:after]:
            self.assertLessEqual(len(block["t"]), PARAGRAPH_LIMIT)

    def test_long_list_item_keeps_one_marker(self):
        builder = BookBuilder()
        builder.add({"k": "li", "m": "1.", "t": "Sentence here. " * 500})
        self.assertEqual(builder.blocks[0]["m"], "1.")
        self.assertTrue(all(block["m"] == "" and block["c"] == 1 for block in builder.blocks[1:]))

    def test_overlong_heading_becomes_paragraphs(self):
        builder = BookBuilder()
        builder.add({"k": "h", "l": 1, "t": "Sentence here. " * 500})
        self.assertTrue(all(block["k"] == "p" and "l" not in block for block in builder.blocks))
        self.assertEqual(builder.headings, [])

    def test_long_pre_is_split_at_blank_lines(self):
        builder = BookBuilder()
        chunk = "\n".join("line %d" % number for number in range(300))
        builder.add({"k": "pre", "t": chunk + "\n\n" + chunk})
        self.assertEqual([block["t"] for block in builder.blocks], [chunk, chunk])

    def test_long_table_is_split(self):
        builder = BookBuilder()
        builder.add({"k": "tbl", "hdr": 1, "rows": [["a", "b"]] * 130})
        self.assertEqual([len(block["rows"]) for block in builder.blocks], [60, 60, 10])
        self.assertEqual([block.get("hdr") for block in builder.blocks], [1, None, None])

    def test_sizes_become_z(self):
        builder = BookBuilder()
        for _ in range(5):
            builder.add({"k": "p", "t": "body text of the usual size"}, size=0.9)
        builder.add({"k": "p", "t": "small print"}, size=0.7)
        builder.add({"k": "p", "t": "large"}, size=1.2)
        builder.add({"k": "p", "t": "larger"}, size=1.8)
        builder.add({"k": "h", "l": 1, "t": "heading"}, size=2.0)
        builder.add({"k": "p", "t": "caption", "z": -1}, size=0.9)
        builder.finish()
        self.assertEqual([block.get("z") for block in builder.blocks],
                         [None] * 5 + [-1, 1, 2, None, -1])

    def test_heading_like_paragraphs_become_level_seven(self):
        builder = BookBuilder()
        builder.add({"k": "p", "f": 1, "t": "<b>A Bold Title</b>"}, emphatic=True)
        builder.add({"k": "p", "t": "Body text. " * 20}, size=1.0)
        builder.add({"k": "p", "t": "A Large Title"}, size=1.5)
        builder.add({"k": "p", "t": "Body text. " * 20}, size=1.0)
        builder.add({"k": "p", "a": "c", "t": "CHAPTER TWO"}, size=1.0)
        builder.add({"k": "p", "t": "Body text. " * 20}, size=1.0)
        builder.add({"k": "h", "l": 1, "t": "Real"})
        builder.add({"k": "p", "a": "c", "t": "Centred but ordinary"}, size=1.0)
        builder.add({"k": "p", "f": 1, "t": "<b>A whole bold sentence.</b>"}, emphatic=True)
        builder.add({"k": "p", "t": "Body text. " * 20}, size=1.0)
        builder.add({"k": "p", "f": 1, "t": "<b>12</b>"}, emphatic=True)
        builder.add({"k": "p", "t": "Body text. " * 20}, size=1.0)
        builder.add({"k": "p", "f": 1, "t": "<b>Before a centred line</b>"}, emphatic=True)
        builder.add({"k": "p", "a": "c", "t": "* * *"}, size=1.0)
        builder.add({"k": "p", "f": 1, "t": "<b>The last line</b>"}, emphatic=True)
        builder.finish()
        self.assertEqual(builder.headings, [(0, 7, "A Bold Title"), (2, 7, "A Large Title"),
                                            (4, 7, "CHAPTER TWO"), (6, 1, "Real")])

    def test_full_stop_ends_a_title_in_capitals_or_a_numbered_one(self):
        builder = BookBuilder()
        for title in ("CHAPTER I.", "Chapter 2.", "Part IV.", "THE END.", "Never.", "Chapter two.",
                      "He left the room.", "Chapter 3,", "BOOK ONE;"):
            builder.add({"k": "p", "f": 1, "t": "<b>%s</b>" % title}, emphatic=True)
            builder.add({"k": "p", "t": "Body text. " * 20})
        builder.finish()
        self.assertEqual(builder.headings, [(0, 7, "CHAPTER I."), (2, 7, "Chapter 2."),
                                            (4, 7, "Part IV."), (6, 7, "THE END.")])

    def test_continuation_is_not_a_title(self):
        builder = BookBuilder()
        builder.add({"k": "p", "f": 1, "t": "<b>The rune</b>"}, emphatic=True)
        builder.add({"k": "img", "src": "/rune.png", "w": 100, "h": 20, "alt": ""})
        builder.add({"k": "p", "f": 1, "t": "<b>means joy</b>", "c": 1}, emphatic=True)
        builder.add({"k": "p", "t": "Body text. " * 20})
        builder.finish()
        self.assertEqual(builder.headings, [])

    def test_letter_spaced_capitals_met_once_are_titles(self):
        body = {"k": "p", "t": "Body text. " * 20}
        head, author = "T H E L O N G R O A D", "A N N A W A L K E R"
        lines = [head, "T H E R I V E R I S W I D E", body, author, "1 1",
                 "T H E B R I D G E O N L Y C R O S S E S", "I N O N E D I R E C T I O N", body,
                 "T H E R I V E R", body, head, "9 8 7 6 5 4 3 2 1", body, ". . . . . . . .", body,
                 "A N N A W A L K E R 7 1", body, "A W A L K E R 5 1", body,
                 "I O O T H E L O N G R O A D", body, "T H E R I V E R A N D R A I N", body, author,
                 "B O O K T W O", "T H E R I V E R", body, "— t h e F e r r y m a n", body,
                 "F E A R", body, "A B C D E 1 2 3", body, "Y O U , I N C", body,
                 "W H A T A W A L K E R ' S D A Y F E E L S L I K E .", body]
        builder = BookBuilder()
        for line in lines:
            builder.add(line if isinstance(line, dict) else {"k": "p", "t": line})
        builder.finish()
        self.assertEqual(builder.headings, [
            (1, 7, "T H E R I V E R I S W I D E"),
            (5, 7, "T H E B R I D G E O N L Y C R O S S E S I N O N E D I R E C T I O N"),
            (21, 7, "T H E R I V E R A N D R A I N"), (24, 7, "B O O K T W O"),
            (33, 7, "Y O U , I N C"),
            (35, 7, "W H A T A W A L K E R ' S D A Y F E E L S L I K E .")])
        self.assertEqual([block["t"] for block in builder.blocks[5:7]], lines[5:7])

    def test_runs_of_heading_like_paragraphs_are_not_titles(self):
        builder = BookBuilder()
        builder.add({"k": "p", "t": "Body text. " * 20})
        for line in ("Published by", "Some Press", "New York", "All rights"):
            builder.add({"k": "p", "f": 1, "t": "<b>%s</b>" % line}, emphatic=True)
        builder.finish()
        self.assertEqual(builder.headings, [])

    def test_pervasive_spacing_is_dropped(self):
        builder = BookBuilder()
        builder.begin_section("a")
        for _ in range(10):
            builder.add({"k": "p", "t": "spaced", "s": 1})
        builder.begin_section("b")
        for number in range(10):
            block = {"k": "p", "t": "text"}
            if number == 5:
                block["s"] = 1
            builder.add(block)
        builder.finish()
        self.assertEqual([block.get("s") for block in builder.blocks[1:10]], [None] * 9)
        self.assertEqual(builder.blocks[15]["s"], 1)

    def test_indent_shared_by_all_running_text_is_dropped(self):
        builder = BookBuilder()
        builder.begin_section("a")
        builder.add({"k": "p", "t": "Title", "a": "c"})
        for _ in range(5):
            builder.add({"k": "p", "t": "body", "i": 1})
        builder.add({"k": "p", "t": "quote", "i": 3, "q": 1})
        builder.add({"k": "li", "m": "•", "t": "item", "i": 1})
        builder.begin_section("b")
        for _ in range(5):
            builder.add({"k": "p", "t": "body"})
        builder.add({"k": "p", "t": "quote", "i": 1, "q": 1})
        builder.finish()
        self.assertEqual([block.get("i") for block in builder.blocks],
                         [None] * 6 + [2, None] + [None] * 5 + [1])

    def test_finish_twice(self):
        builder = BookBuilder()
        builder.add({"k": "p", "f": 1, "t": '<a href="%s">x</a>' % builder.link("", "")})
        builder.finish()
        before = [dict(block) for block in builder.blocks]
        builder.finish()
        self.assertEqual(builder.blocks, before)


if __name__ == "__main__":
    unittest.main()
