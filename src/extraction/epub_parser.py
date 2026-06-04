from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup


def parse(file_path: str | Path) -> dict[str, Any]:
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"EPUB file not found: {file_path}")
    if file_path.suffix.lower() != ".epub":
        raise ValueError(f"Expected an .epub file, got: {file_path.suffix}")

    book = epub.read_epub(str(file_path), options={"ignore_ncx": True})
    title = book.get_metadata("DC", "title")
    title = title[0][0] if title else file_path.stem

    chapters: list[dict[str, Any]] = []
    all_text_parts: list[str] = []
    item_index = 0

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        html_content = item.get_content().decode("utf-8", errors="replace")
        soup = BeautifulSoup(html_content, "html.parser")

        # Extract headings and split content by them
        sections = _split_by_headings(soup)

        if sections:
            for section_title, section_text in sections:
                if not section_text.strip():
                    continue
                chapters.append(
                    {
                        "title": section_title,
                        "text": section_text.strip(),
                        "page_range": (item_index, item_index),
                    }
                )
                all_text_parts.append(section_text.strip())
        else:
            body_text = soup.get_text(separator="\n", strip=True)
            if body_text.strip():
                all_text_parts.append(body_text)
                chapters.append(
                    {
                        "title": f"Section {item_index + 1}",
                        "text": body_text.strip(),
                        "page_range": (item_index, item_index),
                    }
                )

        item_index += 1

    # Merge consecutive sections with generic titles if they have no heading
    chapters = _merge_generic_sections(chapters)

    full_text = "\n\n".join(all_text_parts)

    return {
        "title": title,
        "chapters": chapters,
        "full_text": full_text,
    }


def _split_by_headings(soup: BeautifulSoup) -> list[tuple[str, str]]:
    heading_tags = soup.find_all(re.compile(r"^h[12]$", re.IGNORECASE))
    if not heading_tags:
        return []

    sections: list[tuple[str, str]] = []

    for i, heading in enumerate(heading_tags):
        heading_text = heading.get_text(strip=True)
        if not heading_text:
            continue

        # Collect all text between this heading and the next
        content_parts: list[str] = []
        sibling = heading.next_sibling
        next_heading = heading_tags[i + 1] if i + 1 < len(heading_tags) else None

        while sibling:
            if sibling == next_heading:
                break
            if hasattr(sibling, "get_text"):
                text = sibling.get_text(separator="\n", strip=True)
                if text:
                    content_parts.append(text)
            elif isinstance(sibling, str) and sibling.strip():
                content_parts.append(sibling.strip())
            sibling = sibling.next_sibling

        sections.append((heading_text, "\n".join(content_parts)))

    return sections


def _merge_generic_sections(
    chapters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not chapters:
        return chapters

    merged: list[dict[str, Any]] = []
    generic_pattern = re.compile(r"^Section \d+$")

    for ch in chapters:
        if (
            merged
            and generic_pattern.match(ch["title"])
            and generic_pattern.match(merged[-1]["title"])
        ):
            merged[-1]["text"] += "\n\n" + ch["text"]
            merged[-1]["page_range"] = (
                merged[-1]["page_range"][0],
                ch["page_range"][1],
            )
        else:
            merged.append(ch)

    return merged
