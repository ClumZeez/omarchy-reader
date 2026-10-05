"""Shared setup for the backend tests: import path and well-known locations.

Run the suite with:  python3 -B -m unittest discover -s tests/py
"""

import os
import sys

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# Real books and downloaded format samples are optional: tests that need them
# skip when the directory is absent, so the suite passes on any machine.
LIBRARY = os.environ.get("READER_TEST_LIBRARY") or os.path.expanduser("~/Documents/EPUB")
SAMPLES = os.environ.get("READER_TEST_SAMPLES") or os.path.expanduser("~/.cache/omarchy-reader-test-samples")


def library_books() -> list[str]:
    """Paths of the EPUBs in the real library, sorted; empty when absent."""
    if not os.path.isdir(LIBRARY):
        return []
    return sorted(
        os.path.join(LIBRARY, name)
        for name in os.listdir(LIBRARY)
        if name.lower().endswith(".epub")
    )


def sample(*parts: str) -> str:
    """Path of a downloaded sample, e.g. sample("mobi", "se-alice.azw3")."""
    return os.path.join(SAMPLES, *parts)
