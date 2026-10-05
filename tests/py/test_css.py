import support  # noqa: F401  (sets up the import path)

import unittest

from reader import css
from reader.blocks import BOLD, ITALIC, STRIKE, SUB, SUPER, UNDERLINE


def cascade(*sheets):
    return css.Cascade([css.parse_stylesheet(text) for text in sheets])


def element(tag, parent=None, prev=None, first=True, **attrs):
    attrs = {name.rstrip("_").replace("__", ":").replace("_", "-"): value
             for name, value in attrs.items()}
    return css.Element(tag, attrs, parent, prev, first)


def style_of(sheet, tag="p", **attrs):
    engine = cascade(sheet)
    body = element("body")
    body.style = engine.style(body)
    node = element(tag, body, **attrs)
    return engine.style(node)


class ParserTests(unittest.TestCase):
    def rules(self, text):
        return css.parse_stylesheet(text).rules

    def test_rule_and_declarations(self):
        rules = self.rules("p.note, div > em { font-style: italic; color: red }")
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].declarations, (("italic", True, False),))

    def test_only_meaningful_properties_are_kept(self):
        self.assertEqual(self.rules("p { color: red; line-height: 2; letter-spacing: 1px }"), [])

    def test_comments_strings_and_urls(self):
        rules = self.rules('/* x { */ p { /* } */ font-weight: bold; '
                           'background: url(data:image/png;base64,a}b{c); content: "}{;" }\n'
                           "em { font-style: italic }")
        self.assertEqual([rule.parts[0].tag for rule in rules], ["p", "em"])
        self.assertEqual(rules[0].declarations, (("bold", True, False),))

    def test_important(self):
        rules = self.rules("p { font-style: italic ! IMPORTANT; font-weight: bold }")
        self.assertEqual(rules[0].declarations, (("italic", True, True), ("bold", True, False)))

    def test_garbage_does_not_raise(self):
        for text in ("}}}{{{", "p { font-style: italic", "p { ;;; : ; font-style }", "@", "{}",
                     "p { font-style: italic } }} em { font-weight: bold }", "/* never closed",
                     '"never closed', "a,,b { font-style: italic }", "\x00\x00", "p {{{{ x }}}}",
                     "@media screen { p { font-style: italic }", "<!-- p { font-weight: bold } -->"):
            css.parse_stylesheet(text)
        self.assertEqual(len(self.rules("p { font-style: italic } }} em { font-weight: bold }")), 2)
        self.assertEqual(len(self.rules("p { font-style: italic")), 1)
        self.assertEqual(len(self.rules("<!-- p { font-weight: bold } -->")), 1)
        self.assertEqual(len(self.rules("@media screen { p { font-style: italic }")), 1)

    def test_at_rules_are_skipped(self):
        rules = self.rules('@charset "utf-8"; @namespace epub "http://www.idpf.org/2007/ops";\n'
                           "@font-face { font-family: x; font-style: italic }\n"
                           "@page { margin: 1em } @keyframes k { from { font-weight: bold } }\n"
                           "@supports (display: grid) { p { font-weight: bold } }\n"
                           "p { font-style: italic }")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].parts[0].tag, "p")

    def test_media(self):
        text = ("@media print { p { font-style: italic } }"
                "@media amzn-mobi { p { font-weight: bold } }"
                "@media screen, print { em { font-style: normal } }"
                "@media not print { b { font-weight: normal } }"
                "@media (min-width: 600px) { i { font-style: normal } }"
                "@media amzn-kf8 { u { text-decoration: none } }"
                "@media all { @media print { s { font-weight: bold } } }")
        self.assertEqual([rule.parts[0].tag for rule in self.rules(text)], ["em", "b", "u"])
        self.assertTrue(css.media_applies(""))
        self.assertTrue(css.media_applies("only screen and (color)"))
        self.assertFalse(css.media_applies("not screen"))
        self.assertFalse(css.media_applies("print, speech"))

    def test_media_conditions_are_judged_as_on_a_narrow_screen(self):
        for query in ("all and (max-width: 400px)", "(max-width: 30em)", "(min-width: 320px)",
                      "only screen and (min-device-width: 20em) and (max-device-width: 480px)",
                      "(orientation: portrait)", "not handheld", "not all and (min-width: 600px)",
                      "print, (max-width: 400px)", "NOT (min-width: 50em)"):
            self.assertTrue(css.media_applies(query), query)
        for query in ("(max-width: 300px)", "(min-width: 50em)", "(min-width: 2000px)",
                      "screen and (max-width: 400px) and (min-width: 600px)",
                      "(orientation: landscape)", "not (max-width: 400px)"):
            self.assertFalse(css.media_applies(query), query)

    def test_unknown_media_never_apply(self):
        for query in ("handheld", "tv", "(prefers-color-scheme: light)",
                      "not (prefers-color-scheme: light)", "screen and (min-resolution: 2dppx)",
                      "(width >= 600px)", "(min-width: 50vw)", "screen (color)", "not",
                      "all and", "(max-width: 400px) or (color)"):
            self.assertFalse(css.media_applies(query), query)
        rules = self.rules("@media handheld { p { display: none } }"
                           "@media (max-width: 300px) { p { display: none } }"
                           "@media (prefers-color-scheme: dark) { p { display: none } }")
        self.assertEqual(rules, [])

    def test_generated_content_before_an_element_is_a_marker(self):
        rules = self.rules('ol > li:before { content: "" counter(item, lower-roman) ". " }'
                           'ul li::before { content: "\\25cf  " !important }'
                           "li.a:before, li.b { content: counters(item, '.') ' ' }"
                           'li:before { content: "" } li:before { font-style: italic }'
                           "li:after { content: 'x' } p::before { content: attr(title) }")
        self.assertEqual([(rule.parts[0].tag, rule.parts[0].classes, rule.declarations)
                          for rule in rules],
                         [("li", (), (("marker", "lower-roman", False),)),
                          ("li", (), (("marker", "disc", True),)),
                          ("li", ("a",), (("marker", "decimal", False),)),
                          ("li", (), (("marker", "", False),)),
                          ("p", (), (("marker", "disc", False),))])

    def test_imports_are_recorded(self):
        sheet = css.parse_stylesheet('@import "a.css"; @import url(b.css); @import url("c.css") screen;'
                                     "@import 'd.css' print; p { font-style: italic }")
        self.assertEqual(sheet.imports, ["a.css", "b.css", "c.css"])

    def test_unsupported_selectors_are_skipped(self):
        for selector in ("p:hover", "p::first-letter", "p:before", "p ~ p", "p:nth-child(2)",
                         "p:not(.x)", "svg|image", "*|p", "p:last-child", "> p", "p >", "p[a=b i]",
                         "[*|type]", "p..x", "p#"):
            self.assertEqual(self.rules(selector + " { font-style: italic }"), [], selector)

    def test_other_selectors_in_the_group_survive(self):
        rules = self.rules("p:hover, em, a::after { font-style: italic }")
        self.assertEqual([rule.parts[0].tag for rule in rules], ["em"])

    def test_specificity(self):
        specificity = {text: self.rules(text + " { font-style: italic }")[0].specificity
                       for text in ("p", ".a", "#a", "p.a.b", "div p", "#a .b p", "p[x]",
                                    "p:first-child", "*")}
        self.assertEqual(specificity, {
            "p": (0, 0, 1), ".a": (0, 1, 0), "#a": (1, 0, 0), "p.a.b": (0, 2, 1),
            "div p": (0, 0, 2), "#a .b p": (1, 1, 1), "p[x]": (0, 1, 1),
            "p:first-child": (0, 1, 1), "*": (0, 0, 0)})

    def test_escaped_identifiers(self):
        rules = self.rules(r".a\:b, .\31 st { font-style: italic }")
        self.assertEqual([rule.parts[0].classes for rule in rules], [("a:b",), ("1st",)])

    def test_style_attribute(self):
        self.assertEqual(css.parse_declarations("font-style: italic; color: red; FONT-WEIGHT:700"),
                         (("italic", True, False), ("bold", True, False)))
        self.assertEqual(css.parse_declarations("garbage"), ())


