"""Pydantic models for API requests, responses, and internal data structures."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class BookInput(BaseModel):
    """Source material for picture-book generation."""

    source_text: Optional[str] = Field(
        default=None,
        description="Raw text content to adapt into a picture book.",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Path to a .txt / .pdf / .epub file on the server.",
    )


class GenerationConfig(BaseModel):
    """Configurable knobs for the generation pipeline."""

    age_group: str = Field(default="4-6", description="Target age range (e.g. '2-4', '4-6', '6-8').")
    num_pages: int = Field(default=10, ge=1, le=40, description="Desired number of pages.")
    template: str = Field(default="classic", description="Story template key (classic / journey / simple).")
    style: Optional[str] = Field(default=None, description="Optional illustration style override.")
    selected_chapters: Optional[list[int]] = Field(
        default=None,
        description="Indices of chapters to include (None = all).",
    )
    education_goal: Optional[str] = Field(
        default=None,
        description="Optional educational objective for the book.",
    )
    language: str = Field(
        default="en",
        description="Output language for the picture book text ('en', 'zh', etc.).",
    )


# ---------------------------------------------------------------------------
# Internal / response data models
# ---------------------------------------------------------------------------

class PageData(BaseModel):
    """A single page of the picture book."""

    page_number: int
    text: str = ""
    illustration_path: Optional[str] = None
    illustration_prompt: Optional[str] = None
    layout: str = "full"


class BookAnalysis(BaseModel):
    """Result of the NLP analysis stage."""

    segments: list[dict[str, Any]] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    sentiment: dict[str, Any] = Field(default_factory=dict)
    visual_scores: list[dict[str, Any]] = Field(default_factory=list)
    complexity: dict[str, Any] = Field(default_factory=dict)
    key_events: list[str] = Field(default_factory=list)
    character_profiles: list[dict[str, Any]] = Field(default_factory=list)


class StatusEnum(str, Enum):
    """Lifecycle states for a generation job."""

    QUEUED = "queued"
    ANALYZING = "analyzing"
    GENERATING_TEXT = "generating_text"
    GENERATING_IMAGES = "generating_images"
    QA_CHECK = "qa_check"
    COMPLETE = "complete"
    FAILED = "failed"


class GenerationStatus(BaseModel):
    """Real-time status of a generation job."""

    book_id: str
    status: StatusEnum = StatusEnum.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    current_step: str = ""
    error: Optional[str] = None


class PictureBook(BaseModel):
    """The complete picture-book output."""

    book_id: str
    title: str = ""
    pages: list[PageData] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    config: GenerationConfig = Field(default_factory=GenerationConfig)
    qa_results: dict[str, Any] = Field(default_factory=dict)
