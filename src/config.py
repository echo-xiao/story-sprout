import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
GENERATED_DIR = DATA_DIR / "generated"

GENERATED_DIR.mkdir(parents=True, exist_ok=True)

# Gemini — runs on Vertex AI / "Agent Platform" by default (GEMINI_BACKEND=vertex,
# uses ADC locally and the attached service account on Cloud Run). Set
# GEMINI_BACKEND=api_key to use the AI Studio key path instead.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_BACKEND = os.getenv("GEMINI_BACKEND", "vertex").lower()  # "vertex" | "api_key"
GCP_PROJECT = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT", "picture-book-gen")
GCP_LOCATION = os.getenv("GCP_LOCATION", "global")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")

# BYOK gate. When true (the SECURE DEFAULT), generation endpoints require the
# caller's own Gemini key (403 otherwise) so public visitors can't bill the
# project's Vertex backend. Must be EXPLICITLY set to "false" to open generation
# onto the project's own bill — a deliberate choice (e.g. local owner testing),
# never the accidental default. A forgotten env var now fails safe (locked), not
# open.
REQUIRE_USER_KEY = os.getenv("REQUIRE_USER_KEY", "true").lower() != "false"

# MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "picture_book_generator")

# Durable image storage. When GCS_BUCKET is set, image bytes (current + every
# version) live in that GCS bucket; only metadata/version pointers stay in Mongo.
# Unset -> storage.py falls back to local files under GENERATED_DIR (local dev,
# or before the bucket is provisioned), so nothing breaks pre-setup.
GCS_BUCKET = os.getenv("GCS_BUCKET", "picture-book-gen-assets")


# Illustration style — single style; the old 7-entry ILLUSTRATION_STYLES dict
# had no picker anywhere (UI or API), so only this entry was ever used.
DEFAULT_STYLE = (
    "children's picture book illustration inspired by The Color Monster by Anna Llenas, "
    "mixed media collage style with textured paper cutouts, "
    "bold saturated colors (each emotion/scene has a dominant color), "
    "simple expressive characters with big eyes and exaggerated expressions, "
    "white or very light background with lots of negative space, "
    "visible paper texture and torn edges on color shapes, "
    "playful hand-drawn elements mixed with collage, "
    "warm and cozy feeling, NOT scary, NOT realistic"
)
NEGATIVE_PROMPT = "scary, violent, dark, photorealistic, 3D render, adult content, blood, weapons"


