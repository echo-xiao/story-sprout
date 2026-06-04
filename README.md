# Picture Book Generator

Any book → children's picture book. Analyzes book structure and storyline, generates picture books for ages 2-8 with simplified text + AI illustrations + PDF output.

Google Cloud Rapid Agent Hackathon entry. Deadline: 2026-06-11.
Hackathon: https://rapid-agent.devpost.com/

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    TWO-PHASE PIPELINE                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Phase 1: PREPROCESS (once per book, ~30s)                  │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │ Extract  │──▶│ NLP Analysis │──▶│ Save to Disk     │    │
│  │ Text     │   │ (spaCy,      │   │ (chapters,       │    │
│  │          │   │  TextTiling,  │   │  segments,       │    │
│  │          │   │  sentiment)   │   │  characters,     │    │
│  └──────────┘   └──────────────┘   │  profiles)       │    │
│                                     └──────────────────┘    │
│                                                             │
│  Phase 2: GENERATE (per chapter, on demand)                 │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │ Load     │──▶│ LLM Rewrite  │──▶│ Gemini Image Gen │    │
│  │ Preproc  │   │ (Gemini)     │   │ (with char sheet  │    │
│  │ Data     │   │              │   │  references)      │    │
│  └──────────┘   └──────────────┘   └──────────────────┘    │
│       │                                     │               │
│       │         ┌──────────────┐            │               │
│       └────────▶│ Special Pages│◀───────────┘               │
│                 │ (cover, ch   │                             │
│                 │  title, end) │                             │
│                 └──────────────┘                             │
│                        │                                    │
│                 ┌──────────────┐                             │
│                 │ PDF Export   │                             │
│                 └──────────────┘                             │
└─────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

- **No scene filtering**: All analyzed segments become pages. If NLP finds 30 segments in a chapter, 30 pages are generated.
- **Preprocess once, generate many**: NLP analysis is expensive (~30s) but only runs once. Subsequent generation loads from disk.
- **Text embedded in illustrations**: Gemini draws story text naturally into the art (clouds, scrolls, speech bubbles).
- **Character consistency**: Predefined visual identities + character sheet reference images passed to every page prompt.
- **On-demand chapter generation**: User specifies which chapter (and optionally which pages) to generate.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| NLP Analysis | spaCy (NER, POS), TextTiling (segmentation), VADER (sentiment) |
| Text Rewriting | Gemini 2.5 Flash |
| Image Generation | Gemini 2.5 Flash Image |
| Consistency Check | CLIP ViT-B-32 (open_clip) |
| PDF Export | ReportLab |
| Data Storage | MongoDB (optional) + JSON files |
| Agent Orchestrator | Gemini Function Calling |

## Usage

### Step 1: Preprocess a book (once)

```bash
python scripts/preprocess_book.py --input data/sample_books/a_tale_of_two_cities.txt
```

Output:
```
Title: A Tale of Two Cities
Chapters: 15
Segments: 45
Characters: Tom Buchanan (main), Daisy (main), ...

Saved to: data/generated/A_Tale_of_Two_Cities/preprocess/
```

### Step 2: Generate a chapter

```bash
# Generate all pages for chapter 0 (first chapter)
python scripts/generate_chapter.py --book A_Tale_of_Two_Cities --chapter 0

# Generate with special pages (cover, chapter cover, ending, back cover)
python scripts/generate_chapter.py --book A_Tale_of_Two_Cities --chapter 0 --with-special

# Generate only specific pages
python scripts/generate_chapter.py --book A_Tale_of_Two_Cities --chapter 0 --pages 1,2,3

# Generate only the book cover
python scripts/generate_chapter.py --book A_Tale_of_Two_Cities --cover-only

# Generate only special pages (cover + back cover + chapter cover/ending)
python scripts/generate_chapter.py --book A_Tale_of_Two_Cities --special-only --chapter 0
```

### Step 3: Full agent pipeline (alternative)

