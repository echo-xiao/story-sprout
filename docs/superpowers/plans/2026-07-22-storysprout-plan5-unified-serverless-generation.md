# StorySprout Plan 5 — Unified Serverless Generation + GCS Consistency

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every image-regeneration path (page / character / scene / cover) do the SAME atomic "三件套" — generate → auto-QA(+self-correct) → record durable version — and persist AND read every generated artifact (image, version pointer, QA result, chapter_data, history) through GCS, so nothing vanishes on a cold serverless instance.

**Architecture:** Today four separate regen endpoints drift from spec §11.2 rule 2: they run different QA helpers and only some call `record_image_version`; and QA results (`quality.json`) + `chapter_data.json` + version history are read/written on the local `/tmp` filesystem, which is empty on the next Vercel instance. This plan (1) makes every regen path record a durable version, (2) persists QA results + chapter_data to GCS, (3) rewires the history endpoints to the durable `assets.json` version store, and (4) makes the book/PDF assembly read chapter_data from GCS. Image-existence reads were already moved to GCS in a prior commit (`storage.list_keys`).

**Tech Stack:** FastAPI (Python 3.12), `src/core/store.py` (GCS-JSON: `get_json`/`put_json`/asset versions), `src/core/storage.py` (image bytes + `list_keys`/`mirror_to_gcs`/`record_image_version`), `src/generation/page_service.py` (QA + self-correct), DeepSeek (text), Gemini (images/QA-vision), Vercel two-project deploy.

## Global Constraints

- Branch: `refactor/deepseek-gcs-vercel` (continue committing here).
- Backend tests must stay green every task: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` from repo root → **239 passed** (never fewer). When a test asserts local-`/tmp` behaviour that a task changes, update the test to patch `src.core.storage.GENERATED_DIR` / seed the in-memory store fixture (`_fake_store_bucket` in `tests/conftest.py`) — do NOT weaken assertions.
- Tests run with `storage.GCS_BUCKET=""` (conftest autouse `_no_real_gcs`) → `storage.*` falls back to local; and `store._bucket` is an in-memory fake (`_fake_store_bucket`). Every GCS-routed change must keep working in BOTH modes.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Deploy after each task (or batch): backend = `vercel --prod --yes` from repo root (project `storysprout` → `storysprout-nine.vercel.app`). Verify on the live backend with a real regen where the task warrants it.
- **Spec §11.2 rule 2 is the contract:** any image redraw (page/character/scene/cover) = ① swap image ② append version ③ auto-QA(+≤2 self-correct), atomically, through ONE shared path. Do not weaken it.
- GCS is the single source of truth; `GENERATED_DIR` (`/tmp` on Vercel) is per-invocation scratch that must be re-derivable from GCS.
- DRY, YAGNI, TDD where a unit is testable, frequent commits.

### Confirmed current gaps (the analysis this plan closes)
- **QA results** (`ch/quality/page_NNN_quality.json`, `page_service.py:107/177`) written to local `/tmp` only → the QualityCheckPanel score vanishes on refresh.
- **`chapter_data.json`** (`helpers.py:236`, `update_chapter_data_page`) written to local `/tmp` only → the book viewer / PDF (`books.py:484` reads it locally) shows "No generated pages" on serverless.
- **Character-sheet regen** (`generation.py:~1079`) runs `sheet_qa_and_self_correct` but does NOT `record_image_version` → its versions never reach the durable `assets.json`, so the character version carousel is empty.
- **History endpoints** (`get_segment_illustration_history` editor.py:1131, character-sheet history, scene history) read the local `history/` dir → empty on serverless, even though the durable versions live in `assets.json` (GCS) via `record_image_version`.
- **Image-existence reads** (pages/sheets/covers/scenes) — ALREADY fixed via `storage.list_keys` (commits on branch). This plan does NOT redo them; Task 5 only verifies completeness.

---

## File Structure

- **Modify** `src/generation/page_service.py` — after writing `quality_path` locally, also persist the QA JSON to GCS (`store.put_json`).
- **Modify** `src/routes/generation.py` — (a) character-sheet regen: add `record_image_version`; (b) confirm page/scene/special regen persist QA to GCS; (c) any path missing the durable-version call gets it.
- **Modify** `src/routes/helpers.py` — `update_chapter_data_page`: dual-write `chapter_data.json` to GCS via `store.put_json` (keep the local write for same-instance PDF builds).
- **Modify** `src/routes/books.py` — book viewer / PDF assembly (`~484`): read each chapter's `chapter_data.json` from GCS (`store.get_json`) with local fallback.
- **Modify** `src/routes/editor.py` — history endpoints (segment/page, character-sheet, scene) read versions from the durable store (`store` asset versions) instead of the local `history/` dir; QA-result reads come from GCS.
- **Modify** `src/core/store.py` — if no public "list all versions for an asset" accessor exists, add `get_asset_versions(book_id, asset_type, asset_key) -> {versions, selected_version_id}` (thin wrapper over `_load_assets`).
- **Modify** tests under `tests/` — align any test that seeded local `/tmp` artifacts to also seed the store / patch `storage.GENERATED_DIR`.

---

## Task 1: Persist QA results to GCS (so scores survive a refresh)

**Files:** Modify `src/generation/page_service.py`, `src/routes/editor.py` (QA read).

**Interfaces:**
- Produces: QA JSON stored at a durable key `store.put_json(f"{book_id}/quality/{asset_kind}/{asset_key}.json", report)` — the exact key scheme is defined here and consumed by the read endpoint.

- [ ] **Step 1: Read the two QA writers** in `page_service.py` (`qa_and_self_correct` ~line 107, `sheet_qa_and_self_correct` ~line 177). Both do `quality_path.write_text(json.dumps(report))`. Note the `book_id`, page/asset identity available in each.

- [ ] **Step 2: Dual-write QA JSON to GCS.** Immediately after each `quality_path.write_text(...)`, add a durable copy via the store. The functions receive `quality_path` (a `Path` under `GENERATED_DIR`); derive the durable key from its GENERATED_DIR-relative path so no new params are needed:

```python
try:
    from src.config import GENERATED_DIR
    from src.core import store
    rel = str(quality_path.relative_to(GENERATED_DIR))     # e.g. "book/chapters/ch00/quality/page_001_quality.json"
    store.put_json(rel, report)                            # same key, durable in GCS
