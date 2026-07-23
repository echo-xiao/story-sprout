"""Book management endpoints."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from src.config import GENERATED_DIR
from src.core.models import GenerationConfig
from src.routes.helpers import _load_json, _require_user_key, book_owner_email, write_json_atomic
from src.core.pipeline import delete_book

logger = logging.getLogger(__name__)

router = APIRouter()

# Books with a preprocess subprocess in flight. Two kickoffs for the same
# book_id (double-click, or two users submitting the same title) used to spawn
# two subprocesses trampling the same preprocess/ directory. Single-instance
# scope, same as generation.py's _active_generations.
_active_preprocesses: set[str] = set()


# ---------------------------------------------------------------------------
# run_status.json lifecycle helpers
#
# Preprocess status used to be inferred purely from file existence, which broke
# in two ways: a re-POST saw the previous run's analysis.json and reported
# instant "complete" while the new subprocess was still running, and a server
# death mid-run left the book "processing" forever. run_status.json is the
# authoritative per-run record: written atomically (tmp + os.replace) so the
# polling endpoint never sees a torn file.
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_status_path(book_id: str) -> Path:
    return GENERATED_DIR / book_id / "preprocess" / "run_status.json"


def _write_run_status(book_id: str, payload: dict[str, Any]) -> None:
    try:
        write_json_atomic(_run_status_path(book_id), payload)
    except OSError:
        logger.warning("Could not write run_status.json for %s", book_id)


def _read_json_guarded(path: Path) -> Any:
    """Read+parse JSON; None on any failure (missing/torn/corrupt file)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _pid_alive(pid: Any) -> bool:
    """True iff `pid` is an existing process (signal 0 probe)."""
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False


def _preprocess_running(book_id: str) -> bool:
    """True when a preprocess run is in flight for this book — either claimed
    in-memory or recorded by a run_status.json whose pid is alive (covers
    subprocesses orphaned by a server restart)."""
    if book_id in _active_preprocesses:
        return True
    rs = _read_json_guarded(_run_status_path(book_id))
    if isinstance(rs, dict) and rs.get("status") == "running":
        pid = rs.get("pid")
        if pid is not None and _pid_alive(pid):
            return True
    return False


