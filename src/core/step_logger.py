"""Step logger: persists every pipeline step's input/output to files and MongoDB.

Each step is saved as:
  - JSON file: data/generated/{book_id}/steps/{step_number}_{tool_name}.json
  - MongoDB: db.steps collection with {book_id, step_number, tool_name, input, output, timestamp}
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import GENERATED_DIR, MONGODB_URI, MONGODB_DB

logger = logging.getLogger(__name__)

_step_counter: dict[str, int] = {}
_mongo_client = None


def _get_step_num(book_id: str) -> int:
    """Get and increment the step counter for a book."""
    _step_counter.setdefault(book_id, 0)
    _step_counter[book_id] += 1
    return _step_counter[book_id]


def _truncate_for_json(obj: Any, max_str_len: int = 5000) -> Any:
    """Truncate large strings in a nested structure for readable JSON."""
    if isinstance(obj, str):
        if len(obj) > max_str_len:
            return obj[:max_str_len] + f"... ({len(obj)} chars total)"
        return obj
    if isinstance(obj, dict):
        return {k: _truncate_for_json(v, max_str_len) for k, v in obj.items()
                if not k.startswith("_")}
    if isinstance(obj, list):
        if len(obj) > 20:
            return [_truncate_for_json(x, max_str_len) for x in obj[:20]] + [f"... ({len(obj)} items total)"]
        return [_truncate_for_json(x, max_str_len) for x in obj]
    return obj


def log_step(
    book_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_output: dict[str, Any],
    duration_ms: int = 0,
) -> None:
    """Save a pipeline step to files and MongoDB."""
    step_num = _get_step_num(book_id)
    timestamp = datetime.now(timezone.utc).isoformat()

    step_doc = {
        "book_id": book_id,
        "step_number": step_num,
        "tool_name": tool_name,
        "timestamp": timestamp,
        "duration_ms": duration_ms,
        "input": _truncate_for_json(tool_input),
        "output": _truncate_for_json(tool_output),
        "success": "error" not in tool_output,
    }

    # 1. Save to JSON file
    _save_to_file(book_id, step_num, tool_name, step_doc)

    # 2. Save to MongoDB (best effort)
    _save_to_mongo(step_doc)


def _save_to_file(book_id: str, step_num: int, tool_name: str, doc: dict) -> None:
    """Save step to a JSON file."""
    steps_dir = GENERATED_DIR / book_id / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{step_num:02d}_{tool_name}.json"
    filepath = steps_dir / filename

    try:
        filepath.write_text(
            json.dumps(doc, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Step %d (%s) saved to %s", step_num, tool_name, filepath)
    except Exception as e:
        logger.warning("Failed to save step file: %s", e)


def _save_to_mongo(doc: dict) -> None:
    """Save step to MongoDB (best effort, reuses connection)."""
    global _mongo_client
    try:
        if _mongo_client is None:
            import pymongo
            _mongo_client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
        db = _mongo_client[MONGODB_DB]
        db.steps.insert_one(doc.copy())
    except Exception as e:
        logger.debug("MongoDB step save skipped: %s", e)
        _mongo_client = None  # Reset so next call retries connection


def get_steps(book_id: str) -> list[dict]:
    """Load all saved steps for a book from files."""
    steps_dir = GENERATED_DIR / book_id / "steps"
    if not steps_dir.exists():
        return []

    steps = []
    for filepath in sorted(steps_dir.glob("*.json")):
        try:
            doc = json.loads(filepath.read_text(encoding="utf-8"))
            steps.append(doc)
        except Exception:
            continue
    return steps
