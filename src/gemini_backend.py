"""Centralized Gemini client factory.

Every Gemini call in the project (text + image + QA checks) builds its client
through `make_genai_client()`, so the backend is switched in exactly one place.

Backends (GEMINI_BACKEND):
  - "vertex"  -> Vertex AI / "Agent Platform" (Gemini models on Agent Platform).
                 Auth comes from ADC locally or the attached service account on
                 Cloud Run; billing goes to GCP_PROJECT. This is the default and
                 the path required by the hackathon ("Gemini models on Agent
                 Platform"). Used to pre-generate the public sample books.
  - "api_key" -> Google AI Studio key endpoint (GEMINI_API_KEY).

BYOK: a per-request user key (set via `set_user_api_key`) ALWAYS takes
precedence, so a visitor who supplies their own Gemini key bills their own quota
instead of the project's. In-process requests set it through the contextvar;
subprocesses (preprocess / chapter generation) receive it via the
GEMINI_API_KEY + GEMINI_BACKEND=api_key environment variables instead.
"""

from __future__ import annotations

import contextvars
import logging

from src.config import (
    GCP_LOCATION,
    GCP_PROJECT,
    GEMINI_API_KEY,
    GEMINI_BACKEND,
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


def make_genai_client():
    """Return a configured google-genai Client for the active backend."""
    from google import genai

    user_key = _user_api_key.get()
    if user_key:
        logger.debug("Gemini backend: user-supplied API key (BYOK)")
        return genai.Client(api_key=user_key)

    if GEMINI_BACKEND == "vertex":
        if not GCP_PROJECT:
            raise ValueError(
                "GEMINI_BACKEND=vertex but no GCP project is set "
                "(set GCP_PROJECT or GOOGLE_CLOUD_PROJECT)."
            )
        logger.debug("Gemini backend: Vertex AI (project=%s, location=%s)", GCP_PROJECT, GCP_LOCATION)
        return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)

    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_BACKEND=api_key but GEMINI_API_KEY is not set.")
    logger.debug("Gemini backend: AI Studio API key")
    return genai.Client(api_key=GEMINI_API_KEY)
