# Picture Book Generator

Any book → children's picture book. LLM-powered analysis + AI illustrations + interactive editing + PDF output.

Google Cloud Rapid Agent Hackathon entry. Deadline: 2026-06-11.
Hackathon: https://rapid-agent.devpost.com/

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│              6-LAYER PREPROCESS (once per book)                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Layer 1: Extract Text → chapters                               │
│       ↓                                                         │
│  Layer 2: LLM Character ID → characters + aliases + gender      │
│       ↓                                                         │
│  Layer 3: Character Sheets (Gemini Image, on-demand per chapter)│
│       ↓                                                         │
│  Layer 4: Alias Replacement → cleaned text                      │
│       ↓                                                         │
│  Layer 5: TextTiling Segmentation (on cleaned text)             │
│       ↓                                                         │
│  Layer 6: LLM Annotation → characters_in_scene + actions +     │
│           scene_background + sentiment + key_events             │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│              GENERATE (per chapter, on demand)                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Load preprocess data for chapter                            │
│  2. Generate character sheets (reuse existing)                  │
│  3. LLM simplify text → children's language                     │
│  4. Build illustration prompts (background + characters +       │
│     actions + character sheets + story text)                    │
│  5. Gemini Image → page illustrations                           │
│  6. PDF export                                                  │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│              FRONTEND (interactive editing)                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  - View/edit each page: text, characters, actions, background   │
│  - Regenerate single pages on demand                            │
│  - Character sheet management                                   │
│  - PDF export                                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Current Status

### Done
- [x] 6-layer preprocess pipeline (LLM replaces NLP for character ID + annotation)
- [x] LLM character identification with aliases, gender, appearance (DeepSeek/Gemini)
- [x] Alias replacement in text (multi-word only, safe)
- [x] TextTiling segmentation + post-process split for long segments (>400 words)
- [x] LLM annotation per segment: characters_in_scene (with actions), scene_background, sentiment, key_events
- [x] Coreference: LLM resolves pronouns (he/she → specific character) in annotation step
- [x] Character sheet generation (Gemini Image) with gender, appearance from LLM
- [x] Text simplification (DeepSeek) → children's picture book language
- [x] Illustration prompt: background → characters + actions → character sheet reference → text
- [x] Illustration generation (Gemini Image) with character sheet references
- [x] Checkpoint/resume for preprocess (per-chapter annotation caching)
- [x] Checkpoint/resume for illustration generation (skip existing pages)
- [x] PDF export with combined chapters
- [x] MongoDB integration (best-effort)
- [x] Dual LLM support: DeepSeek (text, cheap) + Gemini (images, required for hackathon)

### TODO
- [ ] Frontend: interactive page editor (view/edit segments, regenerate single pages)
- [ ] Frontend: character sheet management (view, regenerate, edit appearance)
- [ ] Frontend: PDF preview and export
- [ ] Preprocess: improve LLM annotation accuracy (characters not physically present get tagged)
- [ ] Illustration: reduce character duplication in single image (Gemini limitation)
- [ ] Illustration: post-process name labels with Pillow (more reliable than Gemini text)
- [ ] Illustration: prevent character sheet elements (FRONT/SIDE labels) leaking into page art
- [ ] Special pages: book cover, chapter covers, back cover
- [ ] Switch text LLM back to Gemini for hackathon submission

## Key Design Decisions

- **LLM-first analysis**: Character identification, alias resolution, coreference, and scene annotation all done by LLM (DeepSeek/Gemini). No spaCy dependency for character work.
- **TextTiling for segmentation**: Algorithmic segmentation is more stable/deterministic than LLM splitting. LLM only annotates, doesn't split.
- **Preprocess once, generate many**: Full book analysis runs once. Chapter generation loads from cached data.
- **6-layer data pipeline**: Each layer saved to disk independently for debugging and resumability.
- **Character actions in prompts**: Each illustration prompt includes what each character is doing, not just who is present.
- **Scene background in prompts**: LLM describes the physical environment for each segment, passed to illustration prompt.
- **Checkpoint/resume**: Both preprocess and generation support resuming from where they left off.
- **Dual LLM**: DeepSeek for text tasks (cheap), Gemini for image generation (hackathon requirement). Switchable via `TEXT_LLM` env var.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Character ID & Annotation | DeepSeek (switchable to Gemini) |
| Text Segmentation | TextTiling algorithm |
| Text Simplification | DeepSeek (switchable to Gemini) |
| Image Generation | Gemini 2.5 Flash Image |
| Character Sheets | Gemini 2.5 Flash Image |
| PDF Export | ReportLab |
| Data Storage | JSON files + MongoDB (optional) |
| Frontend | Next.js (planned) |

## Usage

### Step 1: Preprocess a book (once per book)

```bash
python scripts/preprocess_book.py --input data/sample_books/a_tale_of_two_cities.txt

# Skip character sheet generation (faster, sheets generated on-demand per chapter)
python scripts/preprocess_book.py --input data/sample_books/a_tale_of_two_cities.txt --skip-sheets
```