```bash
python scripts/run_pipeline.py --input data/sample_books/the_great_gatsby.txt --pages 15
```

## Special Pages

| Page Type | Description |
|-----------|-------------|
| **Book Cover** | Main characters + title, iconic scene, Gemini-generated |
| **Chapter Cover** | Chapter title + theme illustration, transition page |
| **Content Pages** | ALL segments from NLP analysis, text embedded in art |
| **Chapter Ending** | Reflective mood, "End of Chapter N" decoration |
| **Back Cover** | "The End" + warm farewell illustration |

## Pipeline Detail

### Phase 1: Preprocess
1. `extract_text` — Detect chapters (Roman numerals, headings), strip metadata
2. `analyze_book` — spaCy NER (characters), TextTiling (segments), sentiment curve, visual scoring, key events, character persona profiling
3. Save to `data/generated/{book_id}/preprocess/*.json`

### Phase 2: Generate
1. Load preprocessed data from disk
2. `generate_character_sheets` — 5 main characters, predefined visual identities (hair/outfit/feature), Pillow labels
3. `simplify_text` — Gemini rewrites ALL segments as narrator + dialogue (batched, 10/batch)
4. `generate_illustration_prompts` — Gemini creates per-page art direction with character identities
5. `generate_images` — Gemini image gen with character sheet references, CLIP consistency check
6. `generate_special_pages` — Book cover, chapter cover, chapter ending, back cover
7. `export_pdf` — Cover + title page + content + ending + back cover

## Project Structure

```
src/
├── agent/
│   ├── gemini_client.py          # Gemini API wrapper
│   ├── text_simplifier.py        # LLM text rewriting
│   ├── illustration_prompter.py  # LLM illustration prompts
│   ├── story_arc_selector.py     # LLM story arc (legacy, not used in new pipeline)
│   └── scene_selector.py         # NLP scene scoring (legacy)
├── analysis/
│   ├── chapter_split.py          # TextTiling segmentation
│   ├── character_extract.py      # spaCy NER with name cleanup
│   ├── character_persona.py      # Character profiling
│   ├── sentiment_curve.py        # Sentiment analysis
│   ├── visual_score.py           # Visual concreteness scoring
│   ├── complexity.py             # Reading level assessment
│   └── key_events.py             # Key event extraction
├── generation/
│   ├── character_sheet.py        # Character reference sheets
│   ├── illustration.py           # Page illustrations with CLIP check
│   ├── consistency_check.py      # CLIP ViT-B-32 consistency
│   └── special_pages.py          # Cover, chapter, ending illustrations
├── renderer/
│   ├── pdf_export.py             # PDF with cover/title/content/end
│   └── layout_engine.py          # HTML viewer
├── agent_orchestrator.py         # Gemini function calling agent
├── mcp_server.py                 # MCP tools (12 tools)
├── state_store.py                # In-memory state store
├── step_logger.py                # Step logging (files + MongoDB)
├── config.py                     # Styles, models, presets
└── models.py                     # Pydantic models

scripts/
├── preprocess_book.py            # Phase 1: analyze book
├── generate_chapter.py           # Phase 2: generate on demand
└── run_pipeline.py               # Full agent pipeline

data/
├── sample_books/                 # Input books (.txt)
└── generated/{book_id}/          # Output
    ├── preprocess/               # Cached analysis
    ├── characters/               # Character sheets
    ├── pages/                    # Page illustrations
    ├── special/                  # Cover, chapter, ending illustrations
    ├── steps/                    # Pipeline step logs
    ├── book.pdf                  # Final PDF
    └── book.json                 # Book metadata
```

## Sample Books

- The Great Gatsby (F. Scott Fitzgerald)
- A Tale of Two Cities (Charles Dickens)
- Frankenstein (Mary Shelley)
- Pride and Prejudice (Jane Austen)
- Don Quixote (Cervantes)
- The Odyssey (Homer)
- The Prince (Machiavelli)
