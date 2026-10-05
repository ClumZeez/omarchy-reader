"""The command line: `reader scan | open | info`, one JSON object on stdout."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import sys
import traceback

from . import library
from .errors import ReaderError

_INTERNAL = "Something went wrong inside Reader."
_USAGE = "Reader was asked to do something it doesn't know how to do."
_TOO_MUCH = "This book asks for more memory than any real book needs, so it was not opened."
_NO_ROOM = "There is no room left on this computer's disk for Reader to work."

# The largest books met in testing need about a twentieth of this. A file
# built to exhaust memory would otherwise take the whole desktop down with
# the shell that launched us.
MEMORY_LIMIT = 2 * 1024 * 1024 * 1024


def limit_memory() -> None:
    """Put a ceiling on this process's memory; called once by the launcher."""
    try:
        import resource
    except ImportError:
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        ceiling = MEMORY_LIMIT if hard == resource.RLIM_INFINITY else min(MEMORY_LIMIT, hard)
        if soft == resource.RLIM_INFINITY or soft > ceiling:
            resource.setrlimit(resource.RLIMIT_AS, (ceiling, hard))
    except (ValueError, OSError):
        pass


def default_cache() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "omarchy-reader")


def _options(argv: list[str]) -> tuple[str, list[str], list[str]]:
    """Split the arguments into the cache directory, `--dir` values and the rest.

    Everything after a bare `--` is taken literally, so a file may be called
    anything at all.
    """
    cache = default_cache()
    dirs: list[str] = []
    rest: list[str] = []
    position = 0
    while position < len(argv):
        argument = argv[position]
        position += 1
        if argument == "--":
            rest.extend(argv[position:])
            break
        name, equals, value = argument.partition("=")
        if name in ("--cache", "--dir"):
            if not equals:
                if position >= len(argv):
                    raise ReaderError("internal", _USAGE)
                value = argv[position]
                position += 1
            if not value:
                # Taken as given it would mean the directory we were started
                # in, which may be the plugin's own.
                raise ReaderError("internal", _USAGE)
            if name == "--cache":
                cache = value
            else:
                dirs.append(value)
        elif argument.startswith("--"):
            raise ReaderError("internal", _USAGE)
        else:
            rest.append(argument)
    return cache, dirs, rest


def _run(argv: list[str]) -> dict:
    cache, dirs, rest = _options(argv)
    command = rest[0] if rest else ""
    if command == "scan" and len(rest) == 1:
        return library.scan(dirs or None, cache)
    if command == "open" and len(rest) == 2 and not dirs:
        return library.open_book(rest[1], cache)
    if command == "info" and len(rest) == 2 and not dirs:
        return library.info(rest[1])
    raise ReaderError("internal", _USAGE)


def _failure(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def main(argv: list[str]) -> int:
    """Run one command; print its result as a single JSON object; return the exit status."""
    out = sys.stdout
    try:
        # Nothing but the result may reach stdout, whatever a module prints.
        with contextlib.redirect_stdout(sys.stderr):
            result = _run(list(argv))
    except ReaderError as error:
        result = _failure(error.code, error.message)
    except MemoryError:
        result = _failure("corrupt", _TOO_MUCH)
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        full = isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EDQUOT)
        result = _failure("internal", _NO_ROOM if full else _INTERNAL)
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    data = text.encode("utf-8", "replace")
    binary = getattr(out, "buffer", None)
    try:
        if binary is not None:
            out.flush()
            binary.write(data)
            binary.flush()
        else:
            out.write(data.decode("utf-8"))
            out.flush()
    except OSError:
        return 1
    return 0 if result.get("ok") else 1
