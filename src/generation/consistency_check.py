"""Check character consistency across illustrations using CLIP embeddings.

The CLIP dependency (open_clip) is optional. If it is not installed,
the consistency check will return a neutral result and log a warning.
"""

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Attempt to import heavy CLIP dependencies at module level so we can
# gate functionality behind their availability.
try:
    import open_clip
    import torch
    from PIL import Image

    CLIP_AVAILABLE = True
except ImportError:
    CLIP_AVAILABLE = False

SIMILARITY_THRESHOLD = 0.65

# CLIP model to use — ViT-B-32 is small and fast enough for a QA check.
_CLIP_MODEL_NAME = "ViT-B-32"
_CLIP_PRETRAINED = "laion2b_s34b_b79k"

# Cached CLIP model (loaded once, reused across all checks)
_cached_clip: tuple | None = None


def _load_clip_model() -> tuple:
    """Load the CLIP model (cached after first call).

    Returns:
        (model, preprocess, tokenizer) tuple.
    """
    global _cached_clip
    if _cached_clip is not None:
        return _cached_clip

    model, _, preprocess = open_clip.create_model_and_transforms(
        _CLIP_MODEL_NAME, pretrained=_CLIP_PRETRAINED
    )
    tokenizer = open_clip.get_tokenizer(_CLIP_MODEL_NAME)
    model.eval()
    _cached_clip = (model, preprocess, tokenizer)
    return _cached_clip


def _embed_image(image_path: str, model: object, preprocess: object) -> np.ndarray:
    """Compute a CLIP embedding for a single image.

    Args:
        image_path: Path to the image file.
        model: Loaded CLIP model.
        preprocess: CLIP image preprocessing transform.

    Returns:
        Normalized embedding as a 1-D numpy array.
    """
    image = Image.open(image_path).convert("RGB")
    image_tensor = preprocess(image).unsqueeze(0)

    with torch.no_grad():
        features = model.encode_image(image_tensor)
        features = features / features.norm(dim=-1, keepdim=True)

    return features.squeeze(0).cpu().numpy()


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    dot = float(np.dot(a, b))
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def check_consistency(
    illustration_paths: list[dict],
    character_sheets: list[dict],
) -> dict:
    """Check visual consistency of characters across page illustrations.

    Compares each page illustration against the character reference sheets
    using CLIP embeddings and cosine similarity. Pages where the similarity
    drops below the threshold are flagged for review.

    Args:
        illustration_paths: List of dicts from generate_illustrations(),
            each with 'page_number' and 'image_path'.
        character_sheets: List of dicts from generate_character_sheets(),
            each with 'character_name' and 'sheet_path'.

    Returns:
        Dictionary with:
            - overall_score: Average similarity across all pages.
            - per_page_scores: List of per-page similarity scores.
            - flagged_pages: Page numbers where similarity < threshold.
    """
    if not CLIP_AVAILABLE:
        logger.warning(
            "open_clip / torch / Pillow not installed. "
            "Skipping CLIP consistency check. "
            "Install with: pip install open-clip-torch torch Pillow"
        )
        page_count = len(illustration_paths)
        return {
            "overall_score": -1.0,
            "per_page_scores": [-1.0] * page_count,
            "flagged_pages": [],
        }

    # Filter to illustrations and character sheets that have valid paths.
    valid_illustrations = [
        p for p in illustration_paths if p.get("image_path") and Path(p["image_path"]).exists()
    ]
    valid_sheets = [
        s for s in character_sheets if s.get("sheet_path") and Path(s["sheet_path"]).exists()
    ]

    if not valid_illustrations:
        logger.warning("No valid illustration images to check.")
        return {
            "overall_score": 0.0,
            "per_page_scores": [],
            "flagged_pages": [],
        }

    if not valid_sheets:
        logger.warning(
            "No valid character sheet images for reference. "
            "Returning neutral scores."
        )
        page_count = len(illustration_paths)
        return {
            "overall_score": -1.0,
            "per_page_scores": [-1.0] * page_count,
            "flagged_pages": [],
        }

    # Load CLIP model
    try:
        model, preprocess, _ = _load_clip_model()
    except Exception as e:
        logger.error("Failed to load CLIP model: %s", e)
        page_count = len(illustration_paths)
        return {
            "overall_score": -1.0,
            "per_page_scores": [-1.0] * page_count,
            "flagged_pages": [],
        }

    # Compute reference embeddings from character sheets and average them.
    ref_embeddings: list[np.ndarray] = []
    for sheet in valid_sheets:
        try:
            emb = _embed_image(sheet["sheet_path"], model, preprocess)
            ref_embeddings.append(emb)
        except Exception as e:
            logger.warning(
                "Failed to embed character sheet '%s': %s",
                sheet.get("character_name", "?"),
                e,
            )

    if not ref_embeddings:
        logger.warning("Could not embed any character sheets.")
        page_count = len(illustration_paths)
        return {
            "overall_score": -1.0,
            "per_page_scores": [-1.0] * page_count,
            "flagged_pages": [],
        }

    # Average reference embedding (normalized).
    ref_mean = np.mean(ref_embeddings, axis=0)
    ref_mean = ref_mean / (np.linalg.norm(ref_mean) + 1e-8)

    # Score each illustration against the reference.
    per_page_scores: list[float] = []
    flagged_pages: list[int] = []

    # Build a lookup of page_number -> index for all illustration_paths
    # (including ones without valid images, which get score -1).
    all_page_numbers = [p.get("page_number", i + 1) for i, p in enumerate(illustration_paths)]
    valid_set = {p["image_path"] for p in valid_illustrations}

    for idx, page in enumerate(illustration_paths):
        image_path = page.get("image_path", "")
        page_num = all_page_numbers[idx]

        if not image_path or image_path not in valid_set:
            per_page_scores.append(-1.0)
            continue

        try:
            page_emb = _embed_image(image_path, model, preprocess)
            score = _cosine_similarity(page_emb, ref_mean)
            per_page_scores.append(round(score, 4))

            if score < SIMILARITY_THRESHOLD:
                flagged_pages.append(page_num)
                logger.info(
                    "Page %d flagged: similarity %.4f < threshold %.2f",
                    page_num,
                    score,
                    SIMILARITY_THRESHOLD,
                )
        except Exception as e:
            logger.warning(
                "Failed to embed illustration for page %d: %s", page_num, e
            )
            per_page_scores.append(-1.0)

    # Compute overall score from valid (non-negative) scores.
    valid_scores = [s for s in per_page_scores if s >= 0]
    overall_score = round(float(np.mean(valid_scores)), 4) if valid_scores else 0.0

    return {
        "overall_score": overall_score,
        "per_page_scores": per_page_scores,
        "flagged_pages": flagged_pages,
    }
