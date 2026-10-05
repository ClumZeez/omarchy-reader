"""Recognise the images the shell can draw, from their headers alone."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

_EXTENSIONS = {
    "png": ".png", "jpeg": ".jpg", "gif": ".gif", "webp": ".webp", "bmp": ".bmp", "svg": ".svg",
}

# Qt refuses to allocate more than 256 MiB for one decoded image (4 bytes a pixel).
_MAX_PIXELS = 64 * 1024 * 1024

_PNG = b"\x89PNG\r\n\x1a\n"
# Baseline, extended and progressive frames, Huffman or arithmetic coded.
# Lossless and hierarchical JPEG (the other frame markers) cannot be loaded.
_JPEG_FRAMES = frozenset({0xC0, 0xC1, 0xC2, 0xC9, 0xCA})
_JPEG_OTHER_FRAMES = frozenset({0xC3, 0xC5, 0xC6, 0xC7, 0xCB, 0xCD, 0xCE, 0xCF})
_JPEG_BARE = frozenset({0x01, *range(0xD0, 0xD9)})
_BMP_HEADERS = frozenset({12, 40, 52, 56, 64, 108, 124})

_SVG_WINDOW = 4096
_SVG_TAG_LIMIT = 64 * 1024
_SVG_PROLOG = re.compile(
    r"(?:\s+|<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^\[>]*(?:\[.*?\]\s*)?>)*", re.S | re.I
)
_SVG_ROOT = re.compile(r"<(?:[A-Za-z_][\w.-]*:)?svg(?=[\s/>])([^>]*)", re.I)
_SVG_ATTRIBUTE = re.compile(r"(?<![\w:.-])(width|height|viewBox)\s*=\s*(\"[^\"]*\"|'[^']*')", re.I)
_SVG_LENGTH = re.compile(r"\s*\+?([0-9]*\.?[0-9]+)\s*(?:px)?\s*", re.I)
_SVG_NUMBER = re.compile(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?")


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """What an image is and how large, in pixels (0 when an SVG does not say).

    `alpha` says the image can be see-through: the reader puts a light
    backing behind such pictures, because line art drawn dark on a
    transparent ground disappears on a dark theme.
    """

    kind: str
    width: int
    height: int
    alpha: bool = False

    @property
    def ext(self) -> str:
        return _EXTENSIONS[self.kind]


def sniff(data: bytes) -> ImageInfo | None:
    """Identify an image the shell can load; None for anything else."""
    data = bytes(data)
    try:
        found = _raster(data)
    except (struct.error, IndexError):  # the header is cut short
        return None
    if found is None:
        return _svg(data)
    kind, width, height = found
    if width <= 0 or height <= 0 or width * height > _MAX_PIXELS:
        return None
    try:
        alpha = _TRANSPARENCY[kind](data)
    except (struct.error, IndexError):
        alpha = False
    return ImageInfo(kind, width, height, alpha)


def _raster(data: bytes) -> tuple[str, int, int] | None:
    """Kind and size of a bitmap format; size 0 when it is one Qt cannot read."""
    if data.startswith(_PNG):
        # A first chunk other than IHDR means Apple's CgBI variant.
        if data[12:16] != b"IHDR":
            return "png", 0, 0
        return ("png", *struct.unpack_from(">II", data, 16))
    if data.startswith(b"\xff\xd8"):
        return ("jpeg", *_jpeg_size(data))
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ("gif", *struct.unpack_from("<HH", data, 6))
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("webp", *_webp_size(data))
    if data[:2] == b"BM" and len(data) >= 18:
        header = struct.unpack_from("<I", data, 14)[0]
        if header not in _BMP_HEADERS:
            return "bmp", 0, 0
        if header == 12:
            return ("bmp", *struct.unpack_from("<HH", data, 18))
        width, height = struct.unpack_from("<ii", data, 18)
        return "bmp", width, abs(height)  # negative height: rows stored top-down
    return None


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """Walk the segments to the frame header; (0, 0) when there is none to trust."""
    pos = 2
    end = len(data)
    while pos + 4 <= end:
        if data[pos] != 0xFF:
            return 0, 0
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte before a marker
            pos += 1
        elif marker in _JPEG_BARE:
            pos += 2
        elif marker in _JPEG_FRAMES:
            height, width = struct.unpack_from(">HH", data, pos + 5)
            return width, height
        elif marker in _JPEG_OTHER_FRAMES or marker in (0xD9, 0xDA):
            return 0, 0
        else:
            pos += 2 + struct.unpack_from(">H", data, pos + 2)[0]
    return 0, 0


def _webp_size(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack_from("<HH", data, 26)
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and data[20] == 0x2F:
        bits = struct.unpack_from("<I", data, 21)[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        return (
            int.from_bytes(data[24:27], "little") + 1,
            int.from_bytes(data[27:30], "little") + 1,
        )
    return 0, 0


def _png_alpha(data: bytes) -> bool:
    """An alpha channel (colour types 4 and 6) or a tRNS chunk before the pixels."""
    if data[25] in (4, 6):
        return True
    pos = 8
    for _ in range(64):  # ancillary chunks ahead of IDAT are few
        if pos + 8 > len(data):
            break
        length, kind = struct.unpack_from(">I4s", data, pos)
        if kind == b"tRNS":
            return True
        if kind in (b"IDAT", b"IEND"):
            break
        pos += 12 + length
    return False


def _gif_alpha(data: bytes) -> bool:
    """The transparent-colour flag of the first frame's graphic control block."""
    pos = 13
    if data[10] & 0x80:
        pos += 3 << ((data[10] & 0x07) + 1)
    for _ in range(64):
        marker = data[pos]
        if marker != 0x21:  # an image descriptor or the trailer: no flag was set
            return False
        if data[pos + 1] == 0xF9:
            return bool(data[pos + 3] & 0x01)
        pos += 2
        while data[pos]:  # skip the extension's data sub-blocks
            pos += data[pos] + 1
        pos += 1
    return False


def _webp_alpha(data: bytes) -> bool:
    chunk = data[12:16]
    if chunk == b"VP8X":
        return bool(data[20] & 0x10)
    if chunk == b"VP8L":
        return bool(struct.unpack_from("<I", data, 21)[0] >> 28 & 1)
    return False


_TRANSPARENCY = {
    "png": _png_alpha,
    "gif": _gif_alpha,
    "webp": _webp_alpha,
    "jpeg": lambda data: False,
    "bmp": lambda data: False,
}


def _svg(data: bytes) -> ImageInfo | None:
    text = data[:_SVG_TAG_LIMIT].decode("utf-8", "replace").removeprefix("\ufeff")
    prolog = _SVG_PROLOG.match(text).end()
    root = _SVG_ROOT.match(text, prolog)
    if root is None or prolog > _SVG_WINDOW:
        return None
    attributes = {
        name.lower(): value[1:-1] for name, value in _SVG_ATTRIBUTE.findall(root.group(1))
    }
    width = _svg_length(attributes.get("width", ""))
    height = _svg_length(attributes.get("height", ""))
    if not (width and height):
        box = _SVG_NUMBER.findall(attributes.get("viewbox", ""))
        if len(box) == 4:
            width, height = _pixels(float(box[2])), _pixels(float(box[3]))
    if not (width and height):
        width = height = 0
    return ImageInfo("svg", width, height, True)


def _svg_length(value: str) -> int:
    """A length in pixels; 0 for relative or physical units, which need a viewBox."""
    match = _SVG_LENGTH.fullmatch(value)
    return _pixels(float(match.group(1))) if match else 0


def _pixels(value: float) -> int:
    return round(value) if 0 < value < 1_000_000 else 0
