"""FastAPI application for the picture-book generator."""

from __future__ import annotations

import asyncio
import re
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
    # Wildcard origins + credentials is an invalid CORS combo; the SPA is served
    # same-origin so cross-origin credentials are not needed.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Book-id validation middleware — every /api/book/{book_id}/... route builds
# filesystem paths from the raw path segment (GENERATED_DIR / book_id), so a
# traversal value like ".." could read or delete outside the books tree.
# Generated ids only ever contain word chars (incl. CJK), hyphens after
# sanitize+lower (see routes/books.py), so anything else is rejected here once
# rather than in every route.
_BOOK_ID_RE = re.compile(r"^[\w\-]{1,100}$")
_BOOK_PATH_RE = re.compile(r"^/api/(?:book|status)/([^/]+)")


class BookIdValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        m = _BOOK_PATH_RE.match(request.url.path)
        if m and not _BOOK_ID_RE.match(m.group(1)):
            return JSONResponse({"detail": "Invalid book id."}, status_code=400)
        return await call_next(request)


app.add_middleware(BookIdValidationMiddleware)


# Request timeout middleware — prevent hung requests from blocking the server
class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=600)
        except asyncio.TimeoutError:
            return JSONResponse({"error": "Request timed out"}, status_code=504)


app.add_middleware(TimeoutMiddleware)


# BYOK middleware — one place that (1) blocks generation endpoints when
# REQUIRE_USER_KEY is on and no key is supplied, and (2) routes the caller's
# Gemini key into the request context so in-request Gemini calls bill the user.
# (Background-task generation also sets the key explicitly in its task closure /
# subprocess env, since the request context is gone by the time those run.)
_GEN_SUFFIXES = (
    "/generate", "/generate/upload", "/regenerate", "/simplify", "/background",
    "/summarize", "/chat", "/autofill", "/quality", "/consistency",
)


class BYOKMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        from src.gemini_backend import set_user_api_key, reset_user_api_key
        from src.config import REQUIRE_USER_KEY

        key = request.headers.get("x-gemini-key")
        path = request.url.path
        is_gen = request.method == "POST" and any(path.endswith(s) for s in _GEN_SUFFIXES)
        if is_gen and REQUIRE_USER_KEY and not key:
            return JSONResponse(
                {"detail": "A Gemini API key is required to generate. Add yours on the Create page."},
                status_code=403,
            )
        # Only route calls through the caller's key when the BYOK gate is on.
        # With the gate off, a browser-saved free-tier key would otherwise
        # hijack image generation (free tier has 0 quota for the image model)
        # and every regen 429'd while the project backend worked fine.
        token = set_user_api_key(key) if (key and REQUIRE_USER_KEY) else None
        try:
            return await call_next(request)
        finally:
            if token is not None:
                reset_user_api_key(token)


app.add_middleware(BYOKMiddleware)

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