Output (saved to `data/generated/{book_id}/preprocess/`):
```
Layer 1: meta.json, chapters.json, full_text.json
Layer 2: llm_characters.json, alias_map.json, character_genders.json
Layer 4: cleaned_chapters.json, cleaned_full_text.json
Layer 5: segments_raw.json
Layer 6: analysis.json, chapter_segments.json
         annotations/ch000.json ... ch044.json (checkpoints)
```

### Step 2: Generate a chapter

```bash
# Generate chapter 0
python scripts/generate_chapter.py --book A_TALE_OF_TWO_CITIES --chapter 0

# Generate multiple chapters
python scripts/generate_chapter.py --book A_TALE_OF_TWO_CITIES --chapter 0,4

# Generate specific pages only
python scripts/generate_chapter.py --book A_TALE_OF_TWO_CITIES --chapter 4 --pages 1,2,3

# Rebuild PDF from existing chapters
python scripts/generate_chapter.py --book A_TALE_OF_TWO_CITIES --pdf-only --chapter 0,4
```

## Preprocess Data Layers

| Layer | File | Description |
|-------|------|-------------|
| 1. Raw Text | chapters.json | Original text split by chapters |
| 2. Characters | llm_characters.json | LLM-identified characters with canonical names, aliases, gender, appearance |
| 2. Aliases | alias_map.json | Multi-word alias → canonical name mapping |
| 3. Character Sheets | characters/*.png | Visual reference sheets (generated on-demand) |
| 4. Cleaned Text | cleaned_chapters.json | Text with aliases replaced by canonical names |
| 5. Segments | segments_raw.json | TextTiling segments with long-segment splitting |
| 6. Annotations | analysis.json | Per-segment: characters_in_scene (with actions), scene_background, sentiment, key_events |

## Illustration Prompt Structure

Each page illustration prompt follows this priority order:

```
1. BACKGROUND/SETTING — scene environment from LLM annotation
2. CHARACTERS AND ACTIONS — who is present and what they are doing
3. CHARACTER APPEARANCE — match reference sheets exactly
4. NAME LABELS — wooden sign below each character's feet
5. STORY TEXT — simplified text embedded as speech bubbles/scrolls
```

## Frontend Design (Planned)

Interactive page editor for reviewing and regenerating illustrations:

```
┌──────────┬────────────────────────────┬──────────────┐
│ Chapters │     Page Editor            │  References  │
│          │                            │              │
│ Ch 1     │  [Generated Illustration]  │ [Char Sheet] │
│  pg 1    │                            │ [Char Sheet] │
│  pg 2    │  📝 Original text (read)   │              │
│  pg 3    │  ✏️ Simplified text (edit) │              │
│ Ch 2     │  🎭 Characters + actions   │              │
│  pg 1    │  🏠 Scene background       │              │
│  ...     │  😊 Sentiment (dropdown)   │              │
│          │                            │              │
│          │  [🔄 Regenerate] [✅ Done] │              │
└──────────┴────────────────────────────┴──────────────┘
```

Key interactions:
- Browse chapters and pages in left panel
- View generated illustration with all metadata
- Edit any field (text, characters, actions, background)
- Regenerate single page with updated parameters
- Manage character sheets (view, regenerate, edit appearance)
- Export final PDF

## Project Structure

```
src/
├── llm_client.py               # Unified LLM client (DeepSeek/Gemini)
├── config.py                   # Models, styles, API keys
├── analysis/
│   ├── chapter_split.py        # TextTiling segmentation
│   ├── coreference.py          # Coreference utilities
│   └── ...                     # (legacy NLP modules, being replaced by LLM)
├── generation/
│   ├── character_sheet.py      # Character reference sheet generation
│   ├── illustration.py         # Page illustration generation
│   └── special_pages.py        # Cover, chapter, ending illustrations
├── agent/
│   ├── gemini_client.py        # Gemini API wrapper
│   └── text_simplifier.py      # LLM text rewriting
├── renderer/
│   └── pdf_export.py           # PDF generation
└── app.py                      # FastAPI backend

scripts/
├── preprocess_book.py          # 6-layer preprocess pipeline
├── generate_chapter.py         # Per-chapter illustration generation
└── resolve_names.py            # (legacy, replaced by preprocess Layer 2+6)

frontend/                       # Next.js app (planned)

data/
├── sample_books/               # Input books (.txt)
└── generated/{book_id}/
    ├── preprocess/             # 6 layers of cached analysis
    │   └── annotations/        # Per-chapter LLM annotation checkpoints
    ├── characters/             # Character sheet images
    ├── chapters/ch{N}/pages/   # Page illustrations per chapter
    ├── special/                # Cover, chapter, ending illustrations
    └── book.pdf                # Combined PDF output
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| GEMINI_API_KEY | (required) | Google Gemini API key (for image generation) |
| DEEPSEEK_API_KEY | (optional) | DeepSeek API key (for text analysis, cheaper) |
| TEXT_LLM | "deepseek" | Which LLM for text tasks: "deepseek" or "gemini" |
| MONGODB_URI | mongodb://localhost:27017 | MongoDB connection string |

## Sample Books

- A Tale of Two Cities (Charles Dickens) — primary demo
- The Great Gatsby (F. Scott Fitzgerald)
- Frankenstein (Mary Shelley)
- Pride and Prejudice (Jane Austen)
- Don Quixote (Cervantes)
- The Odyssey (Homer)
- The Prince (Machiavelli)