class SelectorTests(unittest.TestCase):
    def matches(self, selector, node):
        rules = css.parse_stylesheet(selector + " { font-style: italic }").rules
        return bool(rules) and rules[0].matches(node)

    def setUp(self):
        self.html = element("html")
        self.body = element("body", self.html, class_="main")
        self.div = element("div", self.body, id="ch1", class_="chapter first")
        self.heading = element("h1", self.div)
        self.para = element("p", self.div, self.heading, first=False, class_="x", epub__type="z3998:verse x")
        self.span = element("span", self.para, lang="en-GB", title="", hidden="")
        self.link = element("a", self.para, self.span, first=False, href="#n1")

    def test_type_class_id(self):
        self.assertTrue(self.matches("p", self.para))
        self.assertTrue(self.matches("P", self.para))
        self.assertFalse(self.matches("div", self.para))
        self.assertTrue(self.matches(".x", self.para))
        self.assertFalse(self.matches(".X", self.para))
        self.assertTrue(self.matches("#ch1", self.div))
        self.assertTrue(self.matches("div.chapter.first#ch1", self.div))
        self.assertFalse(self.matches("div.chapter.second", self.div))
        self.assertTrue(self.matches("*", self.para))

    def test_attributes(self):
        self.assertTrue(self.matches("[hidden]", self.span))
        self.assertFalse(self.matches("[hidden]", self.para))
        self.assertTrue(self.matches("[lang=en-GB]", self.span))
        self.assertTrue(self.matches('[lang="en-GB"]', self.span))
        self.assertTrue(self.matches("[lang|=en]", self.span))
        self.assertFalse(self.matches("[lang|=e]", self.span))
        self.assertTrue(self.matches("[lang^=en]", self.span))
        self.assertTrue(self.matches("[lang$=GB]", self.span))
        self.assertTrue(self.matches("[lang*=n-G]", self.span))
        self.assertTrue(self.matches('[title=""]', self.span))
        self.assertFalse(self.matches('[title~=""]', self.span))
        self.assertTrue(self.matches('[epub|type~="z3998:verse"]', self.para))
        self.assertFalse(self.matches('[epub|type~="verse"]', self.para))
        self.assertTrue(self.matches("p[class=x]", self.para))

    def test_combinators(self):
        self.assertTrue(self.matches("div p", self.para))
        self.assertTrue(self.matches("html p", self.para))
        self.assertTrue(self.matches("body.main > div > p", self.para))
        self.assertFalse(self.matches("body > p", self.para))
        self.assertTrue(self.matches("html span", self.span))
        self.assertTrue(self.matches("h1 + p", self.para))
        self.assertFalse(self.matches("h1 + span", self.span))
        self.assertTrue(self.matches("span + a", self.link))
        self.assertTrue(self.matches(".chapter h1 + p > span + a", self.link))
        self.assertTrue(self.matches("div   >   p", self.para))

    def test_descendant_backtracks(self):
        outer = element("div", self.body, class_="a")
        middle = element("section", outer)
        inner = element("div", middle)
        leaf = element("p", inner)
        self.assertTrue(self.matches(".a > section p", leaf))
        self.assertTrue(self.matches("div.a div p", leaf))

    def test_pseudo_classes(self):
        self.assertTrue(self.matches("h1:first-child", self.heading))
        self.assertFalse(self.matches("p:first-child", self.para))
        self.assertTrue(self.matches(":root", self.html))
        self.assertFalse(self.matches(":root", self.body))
        self.assertTrue(self.matches("a:link", self.link))
        self.assertFalse(self.matches("span:link", self.span))

    def test_matching_is_bounded(self):
        node = None
        for _ in range(250):
            node = element("div", node)
        leaf = element("p", node)
        self.assertFalse(self.matches("div " * 15 + "> b p", leaf))


