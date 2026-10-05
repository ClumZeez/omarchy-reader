"""Tolerant zip access: damaged archives open, hostile ones stay bounded."""

from __future__ import annotations

import io
import mmap
import os
import struct
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from urllib.parse import unquote

from .errors import ReaderError

MAX_MEMBER = 256 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024

_LOCAL = b"PK\x03\x04"
_LOCAL_HEADER = struct.Struct("<4sHHHHHLLLHH")
_BARE_DESCRIPTOR = struct.Struct("<LLL")
_SIGNATURES =(_LOCAL, b"PK\x01\x02", b"PK\x05\x06", b"PK\x07\x08")
_STORED, _DEFLATED = 0, 8
_ENCRYPTED, _DESCRIPTOR, _UTF8 = 0x1, 0x8, 0x800
_UNKNOWN_SIZE = 0xFFFFFFFF
_MAX_NAME = 4096
_CHUNK = 64 * 1024
_SALVAGE_STEP = 64
# A damaged archive up to this size is read into memory to be searched; only
# a larger one is mapped. A mapped file that shrinks while it is being read
# kills the process outright, where a read merely comes up short.
_READ_WHOLE = 512 * 1024 * 1024

_DAMAGED = "This book is damaged and can't be opened."
_TOO_BIG = "This book is too large to open."
_PROTECTED = "This book is protected by DRM and can't be opened."
_MISSING = "This book's file can't be found."
_UNREADABLE = "This book's file can't be read."


class _Oversized(ReaderError):
    """A member that would not fit in what it was allowed."""

    def __init__(self) -> None:
        super().__init__("corrupt", _TOO_BIG)


@dataclass(frozen=True, slots=True)
class Member:
    """One file in an archive, as its directory describes it."""

    name: str
    size: int
    crc: int
    encrypted: bool


@dataclass(frozen=True, slots=True)
class _Entry:
    member: Member
    info: zipfile.ZipInfo | None  # None when recovered by the local-header scan
    start: int = 0  # scan only: offset of the member's data
    method: int = _DEFLATED  # scan only
    stored: int = 0  # scan only: byte length of a stored member


