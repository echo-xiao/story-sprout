"""Centralized Gemini client factory.

Every Gemini call in the project (text + image + QA checks) builds its client
through `make_genai_client()`, so the backend is switched in exactly one place.

Runs on the Google AI Studio Developer API (api_key path only).
gemini-3-pro-image is available on this path; no Vertex / GCP identity needed.

BYOK: a per-request user key (set via `set_user_api_key`) ALWAYS takes
precedence, so a visitor who supplies their own Gemini key bills their own quota
instead of the project's.
"""

from __future__ import annotations

import contextvars
import logging
import random
import re
import time

from src.config import (
    GEMINI_API_KEY,
)

logger = logging.getLogger(__name__)

# Per-request user-supplied API key (BYOK). Default None -> fall back to the
# configured project backend.
_user_api_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_api_key", default=None
)


def set_user_api_key(key: str | None):
    """Set the per-request user key for this context. Returns a reset token."""
    return _user_api_key.set(key or None)


def reset_user_api_key(token) -> None:
    try:
        _user_api_key.reset(token)
    except (ValueError, LookupError):
        pass


def get_user_api_key() -> str | None:
    return _user_api_key.get()


# ── Generation-failure capture ─────────────────────────────────────────────
# The image generators retry internally and swallow exceptions (a failed page
# must not kill a whole chapter run), which made failures invisible: to the
# frontend's "file appeared" poll, failure and success look identical. Each
# regen task opens a box (a plain list — mutations made inside run_in_threadpool
# are visible because the OBJECT is shared even though the context is copied);
# generators note their errors into it; the task classifies them into a
# user-facing message when no file was produced.
_gen_error_box: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "gen_error_box", default=None
)


def set_gen_error_box(box: list):
    """Install an error box for this task's context. Returns a reset token."""
    return _gen_error_box.set(box)


def reset_gen_error_box(token) -> None:
    try:
        _gen_error_box.reset(token)
    except (ValueError, LookupError):
        pass


def note_gen_failure(err: object) -> None:
    """Record a generation error into the active box (no-op when none is open)."""
    box = _gen_error_box.get()
    if box is not None:
        box.append(str(err))


def friendly_gen_error(errors: list[str]) -> str | None:
    """Turn raw Gemini errors into a message the user can act on.

    The single most common failure on a public BYOK deployment: a FREE-tier
    key, which has ZERO quota for the image model — tell the user they need a
    billing-enabled (paid) key instead of a generic 'generation failed'.
    """
    joined = " ".join(errors)
    if "free_tier" in joined or "FreeTier" in joined:
        return (
            "Your Gemini API key is on the FREE tier, which has ZERO quota for "
            "the image model — nothing can be drawn with it. Use a key from a "
            "Google Cloud project with BILLING ENABLED (paid tier), then try again."
        )
    if "RESOURCE_EXHAUSTED" in joined or "429" in joined:
        return "Gemini rate limit hit (429). Wait a minute and try again."
    if errors:
        return errors[-1][:300]
    return None


_TRANSIENT_MARKERS = ("rate limit", "429", "resource_exhausted", "resource exhausted", "503", "unavailable")


def _is_free_tier_zero_quota(err_str: str) -> bool:
    low = err_str.lower()
    return "free_tier" in low or "freetier" in low


def _retry_after_seconds(err_str: str) -> float | None:
    """Honour the server's stated wait ('Please retry in 48.8s' / retryDelay '48s')."""
    m = (re.search(r"retry in ([\d.]+)\s*s", err_str, re.I)
         or re.search(r"retrydelay['\"]?\s*[:=]\s*['\"]?(\d+)\s*s", err_str, re.I))
    try:
        return float(m.group(1)) if m else None
    except (TypeError, ValueError):
        return None


def call_gemini_with_backoff(fn, *, max_retries: int = 3, base: float = 5.0, label: str = ""):
    """The SINGLE retry policy for every Gemini call. `fn()` performs one attempt.

    - free-tier / zero-quota 429 → fail immediately (it can NEVER succeed, so
      don't burn 15s of sleeps before reporting it — the killer case on a
      public BYOK deployment).
    - transient 429 / 503 / RESOURCE_EXHAUSTED → honour the server's Retry-After
      when present, else exponential backoff with jitter.
    - anything else → raise straight through.
    """
    last: Exception | None = None
    for attempt in range(max(1, max_retries)):
        try:
            return fn()
        except Exception as e:
            last = e
            es = str(e)
            if _is_free_tier_zero_quota(es):
                raise  # never succeeds — fail fast, no sleep
            transient = any(k in es.lower() for k in _TRANSIENT_MARKERS)
            if not transient or attempt >= max_retries - 1:
                raise
            wait = _retry_after_seconds(es) or (base * (2 ** attempt) + random.uniform(0, 3))
            logger.warning("Gemini%s transient error, retry %d/%d in %.1fs",
                           f" [{label}]" if label else "", attempt + 1, max_retries, wait)
            time.sleep(wait)
    if last:
        raise last


def make_genai_client():
    # BYOK user key wins if set (inert path, kept for now); otherwise the app's
    # own AI Studio key. No Vertex — gemini-3-pro-image is on the Developer API.
    from google import genai

    user_key = _user_api_key.get()
    if user_key:
        return genai.Client(api_key=user_key)
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set.")
    return genai.Client(api_key=GEMINI_API_KEY)