class PropertyTests(unittest.TestCase):
    def test_user_agent_defaults(self):
        self.assertEqual(style_of("", "em").flags, ITALIC)
        self.assertEqual(style_of("", "strong").flags, BOLD)
        self.assertEqual(style_of("", "u").flags, UNDERLINE)
        self.assertEqual(style_of("", "del").flags, STRIKE)
        self.assertEqual(style_of("", "sup").flags, SUPER)
        self.assertEqual(style_of("", "sub").flags, SUB)
        self.assertEqual(style_of("", "h2").flags, BOLD)
        self.assertEqual(style_of("", "span").display, "inline")
        self.assertEqual(style_of("", "div").display, "block")
        self.assertEqual(style_of("", "script").display, "none")
        self.assertEqual(style_of("", "center").align, "c")
        self.assertEqual(style_of("", "pre").white_space, "pre")
        self.assertEqual(style_of("", "blockquote").left, 2.5)
        self.assertEqual(style_of("", "h1").size, 2.0)
        self.assertEqual(style_of("", "ol").list_type, "decimal")
        self.assertEqual(style_of("", "p", hidden="").display, "none")

    def test_display(self):
        self.assertEqual(style_of("p { display: none }").display, "none")
        self.assertEqual(style_of("span { display: block }", "span").display, "block")
        self.assertEqual(style_of("span { display: list-item }", "span").display, "block")
        self.assertEqual(style_of("span { display: table-cell }", "span").display, "block")
        self.assertEqual(style_of("div { display: inline }", "div").display, "inline")
        self.assertEqual(style_of("span { display: inline-block }", "span").display, "inline-block")
        self.assertEqual(style_of("p { display: wibble }").display, "block")

    def test_visibility(self):
        self.assertTrue(style_of("p { visibility: hidden }").hidden)
        self.assertFalse(style_of("body { visibility: hidden } p { visibility: visible }").hidden)
        self.assertTrue(style_of("body { visibility: hidden }").hidden)

    def test_font_style_and_weight(self):
        self.assertEqual(style_of("p { font-style: italic }").flags, ITALIC)
        self.assertEqual(style_of("p { font-style: oblique }").flags, ITALIC)
        self.assertEqual(style_of("em { font-style: normal }", "em").flags, 0)
        for value in ("bold", "bolder", "600", "700", "900"):
            self.assertEqual(style_of("p { font-weight: %s }" % value).flags, BOLD, value)
        for value in ("normal", "lighter", "400", "500"):
            self.assertEqual(style_of("b { font-weight: %s }" % value, "b").flags, 0, value)
        self.assertEqual(style_of("b { font-weight: %s }" % ("9" * 5000), "b").flags, BOLD)

    def test_font_shorthand(self):
        style = style_of("p { font: italic small-caps bold 1.5em/1.2 Georgia, serif }")
        self.assertEqual((style.flags, style.caps, style.size), (BOLD | ITALIC, True, 1.5))
        style = style_of("b { font: 12pt serif }", "b")
        self.assertEqual((style.flags, style.size), (0, 1.0))
        self.assertEqual(style_of("b { font: caption }", "b").flags, BOLD)

    def test_text_decoration(self):
        self.assertEqual(style_of("p { text-decoration: underline }").flags, UNDERLINE)
        self.assertEqual(style_of("p { text-decoration: line-through underline dotted }").flags,
                         UNDERLINE | STRIKE)
        self.assertEqual(style_of("p { text-decoration-line: line-through }").flags, STRIKE)
        self.assertEqual(style_of("u { text-decoration: none }", "u").flags, 0)
        self.assertEqual(style_of("body { text-decoration: underline } p { text-decoration: none }").flags,
                         UNDERLINE)

    def test_vertical_align(self):
        self.assertEqual(style_of("span { vertical-align: super }", "span").flags, SUPER)
        self.assertEqual(style_of("span { vertical-align: sub }", "span").flags, SUB)
        self.assertEqual(style_of("sup { vertical-align: baseline }", "sup").flags, 0)
        self.assertEqual(style_of("td { vertical-align: top }", "td").flags, 0)
        self.assertEqual(style_of("span { vertical-align: top }", "span").flags, 0)
        self.assertEqual(style_of("span { vertical-align: text-top; font-size: 0.6em }", "span").flags,
                         SUPER)

    def test_text_align(self):
        self.assertEqual(style_of("p { text-align: center }").align, "c")
        self.assertEqual(style_of("p { text-align: right }").align, "r")
        self.assertEqual(style_of("p { text-align: end }").align, "r")
        self.assertEqual(style_of("body { text-align: center } p { text-align: justify }").align, "")
        self.assertEqual(style_of("body { text-align: center }").align, "c")

    def test_text_transform_and_small_caps(self):
        self.assertEqual(style_of("p { text-transform: uppercase }").transform, "upper")
        self.assertEqual(style_of("p { text-transform: lowercase }").transform, "lower")
        self.assertEqual(style_of("p { text-transform: capitalize }").transform, "capitalize")
        self.assertEqual(style_of("body { text-transform: uppercase } p { text-transform: none }").transform, "")
        self.assertTrue(style_of("p { font-variant: small-caps }").caps)
        self.assertTrue(style_of("p { font-variant-caps: all-small-caps }").caps)
        self.assertFalse(style_of("body { font-variant: small-caps } p { font-variant: normal }").caps)

    def test_white_space(self):
        self.assertEqual(style_of("p { white-space: pre }").white_space, "pre")
        self.assertEqual(style_of("p { white-space: pre-wrap }").white_space, "pre")
        self.assertEqual(style_of("p { white-space: pre-line }").white_space, "pre-line")
        self.assertEqual(style_of("pre { white-space: normal }", "pre").white_space, "normal")
        self.assertEqual(style_of("p { white-space: nowrap }").white_space, "normal")

    def test_font_size(self):
        self.assertEqual(style_of("p { font-size: 1.5em }").size, 1.5)
        self.assertEqual(style_of("body { font-size: 2em } p { font-size: 50% }").size, 1.0)
        self.assertEqual(style_of("body { font-size: 2em } p { font-size: 1rem }").size, 1.0)
        self.assertEqual(style_of("p { font-size: 24px }").size, 1.5)
        self.assertEqual(style_of("p { font-size: 9pt }").size, 0.75)
        self.assertEqual(style_of("p { font-size: x-large }").size, 1.5)
        self.assertAlmostEqual(style_of("body { font-size: 1.2em } p { font-size: smaller }").size, 1.0)
        self.assertEqual(style_of("body { font-size: 1.2em }").size, 1.2)
        self.assertEqual(style_of("p { font-size: -2em }").size, 1.0)
        self.assertEqual(style_of("p { font-size: 0 }").size, 1.0)
        self.assertEqual(style_of("p { font-size: 0px }").size, 0.1)

    def test_lengths(self):
        self.assertEqual(style_of("p { margin-left: 2em }").left, 2.0)
        self.assertEqual(style_of("p { margin-left: 2em; padding-left: 16px }").left, 3.0)
        self.assertEqual(style_of("p { font-size: 2em; margin-left: 2em }").left, 4.0)
        self.assertEqual(style_of("p { font-size: 2em; margin-left: 2rem }").left, 2.0)
        self.assertEqual(style_of("p { margin-left: 24pt }").left, 2.0)
        self.assertEqual(style_of("p { margin-left: 4ex }").left, 2.0)
        self.assertAlmostEqual(style_of("p { margin-left: 10% }").left, 3.2)
        self.assertEqual(style_of("p { margin-left: auto }").left, 0.0)
        self.assertEqual(style_of("p { margin-left: -1em }").left, -1.0)
        self.assertEqual(style_of("p { margin-left: calc(1em + 2px) }").left, 0.0)
        self.assertEqual(style_of("p { text-indent: -2em }").indent, -2.0)
        self.assertEqual(style_of("body { text-indent: 3em }").indent, 3.0)

    def test_box_shorthands(self):
        style = style_of("p { margin: 1em 2em 3em 4em; padding: 1em }")
        self.assertEqual((style.above, style.left, style.below), (2.0, 5.0, 4.0))
        style = style_of("p { margin: 2em 1em }")
        self.assertEqual((style.above, style.left, style.below), (2.0, 1.0, 2.0))
        style = style_of("p { margin: 1em 2em 3em }")
        self.assertEqual((style.above, style.left, style.below), (1.0, 2.0, 3.0))
        self.assertEqual(style_of("p { margin: 1em; margin-left: 0 }").left, 0.0)

    def test_page_breaks(self):
        self.assertTrue(style_of("p { page-break-before: always }").break_before)
        self.assertTrue(style_of("p { break-before: page }").break_before)
        self.assertTrue(style_of("p { page-break-after: right }").break_after)
        self.assertTrue(style_of("p { -webkit-break-after: page }").break_after)
        self.assertFalse(style_of("p { page-break-before: avoid }").break_before)
        self.assertFalse(style_of("body { page-break-before: always }").break_before)

    def test_list_style(self):
        self.assertEqual(style_of("ul { list-style-type: none }", "ul").list_type, "none")
        self.assertEqual(style_of("ol { list-style: lower-roman inside }", "ol").list_type, "lower-roman")
        self.assertEqual(style_of("ul { list-style: none }", "ul").list_type, "none")
        self.assertEqual(style_of("ol { list-style-type: hebrew }", "ol").list_type, "decimal")
        self.assertEqual(style_of("body { list-style-type: square }", "li").list_type, "square")

    def test_vendor_prefixes(self):
        self.assertEqual(style_of("p { -epub-text-transform: uppercase }").transform, "upper")
        self.assertEqual(style_of("p { --display: none }").display, "block")


