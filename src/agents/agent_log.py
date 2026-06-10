"""Agent activity log for tracking multi-agent collaboration.

Writes timestamped log entries to a JSON file per chapter generation session.
Frontend polls these logs to display agent pipeline status and thinking process.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.config import GENERATED_DIR


def _log_path(book_id: str, chapter_idx: int) -> Path:
    p = GENERATED_DIR / book_id / "chapters" / f"ch{chapter_idx:02d}" / "agent_log.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def clear_log(book_id: str, chapter_idx: int) -> None:
    """Clear logs at the start of a new generation session."""
    path = _log_path(book_id, chapter_idx)
    path.write_text("[]")


def log_event(
    book_id: str,
    chapter_idx: int,
    agent: str,
    action: str,
    detail: str = "",
    result: str = "",
    status: str = "running",  # running | done | warn | error
) -> None:
    """Append an event to the agent log."""
    path = _log_path(book_id, chapter_idx)
    entries = []
    if path.exists():
        try:
            entries = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            entries = []

    entries.append({
        "ts": time.time(),
        "agent": agent,
        "action": action,
        "detail": detail,
        "result": result,
        "status": status,
    })
    path.write_text(json.dumps(entries, ensure_ascii=False))


def get_log(book_id: str, chapter_idx: int) -> list[dict]:
    """Read all log entries for a chapter."""
    path = _log_path(book_id, chapter_idx)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