class Archive:
    """A zip file addressed by canonical member names.

    Canonical names use "/" separators, have no leading "/" or "./", are NFC
    normalised and never leave the archive root.
    """

    def __init__(self, handle: io.BufferedIOBase, raw: bytes | None, budget: int):
        self._handle = handle
        self._raw: bytes | mmap.mmap | None = raw
        self._budget = budget
        self._zip: zipfile.ZipFile | None = None
        self._entries: dict[str, _Entry] = {}
        try:
            self._zip = zipfile.ZipFile(handle)
            self._index_directory(self._zip.infolist())
        except Exception:  # whatever zipfile rejects, the scan may still recover
            self._entries.clear()
        if not self._entries:
            self._index_scan()
        if not self._entries:
            self.close()
            raise ReaderError("corrupt", _DAMAGED)
        self._folded: dict[str, str] = {}
        for name in self._entries:
            self._folded.setdefault(name.casefold(), name)

    @classmethod
    def open(cls, path: str | os.PathLike | bytes, *, budget: int = MAX_TOTAL) -> "Archive":
        """Open a zip from a path, or from bytes for an archive nested in another."""
        if isinstance(path, (bytes, bytearray, memoryview)):
            data = bytes(path)
            return cls(io.BytesIO(data), data, budget)
        try:
            handle = open(path, "rb")
        except FileNotFoundError:
            raise ReaderError("missing", _MISSING) from None
        except OSError:
            raise ReaderError("corrupt", _UNREADABLE) from None
        try:
            return cls(handle, None, budget)
        except BaseException:
            handle.close()
            raise

    def __enter__(self) -> "Archive":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None
        if isinstance(self._raw, mmap.mmap):
            self._raw.close()
        self._raw = b""
        self._handle.close()

    def names(self) -> list[str]:
        """Canonical names in archive order."""
        return list(self._entries)

    def find(self, name: str) -> str | None:
        """The canonical name a reference points at, however loosely it is spelled."""
        candidates = [name, _canonical(name)]
        if "%" in name:
            for encoding in ("utf-8", "latin-1"):
                try:
                    candidates.append(_canonical(unquote(name, encoding=encoding, errors="strict")))
                except UnicodeDecodeError:
                    pass
        spellings = [candidate for candidate in candidates if candidate]
        for spelling in spellings:
            if spelling in self._entries:
                return spelling
        for spelling in spellings:
            found = self._folded.get(spelling.casefold())
            if found is not None:
                return found
        return None

    def member(self, name: str) -> Member:
        """Size and checksum of a member, without inflating it."""
        return self._entry(name).member

    def read(self, name: str, limit: int = MAX_MEMBER) -> bytes:
        """The member's bytes; damaged members give whatever could be recovered."""
        entry = self._entry(name)
        if entry.member.encrypted:
            raise ReaderError("drm", _PROTECTED)
        if self._budget <= 0:
            raise _Oversized()
        limit = min(limit, self._budget)
        data = None
        try:
            if entry.info is not None:
                data = self._read_listed(entry.info, limit)
                if data is None:
                    entry = self._locate(entry)
            if data is None:
                data = self._read_raw(entry, limit)
        except _Oversized:
            # The work was done even though nothing came of it. Left
            # uncharged, a thousand names for one huge member would each be
            # inflated in turn.
            self._budget -= limit
            raise
        self._budget -= len(data)
        return data

    def _entry(self, name: str) -> _Entry:
        entry = self._entries.get(name)
        if entry is None:
            found = self.find(name)
            if found is None:
                raise KeyError(name)
            entry = self._entries[found]
        return entry

    def _add(self, name: str, entry: _Entry) -> None:
        # Re-inserting keeps names() in the order of the copies that win.
        self._entries.pop(name, None)
        self._entries[name] = entry

    def _index_directory(self, infos: list[zipfile.ZipInfo]) -> None:
        for info in infos:
            written = info.orig_filename
            if not info.flag_bits & _UTF8 and not written.isascii():
                # zipfile decoded the unflagged name as cp437, which maps
                # every byte, so encoding it back yields the original bytes.
                written = _decode_name(written.encode("cp437"))
            name = _canonical(written)
            if name is None or _is_directory(written):
                continue
            encrypted = bool(info.flag_bits & _ENCRYPTED)
            self._add(name, _Entry(Member(name, info.file_size, info.CRC, encrypted), info))

    def _data(self) -> bytes | mmap.mmap:
        if self._raw is None:
            size = os.fstat(self._handle.fileno()).st_size
            if size <= _READ_WHOLE:
                self._handle.seek(0)
                self._raw = self._handle.read(size)
            else:
                try:
                    self._raw = mmap.mmap(self._handle.fileno(), 0, access=mmap.ACCESS_READ)
                except (ValueError, OSError):  # more than this process may map
                    raise ReaderError("corrupt", _DAMAGED) from None
        return self._raw

    def _index_scan(self) -> None:
        """Rebuild the member list from local headers alone."""
        raw = self._data()
        end_of_data = len(raw)
        budget = MAX_TOTAL
        pos = 0
        while True:
            pos = raw.find(_LOCAL, pos)
            if pos < 0 or pos + _LOCAL_HEADER.size > end_of_data:
                break
            (_, _, flags, method, _, _, crc, packed, size, name_len, extra_len) = (
                _LOCAL_HEADER.unpack_from(raw, pos)
            )
            name_at = pos + _LOCAL_HEADER.size
            written = bytes(raw[name_at:name_at + name_len])
            start = name_at + name_len + extra_len
            # The signature also turns up inside member data; a real header
            # has a sane name and a method this scan can undo.
            if (
                not 0 < name_len <= _MAX_NAME
                or start > end_of_data
                or method not in (_STORED, _DEFLATED)
                or min(written) < 0x20
            ):
                pos += 4
                continue
            text = _decode_name(written)
            name = _canonical(text)
            if name is None or _is_directory(text):
                pos = start
                continue
            sized = packed != _UNKNOWN_SIZE and not (flags & _DESCRIPTOR and packed == 0)
            if flags & _ENCRYPTED:
                self._add(name, _Entry(Member(name, size, crc, True), None))
                pos = start + packed if sized and start + packed <= end_of_data else start
                continue
            trusted = sized and _at_boundary(raw, start + packed)
            if method == _STORED:
                if trusted:
                    end = start + packed
                elif sized:
                    end = min(start + packed, end_of_data)
                else:
                    end = _next_signature(raw, start)
                    # A descriptor written without its signature looks like
                    # data; its two size fields give it away.
                    bare = end - start - _BARE_DESCRIPTOR.size
                    if bare >= 0 and _BARE_DESCRIPTOR.unpack_from(raw, start + bare)[1:] == (bare, bare):
                        end = start + bare
                    crc = _crc(raw, start, end)
                self._add(name, _Entry(Member(name, end - start, crc, False), None, start, _STORED, end - start))
                pos = end
                continue
            if trusted:
                self._add(name, _Entry(Member(name, size, crc, False), None, start))
                pos = start + packed
                continue
            try:
                _, inflated, computed, end = _inflate(raw, start, budget, keep=False)
            except ReaderError:
                break
            budget -= inflated
            if not sized:
                size, crc = inflated, computed
            if end is not None or inflated:
                self._add(name, _Entry(Member(name, size, crc, False), None, start))
            pos = start if end is None else end

    def _read_listed(self, info: zipfile.ZipInfo, limit: int) -> bytes | None:
        """Read through zipfile; None when it refuses the member."""
        try:
            with self._zip.open(info) as stream:
                data = stream.read(limit + 1)
        except Exception:  # bad CRC, name mismatch, broken stream, unknown method
            return None
        if len(data) > limit:
            raise _Oversized()
        return data

    def _locate(self, entry: _Entry) -> _Entry:
        """Where a listed member's data really is, going by its local header."""
        info = entry.info
        raw = self._data()
        offset = info.header_offset
        if raw[offset:offset + 4] != _LOCAL or offset + _LOCAL_HEADER.size > len(raw):
            raise ReaderError("corrupt", _DAMAGED)
        fields = _LOCAL_HEADER.unpack_from(raw, offset)
        method = fields[3] if fields[3] in (_STORED, _DEFLATED) else info.compress_type
        start = offset + _LOCAL_HEADER.size + fields[9] + fields[10]
        return _Entry(entry.member, None, start, method, info.compress_size)

    def _read_raw(self, entry: _Entry, limit: int) -> bytes:
        """Read straight from the local header, ignoring the checksum."""
        raw = self._data()
        if entry.method == _STORED:
            if entry.stored > limit:
                raise _Oversized()
            data = bytes(raw[entry.start:entry.start + entry.stored])
        elif entry.method == _DEFLATED:
            data = _inflate(raw, entry.start, limit, keep=True)[0]
        else:
            raise ReaderError("corrupt", _DAMAGED)
        if not data and entry.member.size:
            raise ReaderError("corrupt", _DAMAGED)
        return data