class CascadeTests(unittest.TestCase):
    def test_inheritance(self):
        engine = cascade("div { font-style: italic; font-weight: bold; text-align: center;"
                         " margin-left: 3em; display: block }")
        div = element("div")
        div.style = engine.style(div)
        span = element("span", div)
        style = engine.style(span)
        self.assertEqual(style.flags, BOLD | ITALIC)
        self.assertEqual(style.align, "c")
        self.assertEqual(style.left, 0.0)
        self.assertEqual(style.display, "inline")

    def test_inherit_keyword(self):
        engine = cascade("div { font-style: italic } em { font-style: normal }"
                         " em.same { font-style: inherit }")
        div = element("div")
        div.style = engine.style(div)
        self.assertEqual(engine.style(element("em", div)).flags, 0)
        self.assertEqual(engine.style(element("em", div, class_="same")).flags, ITALIC)

    def test_specificity_beats_order(self):
        self.assertEqual(style_of("p.a { font-style: italic } p { font-style: normal }", class_="a").flags,
                         ITALIC)
        self.assertEqual(style_of("#i { font-style: normal } p.a.b { font-style: italic }",
                                  class_="a b", id="i").flags, 0)

    def test_order_breaks_ties(self):
        self.assertEqual(style_of(".a { font-style: italic } .b { font-style: normal }", class_="a b").flags, 0)
        self.assertEqual(style_of(".b { font-style: normal } .a { font-style: italic }", class_="a b").flags,
                         ITALIC)

    def test_later_sheets_win(self):
        engine = cascade("p { font-style: italic }", "p { font-style: normal }")
        self.assertEqual(engine.style(element("p")).flags, 0)

    def test_important(self):
        self.assertEqual(style_of("p { font-style: italic !important } #i { font-style: normal }", id="i").flags,
                         ITALIC)
        self.assertEqual(style_of("p { font-style: italic !important }", style="font-style: normal").flags,
                         ITALIC)
        self.assertEqual(style_of("p { font-style: italic !important }",
                                  style="font-style: normal !important").flags, 0)

    def test_author_beats_user_agent(self):
        self.assertEqual(style_of("* { font-weight: normal }", "b").flags, 0)
        self.assertEqual(style_of("em { font-style: normal }", "em").flags, 0)

    def test_inline_style(self):
        self.assertEqual(style_of("#i { font-style: normal }", id="i", style="font-style: italic").flags, ITALIC)
        self.assertEqual(style_of("", style="display: none").display, "none")

    def test_presentational_attributes(self):
        self.assertEqual(style_of("", align="center").align, "c")
        self.assertEqual(style_of("", align="RIGHT").align, "r")
        self.assertEqual(style_of("", "td", align="middle").align, "c")
        self.assertEqual(style_of("p { text-align: left }", align="center").align, "")
        self.assertEqual(style_of("", align="center", style="text-align: right").align, "r")
        self.assertEqual(style_of("", "table", align="center").align, "")
        self.assertEqual(style_of("", "ol", type="a").list_type, "lower-alpha")
        self.assertEqual(style_of("", "ol", type="I").list_type, "upper-roman")

    def test_font_size_attribute(self):
        self.assertEqual(style_of("", "font", size="7").size, 3.0)
        self.assertEqual(style_of("", "font", size="3").size, 1.0)
        self.assertEqual(style_of("", "font", size="1").size, 0.75)
        self.assertEqual(style_of("", "font", size="+2").size, 1.5)
        self.assertEqual(style_of("", "font", size="-1").size, 0.89)
        self.assertEqual(style_of("", "font", size="12").size, 3.0)
        self.assertEqual(style_of("", "font", size="large").size, 1.0)
        self.assertEqual(style_of("", "p", size="7").size, 1.0)
        self.assertEqual(style_of("font { font-size: 1em }", "font", size="7").size, 1.0)
        self.assertEqual(style_of("", "font", size="7", style="font-size: 2em").size, 2.0)

    def test_marker_is_not_inherited(self):
        engine = cascade("ol { list-style: none } li:before { content: counter(n) '. ' }")
        item = element("li")
        item.style = engine.style(item)
        self.assertEqual((item.style.list_type, item.style.marker), ("disc", "decimal"))
        self.assertEqual(engine.style(element("p", item)).marker, "")

    def test_a_clipped_box_without_size_is_not_shown(self):
        for sheet in ("div { height: 0; overflow: hidden }", "div { width: 0px; overflow: hidden }",
                      "div { max-height: 0em; overflow: clip }",
                      "div { display: block; font-size: 2em; height: 0; overflow: hidden; width: 0 }"):
            self.assertEqual(style_of(sheet, "div").display, "none", sheet)
        for sheet in ("div { height: 0 }", "div { overflow: hidden }",
                      "div { height: auto; overflow: hidden }", "div { height: 10px; overflow: hidden }",
                      "div { height: 0; overflow: hidden; padding-bottom: 56% }",
                      "div { height: 0; overflow: hidden } div { height: auto }",
                      "div { height: 0; overflow: hidden } div { overflow: visible }"):
            self.assertEqual(style_of(sheet, "div").display, "block", sheet)
        engine = cascade("div { height: 0; overflow: hidden }")
        box = element("div")
        box.style = engine.style(box)
        self.assertEqual(engine.style(element("span", box)).display, "inline")
        self.assertEqual(style_of("", "ul", type="square").list_type, "square")
        self.assertEqual(style_of("[hidden] { display: block }", hidden="").display, "block")

    def test_descendant_rule_depends_on_ancestors(self):
        engine = cascade(".poem span { font-style: italic }")
        poem = element("div", class_="poem")
        poem.style = engine.style(poem)
        prose = element("div")
        prose.style = engine.style(prose)
        self.assertEqual(engine.style(element("span", poem)).flags, ITALIC)
        self.assertEqual(engine.style(element("span", prose)).flags, 0)
        self.assertEqual(engine.style(element("span", poem)).flags, ITALIC)

    def test_styles_are_shared(self):
        engine = cascade(".a { font-style: italic }")
        body = element("body")
        body.style = engine.style(body)
        first = engine.style(element("p", body, class_="a"))
        self.assertIs(first, engine.style(element("p", body, class_="a")))

    def test_many_rules_only_cost_what_applies(self):
        engine = cascade("".join(".c%d { font-style: italic }" % number for number in range(5000)))
        self.assertEqual(engine.style(element("p", class_="c4999")).flags, ITALIC)
        self.assertEqual(engine.style(element("p", class_="other")).flags, 0)

    def test_cascade_cache(self):
        sheet = css.parse_stylesheet("p { font-style: italic }")
        self.assertIs(css.parse_stylesheet("p { font-style: italic }"), sheet)
        self.assertIs(css.cascade_for((sheet,)), css.cascade_for((sheet,)))


