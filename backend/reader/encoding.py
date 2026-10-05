"""Decoding text that says nothing, or the wrong thing, about its encoding."""

from __future__ import annotations

import codecs
import re
from collections import Counter

from .textutil import decode_bytes

_SAMPLE = 256 * 1024
_HIGH_RUN = re.compile(rb"[\x80-\xff]+")
_BOMS = (codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)

# What the non-ASCII part of correctly decoded running text is mostly made of.
_CYRILLIC = frozenset(
    "абвгдежзийклмнопрстуфхцчшщъыьэюяёіїєґўАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯЁІЇЄҐЎ")
_CYRILLIC_COMMON = frozenset("оеаинтсрвл")
_GREEK = frozenset("αβγδεζηθικλμνξοπρστυφχψωάέήίόύώςϊϋΐΰΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩΆΈΉΊΌΎΏ")
_GREEK_COMMON = frozenset("αοειτνσρκπάέίό")
_WESTERN = frozenset("éèêëàâäáãåîïíìôöóòõøùûüúçñßæœÿÉÈÊÀÂÄÁÅÎÔÖÓØÙÛÜÚÇÑÆŒ¿¡")
_PUNCTUATION = frozenset("’‘“”–—«»…„‚•°§©®™€£¥№\xa0")
_JAPANESE = frozenset("のにはをたがでてとしれさあるいもなかっきくこうまりらよ、。「」ーンスルイトラ")
_SIMPLIFIED = frozenset(
    "的一是不了人我在有他这为之大来以个中上们到说国和地也子时道出而要于就下得可你年生自会那后能对着事其里所去行过家十"
    "用发天如然作方成者多日都三小军二无同么经法当起与好看学进种将还分此心前面又定见只主没公从，。")
_TRADITIONAL = frozenset(
    "的一是不了人我在有他這為之大來以個中上們到說國和地也子時道出而要於就下得可你年生自會那後能對著事其裡所去行過家十"
    "用發天如然作方成者多日都三小軍二無同麼經法當起與好看學進種將還分此心前面又定見只主沒公從，。")
_KOREAN = frozenset("이다는에의을가를은하고로한지서도기어아리사시해있니대자그")
_CJK = ((0x3000, 0x303F), (0x4E00, 0x9FFF), (0xFF01, 0xFF60))
_KANA = _CJK + ((0x3040, 0x30FF),)
_HANGUL = _CJK + ((0xAC00, 0xD7A3),)

# (codec, characters expected, the commonest letters of an alphabet or the
# Unicode blocks of an ideographic script)
_LEGACY = (
    ("cp1251", _CYRILLIC, _CYRILLIC_COMMON),
    ("koi8-r", _CYRILLIC, _CYRILLIC_COMMON),
    ("cp866", _CYRILLIC, _CYRILLIC_COMMON),
    ("cp1253", _GREEK, _GREEK_COMMON),
    ("cp932", _JAPANESE, _KANA),
    ("euc_jp", _JAPANESE, _KANA),
    ("gb18030", _SIMPLIFIED, _CJK),
    ("big5hkscs", _TRADITIONAL, _CJK),
    ("cp949", _KOREAN, _HANGUL),
)
# The letters beyond ASCII that one language writes with. Text read in the
# wrong Latin code page is spread over letters no single language combines.
_WESTERN_LANGUAGES = (
    "àâæçéèêëîïôœùûüÿ", "äöüß", "áéíñóúü¿¡ºª", "áâãàçéêíóôõúüºª", "àèéìíîòóùú", "áäéèëïíóöúü",
    "åäöéæø", "áðéíóúýþæöø", "àçèéíïòóúü·", "äöõüšž")
_CENTRAL_LANGUAGES = (
    "ąćęłńóśźż", "áčďéěíňóřšťúůýž", "áäčďéíĺľňóôŕšťúýž", "áéíóöőúüű", "čćđšž", "ăâîşţ")
_TURKISH = ("çğıöşüâîûİ",)
_OTHER_LATIN = (
    ("cp1250", _CENTRAL_LANGUAGES),
    ("iso8859-2", _CENTRAL_LANGUAGES),
    ("cp1254", _TURKISH),
)
# Read as Western European, the ą ł ż š ž of another code page are signs
# like these in the middle of words, where no text has them.
_MISREAD = re.compile(r"(?<=[^\W\d_])[\xa1-\xa9\xab\xac\xae-\xb3\xb6\xb8\xb9\xbb-\xbf](?=[^\W\d_])")
LATIN_FIT = 0.9
LATIN_MARGIN = 0.1
LATIN_LETTERS = 3
LATIN_MISREAD = 0.03
_SAME_CODEC = {"shift_jis": "cp932", "gb2312": "gb18030", "gbk": "gb18030", "big5": "big5hkscs",
               "euc_kr": "cp949"}
DECLARED_BONUS = 0.1
MIN_SCORE = 0.5