def _decode_name(written: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return written.decode(encoding)
        except UnicodeDecodeError:
            pass
    return written.decode("latin-1")


def _is_directory(written: str) -> bool:
    return written.partition("\0")[0].endswith(("/", "\\"))


def _canonical(written: str) -> str | None:
    """The canonical form of a member name; None if it is empty or escapes the root."""
    parts: list[str] = []
    for part in written.partition("\0")[0].replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return unicodedata.normalize("NFC", "/".join(parts)) or None


def _at_boundary(raw: bytes | mmap.mmap, pos: int) -> bool:
    """Whether a member could end at pos: the end of the file or another record."""
    return pos == len(raw) or raw[pos:pos + 4] in _SIGNATURES


def _next_signature(raw: bytes | mmap.mmap, pos: int) -> int:
    while True:
        pos = raw.find(b"PK", pos)
        if pos < 0:
            return len(raw)
        if raw[pos:pos + 4] in _SIGNATURES:
            return pos
        pos += 2


def _crc(raw: bytes | mmap.mmap, start: int, end: int) -> int:
    crc = 0
    for pos in range(start, end, _CHUNK):
        crc = zlib.crc32(raw[pos:min(pos + _CHUNK, end)], crc)
    return crc


def _inflate(
    raw: bytes | mmap.mmap, start: int, limit: int, *, keep: bool
) -> tuple[bytes, int, int, int | None]:
    """Inflate the raw deflate stream at start, stopping quietly where it breaks.

    Returns (data, inflated size, crc, end offset); data is empty unless
    `keep`, and the end offset is None when the stream never finished.
    Raises ReaderError once more than `limit` bytes have come out.
    """
    inflater = zlib.decompressobj(-15)
    parts: list[bytes] = []
    size = crc = 0
    pos = start
    pending = b""
    while not inflater.eof:
        if not pending:
            pending = bytes(raw[pos:pos + _CHUNK])
            pos += len(pending)
            if not pending:
                break
        # zlib discards a whole call's output when the call fails, so the
        # failing chunk is replayed in small steps from a saved state.
        saved = inflater.copy() if keep else None
        try:
            out = inflater.decompress(pending, 4 * _CHUNK)
            broken = False
        except zlib.error:
            out = _salvage(saved, pending) if saved else b""
            broken = True
        pending = inflater.unconsumed_tail
        size += len(out)
        if size > limit:
            raise _Oversized()
        crc = zlib.crc32(out, crc)
        if keep:
            parts.append(out)
        if broken:
            break
    end = pos - len(inflater.unused_data) if inflater.eof else None
    return b"".join(parts), size, crc, end


def _salvage(inflater: "zlib._Decompress", chunk: bytes) -> bytes:
    """What a deflate stream yields from a chunk before the point it breaks."""
    parts: list[bytes] = []
    try:
        for pos in range(0, len(chunk), _SALVAGE_STEP):
            parts.append(inflater.decompress(chunk[pos:pos + _SALVAGE_STEP]))
    except zlib.error:
        pass
    return b"".join(parts)
