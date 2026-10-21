"""Content sniffing for uploaded files.

An extension check alone is not a content check: a file named ``data.csv``
can be a ZIP archive or an Excel workbook, and the extractors will happily
try to parse it. This module verifies the leading bytes match the declared
extension, so a mislabelled file is rejected before it reaches pandas,
pyarrow, or openpyxl.

Only the formats the app accepts are sniffed. Anything unknown is rejected
rather than waved through.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional

#: Extension → accepted magic-byte prefixes. An empty tuple means "any
#: content" (text formats, where the first bytes are arbitrary).
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".csv": (),          # text — validated by the parser
    ".tsv": (),
    ".txt": (),
    ".json": (),
    ".jsonl": (),
    ".ndjson": (),
    ".parquet": (b"PAR1",),
    ".xlsx": (b"PK\x03\x04",),
    ".xls": (b"\xd0\xcf\x11\xe0",),
}

#: Formats that are ZIP containers (xlsx is a zip; so is a renamed parquet).
_ZIP_EXTENSIONS = {".xlsx"}

#: Magic bytes that mean "this is not a text file". A file declared as a
#: text format (csv/tsv/json/...) that starts with one of these is being
#: mislabelled — a ZIP, an OLE workbook, a Parquet file or a gzip stream
#: wearing a .csv extension. The text parsers would choke on it, and an
#: archive renamed to .csv is exactly the shape of a decompression-bomb
#: upload.
_BINARY_MAGIC = (
    b"PK\x03\x04",      # zip / xlsx / docx / odt
    b"PK\x05\x06",      # empty zip
    b"\xd0\xcf\x11\xe0",  # OLE2 (legacy .xls/.doc)
    b"PAR1",            # parquet
    b"\x1f\x8b",        # gzip
    b"BZh",             # bzip2
    b"\xfd7zXZ\x00",    # xz
    b"%PDF",            # pdf
    b"\x89PNG\r\n\x1a\n",  # png
    b"GIF8",            # gif
    b"\xff\xd8\xff",    # jpeg
)


class SniffError(ValueError):
    """Raised when the file content does not match its extension."""


def sniff_mismatch(filename: str, head: bytes) -> Optional[str]:
    """Return a human-readable reason when *head* contradicts *filename*.

    Returns ``None`` when the content is consistent with the extension (or
    when the extension is not one we can sniff).
    """
    ext = Path(filename or "").suffix.lower()
    expected = _MAGIC.get(ext)
    if expected is None:
        # Unknown extension — the caller's allowlist already rejected it.
        return None
    if not expected:
        # Text-ish format: the parser validates the syntax, but the content
        # must still not be a binary file wearing a text extension.
        for magic in _BINARY_MAGIC:
            if head.startswith(magic):
                return (
                    f"File content does not match its '{ext}' extension "
                    "(it looks like a binary or archive file). Rename or "
                    "re-export the file before uploading."
                )
        return None
    if not head:
        return f"File is empty (expected {ext} content)"
    if ext in _ZIP_EXTENSIONS and head.startswith(b"PK\x03\x04"):
        return None
    for magic in expected:
        if head.startswith(magic):
            return None
    if ext == ".parquet" and head.startswith(b"PAR1"):
        return None
    return (
        f"File content does not match its '{ext}' extension. "
        "Rename or re-export the file before uploading."
    )


def validate_content(filename: str, content: bytes) -> None:
    """Raise :class:`SniffError` when the content contradicts the extension."""
    reason = sniff_mismatch(filename, content[:16])
    if reason:
        raise SniffError(reason)

    ext = Path(filename or "").suffix.lower()
    if ext in _ZIP_EXTENSIONS:
        # A zip that claims to be xlsx must actually contain an xlsx.
        import io

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = set(zf.namelist())
        except Exception as exc:  # noqa: BLE001
            raise SniffError(f"File is not a readable workbook: {exc}") from exc
        if not any(n.startswith("xl/") for n in names):
            raise SniffError(
                "File is a ZIP archive but does not contain a workbook "
                "(no xl/ entries). Rename or re-export it."
            )
