"""Book management endpoints."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from src.config import GENERATED_DIR
from src.core.models import GenerationConfig
from src.routes.helpers import _require_user_key
from src.core.pipeline import (
    delete_book,
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
    import ipaddress
    import socket
    from urllib.parse import urlparse

    def _check_host(u: str) -> None:
        p = urlparse(u)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise HTTPException(status_code=400, detail="Only http/https URLs are allowed")
        try:
            resolved = socket.getaddrinfo(p.hostname, None)
        except socket.gaierror:
            raise HTTPException(status_code=400, detail="Could not resolve URL host")
        for info in resolved:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise HTTPException(status_code=400, detail="URL resolves to a disallowed address")

    try:
        # Follow redirects MANUALLY, re-validating every hop — otherwise a public
        # URL could 302 to the cloud metadata server / an internal IP (SSRF).
        # NOTE: residual TOCTOU risk — _check_host() resolves DNS, then httpx
        # resolves AGAIN for the actual GET, so a DNS-rebinding attacker with a
        # near-zero TTL could swap the record between the two lookups. The full
        # fix (connect by the pinned, validated IP) is deliberately deferred.
        text = None
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            current = req.url
            for _ in range(6):
                _check_host(current)
                resp = await client.get(current)
                if resp.is_redirect and resp.headers.get("location"):
                    current = str(httpx.URL(current).join(resp.headers["location"]))
                    continue
                resp.raise_for_status()
                text = resp.text
                break
        if text is None:
            raise HTTPException(status_code=400, detail="Too many redirects")

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
    except HTTPException:
        raise
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

def _save_user_info(book_id: str, email: str, api_key: str | None = None) -> None:
    """Save user info to book's preprocess dir for tracking."""
    info_dir = GENERATED_DIR / book_id / "preprocess"
    info_dir.mkdir(parents=True, exist_ok=True)
    user_info = {"email": email}
    if api_key:
        user_info["has_api_key"] = True  # Don't store the actual key on disk
    (info_dir / "user.json").write_text(json.dumps(user_info))