except Exception as e:
    logger.warning("QA result GCS persist failed for %s: %s", quality_path, e)
```

- [ ] **Step 3: Read QA from GCS with local fallback.** Find every place that reads a `*_quality.json` for display (the segment history/quality read in `editor.py` and `get_chapter_segments`'s quality attach if any, plus `check_segment_quality`). Replace the local `read_text` with a helper that tries `store.get_json(rel)` first, then the local file:

```python
def _load_quality(book_id: str, rel_key: str) -> dict | None:
    from src.core import store
    data = store.get_json(rel_key)
    if data is not None:
        return data
    p = GENERATED_DIR / rel_key
    return json.loads(p.read_text()) if p.exists() else None
```

- [ ] **Step 4:** `pytest -q` → 239 passed (update any test that read the local quality file to also seed the store fixture). Commit `fix(qa): persist QA results to GCS so scores survive cold serverless instances`.

---

## Task 2: Persist `chapter_data.json` to GCS + book/PDF reads it from GCS

**Files:** Modify `src/routes/helpers.py` (`update_chapter_data_page`), `src/routes/books.py` (book viewer / PDF).

- [ ] **Step 1: Read `update_chapter_data_page`** (helpers.py:219-260). It reads+writes `GENERATED_DIR/{book}/chapters/chXX/chapter_data.json` under a lock. Note the final `chapter_data` dict it writes.

- [ ] **Step 2: Dual-write chapter_data to GCS.** After the local write, add:

```python
try:
    from src.core import store
    store.put_json(f"{book_id}/chapters/ch{ch_idx:02d}/chapter_data.json", chapter_data)
except Exception as e:
    logger.warning("chapter_data GCS persist failed for %s ch%d: %s", book_id, ch_idx, e)
