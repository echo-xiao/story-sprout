from __future__ import annotations

from pathlib import Path
from typing import Any

from . import epub_parser, pdf_parser, text_input


def extract_text(source: str | Path) -> dict[str, Any]:
    """Auto-detect format and extract structured text from a source.

    Supports: .pdf, .epub, .txt files, or raw text strings.
    """
    # If the source is short enough to be a file path, check if it exists
    source_str = str(source)
    if len(source_str) < 500:
        try:
            path = Path(source_str)
            if path.exists() and path.is_file():
                suffix = path.suffix.lower()
                if suffix == ".pdf":
                    return pdf_parser.parse(path)
                elif suffix == ".epub":
                    return epub_parser.parse(path)
                else:
                    return text_input.parse(path)
        except OSError:
            pass

    # Raw text string
    return text_input.parse(source_str)