async def _run_preprocess(book_id: str, dest: Path, gemini_api_key: str | None = None) -> None:
    """Run preprocess_book.py with error tracking (non-blocking)."""
    import asyncio, os, sys
    env = os.environ.copy()
    if gemini_api_key:
        # Route preprocessing to the USER's key (their billing). Setting the key
        # alone is not enough — make_genai_client() picks Vertex first unless the
        # backend is also switched, so the key was previously ignored.
        env["GEMINI_API_KEY"] = gemini_api_key
        env["GEMINI_BACKEND"] = "api_key"
    # Clear any stale error.json from a previous attempt, else the frontend shows
    # this fresh run as already-failed.
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    preprocess_dir.mkdir(parents=True, exist_ok=True)
    err_file = preprocess_dir / "error.json"
    if err_file.exists():
        try:
            err_file.unlink()
        except OSError:
            pass
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "scripts/preprocess_book.py", "--input", str(dest), "--book-id", book_id, "--skip-sheets",
            cwd=str(Path(__file__).parent.parent.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.error("Preprocess timed out for %s", book_id)
            # Write an error so the frontend stops showing "processing" forever.
            err_file.write_text(json.dumps({"error": "Preprocess timed out after 600s.", "returncode": -1}))
            return

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace") if stderr else ""
            logger.error("Preprocess failed for %s (exit %d): %s", book_id, proc.returncode, stderr_text[-500:])
            error_dir = GENERATED_DIR / book_id / "preprocess"
            error_dir.mkdir(parents=True, exist_ok=True)
            (error_dir / "error.json").write_text(json.dumps({
                "error": stderr_text[-1000:] or "Unknown error",
                "returncode": proc.returncode,
            }))
    except Exception:
        logger.exception("Preprocess crashed for %s", book_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/config")
async def get_app_config() -> dict[str, Any]:
    """Client-readable runtime config — whether BYOK is enforced (so the editor
    knows whether to gate generation behind a user-supplied key)."""
    from src.config import REQUIRE_USER_KEY
    return {"require_user_key": REQUIRE_USER_KEY}


@router.post("/api/generate")
async def start_generation(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
    header_key: str | None = Depends(_require_user_key),  # BYOK 403 gate (belt to the middleware's braces)
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

    # Save user info if provided. The key may arrive in the body config or the
    # x-gemini-key header (the middleware only reads the header) — accept both,
    # but only when the BYOK gate is on; otherwise a saved free-tier key would
    # hijack generation away from the working project backend.
    from src.config import REQUIRE_USER_KEY
    user_api_key = (request.config.gemini_api_key or header_key) if REQUIRE_USER_KEY else None
    if request.config.email:
        _save_user_info(book_id, request.config.email, user_api_key)

    background_tasks.add_task(_run_preprocess, book_id, dest, gemini_api_key=user_api_key)

    return {"book_id": book_id, "status": "preprocessing"}


@router.post("/api/generate/upload")
async def start_generation_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    config: str = Form(default="{}"),
    header_key: str | None = Depends(_require_user_key),  # BYOK 403 gate (belt to the middleware's braces)
) -> dict[str, Any]:
    """Start preprocess from file upload. Returns book_id for editor redirect."""
    # PDF/EPUB support was removed — the extraction module only parses text.
    if not (file.filename or "").lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are supported.")
    upload_dir = GENERATED_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / Path(file.filename or "upload.txt").name
    contents = await file.read()
    dest.write_bytes(contents)

    # Use filename as book_id (fast, no parsing needed)
    import re as _re
    stem = Path(file.filename or "upload").stem
    sanitized = _re.sub(r'[^\w\s\u4e00-\u9fff-]', '', stem)
    book_id = _re.sub(r'\s+', '_', sanitized.strip()).lower()[:60] or "untitled"

    # Extract API key from config form field, falling back to the header.
    # Same BYOK gating as /api/generate above.
    from src.config import REQUIRE_USER_KEY
    parsed_config = json.loads(config) if config else {}
    user_api_key = (parsed_config.get("gemini_api_key") or header_key) if REQUIRE_USER_KEY else None
    if parsed_config.get("email"):
        _save_user_info(book_id, parsed_config["email"], user_api_key)

    background_tasks.add_task(_run_preprocess, book_id, dest, gemini_api_key=user_api_key)

    return {"book_id": book_id, "status": "preprocessing"}


@router.get("/api/books")
async def list_all_books() -> list[dict[str, Any]]:
    return await list_books()


@router.get("/api/book/{book_id}/preprocess/progress")
async def get_preprocess_progress(book_id: str) -> dict[str, Any]:
    """Check preprocess progress by examining which files exist."""
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    if not preprocess_dir.exists():
        return {"status": "not_started", "progress": 0, "step": "Waiting to start...", "steps_done": []}

    # Check for error marker
    error_file = preprocess_dir / "error.json"
    if error_file.exists():
        error_data = json.loads(error_file.read_text())
        return {"status": "error", "progress": 0, "step": "Preprocess failed", "error": error_data.get("error", "Unknown error"), "steps_done": []}

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

    # Current step label + agent mapping
    step_info = {
        0: {"step": "Extracting text and chapters...", "agent": "analyzer"},
        1: {"step": "Identifying characters with AI...", "agent": "analyzer"},
        2: {"step": "Building alias map...", "agent": "analyzer"},
        3: {"step": "Replacing aliases in text...", "agent": "analyzer"},
        4: {"step": "Segmenting into scenes...", "agent": "analyzer"},
        5: {"step": f"Annotating scenes ({annotated_chapters}/{total_chapters} chapters)...", "agent": "analyzer"},
    }
    current = len([s for s in steps_done if s != "annotate_complete"])
    if current >= 5 and "annotate_complete" not in steps_done:
        info = step_info.get(5, {"step": "Annotating...", "agent": "analyzer"})
    else:
        info = step_info.get(current, {"step": "Processing...", "agent": "analyzer"})

    return {
        "status": "complete" if progress >= 100 else "processing",
        "progress": round(progress),
        "step": info["step"],
        "agent": info["agent"],
        "steps_done": steps_done,
        "annotated_chapters": annotated_chapters,
        "total_chapters": total_chapters,
    }


@router.get("/api/books/preprocessed")
async def list_preprocessed_books() -> list[dict[str, Any]]:
    """List all books that have preprocess data (from MongoDB, fallback to disk)."""
    from src.core.db import list_preprocess_books
    from src.routes.helpers import _load_json

    # Try MongoDB first
    mongo_books = list_preprocess_books()
    if mongo_books:
        books = []
        for b in mongo_books:
            book_id = b["book_id"]
            llm_chars = _load_json(book_id, "llm_characters.json")
            num_characters = len(llm_chars.get("characters", [])) if llm_chars else 0
            chapters_dir = GENERATED_DIR / book_id / "chapters"
            generated_chapters = 0
            total_pages = 0
            if chapters_dir.exists():
                for ch_dir in chapters_dir.iterdir():
                    if ch_dir.is_dir() and ch_dir.name.startswith("ch"):
                        generated_chapters += 1
                        pages_dir = ch_dir / "pages"
                        if pages_dir.exists():
                            total_pages += len(list(pages_dir.glob("page_*.*")))
            books.append({
                "book_id": book_id,
                "title": b.get("title", book_id),
                "num_chapters": b.get("num_chapters", 0),
                "num_characters": num_characters,
                "generated_chapters": generated_chapters,
                "total_pages": total_pages,
            })
        return books

    # Fallback to disk scan
    books = []
    if not GENERATED_DIR.exists():
        return books
    for d in sorted(GENERATED_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "preprocess" / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
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