```
(Keep the local write — the same-instance PDF build still reads it fast.)

- [ ] **Step 3: Book viewer / PDF reads chapter_data from GCS.** In `books.py` (~484), the assembly does `chapters_root.glob("ch*")` + `_read_json_guarded(ch_dir / "chapter_data.json")` (local). Replace with a GCS-first enumeration: list chapter indices from GCS and load each chapter_data via the store:

```python
from src.core import store, storage
# Chapter indices that have a durable chapter_data.json in GCS.
ch_idxs = sorted({
    int(re.search(r"/chapters/ch(\d+)/", k).group(1))
    for k in storage.list_keys(f"{book_id}/chapters/")
    if k.endswith("/chapter_data.json")
})
all_chapters = []
for ci in ch_idxs:
    data = store.get_json(f"{book_id}/chapters/ch{ci:02d}/chapter_data.json")
    if isinstance(data, dict) and data.get("pages"):
        all_chapters.append(data)
```
(Local fallback: if `storage.list_keys` is empty AND `chapters_root` exists locally, keep the old local glob path for dev.)

- [ ] **Step 4: The PDF's page IMAGES must be local for `reportlab`.** `export_pdf` reads page image files by path. Before building the PDF, `storage.localize(page_image_key)` each page's image from GCS to `/tmp` (mirror `special_pages.get_style_ref` / Plan 4 Task 4). Read `export_pdf`'s page-image handling and localize each referenced image key first.

- [ ] **Step 5:** `pytest -q` → 239 passed (seed store for any book-viewer test). Commit `fix(pdf): read chapter_data + page images from GCS so View Book / Download PDF work on serverless`.

- [ ] **Step 6: Deploy + live-verify** the PDF: `vercel --prod --yes`; after generating ≥1 page, `curl -o /tmp/t.pdf https://storysprout-nine.vercel.app/api/book/the_happy_prince/pdf` and confirm a non-empty PDF (and the page image appears).

---

## Task 3: Character-sheet regen records a durable version (align the 三件套)

**Files:** Modify `src/routes/generation.py` (`regenerate_character_sheet`, ~1079).

- [ ] **Step 1: Read `regenerate_character_sheet`** fully. It runs `sheet_qa_and_self_correct` and writes the sheet image (mirrored to GCS by `save_inline_image`→`mirror_to_gcs`), but never calls `record_image_version`, so `assets.json` gets no `character:<name>` version for a re-gen.

- [ ] **Step 2: Record the version.** After the final sheet image exists (post-QA), add — mirroring the page path (generation.py:380):

```python
for _ext in (".png", ".jpg"):
    _sheet = chars_dir / f"{safe}_sheet{_ext}"
    if _sheet.exists():
        try:
            from src.core.storage import record_image_version
            record_image_version(
                book_id, "character", canonical_name,
                _sheet.read_bytes(),
                content_type="image/png" if _ext == ".png" else "image/jpeg",
            )
        except Exception as _e:
            logger.warning("character sheet version record failed: %s", _e)
        break
```
Use the SAME `asset_type="character"` + `asset_key=<canonical_name>` the store already uses (verified in `assets.json`: key `character:Happy Prince`).

- [ ] **Step 3:** `pytest -q` → 239 passed. Commit `fix(character): record a durable version on sheet regen (spec §11.2 rule 2)`.

---

## Task 4: History endpoints read the durable `assets.json` version store

**Files:** Modify `src/core/store.py` (add `get_asset_versions` if absent), `src/routes/editor.py` (segment/page, character-sheet, scene history endpoints).

**Interfaces:**
- Consumes: `store.get_asset_versions(book_id, asset_type, asset_key) -> {"versions": [{id,url,hash,created_at,storage_key,quality?}], "selected_version_id": str|None}`.

- [ ] **Step 1: Ensure a list accessor exists.** In `store.py`, `_load_assets` + `get_selected_version` exist; add if missing:

```python
def get_asset_versions(book_id: str, asset_type: str, asset_key: str) -> dict:
    rec = _load_assets(book_id).get(f"{asset_type}:{asset_key}")
    return rec or {"versions": [], "selected_version_id": None}
```

