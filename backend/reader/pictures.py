"""Putting a book's pictures in the cache, within fixed bounds."""

from __future__ import annotations

import base64
import binascii
import os
import re
from urllib.parse import unquote_to_bytes

from . import images
from .textutil import write_atomic

MAX_PICTURES = 5000
MAX_PICTURE = 64 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_DATA_URI = 16 * 1024 * 1024
JUNK_WORD = 200

_NOT_BASE64 = re.compile(r"[^A-Za-z0-9+/_-]")


class Pictures:
    """Copies a book's pictures to `<out_dir>/img/NNNN.<ext>`, within fixed bounds.

    The bounds are what keep a hostile book from filling the disk: once the
    count or the total is spent, further pictures are simply left out.
    """

    def __init__(self, out_dir: str, *, count: int = MAX_PICTURES, each: int = MAX_PICTURE,
                 total: int = MAX_TOTAL) -> None:
        self.folder = os.path.join(out_dir, "img")
        self.each = each
        self.count = count
        self.total = total
        self.written = 0

    def store(self, data: bytes) -> dict | None:
        """Write one picture; `{"src", "w", "h", "al"}`, or None when it is not kept."""
        if not data or len(data) > min(self.each, self.total) or self.written >= self.count:
            return None
        found = images.sniff(data)
        if found is None:
            return None
        self.written += 1
        self.total -= len(data)
        path = os.path.join(self.folder, "%04d%s" % (self.written, found.ext))
        write_atomic(path, data)
        return {"src": path, "w": found.width, "h": found.height, "al": 1 if found.alpha else 0}

    def store_data_uri(self, href: str) -> dict | None:
        """Write the picture a `data:` URI carries."""
        if len(href) > MAX_DATA_URI:
            return None
        header, _, payload = href[5:].partition(",")
        if header.lower().endswith(";base64"):
            return self.store(decode_base64(payload))
        return self.store(unquote_to_bytes(payload))


def decode_base64(text: str) -> bytes:
    """Decode base64 as it is found in books: wrapped, unpadded, with junk in it.

    A short word holding characters outside the alphabet is a stray line and
    goes entirely; anywhere else only the offending characters go.
    """
    words = text.split()
    kept = []
    for word in words:
        if _NOT_BASE64.search(word.rstrip("=")):
            if len(words) > 1 and len(word) <= JUNK_WORD:
                continue
            word = _NOT_BASE64.sub("", word)
        kept.append(word.rstrip("="))
    data = "".join(kept).replace("-", "+").replace("_", "/")
    data = data[:len(data) - (len(data) % 4 == 1)]
    try:
        return base64.b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return b""


def image_block(picture: dict, alt: str = "") -> dict:
    """The block that shows a stored picture on its own."""
    block = {"k": "img", "src": picture["src"], "w": picture["w"], "h": picture["h"], "alt": alt}
    if picture["al"]:
        block["al"] = 1
    return block
