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
    book_id = _re.sub(r'\s+', '_', sanitized.strip())[:60] or "untitled"

    # Run preprocess as a separate process (non-blocking)
    import subprocess
    subprocess.Popen(
        ["python", "scripts/preprocess_book.py", "--input", str(dest), "--skip-sheets"],
        cwd=str(Path(__file__).parent.parent),
    )

    return {"book_id": book_id, "status": "preprocessing"}


@app.post("/api/generate/upload")
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
    book_id = _re.sub(r'\s+', '_', sanitized.strip()).upper()[:60] or "UNTITLED"

    # Run preprocess as a separate process (non-blocking)
    import subprocess
    subprocess.Popen(
        ["python", "scripts/preprocess_book.py", "--input", str(dest), "--skip-sheets"],
        cwd=str(Path(__file__).parent.parent),
    )

    return {"book_id": book_id, "status": "preprocessing"}


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


@app.get("/api/book/{book_id}/preprocess/progress")
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


@app.get("/api/books/preprocessed")
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


@app.get("/api/book/{book_id}/segment/{seg_id}/history")
async def get_segment_illustration_history(book_id: str, seg_id: int) -> dict[str, Any]:
    """Get all historical illustrations for a segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        return {"images": []}

    segments = analysis.get("segments", [])
    target = next((s for s in segments if s.get("id") == seg_id), None)
    if not target:
        return {"images": []}

    ch_idx = target.get("chapter_idx", 0)
    ch_segments = sorted([s for s in segments if s.get("chapter_idx") == ch_idx], key=lambda s: s.get("id", 0))
    page_num = next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)

    # Find all versions in pages dir + history dir
    images = []
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"
    pages_dir = ch_dir / "pages"
    history_dir = ch_dir / "history"

    # Current image + quality
    if pages_dir.exists():
        for ext in (".png", ".jpg"):
            current = pages_dir / f"page_{page_num:03d}{ext}"
            if current.exists():
                entry: dict[str, Any] = {
                    "url": f"/static/{book_id}/chapters/ch{ch_idx:02d}/pages/{current.name}",
                    "version": "current",
                    "timestamp": current.stat().st_mtime,
                }
                # Attach quality if exists
                qf = ch_dir / "quality" / f"page_{page_num:03d}_quality.json"
                if qf.exists():
                    entry["quality"] = json.loads(qf.read_text(encoding="utf-8"))
                images.append(entry)
                break

    # Historical images + quality
    if history_dir.exists():
        for f in sorted(history_dir.glob(f"page_{page_num:03d}_*.*"), reverse=True):
            if f.suffix == ".json":
                continue  # skip quality files, they're attached below
            version_ts = f.stem.split("_")[-1]
            entry = {
                "url": f"/static/{book_id}/chapters/ch{ch_idx:02d}/history/{f.name}",
                "version": version_ts,
                "timestamp": f.stat().st_mtime,
            }
            # Attach quality for this version
            qf = history_dir / f"page_{page_num:03d}_{version_ts}_quality.json"
            if qf.exists():
                entry["quality"] = json.loads(qf.read_text(encoding="utf-8"))
            images.append(entry)

    return {"images": images}


@app.post("/api/book/{book_id}/segment/{seg_id}/simplify")
async def simplify_segment_text(book_id: str, seg_id: int) -> dict[str, Any]:
    """Generate simplified text for a single segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data.")
    target = next((s for s in analysis["segments"] if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    from src.agent.text_simplifier import simplify_text
    scene = {
        "page_number": 1,
        "original_text": target.get("text", ""),
        "key_characters": target.get("characters_in_scene", []),
        "scene_summary": target.get("scene_summary", ""),
    }
    result = simplify_text([scene], "4-6")
    simplified = result[0].get("page_text", "") if result else ""
    scene_direction = result[0].get("scene_direction", "") if result else ""

    # Save back
    target["simplified_text"] = simplified
    target["scene_direction"] = scene_direction
    _save_json(book_id, "analysis.json", analysis)

    return {"simplified_text": simplified, "scene_direction": scene_direction}


@app.post("/api/book/{book_id}/segment/{seg_id}/background")
async def generate_segment_background(book_id: str, seg_id: int) -> dict[str, Any]:
    """Generate scene background description for a single segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data.")
    target = next((s for s in analysis["segments"] if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    from src.llm_client import generate_json
    result = generate_json(
        f"""Describe the physical setting/environment of this scene from a novel.
Be specific and visual: location, time of day, weather, objects, atmosphere, colors.

Scene text:
{target.get('text', '')[:1000]}

Return JSON: {{"scene_background": "detailed visual description..."}}"""
    )
    background = result.get("scene_background", "")

    target["scene_background"] = background
    _save_json(book_id, "analysis.json", analysis)

    return {"scene_background": background}


@app.post("/api/book/{book_id}/segment/{seg_id}/summarize")
async def summarize_segment(book_id: str, seg_id: int) -> dict[str, Any]:
    """Generate summary and sentiment for a single segment."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data.")
    target = next((s for s in analysis["segments"] if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    from src.llm_client import generate_json
    result = generate_json(
        f"""Summarize this scene in one sentence. Also determine the sentiment.

Scene text:
{target.get('text', '')[:1000]}

Return JSON: {{"scene_summary": "one sentence summary", "sentiment": "positive/negative/neutral/tense/emotional"}}"""
    )
    summary = result.get("scene_summary", "")
    sentiment = result.get("sentiment", "neutral")

    target["scene_summary"] = summary
    target["sentiment"] = sentiment
    _save_json(book_id, "analysis.json", analysis)

    return {"scene_summary": summary, "sentiment": sentiment}


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []  # [{"role": "user"/"assistant", "content": "..."}]


@app.post("/api/book/{book_id}/segment/{seg_id}/chat")
async def chat_segment_prompt(book_id: str, seg_id: int, req: ChatRequest) -> dict[str, Any]:
    """AI assistant to help generate/refine illustration prompt fields via chat."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data.")
    target = next((s for s in analysis["segments"] if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    # Build context from current segment
    context = (
        f"Original text:\n{target.get('text', '')[:1500]}\n\n"
        f"Current simplified_text: {target.get('simplified_text', '')}\n"
        f"Current scene_background: {target.get('scene_background', '')}\n"
        f"Current characters & actions: {json.dumps(target.get('character_actions', []), ensure_ascii=False)}\n"
        f"Current scene_summary: {target.get('scene_summary', '')}\n"
        f"Current sentiment: {target.get('sentiment', 'neutral')}\n"
    )

    system_prompt = """You are an illustration prompt assistant for a children's picture book generator.
The user is editing a page of a picture book adapted from a novel. They will describe what they want the illustration to look like, or ask you to adjust specific fields.

You have access to the current segment data (original text, simplified text, scene background, characters & actions, summary, sentiment).

Based on the user's request, return a JSON object with TWO keys:
1. "reply": a short, helpful response to the user (in the same language the user uses)
2. "updates": an object containing ONLY the fields that should be updated. Possible fields:
   - "simplified_text": the picture-book text for this page
   - "scene_background": visual description of the setting
   - "character_actions": array of {"name": "...", "action": "..."} objects
   - "scene_summary": one-sentence summary
   - "sentiment": one of "positive", "negative", "neutral", "tense", "emotional"

Only include fields in "updates" that the user wants to change. If the user is just asking a question, return empty updates {}.

Example response:
{"reply": "I've updated the background to a rainy night scene.", "updates": {"scene_background": "A dark, rainy night in London..."}}"""

    # Build conversation for LLM
    conversation = f"Current segment context:\n{context}\n\n"
    for msg in req.history[-10:]:  # keep last 10 messages
        role = msg.get("role", "user")
        conversation += f"{'User' if role == 'user' else 'Assistant'}: {msg['content']}\n"
    conversation += f"User: {req.message}"

    from src.llm_client import generate_json
    result = generate_json(conversation, system=system_prompt)

    reply = result.get("reply", "")
    updates = result.get("updates", {})

    # Apply updates to analysis
    if updates:
        for field in ("simplified_text", "scene_background", "scene_summary", "sentiment"):
            if field in updates:
                target[field] = updates[field]
        if "character_actions" in updates:
            target["character_actions"] = updates["character_actions"]
            target["characters_in_scene"] = [
                a["name"] for a in updates["character_actions"] if a.get("name")
            ]
        _save_json(book_id, "analysis.json", analysis)

    return {"reply": reply, "updates": updates}


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

    # Move existing illustration + quality file to history before regenerating
    ch_base = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"
    ch_dir = ch_base / "pages"
    history_dir = ch_base / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    import time as _time
    ts = int(_time.time())
    for ext in (".png", ".jpg"):
        old_img = ch_dir / f"page_{page_num:03d}{ext}"
        if old_img.exists():
            old_img.rename(history_dir / f"page_{page_num:03d}_{ts}{ext}")
    # Move quality file too
    quality_file = ch_base / "quality" / f"page_{page_num:03d}_quality.json"
    if quality_file.exists():
        quality_file.rename(history_dir / f"page_{page_num:03d}_{ts}_quality.json")

    async def _regen():
        from src.agent.text_simplifier import simplify_text
        from src.generation.illustration import generate_illustrations
        from src.generation.character_sheet import _safe_filename, generate_character_sheets

        # Step 1: Generate character sheets if missing
        chars_dir = GENERATED_DIR / book_id / "characters"
        chars_dir.mkdir(parents=True, exist_ok=True)
        character_sheets = []
        chars_to_generate = []

        for name in target.get("characters_in_scene", []):
            safe = _safe_filename(name)
            found = False
            for ext in (".png", ".jpg"):
                sheet_path = chars_dir / f"{safe}_sheet{ext}"
                if sheet_path.exists():
                    character_sheets.append({
                        "character_name": name,
                        "sheet_path": str(sheet_path),
                    })
                    found = True
                    break
            if not found:
                # Find character profile from LLM data
                llm_chars = _load_json(book_id, "llm_characters.json") or {}
                for c in llm_chars.get("characters", []):
                    if c.get("canonical_name") == name:
                        chars_to_generate.append({
                            "name": name,
                            "role": c.get("role", "supporting"),
                            "gender": c.get("gender", "unknown"),
                            "appearance_description": [c.get("appearance", ""), c.get("description", "")],
                        })
                        break

        if chars_to_generate:
            new_sheets = generate_character_sheets(chars_to_generate, book_id)
            character_sheets.extend(new_sheets)

        # Step 2: Simplify text if not done yet
        simplified_text = target.get("simplified_text", "")
        if not simplified_text:
            scene = {
                "page_number": page_num,
                "original_text": target.get("text", ""),
                "key_characters": target.get("characters_in_scene", []),
                "scene_summary": target.get("scene_summary", ""),
            }
            result = simplify_text([scene], "4-6")
            if result:
                simplified_text = result[0].get("page_text", "")
                scene_direction = result[0].get("scene_direction", "")
                # Save back to analysis
                target["simplified_text"] = simplified_text
                target["scene_direction"] = scene_direction
                _save_json(book_id, "analysis.json", analysis)

        # Step 3: Generate illustration
        ch_dir.mkdir(parents=True, exist_ok=True)
        page_prompt = {
            "page_number": page_num,
            "text": simplified_text or target.get("text", ""),
            "scene_description": target.get("scene_direction", target.get("scene_summary", "")),
            "scene_background": target.get("scene_background", ""),
            "key_characters": target.get("characters_in_scene", []),
            "character_actions": target.get("character_actions", []),
        }

        generate_illustrations(
            [page_prompt], character_sheets, book_id,
            pages_dir=str(ch_dir),
        )

    # Run in separate process to not block uvicorn
    import subprocess
    subprocess.Popen(
        ["python", "scripts/generate_chapter.py", "--book", book_id,
         "--chapter", str(ch_idx), "--pages", str(page_num)],
        cwd=str(Path(__file__).parent.parent),
    )
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


@app.get("/api/book/{book_id}/segment/{seg_id}/quality")
async def get_segment_quality(book_id: str, seg_id: int, version: str = "current") -> dict[str, Any]:
    """Get cached quality check result for a segment. Use version=current or a timestamp."""
    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        return {}

    segments = analysis.get("segments", [])
    target = next((s for s in segments if s.get("id") == seg_id), None)
    if not target:
        return {}

    ch_idx = target.get("chapter_idx", 0)
    ch_segments = sorted([s for s in segments if s.get("chapter_idx") == ch_idx], key=lambda s: s.get("id", 0))
    page_num = next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)

    ch_base = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}"

    if version == "current":
        quality_file = ch_base / "quality" / f"page_{page_num:03d}_quality.json"
    else:
        quality_file = ch_base / "history" / f"page_{page_num:03d}_{version}_quality.json"

    if quality_file.exists():
        return json.loads(quality_file.read_text(encoding="utf-8"))
    return {}


@app.post("/api/book/{book_id}/segment/{seg_id}/quality")
async def check_segment_quality(book_id: str, seg_id: int) -> dict[str, Any]:
    """Run quality check on a single segment's illustration."""
    from src.generation.gemini_consistency_check import check_page_quality

    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")

    segments = analysis.get("segments", [])
    target = next((s for s in segments if s.get("id") == seg_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Segment {seg_id} not found.")

    ch_idx = target.get("chapter_idx", 0)
    ch_segments = sorted([s for s in segments if s.get("chapter_idx") == ch_idx], key=lambda s: s.get("id", 0))
    page_num = next((i + 1 for i, s in enumerate(ch_segments) if s.get("id") == seg_id), 1)

    # Find illustration
    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "pages"
    ill_path = ""
    for ext in (".png", ".jpg"):
        candidate = ch_dir / f"page_{page_num:03d}{ext}"
        if candidate.exists():
            ill_path = str(candidate)
            break
    if not ill_path:
        raise HTTPException(status_code=404, detail="No illustration found for this segment.")

    # Find character sheets
    import re as _re
    chars_dir = GENERATED_DIR / book_id / "characters"
    llm_chars = _load_json(book_id, "llm_characters.json") or {}
    scene_chars = target.get("characters_in_scene", [])
    character_sheets = []
    for char in llm_chars.get("characters", []):
        name = char.get("canonical_name", "")
        if name not in scene_chars:
            continue
        safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
        safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
        for ext in (".png", ".jpg"):
            sheet_path = chars_dir / f"{safe}_sheet{ext}"
            if sheet_path.exists():
                character_sheets.append({
                    "character_name": name,
                    "sheet_path": str(sheet_path),
                    "visual_identity": char.get("appearance", ""),
                })
                break

    page_text = target.get("simplified_text", target.get("text", ""))
    result = check_page_quality(ill_path, character_sheets, page_text, scene_chars, page_num)
    result["page"] = page_num
    result["segment_id"] = seg_id
    return result


@app.get("/api/book/{book_id}/chapter/{ch_idx}/consistency")
async def get_chapter_consistency(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Get cached consistency/quality check results for a chapter."""
    consistency_path = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "consistency.json"
    if consistency_path.exists():
        return json.loads(consistency_path.read_text(encoding="utf-8"))
    return {}


@app.post("/api/book/{book_id}/chapter/{ch_idx}/consistency")
async def check_chapter_consistency(book_id: str, ch_idx: int) -> dict[str, Any]:
    """Run full quality check on a chapter's illustrations.

    Checks 5 dimensions per page + style coherence across pages:
    1. Character consistency (vs reference sheets)
    2. Spelling errors in embedded text
    3. Duplicate characters (same person drawn twice)
    4. Name-face mismatch (label points to wrong character)
    5. Missing/extra characters
    6. Style coherence (across all pages)
    """
    from src.generation.gemini_consistency_check import (
        check_page_quality,
        check_style_consistency,
    )

    analysis = _load_json(book_id, "analysis.json")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found.")

    segments = analysis.get("segments", [])
    ch_segments = sorted(
        [s for s in segments if s.get("chapter_idx") == ch_idx],
        key=lambda s: s.get("id", 0),
    )

    ch_dir = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "pages"
    chars_dir = GENERATED_DIR / book_id / "characters"

    # Build character sheets
    import re as _re
    llm_chars = _load_json(book_id, "llm_characters.json") or {}
    character_sheets = []
    for char in llm_chars.get("characters", []):
        name = char.get("canonical_name", "")
        safe = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
        safe = _re.sub(r'\s+', '_', safe.strip()).lower()[:50]
        for ext in (".png", ".jpg"):
            sheet_path = chars_dir / f"{safe}_sheet{ext}"
            if sheet_path.exists():
                character_sheets.append({
                    "character_name": name,
                    "sheet_path": str(sheet_path),
                    "visual_identity": char.get("appearance", ""),
                })
                break

    # Per-page quality check
    ill_paths = []
    per_page_results = []
    per_character_scores: dict[str, list[int]] = {}

    for idx, seg in enumerate(ch_segments):
        page_num = idx + 1
        ill_path = ""
        for ext in (".png", ".jpg"):
            img_path = ch_dir / f"page_{page_num:03d}{ext}"
            if img_path.exists():
                ill_path = str(img_path)
                break
        if not ill_path:
            continue
        ill_paths.append(ill_path)

        scene_chars = seg.get("characters_in_scene", [])
        page_text = seg.get("simplified_text", seg.get("text", ""))
        relevant_sheets = [s for s in character_sheets if s["character_name"] in scene_chars]

        result = check_page_quality(ill_path, relevant_sheets, page_text, scene_chars, page_num)
        result["page"] = page_num
        per_page_results.append(result)

        for c in result.get("character_consistency", {}).get("characters", []):
            per_character_scores.setdefault(c["name"], []).append(c.get("score", 100))

    # Aggregate per-character
    per_character_avg = []
    for name, scores in per_character_scores.items():
        avg = round(sum(scores) / len(scores)) if scores else 100
        per_character_avg.append({"name": name, "score": avg})
    char_overall = round(sum(c["score"] for c in per_character_avg) / len(per_character_avg)) if per_character_avg else 100

    # Style coherence
    # Use book cover as style reference if available
    cover_path = None
    special_dir = GENERATED_DIR / book_id / "special"
    if special_dir.exists():
        for ext in (".png", ".jpg"):
            candidate = special_dir / f"book_cover{ext}"
            if candidate.exists():
                cover_path = str(candidate)
                break
    style_result = check_style_consistency(ill_paths, reference_path=cover_path)

    # Aggregate dimension scores
    n = max(len(per_page_results), 1)
    dim_scores = {
        "character_consistency": round(sum(r.get("character_consistency", {}).get("score", 100) for r in per_page_results) / n),
        "spelling": round(sum(r.get("spelling", {}).get("score", 100) for r in per_page_results) / n),
        "duplicate_characters": round(sum(r.get("duplicate_characters", {}).get("score", 100) for r in per_page_results) / n),
        "name_face_mismatch": round(sum(r.get("name_face_mismatch", {}).get("score", 100) for r in per_page_results) / n),
        "character_count": round(sum(r.get("character_count", {}).get("score", 100) for r in per_page_results) / n),
        "style_coherence": style_result.get("score", 100),
    }

    consistency_result = {
        "overall_score": round(sum(dim_scores.values()) / len(dim_scores)),
        "dimensions": dim_scores,
        "character_match": {"score": char_overall, "per_character": per_character_avg},
        "style_coherence": style_result,
        "per_page": per_page_results,
    }

    # Cache to disk
    consistency_path = GENERATED_DIR / book_id / "chapters" / f"ch{ch_idx:02d}" / "consistency.json"
    consistency_path.parent.mkdir(parents=True, exist_ok=True)
    consistency_path.write_text(json.dumps(consistency_result, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    return consistency_result


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
