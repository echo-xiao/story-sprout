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
# Mount frontend (SPA) -- must be last so API routes take priority
# ---------------------------------------------------------------------------

_frontend_build = Path(__file__).parent.parent / "frontend" / ".next"
if _frontend_build.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_build), html=True), name="frontend")
