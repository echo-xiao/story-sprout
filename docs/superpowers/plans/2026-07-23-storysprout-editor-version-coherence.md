# StorySprout — Editor Version Coherence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the immutable **version** the single unit of identity across the editor, so reference (character/scene), QA, and stale ("red") all key off `version-id` instead of mutable "current files" — eliminating cross-page inconsistency, wrong stale flags, and lost QA text, with tests proving the linkage (联动).

**Architecture (the framework fix):** Today two identity systems coexist and drift: (a) immutable **versions** in `assets.json` (content-addressed, what the carousel/select use) and (b) mutable **current files** keyed by name/page-number (`{safe}_sheet`, `page_NNN`, `page_NNN_quality.json`) that generation/reference/stale/QA actually read. Every bug is a symptom of that drift. This plan makes the version the atom: a version carries its **image** (content-addressed), its **QA result**, and its **provenance** (which character/scene version-ids a page was generated against); the "current file" becomes a derived cache. Reference resolution, stale, and QA display all resolve through `get_selected_version` / provenance.

**Tech Stack:** FastAPI (Python 3.12); `src/core/store.py` (GCS-JSON asset versions), `src/core/storage.py` (image bytes + `localize`), `src/routes/generation.py` (page/character/scene gen + `get_stale_pages`), `src/routes/editor.py` (history/select endpoints), `src/generation/page_service.py` + `gemini_consistency_check.py` (QA), `src/generation/illustration.py` (`_find_scene_sheet`). Tests: pytest + in-memory fake GCS (`tests/conftest.py`).

## Global Constraints

- Backend suite stays green: `/Users/echoooooo/miniconda3/envs/picture_book_generator/bin/python -m pytest -q` (baseline after Task 1 = **269 passed**; never fewer + this plan's new tests). Frontend (`cd frontend && npx tsc --noEmit` + `npm test`) stays green for any FE-touching task.
- BOTH modes work: prod (real GCS) and test (`storage.GCS_BUCKET=""` local fallback + `store._bucket` in-memory fake).
- Every change is ADDITIVE with a guarded FALLBACK to today's behavior — a missing version/QA/provenance must degrade gracefully, never break generation.
- **Version is the atom:** the version-id is authoritative; never introduce a new mutable-file-keyed source of truth.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Deploy target is the EXISTING backend project only: `vercel --prod --yes` from repo root → `storysprout` (`storysprout-nine.vercel.app`). Vercel consolidation is a SEPARATE plan; do not touch it here.
- asset_key = canonical character name / location name (matches `record_image_version`). Character/scene/page `asset_type` = `"character"`/`"scene"`/`"page"`; page asset_key = `f"ch{ch_idx:02d}:p{page_num:03d}"`.

---

## Task 1: Sticky selection — DONE (commit 114dc3f)

`set_selected_version` marks `user_selected=True`; `add_asset_version` no longer overrides a manual pick. Foundation for "the selected version is authoritative." No action — listed for context.

---

## Task 2: `selected_version_image` + `selected_version_id` resolvers (storage/store)

**Files:** Modify `src/core/storage.py`; Test `tests/test_selected_version_image.py` (create).

**Interfaces:**
- Produces: `storage.selected_version_image(book_id, asset_type, asset_key) -> str | None` — localized path of the selected version's immutable image, or None. Consumed by Tasks 3-4, 6.

- [ ] **Step 1: Failing test** — `tests/test_selected_version_image.py`:

```python
import src.core.store as store
import src.core.storage as storage
from pathlib import Path


def test_returns_localized_selected_version(monkeypatch, tmp_path):
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "selimg"
    key = f"{b}/characters/X_deadbeef1234.png"
    (tmp_path / key).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / key).write_bytes(b"\x89PNG\r\n\x1a\nSEL")
    store.add_asset_version(b, "character", "X", "url", image_hash="deadbeef1234", storage_key=key)
    path = storage.selected_version_image(b, "character", "X")
    assert path and path.endswith("X_deadbeef1234.png")
    assert Path(path).read_bytes() == b"\x89PNG\r\n\x1a\nSEL"


def test_none_when_no_version():
    assert storage.selected_version_image("nobook", "character", "Nobody") is None
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: selected_version_image`).
- [ ] **Step 3: Implement** in `src/core/storage.py`:

