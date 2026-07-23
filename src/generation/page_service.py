"""Shared single-page QA + bounded self-correction policy.

ONE place for the rule "QA-check a freshly generated page; if it scores below
threshold, regenerate it once with the QA feedback and keep whichever image
scores higher" — plus the threshold itself. Both the ADK pipeline (Artist) and
the single-page regen endpoint use this so they can't drift (the threshold was
previously hard-coded as 50 in two backends and 75 in the frontend, with 1 vs 3
retries). The frontend now only *triggers* regeneration; it no longer runs its
own retry loop.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Single source of truth for the self-correction policy.
SELF_CORRECT_THRESHOLD = 50
MAX_SELF_CORRECT_RETRIES = 1

# Character sheets use a MUCH more lenient threshold than pages: a low sheet
# score is usually a soft problem (e.g. missing back view), and force-redrawing
# risks drifting an already-established character identity across the book.
# Only truly broken sheets retry.
SHEET_SELF_CORRECT_THRESHOLD = 35


def qa_and_self_correct(
    *,
    image_path: str,
    character_sheets: list,
    expected_text: str,
    expected_characters: list,
    page_num: int,
    history_dir: Path,
    quality_path: Path,
    regenerate_fn: Callable[[str], str],
    seg_id=None,
    self_correct: bool = True,
    threshold: int = SELF_CORRECT_THRESHOLD,
) -> dict:
    """QA `image_path`; if below `threshold`, regenerate once and keep the better.

    `regenerate_fn(feedback) -> new_image_path` performs the actual regeneration
    in place (returning the new path, or "" on failure). The old image is moved
    to `history_dir` first so the generator's on-disk checkpoint doesn't skip it;
    if the retry scores worse it is restored. The (possibly updated) quality
    report is written to `quality_path` and returned.
    """
    from src.generation.gemini_consistency_check import check_page_quality

    def _qa(path: str) -> dict:
        res = check_page_quality(path, character_sheets, expected_text, expected_characters, page_num)
        res["page"] = page_num
        if seg_id is not None:
            res["segment_id"] = seg_id
        return res

    result = _qa(image_path)
    score = result.get("overall_score")  # None when the QA call failed (unknown)
    if (
        self_correct
        and score is not None
        and score < threshold
        and result.get("regeneration_feedback")
    ):
        old_score = score
        feedback = result["regeneration_feedback"]
        bad = Path(image_path)
        history_dir.mkdir(parents=True, exist_ok=True)
        backup = history_dir / f"{bad.stem}_selfcorrect_prev{bad.suffix}"
        # MOVE (not copy): the generator's on-disk checkpoint skips existing
        # page files, so the live image must be out of the way for the retry.
        shutil.move(str(bad), str(backup))
        try:
            new_path = regenerate_fn(feedback) or ""
        except Exception as e:
            # A crashed retry must not leave the page image-less — the live
            # file was moved away above. Restore, record, and report normally
            # (callers treat self-correct as best-effort and only log).
            new_path = ""
            result["self_correct_error"] = str(e)[:200]
        if not new_path:
            shutil.copy2(str(backup), str(bad))
            result["self_correct_attempted"] = True
        else:
            new_result = _qa(new_path)
            # None (QA failed/unknown) is NOT an improvement — keep the original.
            new_score = new_result.get("overall_score")
            kept_new = new_score is not None and new_score >= old_score
            if not kept_new:
                Path(new_path).unlink(missing_ok=True)
                shutil.copy2(str(backup), str(bad))
            result = new_result if kept_new else result
            result["self_correct_attempted"] = True
            result["self_correct"] = {
                "old_score": old_score, "new_score": new_score,
                "kept": "new" if kept_new else "old",
            }

    # Don't cache a failed/unknown verdict (overall_score None) as if it were a
    # real one — a later run would trust the stale "100s" and skip re-checking.
    if result.get("overall_score") is not None:
        quality_path.parent.mkdir(parents=True, exist_ok=True)
        quality_path.write_text(
            json.dumps(result, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
        )
        try:
            from src.config import GENERATED_DIR
            from src.core import store
            rel = str(quality_path.relative_to(GENERATED_DIR))
            store.put_json(rel, result)
        except Exception as e:
            logger.warning("QA result GCS persist failed for %s: %s", quality_path, e)
    return result


def sheet_qa_and_self_correct(
    *,
    sheet_path: str,
    char_name: str,
    appearance: str,
    visual_details: dict,
    gender: str,
    role: str,
    history_dir: Path,
    quality_path: Path,
    regenerate_fn: Callable[[str], str] | None = None,
    threshold: int = SHEET_SELF_CORRECT_THRESHOLD,
) -> dict:
    """QA a character reference sheet; optionally self-correct once (pages' policy,
    lenient threshold).

    `regenerate_fn(feedback) -> new_sheet_path` regenerates IN PLACE over the
    current sheet (the extension may change with the returned mime type);
    None = report-only, used by the manual quality endpoint.
    """
    from src.generation.gemini_consistency_check import check_character_sheet_quality

    def _qa(path: str) -> dict:
        return check_character_sheet_quality(path, char_name, appearance, visual_details, gender, role)

    result = _qa(sheet_path)
    score = result.get("overall_score")  # None when the QA call failed (unknown)
    if (
        regenerate_fn is not None
        and score is not None
        and score < threshold
        and result.get("regeneration_feedback")
    ):
        old_score = score
        bad = Path(sheet_path)
        history_dir.mkdir(parents=True, exist_ok=True)
        # COPY (not move): the retry overwrites the current path in place, and a
        # copy means a crashed retry can never leave the character sheet-less.
        backup = history_dir / f"{bad.stem}_selfcorrect_prev{bad.suffix}"
        shutil.copy2(str(bad), str(backup))
        new_path = regenerate_fn(result["regeneration_feedback"]) or ""
        if new_path:
            new_result = _qa(new_path)
            new_score = new_result.get("overall_score")  # None = QA failed/unknown
            kept_new = new_score is not None and new_score >= old_score
            if kept_new:
                # Extension drift cleanup: a .jpg retry leaves the old .png in
                # place and the UI's glob would resurrect the rejected sheet.
                if Path(new_path) != bad:
                    bad.unlink(missing_ok=True)
                result = new_result
            else:
                if Path(new_path) != bad:
                    Path(new_path).unlink(missing_ok=True)
                shutil.copy2(str(backup), str(bad))
            result["self_correct"] = {
                "old_score": old_score, "new_score": new_score,
                "kept": "new" if kept_new else "old",
            }
        result["self_correct_attempted"] = True

    # Never cache a failed/unknown verdict (overall_score None) as a real one.
    if result.get("overall_score") is not None:
        quality_path.parent.mkdir(parents=True, exist_ok=True)
        quality_path.write_text(
            json.dumps(result, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
        )
        try:
            from src.config import GENERATED_DIR
            from src.core import store
            rel = str(quality_path.relative_to(GENERATED_DIR))
            store.put_json(rel, result)
        except Exception as e:
            logger.warning("QA result GCS persist failed for %s: %s", quality_path, e)
    return result