def _compute_book_id(source_text: str) -> str:
    """Slug of the first line (human readable) + a content-hash suffix.

    The slug alone collided: different books whose first line matched (e.g.
    raw Gutenberg headers all start identically) silently overwrote each
    other. The md5 suffix makes the id a function of the FULL text — the same
    text re-uploaded maps to the same id (idempotent retry), different text
    always gets a distinct id.
    """
    first_line = source_text.strip().split("\n")[0].strip()[:100]
    sanitized = re.sub(r'[^\w\s一-鿿-]', '', first_line)
    slug = re.sub(r'\s+', '_', sanitized.strip()).lower()[:52] or "untitled"
    digest = hashlib.md5(source_text.encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{digest}"


class FetchUrlRequest(BaseModel):
    url: str


def _resolve_safe_ip(host: str) -> str:
    """Resolve `host` and require EVERY returned address to be public.

    Returns one validated IP. The caller connects to exactly this IP, so the
    address can't be swapped for an internal one between validation and the
    actual request (the DNS-rebinding TOCTOU the old code documented but left
    open: it validated the name, then let httpx resolve it a second time)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Could not resolve URL host")
    ips: list[str] = []
    v4: list[str] = []
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise HTTPException(status_code=400, detail="URL resolves to a disallowed address")
        ips.append(addr)
        if info[0] == socket.AF_INET:
            v4.append(addr)
    if not ips:
        raise HTTPException(status_code=400, detail="Could not resolve URL host")
    # Prefer IPv4: Cloud Run has no outbound IPv6, and getaddrinfo there lists
    # AAAA records first — connecting to one hung the full httpx timeout and
    # the Next.js proxy cut the request at 30s (fetch-url 500'd for ANY host
    # that publishes an AAAA, including gutenberg.org). Validation above still
    # covers every returned address, v6 included.
    return (v4 or ips)[0]


# A whole novel is a few MB of plain text; 10 MB is generous. Reading the
# response un-streamed put the entire body in memory — a URL serving gigabytes
# (or a slow infinite stream) OOM'd the single Cloud Run instance in one request.
MAX_FETCH_BYTES = 10 * 1024 * 1024


async def _fetch_url_text(url: str, max_redirects: int = 6,
                          transport: httpx.AsyncBaseTransport | None = None) -> str:
    """GET text from a public URL, following redirects manually and pinning
    each hop to a validated IP (Host header + TLS SNI keep the real hostname).
    Streams the body and aborts past MAX_FETCH_BYTES."""
    # 20s, NOT 30s: the in-container Next.js proxy cuts upstream requests at
    # ~30s — with an equal httpx timeout our clean 400 lost the race and the
    # client saw a bare proxy 500 instead.
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, transport=transport) as client:
        current = url
        for _ in range(max_redirects):
            p = urlparse(current)
            if p.scheme not in ("http", "https") or not p.hostname:
                raise HTTPException(status_code=400, detail="Only http/https URLs are allowed")
            ip = _resolve_safe_ip(p.hostname)
            # Connect to the validated IP; keep the hostname for Host + cert check.
            pinned = httpx.URL(current).copy_with(host=ip)
            host_header = p.hostname if p.port is None else f"{p.hostname}:{p.port}"
            extensions = {"sni_hostname": p.hostname} if p.scheme == "https" else {}
            req = client.build_request("GET", pinned, headers={"Host": host_header}, extensions=extensions)
            resp = await client.send(req, stream=True)
            try:
                if resp.is_redirect and resp.headers.get("location"):
                    current = str(httpx.URL(current).join(resp.headers["location"]))
                    continue
                resp.raise_for_status()
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_FETCH_BYTES:
                    raise HTTPException(status_code=413, detail="File too large (max 10 MB of text)")
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_FETCH_BYTES:
                        raise HTTPException(status_code=413, detail="File too large (max 10 MB of text)")
                return bytes(body).decode(resp.charset_encoding or "utf-8", errors="replace")
            finally:
                await resp.aclose()
    raise HTTPException(status_code=400, detail="Too many redirects")


@router.post("/api/fetch-url")
async def fetch_book_from_url(req: FetchUrlRequest) -> dict[str, Any]:
    """Fetch plain text from a URL (e.g. Project Gutenberg).

    SSRF-hardened: redirects are followed manually and every hop is pinned to
    a validated public IP (see _fetch_url_text), closing the DNS-rebinding hole.
    """
    try:
        text = await _fetch_url_text(req.url)

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
    import asyncio
    import sys
    env = os.environ.copy()
    if gemini_api_key:
        # Route preprocessing to the USER's key (their billing).
        env["GEMINI_API_KEY"] = gemini_api_key
    # Clear any stale error.json from a previous attempt, else the frontend shows
    # this fresh run as already-failed. (The POST handler clears it too, before
    # this task is even scheduled; kept here for direct callers.)
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    preprocess_dir.mkdir(parents=True, exist_ok=True)
    err_file = preprocess_dir / "error.json"
    if err_file.exists():
        try:
            err_file.unlink()
        except OSError:
            pass
    started_at = _utc_now_iso()
    _write_run_status(book_id, {"status": "running", "pid": None, "started_at": started_at})
    timeout_s = int(os.getenv("PREPROCESS_TIMEOUT_SECONDS", "1800"))

    def _mark_failed(message: str) -> None:
        _write_run_status(book_id, {
            "status": "failed", "pid": None, "started_at": started_at,
            "finished_at": _utc_now_iso(), "error": message,
        })

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "scripts/preprocess_book.py", "--input", str(dest), "--book-id", book_id, "--skip-sheets",
            cwd=str(Path(__file__).parent.parent.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        # Record the real pid so a restarted server can still detect this run
        # (409 on duplicate POST) and the progress poll can detect its death.
        _write_run_status(book_id, {"status": "running", "pid": proc.pid, "started_at": started_at})
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.error("Preprocess timed out for %s", book_id)
            # Write an error so the frontend stops showing "processing" forever.
            err_file.write_text(json.dumps({"error": f"Preprocess timed out after {timeout_s}s.", "returncode": -1}))
            _mark_failed(f"Preprocess timed out after {timeout_s}s.")
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
            _mark_failed(stderr_text[-1000:] or "Unknown error")
        else:
            _write_run_status(book_id, {
                "status": "complete", "pid": proc.pid, "started_at": started_at,
                "finished_at": _utc_now_iso(),
            })
    except Exception as e:
        logger.exception("Preprocess crashed for %s", book_id)
        # Without a marker the progress endpoint reports "processing" forever
        # and both loading screens spin until the user gives up.
        try:
            err_file.write_text(json.dumps({
                "error": f"Preprocess crashed: {str(e)[:500]}",
                "returncode": -1,
            }))
        except OSError:
            pass
        _mark_failed(f"Preprocess crashed: {str(e)[:500]}")
    finally:
        _active_preprocesses.discard(book_id)


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


class FeedbackRequest(BaseModel):
    message: str
    email: str | None = None
    context: str | None = None


def _resend_owner_email(to_addr: str, subject: str, body: str, reply_to: str | None) -> bool:
    """Send via Resend's HTTP API — just an API key, no Gmail 2FA / app password.
    Set RESEND_API_KEY + FEEDBACK_EMAIL_TO; from-address defaults to Resend's
    onboarding sender (works to your own verified inbox without a domain)."""
    key = os.getenv("RESEND_API_KEY", "").strip()
    if not (key and to_addr):
        return False
    payload: dict[str, Any] = {
        "from": os.getenv("RESEND_FROM", "Story Sprout <onboarding@resend.dev>"),
        "to": [to_addr],
        "subject": subject,
        "text": body,
    }
    if reply_to:
        payload["reply_to"] = reply_to
    try:
        r = httpx.post("https://api.resend.com/emails",
                       headers={"Authorization": f"Bearer {key}"},
                       json=payload, timeout=15)
        if r.status_code < 300:
            return True
        logger.warning("Resend email failed %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("Resend email error: %s", e)
    return False


def _smtp_owner_email(to_addr: str, subject: str, body: str, reply_to: str | None) -> bool:
    """Send via SMTP (e.g. Gmail App Password). Set SMTP_USER + SMTP_PASSWORD,
    optionally SMTP_HOST/SMTP_PORT (default smtp.gmail.com:587)."""
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    if not (user and password and to_addr):
        return False
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    try:
        import smtplib
        from email.message import EmailMessage
        m = EmailMessage()
        m["Subject"] = subject
        m["From"] = user
        m["To"] = to_addr
        if reply_to:
            m["Reply-To"] = reply_to
        m.set_content(body)
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(m)
        return True
    except Exception as e:
        logger.warning("SMTP email failed: %s", e)
        return False


def _send_owner_email(subject: str, body: str, reply_to: str | None = None) -> bool:
    """Best-effort email to the owner's inbox. Returns True if sent, never raises.

    Tries Resend first (API key only — the easy path), then SMTP (Gmail App
    Password). Both unset = no-op. Recipient: FEEDBACK_EMAIL_TO, or SMTP_USER.
    """
    to_addr = os.getenv("FEEDBACK_EMAIL_TO", "").strip() or os.getenv("SMTP_USER", "").strip()
    if not to_addr:
        return False
    return (_resend_owner_email(to_addr, subject, body, reply_to)
            or _smtp_owner_email(to_addr, subject, body, reply_to))


def _email_feedback_to_owner(msg: str, email: str | None, context: str | None) -> None:
    """Email a feedback note to the owner, Reply-To the user. No-op until SMTP
    is configured; the note is already persisted regardless."""
    _send_owner_email(
        "📖 New Story Sprout feedback",
        f"Message:\n{msg}\n\nFrom: {email or '(no email given)'}\nPage: {context or '(unknown)'}\n",
        reply_to=email,
    )


def _format_usage_digest(data: dict, hours: int) -> str:
    """Plain-text usage digest for the owner email."""
    if not data.get("available"):
        return "MongoDB was unavailable — no usage data for this window."
    books = data["new_books"]
    fb = data["feedback"]
    lines = [f"Story Sprout — last {hours}h", ""]
    lines.append(f"New books: {len(books)}")
    for b in books[:50]:
        lines.append(f"  • {b.get('title', '(untitled)')}  [{b.get('book_id', '?')}]  {b.get('created_at', '')[:19]}")
    lines.append("")
    lines.append(f"Feedback: {len(fb)}")
    for f in fb[:50]:
        who = f.get("email") or "(anon)"
        lines.append(f"  • {who}: {(f.get('message') or '')[:200]}")
    lines.append("")
    lines.append(f"Total books all-time: {data['total_books']}")
    return "\n".join(lines)


@router.get("/api/book/{book_id}/pdf")
async def download_book_pdf(book_id: str, inline: bool = False) -> FileResponse:
    """Build the book PDF on demand from chapter_data + special pages — the
    single source of truth the editors already maintain — so it can never go
    stale. No pre-generated book.pdf to keep in sync across N edit endpoints;
    the PDF is derived per request and streamed back.

    inline=1 serves it with `Content-Disposition: inline` so the reader can
    embed the whole book in an <iframe> (browser-native PDF viewer) instead of
    triggering a download — the "show as PDF" reading mode. Default attachment
    keeps the Download button's save-to-file behaviour.
    """
    import re as _re
    import tempfile

    from src.core import storage, store

    book_dir = GENERATED_DIR / book_id
    chapters_root = book_dir / "chapters"

    # GCS-first: enumerate chapter indices from durable storage so a cold
    # serverless instance (empty /tmp) can still build the PDF.
    all_chapters = []
    try:
        gcs_keys = storage.list_keys(f"{book_id}/chapters/")
        ch_idxs = sorted({
            int(_re.search(r"/chapters/ch(\d+)/", k).group(1))
            for k in gcs_keys
            if k.endswith("/chapter_data.json") and _re.search(r"/chapters/ch(\d+)/", k)
        })
        for ci in ch_idxs:
            data = store.get_json(f"{book_id}/chapters/ch{ci:02d}/chapter_data.json")
            if isinstance(data, dict) and data.get("pages"):
                all_chapters.append(data)
    except Exception as e:
        logger.warning("GCS chapter_data enumeration failed for %s: %s", book_id, e)

    # Local fallback: dev mode or when GCS has no chapter_data keys yet.
    if not all_chapters and chapters_root.exists():
        for ch_dir in sorted(chapters_root.glob("ch*")):
            data = _read_json_guarded(ch_dir / "chapter_data.json")
            if isinstance(data, dict) and data.get("pages"):
                all_chapters.append(data)

    if not all_chapters:
        raise HTTPException(status_code=404, detail="No generated pages yet — generate a chapter first.")

    all_chapters.sort(key=lambda c: c.get("chapter_idx", 0))
    combined: list[dict] = []
    for ch in all_chapters:
        ch_num = ch.get("chapter_idx", 0) + 1
        for p in ch.get("pages", []):
            p["_chapter_num"] = ch_num
            combined.append(p)

    # Step 4: materialize each page's image from GCS to local disk so reportlab
    # can read it as a local path (export_pdf checks os.path.exists(image_path)).
    for p in combined:
        image_path = p.get("image_path", "")
        if not image_path:
            continue
        try:
            key = str(Path(image_path).relative_to(GENERATED_DIR))
            storage.localize(key)
        except Exception:
            # Not under GENERATED_DIR, or localize failed — export_pdf handles
            # a missing image by rendering a text-only page; do not crash.
            pass

    title = (_load_json(book_id, "meta.json") or {}).get("title", book_id)
    special_dir = str(book_dir / "special")

    from src.renderer.pdf_export import export_pdf
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        await run_in_threadpool(export_pdf, combined, title, tmp_path, special_dir=special_dir)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        logger.exception("On-demand PDF build failed for %s", book_id)
        raise HTTPException(status_code=500, detail="PDF build failed.")

    safe = _re.sub(r"[^\w.-]", "_", title)[:60] or "book"
    return FileResponse(
        tmp_path, media_type="application/pdf", filename=f"{safe}.pdf",
        content_disposition_type="inline" if inline else "attachment",
        background=BackgroundTask(lambda: os.path.exists(tmp_path) and os.unlink(tmp_path)),
    )


@router.post("/api/generate")
async def start_generation(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
    header_key: str | None = Depends(_require_user_key),  # BYOK 403 gate (belt to the middleware's braces)
) -> dict[str, Any]:
    """Start preprocess from text. Returns book_id for editor redirect."""
    if not request.source_text.strip():
        raise HTTPException(status_code=400, detail="source_text cannot be empty.")

    # Save text to file
    upload_dir = GENERATED_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"text_{uuid.uuid4().hex[:8]}.txt"
    dest.write_text(request.source_text, encoding="utf-8")

    # Quick book_id: first-line slug + content hash (no heavy parsing). See
    # _compute_book_id \u2014 the hash suffix keeps different books with identical
    # first lines from overwriting each other.
    book_id = _compute_book_id(request.source_text)

    # Duplicate-run guard: in-memory claim first, then run_status.json \u2014 the
    # latter catches a subprocess orphaned by a server restart (the in-memory
    # set is empty after a restart but the orphan is still writing files).
    if _preprocess_running(book_id):
        raise HTTPException(
            status_code=409,
            detail=f"'{book_id}' is already preprocessing \u2014 wait for it to finish.",
        )

    # The key travels in the x-gemini-key header only (the BYOK middleware and
    # gate read nothing else, so a body-only key could never reach this point).
    # Honor it only when the gate is on; otherwise a saved free-tier key would
    # hijack generation away from the working project backend.
    from src.config import REQUIRE_USER_KEY
    user_api_key = header_key if REQUIRE_USER_KEY else None
    if request.config.email:
        _save_user_info(book_id, request.config.email, user_api_key)

    # Already fully preprocessed (same text → same content-hash id)? Reuse it
    # instead of re-running the whole pipeline — saves the LLM cost and keeps any
    # editor changes. The owner was just recorded above, so it shows in their
    # library. (A failed/partial run still re-runs.)
    rs = _read_json_guarded(_run_status_path(book_id))
    analysis_exists = (GENERATED_DIR / book_id / "preprocess" / "analysis.json").exists()
    if analysis_exists and not _preprocess_running(book_id) and (rs is None or rs.get("status") == "complete"):
        return {"book_id": book_id, "status": "exists"}

    # No await between the membership check above and this claim, so two
    # concurrent kickoffs can't both pass; _run_preprocess releases in finally.
    _active_preprocesses.add(book_id)
    # Stale per-run state from a previous attempt must not poison this run's
    # progress reporting: clear error.json and stamp run_status BEFORE the task
    # is scheduled, so the very first progress poll already sees "running"
    # (content artifacts like analysis.json are user-editable and stay put \u2014
    # run_status alone now prevents the false instant-complete on re-POST).
    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    preprocess_dir.mkdir(parents=True, exist_ok=True)
    try:
        (preprocess_dir / "error.json").unlink(missing_ok=True)
    except OSError:
        pass
    _write_run_status(book_id, {"status": "running", "pid": None, "started_at": _utc_now_iso()})
    background_tasks.add_task(_run_preprocess, book_id, dest, gemini_api_key=user_api_key)

    return {"book_id": book_id, "status": "preprocessing"}


def _error_response(message: str) -> dict[str, Any]:
    return {"status": "error", "progress": 0, "step": "Preprocess failed",
            "error": message, "steps_done": []}


@router.get("/api/book/{book_id}/preprocess/progress")
async def get_preprocess_progress(book_id: str) -> dict[str, Any]:
    """Check preprocess progress: run_status.json is authoritative when present
    (legacy books without one keep the file-existence behavior)."""
    import asyncio

    preprocess_dir = GENERATED_DIR / book_id / "preprocess"
    if not preprocess_dir.exists():
        return {"status": "not_started", "progress": 0, "step": "Waiting to start...", "steps_done": []}

    # run_status.json first — it disambiguates "old artifacts on disk" from
    # "this run". A torn/corrupt read means a writer is mid-replace → treat as
    # still running rather than 500ing or trusting stale files.
    rs_path = preprocess_dir / "run_status.json"
    run_status: dict[str, Any] | None = None
    if rs_path.exists():
        run_status = _read_json_guarded(rs_path)
        if not isinstance(run_status, dict):
            run_status = {"status": "running", "pid": None}

    if run_status is not None and run_status.get("status") == "running":
        pid = run_status.get("pid")
        if pid is not None and not _pid_alive(pid):
            # Tiny race: the subprocess just exited and the parent hasn't
            # stamped the final status yet — re-read once before declaring death.
            await asyncio.sleep(0.2)
            run_status = _read_json_guarded(rs_path) or run_status
            if run_status.get("status") == "running":
                return _error_response("preprocess process died — re-submit the book")

    if run_status is not None and run_status.get("status") == "failed":
        return _error_response(run_status.get("error") or "Unknown error")

    # Legacy error marker (books without run_status.json, or direct CLI runs).
    error_file = preprocess_dir / "error.json"
    if run_status is None and error_file.exists():
        error_data = _read_json_guarded(error_file) or {}
        return _error_response(error_data.get("error", "Unknown error"))

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
    chs = _read_json_guarded(preprocess_dir / "chapters.json")
    if isinstance(chs, list):
        total_chapters = len(chs)
    else:
        cs = _read_json_guarded(preprocess_dir / "chapter_segments.json")
        if isinstance(cs, (list, dict)):
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

    status = "complete" if progress >= 100 else "processing"
    if run_status is not None and run_status.get("status") == "running":
        # A live run is NEVER "complete", even if a previous run's analysis.json
        # is still on disk (re-POST used to instantly report complete from it).
        if status == "complete":
            steps_done = [s for s in steps_done if s != "annotate_complete"]
        status = "processing"
        progress = min(progress, 99)
    elif run_status is not None and run_status.get("status") == "complete":
        status = "complete"
        progress = 100

    return {
        "status": status,
        "progress": round(progress),
        "step": info["step"],
        "agent": info["agent"],
        "steps_done": steps_done,
        "annotated_chapters": annotated_chapters,
        "total_chapters": total_chapters,
    }


def _page_counts(book_id: str) -> tuple[int, int]:
    """(generated_chapters, total_pages) from disk. A chapter only counts as
    generated when it actually contains at least one page image — a bare chXX
    dir (e.g. from a crashed run) used to count."""
    from src.core import storage
    # Count durable page images from GCS, not this instance's ephemeral /tmp
    # (empty on a cold serverless instance → the Library showed 0 pages).
    by_chapter: dict[str, int] = {}
    for key in storage.list_keys(f"{book_id}/chapters/"):
        parts = key.split("/")
        # {book_id}/chapters/chXX/pages/page_NNN.ext
        if len(parts) >= 5 and parts[-2] == "pages" \
                and parts[-1].startswith("page_") and parts[-1].endswith((".png", ".jpg")):
            by_chapter[parts[-3]] = by_chapter.get(parts[-3], 0) + 1
    generated_chapters = sum(1 for n in by_chapter.values() if n > 0)
    total_pages = sum(by_chapter.values())
    return generated_chapters, total_pages


def _book_status(book_id: str, generated_chapters: int) -> str:
    """Derive a coarse lifecycle status from run_status/error/analysis."""
    pre = GENERATED_DIR / book_id / "preprocess"
    rs = _read_json_guarded(pre / "run_status.json")
    rs_status = rs.get("status") if isinstance(rs, dict) else None
    if rs_status == "running":
        pid = rs.get("pid")
        if pid is None or _pid_alive(pid):
            return "processing"
        return "failed"  # run_status says running but the process is gone
    if rs_status == "failed" or (pre / "error.json").exists():
        return "failed"
    if (pre / "analysis.json").exists():
        return "generated" if generated_chapters > 0 else "ready"
    # Legacy book mid-flight (no run_status, no analysis yet)
    return "processing"


def _sample_book_ids() -> set[str]:
    """Book ids everyone can see (public samples). Configurable via env."""
    return {s.strip() for s in os.getenv("SAMPLE_BOOK_IDS", "the_great_gatsby").split(",") if s.strip()}


@router.get("/api/books/preprocessed")
async def list_preprocessed_books(email: str | None = None) -> list[dict[str, Any]]:
    """Books this viewer may see: their OWN (matched by the email they used to
    create them) plus the public samples. Soft isolation — email is identity,
    not auth — so a visitor without an email sees only the samples.

    UNION of MongoDB and the disk scan, deduped by book_id (Mongo record wins
    when both exist).
    """
    from src.core.store import list_books
    from src.routes.helpers import _load_json

    samples = _sample_book_ids()
    books_by_id: dict[str, dict[str, Any]] = {}

    try:
        store_books = list_books()
    except Exception as e:
        logger.warning("Store library listing failed (%s); serving disk scan only", e)
        store_books = []
    for b in store_books:
        # Per-book guard: one bad record must not kill the whole listing.
        try:
            book_id = b["book_id"]
            llm_chars = _load_json(book_id, "llm_characters.json")
            num_characters = len(llm_chars.get("characters", [])) if llm_chars else 0
            generated_chapters, total_pages = _page_counts(book_id)
            books_by_id[book_id] = {
                "book_id": book_id,
                "title": b.get("title", book_id),
                "num_chapters": b.get("num_chapters", 0),
                "num_characters": num_characters,
                "generated_chapters": generated_chapters,
                "total_pages": total_pages,
                "status": _book_status(book_id, generated_chapters),
            }
        except Exception as e:
            logger.warning("Skipping bad store library record %r: %s", b, e)

    # Disk scan — adds books Mongo doesn't know about (doc never created, or
    # Mongo down). Per-book guard so one corrupt meta.json doesn't kill it.
    if GENERATED_DIR.exists():
        for d in sorted(GENERATED_DIR.iterdir()):
            try:
                if not d.is_dir() or d.name in books_by_id:
                    continue
                pre = d / "preprocess"
                meta_path = pre / "meta.json"
                # Books still preprocessing (or failed before Layer 1) have no
                # meta.json yet but DO have run-state — list them too, with
                # status carrying the truth.
                if not meta_path.exists() and not (pre / "run_status.json").exists() \
                        and not (pre / "error.json").exists():
                    continue
                meta = _read_json_guarded(meta_path)
                if not isinstance(meta, dict):
                    meta = {}
                generated_chapters, total_pages = _page_counts(d.name)
                chars_data = _read_json_guarded(pre / "llm_characters.json")
                num_characters = len(chars_data.get("characters", [])) if isinstance(chars_data, dict) else 0
                books_by_id[d.name] = {
                    "book_id": d.name,
                    "title": meta.get("title", d.name),
                    "num_chapters": meta.get("num_chapters", 0),
                    "num_characters": num_characters,
                    "generated_chapters": generated_chapters,
                    "total_pages": total_pages,
                    "status": _book_status(d.name, generated_chapters),
                }
            except Exception as e:
                logger.warning("Skipping unreadable book dir %s: %s", d, e)

    # Public product: show every book (no per-owner isolation).
    out = []
    for bid, rec in books_by_id.items():
        rec["is_sample"] = bid in samples
        out.append(rec)
    return out


@router.delete("/api/book/{book_id}")
async def delete_book_endpoint(
    book_id: str,
    user_key: str | None = Depends(_require_user_key),  # BYOK 403 gate when enforced
) -> dict[str, str]:
    # rmtree below — a book_id like ".." would delete the whole data dir.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", book_id) or ".." in book_id:
        raise HTTPException(status_code=400, detail="Invalid book id.")
    # Deleting under a live preprocess subprocess would race its writes (it
    # would happily recreate half the tree after the rmtree).
    if _preprocess_running(book_id):
        raise HTTPException(
            status_code=409,
            detail=f"'{book_id}' is currently preprocessing — wait for it to finish before deleting.",
        )
    book_dir = GENERATED_DIR / book_id
    try:
        deleted = await delete_book(book_id)
    except Exception as e:
        # Mongo outage must not block deletion — the disk removal below is the
        # part the user actually sees; orphaned docs get cleaned on a retry.
        logger.warning("Mongo delete failed for %s (%s); proceeding with disk removal", book_id, e)
        deleted = book_dir.exists()
    if not deleted:
        raise HTTPException(status_code=404, detail="Book not found.")

    if book_dir.exists():
        import shutil
        shutil.rmtree(book_dir, ignore_errors=True)

    return {"status": "deleted", "book_id": book_id}