```python
def selected_version_image(book_id: str, asset_type: str, asset_key: str) -> str | None:
    """Localized path of the SELECTED version's immutable, content-addressed
    image, or None. The hash key never changes content, so localize's cache is
    safe and every serverless instance resolves the identical bytes — the anchor
    for cross-page consistency."""
    from src.core.store import get_selected_version
    sel = get_selected_version(book_id, asset_type, asset_key)
    if not sel or not sel.get("storage_key"):
        return None
    return localize(sel["storage_key"])
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Full suite (≥271) + commit** `feat(storage): selected_version_image resolver`.

---

## Task 3: Character reference reads the selected version (both page paths)

**Files:** Modify `src/routes/generation.py` — `_sheets_for` (~29-45) AND the inline story-page loop (~241-269). Test `tests/test_sheets_for_selected.py` (create).

**Interfaces:** Consumes `storage.selected_version_image(book_id, "character", name)`.

- [ ] **Step 1: Failing test** — `tests/test_sheets_for_selected.py`:

```python
import src.core.store as store
import src.routes.generation as gen
from pathlib import Path


def test_sheets_for_uses_selected_version(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "shb"
    chars = tmp_path / b / "characters"; chars.mkdir(parents=True)
    (chars / f"{gen._safe_filename('Swallow')}_sheet.png").write_bytes(b"OLD-CURRENT")
    vkey = f"{b}/characters/Swallow_abc123abc123.png"
    (tmp_path / vkey).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / vkey).write_bytes(b"SELECTED")
    store.add_asset_version(b, "character", "Swallow", "url", image_hash="abc123abc123", storage_key=vkey)
    out = gen._sheets_for(b, ["Swallow"])
    assert Path(out[0]["sheet_path"]).read_bytes() == b"SELECTED"
```

- [ ] **Step 2: Run → FAIL** (returns OLD-CURRENT).
- [ ] **Step 3: Implement `_sheets_for`** — prefer the selected version, fall back to current file:

```python
def _sheets_for(book_id: str, names: list[str]) -> list[dict]:
    chars_dir = GENERATED_DIR / book_id / "characters"
    out: list[dict] = []
    for name in names:
        sel = storage.selected_version_image(book_id, "character", name)
        if sel:
            out.append({"character_name": name, "sheet_path": sel})
            continue
        safe = _safe_filename(name)
        for ext in (".png", ".jpg"):
            storage.localize(f"{book_id}/characters/{safe}_sheet{ext}")
            p = chars_dir / f"{safe}_sheet{ext}"
            if p.exists():
                out.append({"character_name": name, "sheet_path": str(p)})
                break
    return out
```

- [ ] **Step 4: Implement the inline story-page loop (~241-269)** — same preference before the current-file localize; on-the-fly `generate_character_sheets` only runs when NEITHER a selected version NOR a current file exists (guard the existing `if not found:` block by first checking `storage.selected_version_image`). Prepend to the loop body:

```python
            sel = storage.selected_version_image(book_id, "character", name)
            if sel:
                character_sheets.append({"character_name": name, "sheet_path": sel})
                continue
```

- [ ] **Step 5: Run → PASS.**
- [ ] **Step 6: Full suite (≥272) + commit** `fix(pages): character reference reads the selected immutable version`.

---

## Task 4: Scene reference reads the selected version

**Files:** Modify `src/generation/illustration.py` — `_find_scene_sheet` (~253-291). Test `tests/test_find_scene_sheet_selected.py` (create).

- [ ] **Step 1: Failing test** — `tests/test_find_scene_sheet_selected.py`:

```python
import src.core.store as store
import src.generation.illustration as illus
from pathlib import Path


