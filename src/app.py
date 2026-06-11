"""FastAPI application for the picture-book generator."""

from __future__ import annotations

import asyncio
from pathlib import Path

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.config import GENERATED_DIR

GCS_BUCKET = os.getenv("GCS_BUCKET", "picture-book-gen-assets")
from src.routes import books, editor, generation

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


# Request timeout middleware — prevent hung requests from blocking the server
class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=600)
        except asyncio.TimeoutError:
            return JSONResponse({"error": "Request timed out"}, status_code=504)


app.add_middleware(TimeoutMiddleware)

# Serve generated images / assets — local first, fallback to GCS
GENERATED_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/static/{file_path:path}")
async def serve_static(file_path: str):
    """Serve from local disk if available, otherwise redirect to GCS."""
    local_path = (GENERATED_DIR / file_path).resolve()
    if not local_path.is_relative_to(GENERATED_DIR.resolve()):
        return JSONResponse({"error": "Not found"}, status_code=404)
    if local_path.exists() and local_path.is_file():
        from fastapi.responses import FileResponse
        return FileResponse(str(local_path))
    # Fallback to GCS
    gcs_url = f"https://storage.googleapis.com/{GCS_BUCKET}/{file_path}"
    return RedirectResponse(url=gcs_url)

# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------

app.include_router(books.router)
app.include_router(editor.router)
app.include_router(generation.router)

# ---------------------------------------------------------------------------
# Mount frontend (SPA) -- must be last so API routes take priority
# ---------------------------------------------------------------------------

# Production: static export from Next.js (Docker build)
_frontend_static = Path(__file__).parent.parent / "frontend-static"
# Dev: Next.js .next directory
_frontend_dev = Path(__file__).parent.parent / "frontend" / ".next"

if _frontend_static.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_static), html=True), name="frontend")
elif _frontend_dev.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dev), html=True), name="frontend")
