"""FastAPI application for the picture-book generator."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import GENERATED_DIR
from src.models import GenerationConfig, GenerationStatus, StatusEnum
from src.pipeline import (
    delete_book,
    generate_picture_book,
    get_book,
    get_status,
    list_books,
    save_status,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Picture Book Generator",
    version="0.1.0",
    description="Generate illustrated children's picture books from text.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated images / assets
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(GENERATED_DIR)), name="static")


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

@app.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/generate")
async def start_generation(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Start generation from JSON body with source_text."""
    if not request.source_text.strip():
        raise HTTPException(status_code=400, detail="source_text cannot be empty.")

    book_id = uuid.uuid4().hex[:12]
    initial_status = GenerationStatus(
        book_id=book_id,
        status=StatusEnum.QUEUED,
        progress=0,
        current_step="queued",
    )
    await save_status(initial_status)

    background_tasks.add_task(_run_generation, request.source_text, request.config, book_id)
    return {"book_id": book_id, "status": "queued"}


@app.post("/api/generate/upload")
async def start_generation_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    config: str = Form(default="{}"),
) -> dict[str, Any]:
    """Start generation from file upload."""
    upload_dir = GENERATED_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / (file.filename or "upload.txt")
    contents = await file.read()
    dest.write_bytes(contents)

    try:
        config_dict = json.loads(config)
        gen_config = GenerationConfig(**config_dict)
    except (json.JSONDecodeError, Exception):
        gen_config = GenerationConfig()

    book_id = uuid.uuid4().hex[:12]
    initial_status = GenerationStatus(
        book_id=book_id,
        status=StatusEnum.QUEUED,
        progress=0,
        current_step="queued",
    )
    await save_status(initial_status)

    background_tasks.add_task(_run_generation, str(dest), gen_config, book_id)
    return {"book_id": book_id, "status": "queued"}


@app.get("/api/status/{book_id}")
async def get_generation_status(book_id: str) -> dict[str, Any]:
    status = await get_status(book_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    return status


@app.get("/api/book/{book_id}")
async def get_book_data(book_id: str) -> dict[str, Any]:
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    return book


@app.get("/api/book/{book_id}/steps")
async def get_book_steps(book_id: str) -> list[dict[str, Any]]:
    """Get all intermediate pipeline steps for a book."""
    from src.step_logger import get_steps
    steps = get_steps(book_id)
    if not steps:
        raise HTTPException(status_code=404, detail="No steps found. Book may not exist or is still generating.")
    return steps


@app.get("/api/book/{book_id}/html", response_class=HTMLResponse)
async def get_book_html(book_id: str) -> HTMLResponse:
    html_path = GENERATED_DIR / book_id / "book.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="HTML not found. Book may still be generating.")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/book/{book_id}/pdf")
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


@app.get("/api/books")
async def list_all_books() -> list[dict[str, Any]]:
    return await list_books()