- [ ] **Step 2: Rewire `get_segment_illustration_history`** (editor.py:1131) to build the carousel from `store.get_asset_versions(book_id, "page", f"ch{ch_idx:02d}:p{page_num:03d}")` — map each version to `{url: v["url"], version: v["id"], timestamp: v["created_at"], quality: <from Task 1 GCS quality if present>}`, marking the `selected_version_id` as `version: "current"`. Drop the local `history/` glob. (Derive `ch_idx`/`page_num` for the segment as the endpoint already does.)

- [ ] **Step 3: Rewire the character-sheet history + scene history endpoints** the same way, using `asset_type="character"`/`asset_key=<canonical_name>` and `asset_type="scene"`/`asset_key=<scene_name>`.

- [ ] **Step 4: Restore-version already writes the pointer** (`select_version` in store) — confirm the restore endpoints call `store.select_version(...)` (durable) and not a local file swap; fix if they still swap local files.

- [ ] **Step 5:** `pytest -q` → 239 passed (seed the store fixture with a couple of versions for the history tests instead of local files). Commit `fix(history): serve version carousels from the durable assets.json store`.

---

## Task 5: Verify image-existence + URL reads are fully GCS (audit, not rework)

**Files:** none expected (audit); fix any stragglers found.

- [ ] **Step 1: Grep for remaining local-fs existence in READ paths:**
```bash
grep -rnE "\.exists\(\)|\.glob\(|\.iterdir\(" src/routes/editor.py src/routes/books.py src/generation/special_pages.py \
 | grep -iE "GENERATED_DIR|img|page_|_sheet|_scene|cover|portrait|history|special|\.png|\.jpg"
```
Anything in a GET/read endpoint that still checks local `/tmp` for an image's existence/URL → convert to `storage.list_keys`/`storage.exists` + `storage.image_url` (pattern already used for pages/sheets/covers/scenes). Generation-time `.exists()` (same-instance writes) are fine — leave them.

- [ ] **Step 2:** For each straggler fixed, add the matching `storage.GENERATED_DIR` patch to its test. `pytest -q` → 239 passed. Commit if any change.

- [ ] **Step 3: Deploy + full live smoke** (`vercel --prod --yes` for the backend): generate a page + a character sheet + a cover on the live app; refresh each editor surface; confirm image, QA score, and version carousel ALL persist; download the PDF.

---

## Self-Review

**Spec coverage (§11.2 rule 2 + §4 durable versions):**
- Rule 2 "every redraw = swap + version + auto-QA, one path" → Task 3 gives the character path its missing version; Tasks 1/4 make QA + version durable+visible for all paths. ✅
- §4 "version list in assets.json" → Task 4 makes the carousels read it. ✅
- View Book / Download PDF on serverless → Task 2. ✅

**Root-cause coverage:** every symptom the user reported maps to a task — illustrations vanish (prior commits + Task 5 audit), history not archived (Task 4), QA not visible (Task 1), character regen inconsistent / no version (Task 3), PDF/book empty (Task 2).

**Placeholder scan:** each task names exact files/functions + the concrete change pattern (with code) + a verification command; the two spots needing fresh reads of current code (page_service QA writers, book-viewer assembly, char-sheet regen) say "read X first" because the surrounding code is best matched live — not left as TBD.

**Test-mode safety:** every change routes through `storage.*` / `store.*`, which fall back to local files / the in-memory fake in tests — so both GCS (prod) and local (tests/dev) keep working. Tests that seeded local `/tmp` artifacts are updated to seed the store, not weakened.

**Ordering:** Task 1 (QA→GCS) before Task 4 (history shows QA) — Task 4 reads the durable QA. Task 2 (chapter_data→GCS) is independent. Task 3 (character version) before Task 4's character carousel. Run 1 → 2 → 3 → 4 → 5.

**Risk:** Tasks 2 (PDF localize) and 4 (history rewire) have behaviour only fully verifiable on a live deploy — each task ends with a live-verify step; `pytest` proves no regression but not serverless correctness (inherent, per spec §8).
