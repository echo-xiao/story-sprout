from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF


def parse(file_path: str | Path) -> dict[str, Any]:
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")
    if file_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {file_path.suffix}")

    doc = fitz.open(str(file_path))
    title = doc.metadata.get("title", "") or file_path.stem

    pages: list[str] = []
    page_blocks: list[list[dict[str, Any]]] = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        pages.append(page.get_text())

        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    page_blocks.append([])
                    page_blocks[-1].append(
                        {
                            "text": span["text"],
                            "size": span["size"],
                            "page": page_num,
                        }
                    )

    # Flatten spans across all pages for heading detection
    all_spans: list[dict[str, Any]] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    all_spans.append(
                        {
                            "text": span["text"].strip(),
                            "size": span["size"],
                            "page": page_num,
                        }
                    )

    # Determine median font size to identify headings
    sizes = [s["size"] for s in all_spans if s["text"]]
    median_size = sorted(sizes)[len(sizes) // 2] if sizes else 12.0
    heading_threshold = median_size * 1.2

    chapter_pattern = re.compile(
        r"^(chapter\s+\d+|chapter\s+[ivxlcdm]+|part\s+\d+|part\s+[ivxlcdm]+)",
        re.IGNORECASE,
    )

    # Detect chapter boundaries
    chapter_starts: list[dict[str, Any]] = []
    for span in all_spans:
        if not span["text"]:
            continue
        is_large = span["size"] >= heading_threshold
        is_chapter_pattern = bool(chapter_pattern.match(span["text"]))
        if is_large or is_chapter_pattern:
            chapter_starts.append(
                {
                    "title": span["text"],
                    "page": span["page"],
                }
            )

    # Deduplicate consecutive headings on the same page with the same text
    deduped: list[dict[str, Any]] = []
    for ch in chapter_starts:
        if deduped and deduped[-1]["title"] == ch["title"] and deduped[-1]["page"] == ch["page"]:
            continue
        deduped.append(ch)
    chapter_starts = deduped

    full_text = "\n".join(pages)

    if not chapter_starts:
        chapters = [
            {
                "title": title,
                "text": full_text,
                "page_range": (0, len(doc) - 1),
            }
        ]
    else:
        chapters = []
        for i, ch in enumerate(chapter_starts):
            start_page = ch["page"]
            end_page = (
                chapter_starts[i + 1]["page"] - 1
                if i + 1 < len(chapter_starts)
                else len(doc) - 1
            )
            end_page = max(start_page, end_page)
            chapter_text = "\n".join(pages[start_page : end_page + 1])
            chapters.append(
                {
                    "title": ch["title"],
                    "text": chapter_text,
                    "page_range": (start_page, end_page),
                }
            )

    doc.close()

    return {
        "title": title,
        "chapters": chapters,
        "full_text": full_text,
    }
