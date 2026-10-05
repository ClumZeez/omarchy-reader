"""The one error type the backend reports to the reader."""

CODES = ("missing", "unsupported", "drm", "corrupt", "internal")


class ReaderError(Exception):
    """A book could not be handled.

    `message` is shown to the reader verbatim, so it is one plain sentence
    with no format jargon; `code` is one of CODES.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code if code in CODES else "internal"
        self.message = message