def test_find_scene_sheet_uses_selected(monkeypatch, tmp_path):
    monkeypatch.setattr("src.generation.illustration.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.store.load_preprocess_file",
                        lambda book_id, fn: {"locations": [{"name": "The Garden", "aliases": []}]})
    b = "scb"
    vkey = f"{b}/scenes/The_Garden_9f9f9f9f9f9f.png"
    (tmp_path / vkey).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / vkey).write_bytes(b"SELECTED-SCENE")
    store.add_asset_version(b, "scene", "The Garden", "url", image_hash="9f9f9f9f9f9f", storage_key=vkey)
    p = illus._find_scene_sheet(b, "a page set in The Garden at dusk")
    assert Path(p).read_bytes() == b"SELECTED-SCENE"
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — once a location `name` matches, prefer the selected version before the current-file localize:

```python
            if n.lower() in bg_lower:
                sel = storage.selected_version_image(book_id, "scene", name)
                if sel:
                    return sel
                safe = re.sub(r'[^\w\s一-鿿-]', '', name)
                safe = re.sub(r'\s+', '_', safe.strip()).lower()[:50]
                for ext in (".png", ".jpg"):
                    storage.localize(f"{book_id}/scenes/{safe}_scene{ext}")
                    path = scenes_dir / f"{safe}_scene{ext}"
                    if path.exists():
                        return str(path)
                break
```
(Ensure `from src.core import storage` is imported in `_find_scene_sheet`; it already imports `storage`.)

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Full suite (≥273) + commit** `fix(pages): scene reference reads the selected immutable version`.

---

## Task 5: QA attached to the version (per-version QA, not per-page)

**Files:** Modify `src/core/store.py` (add `set_version_quality` / expose quality via `get_selected_version`), `src/routes/generation.py` (after page/character/scene QA, write QA onto the version), `src/routes/editor.py` (history endpoints read quality from the version). Test `tests/test_qa_per_version.py` (create).

**Interfaces:**
- Produces: `store.set_version_quality(book_id, asset_type, asset_key, version_id, quality: dict) -> bool` — stores `quality` on that version record (via `_mutate_json`). Version dicts gain an optional `"quality"` field. History endpoints attach `v["quality"]` per version.

- [ ] **Step 1: Failing test** — `tests/test_qa_per_version.py`:

```python
import src.core.store as store


def test_quality_stored_and_read_per_version():
    b = "qav"
    v1 = store.add_asset_version(b, "page", "ch00:p001", "u1", image_hash="h1", storage_key="k1")
    v2 = store.add_asset_version(b, "page", "ch00:p001", "u2", image_hash="h2", storage_key="k2")
    assert store.set_version_quality(b, "page", "ch00:p001", v1, {"overall_score": 80}) is True
    assert store.set_version_quality(b, "page", "ch00:p001", v2, {"overall_score": 95}) is True
    versions = store.list_asset_versions(b, "page", "ch00:p001")["versions"]
    by_id = {v["id"]: v for v in versions}
    assert by_id[v1]["quality"]["overall_score"] == 80
    assert by_id[v2]["quality"]["overall_score"] == 95  # each version keeps ITS OWN QA
```