class ImportTests(unittest.TestCase):
    def collect(self, text, files):
        fetched = []

        def fetch(href, base):
            fetched.append((href, base))
            return files.get(href)

        sheets = []
        css.collect_sheets(text, "OEBPS/css/main.css", fetch, sheets)
        return sheets, fetched

    def test_imported_rules_come_first(self):
        sheets, fetched = self.collect('@import "base.css"; p { font-style: normal }',
                                       {"base.css": "p { font-style: italic }"})
        self.assertEqual(len(sheets), 2)
        self.assertEqual(fetched, [("base.css", "OEBPS/css/main.css")])
        self.assertEqual(css.Cascade(sheets).style(element("p")).flags, 0)

    def test_nested_import_base(self):
        _, fetched = self.collect('@import "../shared/a.css";',
                                  {"../shared/a.css": '@import "b.css";', "b.css": ""})
        self.assertEqual(fetched[1], ("b.css", "OEBPS/shared/a.css"))

    def test_missing_import(self):
        sheets, _ = self.collect('@import "gone.css"; p { font-style: italic }', {})
        self.assertEqual(len(sheets), 1)

    def test_import_cycle_and_depth(self):
        sheets, fetched = self.collect('@import "a.css";', {"a.css": '@import "a.css"; @import "main.css";',
                                                            "main.css": '@import "a.css";'})
        self.assertLessEqual(len(fetched), 3)
        files = {"s%d.css" % number: '@import "s%d.css";' % (number + 1) for number in range(50)}
        sheets, fetched = self.collect('@import "s0.css";', files)
        self.assertEqual(len(fetched), css.MAX_IMPORT_DEPTH)

    def test_sheet_count_is_capped(self):
        files = {"s%d.css" % number: "p { color: red } /* %d */" % number for number in range(200)}
        text = "".join('@import "s%d.css";' % number for number in range(200))
        sheets, _ = self.collect(text, files)
        self.assertEqual(len(sheets), css.MAX_SHEETS)


if __name__ == "__main__":
    unittest.main()
