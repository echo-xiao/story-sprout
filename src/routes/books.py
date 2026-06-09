"""Book management endpoints."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from src.config import GENERATED_DIR
from src.core.models import GenerationConfig
from src.core.pipeline import (
    delete_book,
    generate_picture_book,
    get_book,
    get_status,
    list_books,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class FetchUrlRequest(BaseModel):
    url: str


@router.post("/api/fetch-url")
async def fetch_book_from_url(req: FetchUrlRequest) -> dict[str, Any]:
    """Fetch plain text from a URL (e.g. Project Gutenberg)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(req.url)
            resp.raise_for_status()
            text = resp.text

        # Try to extract title from Gutenberg header
        title = ""
        for line in text.split("\n")[:50]:
            if line.strip().lower().startswith("title:"):
                title = line.split(":", 1)[1].strip()
                break

        # Strip Gutenberg header/footer if present
        start_markers = ["*** START OF THE PROJECT GUTENBERG", "*** START OF THIS PROJECT GUTENBERG"]
        end_markers = ["*** END OF THE PROJECT GUTENBERG", "*** END OF THIS PROJECT GUTENBERG"]
        for marker in start_markers:
            idx = text.find(marker)
            if idx != -1:
                text = text[text.index("\n", idx) + 1:]
                break
        for marker in end_markers:
            idx = text.find(marker)
            if idx != -1:
                text = text[:idx]
                break

        return {"text": text.strip(), "title": title or "Untitled"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {str(e)}")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    source_text: str
    config: GenerationConfig = GenerationConfig()


# ---------------------------------------------------------------------------
# Background task wrapper
# ---------------------------------------------------------------------------

async def _run_generation(source: str, config: GenerationConfig, book_id: str) -> None:
    try:
        await generate_picture_book(source, config, book_id=book_id)
    except Exception:
        logger.exception("Background generation failed for book_id=%s", book_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/generate")
async def start_generation(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Start preprocess from text. Returns book_id for editor redirect."""
    if not request.source_text.strip():
        raise HTTPException(status_code=400, detail="source_text cannot be empty.")

    import re as _re

    # Save text to file
    upload_dir = GENERATED_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"text_{uuid.uuid4().hex[:8]}.txt"
    dest.write_text(request.source_text, encoding="utf-8")

    # Quick book_id from first line (no heavy parsing)
    first_line = request.source_text.strip().split("\n")[0].strip()[:100]
    sanitized = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', first_line)
    book_id = _re.sub(r'\s+', '_', sanitized.strip()).lower()[:60] or "untitled"

    # Run preprocess as a separate process (non-blocking)
    import subprocess
    subprocess.Popen(
        ["python", "scripts/preprocess_book.py", "--input", str(dest), "--skip-sheets"],
        cwd=str(Path(__file__).parent.parent.parent),
    )

    return {"book_id": book_id, "status": "preprocessing"}


@router.post("/api/generate/upload")
async def start_generation_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    config: str = Form(default="{}"),
) -> dict[str, Any]:
    """Start preprocess from file upload. Returns book_id for editor redirect."""
    upload_dir = GENERATED_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / (file.filename or "upload.txt")
    contents = await file.read()
    dest.write_bytes(contents)

    # Use filename as book_id (fast, no parsing needed)
    import re as _re
    stem = Path(file.filename or "upload").stem
    sanitized = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', stem)
    book_id = _re.sub(r'\s+', '_', sanitized.strip()).lower()[:60] or "untitled"

    # Run preprocess as a separate process (non-blocking)
    import subprocess
    subprocess.Popen(
        ["python", "scripts/preprocess_book.py", "--input", str(dest), "--skip-sheets"],
        cwd=str(Path(__file__).parent.parent.parent),
    )

    return {"book_id": book_id, "status": "preprocessing"}


@router.get("/api/status/{book_id}")
async def get_generation_status(book_id: str) -> dict[str, Any]:
    status = await get_status(book_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    return status


@router.get("/api/book/{book_id}")
async def get_book_data(book_id: str) -> dict[str, Any]:
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    return book


@router.get("/api/book/{book_id}/steps")
async def get_book_steps(book_id: str) -> list[dict[str, Any]]:
    """Get all intermediate pipeline steps for a book."""
    from src.core.step_logger import get_steps
    steps = get_steps(book_id)
    if not steps:
        raise HTTPException(status_code=404, detail="No steps found. Book may not exist or is still generating.")
    return steps


@router.get("/api/book/{book_id}/html", response_class=HTMLResponse)
async def get_book_html(book_id: str) -> HTMLResponse:
    html_path = GENERATED_DIR / book_id / "book.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="HTML not found. Book may still be generating.")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@router.get("/api/book/{book_id}/pdf")
async def get_book_pdf(book_id: str) -> FileResponse:
    from src.renderer import export_pdf

    book = await get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found.")

    pdf_path = GENERATED_DIR / book_id / "book.pdf"
    if not pdf_path.exists():
        pages = book.get("pages", [])
        title = book.get("title", "Untitled")
        export_pdf(pages, title, str(pdf_path))

    if not pdf_path.exists():
        raise HTTPException(status_code=500, detail="PDF generation failed.")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"{book.get('title', 'picture_book')}.pdf",
    )


@router.get("/api/books")
async def list_all_books() -> list[dict[str, Any]]:
    return await list_books()


@router.get("/api/book/{book_id}/preprocess/progress")
async def get_preprocess_progress(book_id: str) -> dict[str, Any]:
    """Check preprocess progress by examining which files exist."""
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    if not preprocess_dir.exists():
        return {"status": "not_started", "progress": 0, "step": "Waiting to start...", "steps_done": []}

    steps_done = []
    # Check each layer
    if (preprocess_dir / "chapters.json").exists():
        steps_done.append("extract_text")
    if (preprocess_dir / "llm_characters.json").exists():
        steps_done.append("identify_characters")
    if (preprocess_dir / "alias_map.json").exists():
        steps_done.append("build_aliases")
    if (preprocess_dir / "cleaned_chapters.json").exists():
        steps_done.append("replace_aliases")
    if (preprocess_dir / "segments_raw.json").exists():
        steps_done.append("segment_text")
    # Check annotation progress
    annotations_dir = preprocess_dir / "annotations"
    total_chapters = 0
    annotated_chapters = 0
    # Get total chapters from chapters.json (available early) or chapter_segments.json
    if (preprocess_dir / "chapters.json").exists():
        chs = json.loads((preprocess_dir / "chapters.json").read_text())
        total_chapters = len(chs)
    elif (preprocess_dir / "chapter_segments.json").exists():
        cs = json.loads((preprocess_dir / "chapter_segments.json").read_text())
        total_chapters = len(cs)
    if annotations_dir.exists():
        annotated_chapters = len(list(annotations_dir.glob("ch*.json")))
    if (preprocess_dir / "analysis.json").exists():
        steps_done.append("annotate_complete")

    # Calculate progress
    base_progress = len([s for s in steps_done if s != "annotate_complete"]) * 15  # 5 steps * 15% = 75%
    if total_chapters > 0 and "segment_text" in steps_done:
        annotation_progress = (annotated_chapters / total_chapters) * 25  # annotations = 25%
    else:
        annotation_progress = 0

    progress = min(100, base_progress + annotation_progress)
    if "annotate_complete" in steps_done:
        progress = 100

    # Current step label
    step_labels = {
        0: "Extracting text and chapters...",
        1: "Identifying characters with AI...",
        2: "Building alias map...",
        3: "Replacing aliases in text...",
        4: "Segmenting into scenes...",
        5: f"Annotating scenes ({annotated_chapters}/{total_chapters} chapters)...",
    }
    current = len([s for s in steps_done if s != "annotate_complete"])
    if current >= 5 and "annotate_complete" not in steps_done:
        step = step_labels.get(5, "Annotating...")
    else:
        step = step_labels.get(current, "Processing...")

    return {
        "status": "complete" if progress >= 100 else "processing",
        "progress": round(progress),
        "step": step,
        "steps_done": steps_done,
        "annotated_chapters": annotated_chapters,
        "total_chapters": total_chapters,
    }


@router.get("/api/books/preprocessed")
async def list_preprocessed_books() -> list[dict[str, Any]]:
    """List all books that have preprocess data (from disk)."""
    books = []
    for d in sorted(GENERATED_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "preprocess" / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            # Count generated chapters
            chapters_dir = d / "chapters"
            generated_chapters = 0
            total_pages = 0
            if chapters_dir.exists():
                for ch_dir in chapters_dir.iterdir():
                    if ch_dir.is_dir() and ch_dir.name.startswith("ch"):
                        generated_chapters += 1
                        pages_dir = ch_dir / "pages"
                        if pages_dir.exists():
                            total_pages += len(list(pages_dir.glob("page_*.*")))

            # Get character count
            chars_path = d / "preprocess" / "llm_characters.json"
            num_characters = 0
            if chars_path.exists():
                chars_data = json.loads(chars_path.read_text(encoding="utf-8"))
                num_characters = len(chars_data.get("characters", []))

            books.append({
                "book_id": d.name,
                "title": meta.get("title", d.name),
                "num_chapters": meta.get("num_chapters", 0),
                "num_characters": num_characters,
                "generated_chapters": generated_chapters,
                "total_pages": total_pages,
            })
    return books


@router.delete("/api/book/{book_id}")
async def delete_book_endpoint(book_id: str) -> dict[str, str]:
    deleted = await delete_book(book_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Book not found.")

    book_dir = GENERATED_DIR / book_id
    if book_dir.exists():
        import shutil
        shutil.rmtree(book_dir, ignore_errors=True)

    return {"status": "deleted", "book_id": book_id}