def decode_text(raw: bytes, *, declared: str = "", language: str = "") -> str:
    """Decode a file that may say nothing, or the wrong thing, about its encoding.

    textutil.decode_bytes does the work whenever the bytes speak for
    themselves (a byte-order mark, UTF-8) or read as Western European text;
    otherwise the legacy encoding is chosen by what the decoded text looks
    like, which is what tells windows-1251 from KOI8-R, both from Shift-JIS
    and Polish from French when nothing else does.
    """
    codec = _guess(raw, declared)
    if not codec:
        return decode_bytes(raw, declared=declared, language=language)
    text = raw.decode(codec, "replace").removeprefix("\ufeff")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _guess(raw: bytes, declared: str) -> str:
    """The codec to force, or "" to leave the decision to decode_bytes."""
    sample = raw[:_SAMPLE]
    if sample.startswith(_BOMS) or len(sample) < 4:
        return ""
    even, odd = sample[0::2], sample[1::2]
    if odd.count(0) > 0.3 * len(odd) and even.count(0) < 0.05 * len(even):
        return "utf-16-le"
    if even.count(0) > 0.3 * len(even) and odd.count(0) < 0.05 * len(odd):
        return "utf-16-be"
    runs = _HIGH_RUN.findall(sample)
    if not runs:
        return ""
    loose = sample.decode("utf-8", "replace")
    bad = loose.count("\ufffd")
    good = len(loose) - len(loose.encode("ascii", "ignore")) - bad
    if good >= 3 * bad:
        return ""
    try:
        named = codecs.lookup(declared).name if declared else ""
    except LookupError:
        named = ""
    named = _SAME_CODEC.get(named, named)
    # Accented Latin letters stand alone between ASCII ones; other scripts
    # come in runs of high bytes.
    run = sum(map(len, runs)) / len(runs)
    if run < 2 and not named:
        latin = _other_latin(sample)
        if latin:
            return latin
    if run < 1.5:
        return ""
    best, best_score = "", _score_latin(sample) * 1.5 / run
    for codec, expected, model in _LEGACY:
        score = _score(sample, codec, expected, model)
        if codec == named:
            score += DECLARED_BONUS
        if score > best_score + 0.02:
            best, best_score = codec, score
    return best if best_score >= MIN_SCORE else ""


def _other_latin(sample: bytes) -> str:
    """The Central European or Turkish code page the sample clearly reads better in, or ""."""
    western = _marks(sample, "cp1252")
    misread = len(_MISREAD.findall(sample.decode("cp1252", "replace")))
    # Signs inside words show the text is not Western, whatever mixture of
    # languages it is in; without them one language has to account for it.
    mixed = misread >= 2 and misread >= LATIN_MISREAD * sum(western.values())
    needed = LATIN_FIT
    if not mixed:
        needed = max(needed, _fit(western, _WESTERN_LANGUAGES) + LATIN_MARGIN)
    best, best_fit = "", 0.0
    for codec, languages in _OTHER_LATIN:
        fit = _fit(_marks(sample, codec), ("".join(languages),) if mixed else languages)
        if fit >= needed and fit > best_fit:
            best, best_fit = codec, fit
    return best


def _marks(sample: bytes, codec: str) -> Counter:
    """How often each non-ASCII character other than punctuation occurs in this codec."""
    return Counter(char for char in _foreign(sample, codec) or () if char not in _PUNCTUATION)


def _fit(marks: Counter, languages: tuple[str, ...]) -> float:
    """The largest share of the characters that the letters of one language account for."""
    best = 0.0
    for letters in languages:
        found = [count for char, count in marks.items()
                 if char in letters or char.lower() in letters]
        if len(found) >= LATIN_LETTERS:
            best = max(best, sum(found) / sum(marks.values()))
    return best


def _foreign(sample: bytes, codec: str) -> list[str] | None:
    """The non-ASCII characters the sample holds in this codec; None when it is not in it."""
    text = sample.decode(codec, "replace")
    found = [char for char in text if char > "\x7f"]
    # One broken character is the sample's cut end; more is the wrong codec.
    if not found or found.count("\ufffd") > 1 + len(found) // 50:
        return None
    return found


def _share(chars: list[str], wanted: frozenset) -> float:
    return sum(1 for char in chars if char in wanted) / len(chars) if chars else 0.0


def _stray(chars: list[str]) -> float:
    """The share of C1 controls and private-use characters: signs of a wrong codec."""
    return sum(1 for char in chars
               if "\x80" <= char <= "\x9f" or "\ue000" <= char <= "\uf8ff") / len(chars)


def _score_latin(sample: bytes) -> float:
    chars = _foreign(sample, "cp1252")
    if chars is None:
        return 0.0
    return _share(chars, _WESTERN) + 0.5 * _share(chars, _PUNCTUATION) - 2 * _stray(chars)


def _score(sample: bytes, codec: str, expected: frozenset, model: frozenset | tuple) -> float:
    chars = _foreign(sample, codec)
    if chars is None:
        return 0.0
    if isinstance(model, tuple):
        inside = sum(1 for char in chars
                     if any(low <= ord(char) <= high for low, high in model)) / len(chars)
        return (0.55 * inside + 0.45 * min(1.0, 2.5 * _share(chars, expected))
                - 2 * _stray(chars))
    letters = [char for char in chars if char in expected]
    common = _share([char.lower() for char in letters], model)
    lower = _share(letters, frozenset(char for char in expected if char == char.lower()))
    base = (0.5 * min(1.0, len(letters) / len(chars) + _share(chars, _PUNCTUATION))
            + 0.5 * min(1.0, common / 0.5))
    # Running text is mostly lower case; a wrong Cyrillic codec flips the case.
    return base * (0.8 + 0.2 * min(1.0, lower / 0.6)) - 2 * _stray(chars)