@app.delete("/api/book/{book_id}")
async def delete_book_endpoint(book_id: str) -> dict[str, str]:
    deleted = await delete_book(book_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Book not found.")

    book_dir = GENERATED_DIR / book_id
    if book_dir.exists():
        import shutil
        shutil.rmtree(book_dir, ignore_errors=True)

    return {"status": "deleted", "book_id": book_id}


# ---------------------------------------------------------------------------
# Segment-level APIs (read/edit preprocess data + regenerate illustrations)
# ---------------------------------------------------------------------------

def _load_json(book_id: str, filename: str) -> dict | list | None:
    path = GENERATED_DIR / book_id / "preprocess" / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(book_id: str, filename: str, data: Any) -> None:
    path = GENERATED_DIR / book_id / "preprocess" / filename
    path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")


@app.get("/api/book/{book_id}/preprocess/chapters")
async def get_chapters(book_id: str) -> dict[str, Any]:
    """Get chapter list with segment counts."""
    chapter_segments = _load_json(book_id, "chapter_segments.json")
    meta = _load_json(book_id, "meta.json")
    if not chapter_segments:
        raise HTTPException(status_code=404, detail="No preprocess data found.")
    return {"meta": meta, "chapters": chapter_segments}


@app.get("/api/book/{book_id}/preprocess/characters")
async def get_characters(book_id: str) -> dict[str, Any]:
    """Get character list with sheets and gender info."""
    llm_chars = _load_json(book_id, "llm_characters.json")
    genders = _load_json(book_id, "character_genders.json") or {}
    alias_map = _load_json(book_id, "alias_map.json") or {}

    # Find character sheet images — match by safe filename
    import re as _re
    chars_dir = GENERATED_DIR / book_id / "characters"
    sheets = {}
    if chars_dir.exists():
        sheet_files = {f.stem.replace("_sheet", ""): f for f in chars_dir.glob("*_sheet.*")}
        # Match each character's canonical name to a sheet file
        all_chars = llm_chars.get("characters", []) if llm_chars else []
        for char in all_chars:
            name = char.get("canonical_name", "")
            # Convert name to safe filename (same logic as character_sheet._safe_filename)
            safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
            safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
            if safe in sheet_files:
                sheets[name] = f"/static/{book_id}/characters/{sheet_files[safe].name}"

    return {
        "characters": llm_chars.get("characters", []) if llm_chars else [],
        "genders": genders,
        "alias_map": alias_map,
        "sheets": sheets,
    }


@app.get("/api/book/{book_id}/preprocess/chapter/{ch_idx}/segments")
async def get_chapter_segments(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Get all segments for a chapter with full data."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")

    segments = analysis.get("segments", [])
    ch_segments = [s for s in segments if s.get("chapter_idx") == ch_idx]

    # Add illustration paths if they exist
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"
    for seg in ch_segments:
        page_num = seg.get("id", 0) - min((s.get("id", 0) for s in ch_segments), default=0) + 1
        for ext in (".png", ".jpg"):
            img_path = ch_dir / "pages" / f"page_{page_num:03d}{ext}"
            if img_path.exists():
                seg["illustration_url"] = f"/static/{book_id}/chapters/ch{ch_idx:02d}/pages/{img_path.name}"
                break

    # Chapter info
    chapter_segments = _load_json(book_id, "chapter_segments.json") or {}
    ch_info = chapter_segments.get(str(ch_idx), {})

    return {
        "chapter_idx": ch_idx,
        "chapter_title": ch_info.get("chapter_title", f"Chapter {ch_idx + 1}"),
        "segments": ch_segments,
    }


class SegmentUpdate(BaseModel):
    text: Optional[str] = None
    simplified_text: Optional[str] = None
    characters_in_scene: Optional[list[str]] = None
    character_actions: Optional[list[dict[str, str]]] = None
    scene_background: Optional[str] = None
    scene_summary: Optional[str] = None
    sentiment: Optional[str] = None


@app.put("/api/book/{book_id}/segment/{seg_id}")
async def update_segment(book_id: str, seg_id: int, update: SegmentUpdate) -> dict[str, Any]:
    """Update a single segment's fields."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")

    segments = analysis.get("segments", [])
    target = None
    for seg in segments:
        if seg.get("id") == seg_id:
            target = seg
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    # Apply updates
    update_dict = update.model_dump(exclude_none=True)
    for key, value in update_dict.items():
        target[key] = value

    # Save back
    _save_json(book_id, "analysis.json", analysis)

    return {"status": "updated", "segment_id": seg_id, "updated_fields": list(update_dict.keys())}


@app.post("/api/book/{book_id}/segment/{seg_id}/regenerate")
async def regenerate_segment_illustration(
    book_id: str, seg_id: int, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Regenerate illustration for a single segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")

    segments = analysis.get("segments", [])
    target = None
    for seg in segments:
        if seg.get("id") == seg_id:
            target = seg
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    ch_idx = target.get("chapter_idx", 0)

    # Find page number within chapter
    ch_segments = [s for s in segments if s.get("chapter_idx") == ch_idx]
    ch_segments.sort(key=lambda s: s.get("id", 0))
    page_num = next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)

    # Delete existing illustration so it gets regenerated
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "pages"
    for ext in (".png", ".jpg"):
        old_img = ch_dir / f"page_{page_num:03d}{ext}"
        if old_img.exists():
            old_img.unlink()

    async def _regen():
        from src.generation.illustration import generate_illustrations
        from src.generation.character_sheet import _safe_filename

        # Load character sheets
        chars_dir = GENERATED_DIR / book_id / "characters"
        character_sheets = []
        for name in target.get("characters_in_scene", []):
            safe = _safe_filename(name)
            for ext in (".png", ".jpg"):
                sheet_path = chars_dir / f"{safe}_sheet{ext}"
                if sheet_path.exists():
                    character_sheets.append({
                        "character_name": name,
                        "sheet_path": str(sheet_path),
                    })
                    break

        # Build page prompt
        page_prompt = {
            "page_number": page_num,
            "text": target.get("simplified_text", target.get("text", "")),
            "scene_description": target.get("scene_summary", ""),
            "scene_background": target.get("scene_background", ""),
            "key_characters": target.get("characters_in_scene", []),
            "character_actions": target.get("character_actions", []),
        }

        generate_illustrations(
            [page_prompt], character_sheets, book_id,
            pages_dir=str(ch_dir),
        )

    background_tasks.add_task(_regen)
    return {"status": "regenerating", "segment_id": seg_id, "page_number": page_num}


@app.post("/api/book/{book_id}/chapter/{ch_idx}/generate")
async def generate_chapter_endpoint(
    book_id: str, ch_idx: int, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Generate illustrations for a chapter (text simplification + illustration)."""
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    if not preprocess_dir.exists():
        raise HTTPException(status_code=404, detail="No preprocess data. Run preprocess first.")

    # Initialize progress file
    progress_file = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "progress.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(json.dumps({"status": "starting", "progress": 0, "current_step": "Starting...", "total_pages": 0, "completed_pages": 0}))

    async def _gen():
        import subprocess
        subprocess.run(
            ["python", "scripts/generate_chapter.py", "--book", book_id, "--chapter", str(ch_idx)],
            cwd=str(Path(__file__).parent.parent),
        )
        # Mark complete
        progress_file.write_text(json.dumps({"status": "complete", "progress": 100, "current_step": "Done", "total_pages": 0, "completed_pages": 0}))

    background_tasks.add_task(_gen)
    return {"status": "generating", "book_id": book_id, "chapter": ch_idx}


@app.get("/api/book/{book_id}/chapter/{ch_idx}/progress")
async def get_chapter_progress(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Get generation progress for a chapter."""
    progress_file = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "progress.json"
    if progress_file.exists():
        return json.loads(progress_file.read_text())

    # Estimate progress from existing page files
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "pages"
    if not ch_dir.exists():
        return {"status": "not_started", "progress": 0, "current_step": "Not started", "total_pages": 0, "completed_pages": 0}

    pages = list(ch_dir.glob("page_*.*"))
    # Get total from preprocess
    analysis = _load_json(book_id, "analysis.json")
    total = 0
    if analysis:
        total = sum(1 for s in analysis.get("segments", []) if s.get("chapter_idx") == ch_idx)

    completed = len(pages)
    progress = int(completed / total * 100) if total > 0 else 0
    return {
        "status": "complete" if completed >= total and total > 0 else "generating",
        "progress": progress,
        "current_step": f"Page {completed}/{total}",
        "total_pages": total,
        "completed_pages": completed,
    }


@app.post("/api/book/{book_id}/characters/{char_name}/regenerate")
async def regenerate_character_sheet(
    book_id: str, char_name: str, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Regenerate character sheet for a specific character."""
    from src.generation.character_sheet import _safe_filename

    # Delete existing sheet
    chars_dir = GENERATED_DIR / book_id / "characters"
    safe = _safe_filename(char_name)
    for ext in (".png", ".jpg"):
        old = chars_dir / f"{safe}_sheet{ext}"
        if old.exists():
            old.unlink()

    async def _regen():
        from src.generation.character_sheet import generate_character_sheets
        llm_chars = _load_json(book_id, "llm_characters.json") or {}
        characters = llm_chars.get("characters", [])

        profile = None
        for c in characters:
            if c.get("canonical_name") == char_name:
                profile = {
                    "name": c["canonical_name"],
                    "role": c.get("role", "supporting"),
                    "gender": c.get("gender", "unknown"),
                    "personality_traits": [],
                    "appearance_description": [c.get("appearance", ""), c.get("description", "")],
                }
                break

        if profile:
            generate_character_sheets([profile], book_id)

    background_tasks.add_task(_regen)
    return {"status": "regenerating", "character": char_name}


# ---------------------------------------------------------------------------
# Mount frontend (SPA) -- must be last so API routes take priority
# ---------------------------------------------------------------------------

_frontend_build = Path(__file__).parent.parent / "frontend" / ".next"
if _frontend_build.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_build), html=True), name="frontend")