- [ ] **Step 2: Run → FAIL** (`set_version_quality` missing).
- [ ] **Step 3: Implement `set_version_quality`** in `store.py` (mirror `set_selected_version`'s `_mutate_json` shape):

```python
def set_version_quality(book_id: str, asset_type: str, asset_key: str,
                        version_id: str, quality: dict) -> bool:
    k = _rec_key(asset_type, asset_key)
    out = {"ok": False}
    def _mut(assets):
        rec = assets.get(k)
        if not rec:
            return
        for v in rec["versions"]:
            if v["id"] == version_id:
                v["quality"] = quality
                assets[k] = rec
                out["ok"] = True
                return
    _mutate_json(_assets_key(book_id), _mut)
    return out["ok"]
```

- [ ] **Step 4: Wire generation → version quality.** In `generation.py`, at each spot that records a version AND has a QA result (page regen: after `qa_and_self_correct` returns `result`; character sheet: after `_run_character_sheet_quality`; scene: after QA), call `store.set_version_quality(book_id, asset_type, asset_key, <the recorded version id>, result)` when `result` and `result.get("overall_score") is not None`. Capture the version id returned by `record_image_version` (extend the `record_image_version` call sites to keep the returned id, or re-read the selected version id right after recording). Guard in try/except (never break generation).

- [ ] **Step 5: History endpoints read per-version quality.** In `editor.py`, where `get_segment_illustration_history` / `get_special_page_history` build the carousel, attach `entry["quality"] = v.get("quality")` for EACH version from `list_asset_versions` (not just the current page's `_load_quality`). Keep `_load_quality` as a fallback for the current entry when a version has no stored quality.

- [ ] **Step 6: Test the wiring** — extend `tests/test_qa_per_version.py` with a test that a page regen (stubbing Gemini QA to return `{"overall_score": 88}`) results in the recorded version carrying `quality.overall_score == 88` (mirror the T3 character-regen endpoint test pattern for stubbing).

- [ ] **Step 7: Full suite + `cd frontend && npx tsc --noEmit` (if editor JSON shape referenced) + commit** `feat(qa): attach QA to each version so it survives across versions/pages`.

---

## Task 6: QA parse robustness (no lost QA on malformed model JSON)

**Files:** Modify `src/generation/gemini_consistency_check.py` (`check_page_quality` / `check_character_sheet_quality` JSON parse). Test `tests/test_qa_parse_robust.py` (create).

**Interfaces:** the QA parser tolerates a model reply wrapped in prose / trailing commas / ```json fences, extracting the JSON object; on a truly unparseable reply it returns a structured `{"overall_score": None, "parse_error": "..."}` (so the caller's existing "don't cache None" guard holds) instead of throwing.

- [ ] **Step 1: Failing test** — `tests/test_qa_parse_robust.py`:

```python
import src.generation.gemini_consistency_check as qa


def test_parse_tolerates_fenced_and_trailing_comma():
    raw = '```json\n{"overall_score": 91, "character_consistency": {"score": 90, "issues": [],}}\n```'
    parsed = qa._parse_quality_json(raw)  # helper extracted in Step 3
    assert parsed["overall_score"] == 91


def test_parse_unparseable_returns_none_not_raise():
    parsed = qa._parse_quality_json("the character looks great, no json here")
    assert parsed.get("overall_score") is None
    assert "parse_error" in parsed
```

- [ ] **Step 2: Run → FAIL** (`_parse_quality_json` missing).
- [ ] **Step 3: Implement `_parse_quality_json(raw: str) -> dict`** — strip ```json fences, grab the first `{...}` block, remove trailing commas (`re.sub(r',\s*([}\]])', r'\1', s)`), `json.loads`; on any failure return `{"overall_score": None, "parse_error": <str>}`. Route the existing `json.loads(...)` in `check_page_quality` / `check_character_sheet_quality` through it. (This is why the "Expecting ',' delimiter" replies silently lost QA text.)

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Full suite + commit** `fix(qa): tolerant JSON parsing so a malformed model reply doesn't drop the QA result`.

---

## Task 7: Record page provenance + version-based stale ("red")

**Files:** Modify `src/routes/helpers.py` (`update_chapter_data_page` accepts `refs`), `src/routes/generation.py` (record refs at page gen; rewrite `get_stale_pages`). Test `tests/test_stale_versioned.py` (create).

**Interfaces:**
- `update_chapter_data_page(..., refs: dict | None = None)` stores `entry["refs"] = refs` (shape `{"characters": {name: version_id}, "scenes": {name: version_id}}`) in `chapter_data.json` (durable, GCS).
- `get_stale_pages` returns a page as stale when any recorded ref version-id ≠ the currently-selected version-id for that character/scene. Fallback: pages with no `refs` (legacy) → NOT stale (avoid false red).

- [ ] **Step 1: Failing test** — `tests/test_stale_versioned.py` (drives the endpoint through the fake store; seeds analysis + chapter_data with refs + selections):

```python
import src.core.store as store
import src.routes.generation as gen
import asyncio


def test_page_stale_when_selected_char_version_differs(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    b = "stb"
    # analysis: one page (seg 1) in ch0 referencing character "Swallow"
    monkeypatch.setattr("src.routes.generation._load_json",
        lambda book_id, fn: {"segments": [{"id": 1, "chapter_idx": 0,
            "characters_in_scene": ["Swallow"], "scene_background": ""}]} if fn == "analysis.json" else {})
    # character has two versions; page was generated against v1; user has selected v2
    v1 = store.add_asset_version(b, "character", "Swallow", "u1", image_hash="h1", storage_key="k1")
    v2 = store.add_asset_version(b, "character", "Swallow", "u2", image_hash="h2", storage_key="k2")
    store.set_selected_version(b, "character", "Swallow", v2)
    # chapter_data records the page's provenance = v1
    store.put_json(f"{b}/chapters/ch00/chapter_data.json",
                   {"pages": [{"page_number": 1, "refs": {"characters": {"Swallow": v1}, "scenes": {}}}]})
    res = asyncio.run(gen.get_stale_pages(b, 0))
    assert res["stale"] and res["stale"][0]["page"] == 1

    # after the user selects v1 (matching the page), it is NOT stale
    store.set_selected_version(b, "character", "Swallow", v1)
    res2 = asyncio.run(gen.get_stale_pages(b, 0))
    assert res2["stale"] == []
```

- [ ] **Step 2: Run → FAIL** (current mtime logic ignores versions).
- [ ] **Step 3: Implement `update_chapter_data_page` refs param** — add `refs: dict | None = None`; in the entry-update block, `if refs is not None: entry["refs"] = refs`.

- [ ] **Step 4: Record refs at page gen.** In the segment regen, after resolving character/scene references (Tasks 3-4), build `refs = {"characters": {name: (store.get_selected_version(book_id,"character",name) or {}).get("id") for name in used_chars}, "scenes": {loc: (store.get_selected_version(book_id,"scene",loc) or {}).get("id")}}` and pass it to the `update_chapter_data_page(...)` call (generation.py:397).

- [ ] **Step 5: Rewrite `get_stale_pages`** to compare provenance vs current selection (read `chapter_data.json` from the store for each page's `refs`; for each ref, `store.get_selected_version(book_id, type, name)["id"]` ≠ recorded id ⇒ stale reason). Drop the mtime logic. Pages with no `refs` ⇒ skip (not stale).

- [ ] **Step 6: Run → PASS** + a second test: two pages, one references the changed character and one doesn't → only the first is stale.

- [ ] **Step 7: Full suite + commit** `fix(stale): mark a page red when its reference version != the selected version (not by file mtime)`.

---

## Task 8: Editor-wide 联动 integration tests

**Files:** Test `tests/test_editor_version_coherence.py` (create). No production code — proves the whole subsystem is consistent end-to-end.

- [ ] **Step 1: Write the integration tests** covering the LINKAGE across all editor surfaces:

```python
"""编辑器全环节联动一致: selection + reference + QA + stale all agree, per asset type."""
import src.core.store as store
import src.routes.generation as gen
import src.generation.illustration as illus
from pathlib import Path
import asyncio


def _img(tmp, key, data):
    p = tmp / key; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return key


def test_character_ref_stable_across_regen_and_stale_agrees(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    monkeypatch.setattr("src.core.storage.GENERATED_DIR", tmp_path)
    b = "coh"
    k1 = _img(tmp_path, f"{b}/characters/Swallow_1111.png", b"V1")
    v1 = store.add_asset_version(b, "character", "Swallow", "u", image_hash="1111", storage_key=k1)
    store.set_selected_version(b, "character", "Swallow", v1)
    # a later regen appends V2 but selection is sticky on V1
    _img(tmp_path, f"{b}/characters/Swallow_2222.png", b"V2")
    store.add_asset_version(b, "character", "Swallow", "u", image_hash="2222", storage_key=f"{b}/characters/Swallow_2222.png")
    # REFERENCE: two page resolutions => identical V1
    a = gen._sheets_for(b, ["Swallow"]); c = gen._sheets_for(b, ["Swallow"])
    assert Path(a[0]["sheet_path"]).read_bytes() == b"V1" and a[0]["sheet_path"] == c[0]["sheet_path"]
    # STALE: a page whose provenance is V1 is NOT stale (selection is still V1) — agreement
    monkeypatch.setattr("src.routes.generation._load_json",
        lambda book_id, fn: {"segments": [{"id": 1, "chapter_idx": 0, "characters_in_scene": ["Swallow"], "scene_background": ""}]} if fn == "analysis.json" else {})
    store.put_json(f"{b}/chapters/ch00/chapter_data.json", {"pages": [{"page_number": 1, "refs": {"characters": {"Swallow": v1}, "scenes": {}}}]})
    assert asyncio.run(gen.get_stale_pages(b, 0))["stale"] == []
    # ...then user selects V2: reference AND stale BOTH flip together
    v2 = store.list_asset_versions(b, "character", "Swallow")["versions"][-1]["id"]
    store.set_selected_version(b, "character", "Swallow", v2)
    assert Path(gen._sheets_for(b, ["Swallow"])[0]["sheet_path"]).read_bytes() == b"V2"
    assert asyncio.run(gen.get_stale_pages(b, 0))["stale"][0]["page"] == 1


def test_qa_travels_with_the_version(monkeypatch, tmp_path):
    b = "cohqa"
    v1 = store.add_asset_version(b, "page", "ch00:p001", "u", image_hash="h1", storage_key="k1")
    store.set_version_quality(b, "page", "ch00:p001", v1, {"overall_score": 77})
    v2 = store.add_asset_version(b, "page", "ch00:p001", "u", image_hash="h2", storage_key="k2")
    store.set_version_quality(b, "page", "ch00:p001", v2, {"overall_score": 93})
    vs = {v["id"]: v for v in store.list_asset_versions(b, "page", "ch00:p001")["versions"]}
    assert vs[v1]["quality"]["overall_score"] == 77 and vs[v2]["quality"]["overall_score"] == 93
```
(Add a scene-parallel test mirroring the character one.)

- [ ] **Step 2: Run → PASS** (Tasks 2-7 merged).
- [ ] **Step 3: Full suite + commit** `test(editor): 联动 — selection/reference/QA/stale agree across regens for character/scene/page`.

---

## Task 9: Deploy + live-verify editor coherence

**Files:** none. Deploy backend only (`vercel --prod --yes` → `storysprout`).

- [ ] **Step 1: Deploy** the backend project only.
- [ ] **Step 2:** Pin a character + scene version; regen 2 pages sharing them → confirm identical reference (same `{name}_{hash}`), each version shows ITS OWN QA text, and the pages are NOT red. Then select a DIFFERENT character version → the dependent pages turn red (stale) AND regenerating them uses the newly-selected version. All four signals (reference, QA, stale, selection) agree.

---

## Self-Review

**Root-cause coverage:** the mutable-current-file drift is closed by making the version the atom — reference (T3/T4), QA (T5/T6), and stale (T7) all key off `version-id`/selection; sticky selection (T1) makes "selected" authoritative.
**联动 coverage:** T8 asserts the four signals move together across regen + reselect, for character/scene/page — the "editor 全环节一致" requirement.
**Placeholder scan:** concrete files, code, commands, pass/fail per step.
**Fallback safety:** every site degrades to today's behavior when a version/QA/refs record is absent (never-generated assets, legacy pages) — additive, low-risk.
**Type consistency:** `selected_version_image(book_id, asset_type, asset_key)->str|None`, `set_version_quality(book_id, asset_type, asset_key, version_id, quality)->bool`, refs shape `{"characters":{name:vid},"scenes":{name:vid}}` used identically across T2-T8.
**Risk:** T5/T7 touch the page-gen flow; live verification (T9) is the only full check of serverless behavior, but immutable/version-keyed resolution removes the mtime/cache class of failures by construction.
