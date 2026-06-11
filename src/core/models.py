"""Pydantic models for API requests."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GenerationConfig(BaseModel):
    """Configurable knobs for the generation pipeline.

    Only email + gemini_api_key are read by the routes; the old per-book knobs
    (age/pages/template/...) were silently ignored and have been removed.
    Pydantic ignores extra client fields by default, so old frontends still work.
    """

    email: Optional[str] = Field(default=None, description="User's email address.")
    gemini_api_key: Optional[str] = Field(default=None, description="User's Gemini API key.")
