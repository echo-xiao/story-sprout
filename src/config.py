import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
GENERATED_DIR = DATA_DIR / "generated"
SAMPLE_BOOKS_DIR = DATA_DIR / "sample_books"

GENERATED_DIR.mkdir(parents=True, exist_ok=True)

# Environment: "test" (DeepSeek + Alicloud) or "production" (Gemini)
APP_ENV = os.getenv("APP_ENV", "test")

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"

# DeepSeek (text analysis)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Alibaba Cloud / DashScope (image generation)
ALICLOUD_API_KEY = os.getenv("ALICLOUD_API_KEY", "")
ALICLOUD_IMAGE_MODEL = os.getenv("ALICLOUD_IMAGE_MODEL", "wan2.7-image-pro")

# LLM selection based on environment (can be overridden by explicit env vars)
_default_text_llm = "gemini" if APP_ENV == "production" else "deepseek"
_default_image_llm = "gemini" if APP_ENV == "production" else "alicloud"

TEXT_LLM = os.getenv("TEXT_LLM", _default_text_llm)   # "deepseek" or "gemini"
IMAGE_LLM = os.getenv("IMAGE_LLM", _default_image_llm)  # "alicloud" or "gemini"

# MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "picture_book_generator")


# NLP
SPACY_MODEL = "en_core_web_lg"

# Image generation styles
ILLUSTRATION_STYLES = {
    "watercolor": "watercolor children's book illustration, soft pastel colors, rounded shapes, warm lighting, gentle and friendly style",
    "risograph": (
        "risograph print on rough textured paper, "
        "visible paper grain and fiber texture throughout, "
        "ink appears uneven and slightly blotchy like real screen printing, "
        "misregistered color layers with visible offset between ink passes, "
        "only 3 spot ink colors: mustard ochre, dark teal, and burnt sienna on cream paper, "
        "hand-drawn imperfect lines, rough edges, organic shapes, "
        "looks like a handmade zine printed on a Riso machine, "
        "NOT clean digital art, NOT flat vector, NOT polished, "
        "lo-fi, tactile, imperfect, charming, artisanal print quality"
    ),
    "flat": "flat vector children's book illustration, bold outlines, bright solid colors, minimal shading, modern geometric shapes",
    "pencil": "soft colored pencil children's book illustration, gentle strokes, warm tones, textured paper background",
    "gouache": (
        "textured gouache painting children's book illustration, "
        "thick opaque brushstrokes visible on rough paper texture, "
        "hand-painted imperfect edges, paint slightly uneven and blotchy, "
        "warm limited palette (ochre yellow, navy blue, cream white, burnt orange), "
        "simple bold shapes with visible brush marks, "
        "cozy handmade feel like painted with real gouache on textured paper, "
        "NOT digital, NOT clean, NOT vector, NOT smooth gradients"
    ),
    "procreate_textured": (
        "Procreate textured brush children's book illustration, "
        "visible grainy brush texture like crayon or oil pastel on rough paper, "
        "colors slightly uneven with paper grain showing through, "
        "warm limited palette (mustard yellow, dark navy blue, burnt orange, cream), "
        "hand-drawn imperfect shapes with rough textured edges, "
        "cozy indie illustration style, tactile handmade quality, "
        "like a linocut print or stamp with grain overlay, "
        "simple bold compositions, NOT smooth, NOT polished, NOT vector"
    ),
    "color_monster": (
        "children's picture book illustration inspired by The Color Monster by Anna Llenas, "
        "mixed media collage style with textured paper cutouts, "
        "bold saturated colors (each emotion/scene has a dominant color), "
        "simple expressive characters with big eyes and exaggerated expressions, "
        "white or very light background with lots of negative space, "
        "visible paper texture and torn edges on color shapes, "
        "playful hand-drawn elements mixed with collage, "
        "warm and cozy feeling, NOT scary, NOT realistic"
    ),
}
DEFAULT_STYLE = ILLUSTRATION_STYLES["color_monster"]
NEGATIVE_PROMPT = "scary, violent, dark, photorealistic, 3D render, adult content, blood, weapons, text, words, letters"

# Age presets
AGE_PRESETS = {
    "2-4": {
        "max_words_per_page": 25,
        "max_sentence_length": 8,
        "flesch_kincaid_max": 2.0,
        "vocabulary_level": "basic",
        "description": "Toddler: very simple words, short sentences, repetition",
    },
    "4-6": {
        "max_words_per_page": 50,
        "max_sentence_length": 12,
        "flesch_kincaid_max": 4.0,
        "vocabulary_level": "intermediate",
        "description": "Preschool: simple sentences, basic story structure",
    },
    "6-8": {
        "max_words_per_page": 80,
        "max_sentence_length": 15,
        "flesch_kincaid_max": 6.0,
        "vocabulary_level": "advanced",
        "description": "Early reader: compound sentences, richer vocabulary",
    },
}

# Picture book templates
STORY_TEMPLATES = {
    "classic": {
        "structure": ["introduction", "problem", "attempt_1", "attempt_2", "climax", "resolution", "ending"],
        "min_pages": 7,
        "description": "Classic story arc with problem-solving",
    },
    "journey": {
        "structure": ["departure", "encounter_1", "encounter_2", "encounter_3", "challenge", "transformation", "return"],
        "min_pages": 7,
        "description": "Journey/adventure structure",
    },
    "simple": {
        "structure": ["setup", "event_1", "event_2", "event_3", "conclusion"],
        "min_pages": 5,
        "description": "Simple sequential events",
    },
}
