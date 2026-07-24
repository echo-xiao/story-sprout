# Migrate the JSON data layer from GCS objects to Firestore

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the GCS-object JSON store (`src/core/store.py`) with Firestore so the app gets strong read-your-writes consistency — killing the whole "编辑了保存不上 / 刷新也不在" class of bugs — while keeping the store's public interface byte-for-byte identical so no upper-layer code changes.

**Architecture:** `store.py` today implements ~20 data functions (books, characters, chapters, preprocess files, asset versions) ENTIRELY on top of four primitives: `get_json(key)`, `put_json(key, data)`, `_mutate_json(key, mutator)`, `_list_keys(suffix)`. We reimplement ONLY those four against Firestore; everything above them keeps working unchanged. Each current "key" (e.g. `the_happy_prince/preprocess/analysis.json`) becomes one Firestore document in a single flat collection, holding `{ "key": <original>, "data": <the JSON> }`. `_mutate_json`'s hand-rolled optimistic-concurrency loop is replaced by a native Firestore transaction (auto-retries on contention). Image BYTES stay in GCS via the separate `src/core/storage.py` — untouched.

**Tech Stack:** FastAPI + Vercel Python serverless; `google-cloud-firestore` (HTTP/gRPC, no connection pool — serverless-friendly); the existing `GCS_SA_JSON` service-account credentials + GCP project; pytest with an in-memory fake Firestore.

## Global Constraints
- **Firestore is THE single source of truth.** After cutover, every read of app state resolves to Firestore and nothing else. There is NO dual-write to GCS-JSON and NO stale local-`/tmp` copy that can shadow Firestore — the local-file path survives ONLY as the local-dev store when neither Firestore nor GCS is configured (see Task 2B). This is the whole point: one authoritative copy, so a read after a write is always the value just written.
- **Reference/selection state is Firestore-authoritative too.** Image BYTES stay in GCS (content-addressed, immutable → no consistency problem), but the POINTERS that decide which image is referenced — `assets.json`'s `selected_version_id`, the version list, `user_selected` ("永远用我手动选的那版"), per-version QA — live in Firestore as the sole authority. Every consumer (page generation reading a character/scene sheet, the editor carousel, stale checks) reads the same Firestore selection, so references can never disagree across the editor.
- **Interface is frozen.** The signatures and semantics of every public `store.py` function stay identical. Upper layers (`editor.py`, `generation.py`, `storage.py`) MUST NOT change, EXCEPT the deliberate `helpers._load_json`/`_save_json` change in Task 2B that makes Firestore authoritative. A diff touching any other `store.*` caller is a red flag unless listed here.
- **Images do NOT move.** `src/core/storage.py` (image bytes, `image_url`, `localize`, its own `list_keys`) stays on GCS. Only `store.py`'s JSON documents move to Firestore.
- **Reversible via a switch.** A `STORE_BACKEND` config (`"firestore"` | `"gcs"`) selects the backend. GCS data is NOT deleted by this plan, so rollback = flip the switch. Default flips to `"firestore"` only at cutover (Task 6).
- **Backend test suite stays green:** baseline **313 passed**; never fewer, plus new tests. The suite must pass on the Firestore backend (in-memory fake).
- **Firestore document limit is 1 MiB.** Current largest doc (`analysis.json`) is ~282 KB — fits. This plan keeps the whole-doc model; per-segment splitting is a NON-goal (noted as future work in Self-Review).
- **No secrets in git** (memory: .env never in git). Reuse `GCS_SA_JSON`; add only non-secret config.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Commit = push to GitHub main + deploy production (memory) — but for THIS plan, deploy happens only at Task 6 cutover; earlier tasks commit+push without deploying.

---

## Task 1: Firestore primitives behind a backend switch

**Files:**
- Modify: `src/core/store.py` (add Firestore impls of the 4 primitives; select via `STORE_BACKEND`)
- Modify: `src/config.py` (add `STORE_BACKEND`, `FIRESTORE_DATABASE`)
- Test: `tests/test_store_firestore_primitives.py` (create)

**Interfaces:**
- Consumes: `GCS_SA_JSON` (service-account JSON string, has `project_id`), a new `STORE_BACKEND` env (default `"gcs"` until cutover), `FIRESTORE_DATABASE` (default `"(default)"`).
- Produces (unchanged signatures, now Firestore-backed when `STORE_BACKEND=="firestore"`):
  - `get_json(key: str) -> Optional[Any]`
  - `put_json(key: str, data: Any) -> None`
  - `_mutate_json(key: str, mutator, retries: int = 8) -> Any`
  - `_list_keys(suffix: str = "") -> list[str]`
  - New internal: `_fs_collection()` returning a Firestore `CollectionReference` (monkeypatchable in tests), `_doc_id(key: str) -> str` (encodes `/` since Firestore doc IDs forbid it).

**Design notes (implementer read carefully):**
- Collection name: `"json_store"`. Document id = `_doc_id(key)` = `key.replace("/", "|")` (`|` is legal in Firestore ids and never appears in our keys, which are `book_id/segment/filename` slugs). Each doc body: `{"key": key, "data": <value>}`. Reading returns `doc.to_dict()["data"]`; missing doc → `None` (mirrors `get_json`).
- `_mutate_json` via transaction:
  ```python
  @firestore.transactional
  def _txn(txn, ref):
      snap = ref.get(transaction=txn)
      obj = (snap.to_dict() or {}).get("data") or {}
      result = mutator(obj)
      txn.set(ref, {"key": key, "data": obj})
      return result
  ```
  Firestore retries the transaction on contention automatically; drop the manual `if_generation_match` loop for this backend. Keep `retries` param for signature compatibility (unused on Firestore).
- `_list_keys(suffix)`: stream the collection, return `[d.to_dict()["key"] for d ... if key.endswith(suffix)]`. Data is small (tens of docs); Python-side filter is fine. (Firestore has no native "ends-with".)
- Keep the EXISTING GCS implementations intact, renamed `_gcs_get_json` etc.; the public `get_json`/`put_json`/`_mutate_json`/`_list_keys` dispatch on `STORE_BACKEND`. This is what makes rollback a config flip.
- Firestore client is a module singleton like `_client`, built from `GCS_SA_JSON` (same `from_service_account_info`), `firestore.Client(project=info["project_id"], credentials=creds, database=FIRESTORE_DATABASE)`. HTTP/gRPC — safe to reuse across serverless invocations.

- [ ] **Step 1: Write the failing test** — an in-memory fake Firestore collection (dict of `doc_id -> body`, supporting `.document(id).get()/.set()`, `.stream()`, and `firestore.transactional`), monkeypatched onto `store._fs_collection`. Assert, with `STORE_BACKEND="firestore"`:

```python
def test_firestore_primitives_roundtrip(fake_fs, monkeypatch):
    monkeypatch.setattr("src.config.STORE_BACKEND", "firestore")
    import src.core.store as store
    assert store.get_json("b/preprocess/analysis.json") is None
    store.put_json("b/preprocess/analysis.json", {"segments": [{"id": 1}]})
    assert store.get_json("b/preprocess/analysis.json") == {"segments": [{"id": 1}]}
    # mutate is atomic + returns the mutator's return
    out = store._mutate_json("b/x.json", lambda o: o.setdefault("n", 0) or o.__setitem__("n", 1))
    assert store.get_json("b/x.json") == {"n": 1}
    # list by suffix
    store.put_json("b/meta.json", {"title": "T"})
    assert "b/meta.json" in store._list_keys("/meta.json")
    assert "b/preprocess/analysis.json" not in store._list_keys("/meta.json")

def test_firestore_mutate_no_lost_update(fake_fs, monkeypatch):
    # A concurrent write committed inside the mutator forces the transaction to
    # re-run and preserve BOTH updates (fake_fs raises a contention error the
    # first commit, mirroring Firestore's transactional retry).
    ...
```

- [ ] **Step 2: Run → FAIL** (`_fs_collection`/backend dispatch not implemented).

- [ ] **Step 3: Implement** the Firestore primitives + `STORE_BACKEND` dispatch + config vars as described in Design notes. Keep GCS impls as `_gcs_*`.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit** `feat(store): Firestore-backed json primitives behind STORE_BACKEND switch (gcs default)`.

---

## Task 2: Whole suite green on the Firestore backend

**Files:**
- Modify: `tests/conftest.py` (add a `fake_fs` fixture + an autouse switch so the store tests can run on Firestore; keep the GCS fake for the GCS-backend tests)
- Modify: the handful of tests that poke GCS internals directly — `tests/test_asset_version_concurrency.py`, `tests/test_store_primitives.py`, `tests/test_store_data.py`, `tests/test_special_save_concurrency.py`, `tests/test_segment_save_concurrency.py` (only where they reach `store._bucket`/`get_blob`; the ones that use the public API are backend-agnostic and unchanged)

**Interfaces:**
- Consumes: `store` public API + the new `_fs_collection` seam.
- Produces: a parametrizable test store so the SAME behavioural tests (assets concurrency, special/segment save, backfill, etc.) pass on Firestore.

- [ ] **Step 1:** Add the in-memory fake Firestore to `conftest.py` (dict-backed collection with `.document`, `.get`, `.set`, `.stream`, transactional-with-contention support). Provide a fixture that sets `STORE_BACKEND="firestore"` and points `store._fs_collection` at the fake.

- [ ] **Step 2:** Run the full suite on Firestore: `STORE_BACKEND=firestore python -m pytest -q`. Fix any test that assumed GCS-specific internals (rewrite to the public API or the new fake). Behavioural tests (`add_asset_version` retries, save-no-lost-update, per-version QA) MUST pass unchanged in meaning.

- [ ] **Step 3:** Run the full suite on BOTH backends and confirm green:
  - `python -m pytest -q` (gcs default) → ≥313
  - `STORE_BACKEND=firestore python -m pytest -q` → ≥313 + new tests

- [ ] **Step 4: Commit** `test(store): run the data-layer suite against the Firestore backend (both backends green)`.

---

## Task 2B: Make Firestore the single source of truth (drop the stale local fallback)

**Files:**
- Modify: `src/routes/helpers.py` (`_load_json`, `_save_json`, `write_local_preprocess`)
- Test: `tests/test_load_json_authoritative.py` (create)

**Why:** Today `_load_json` reads the store, then falls back to a per-instance local `/tmp` copy on any hiccup — and that copy is cross-request STALE on serverless, which is the observed "save → refresh → 旧值 / 不在" bug. With Firestore as the single strongly-consistent authority, that fallback must never shadow it.

**Interfaces:**
- Consumes: `store.get_json`/`load_preprocess_file` (Firestore-backed), `STORE_BACKEND`, `GCS_BUCKET`.
- Produces: `_load_json` returns the Firestore value (retrying transient errors) and does NOT serve a stale local copy when a durable backend (Firestore or GCS) is configured; the local-file read remains only when NO durable backend is configured (pure local dev).

- [ ] **Step 1: Failing test** — with `STORE_BACKEND="firestore"` and a fake Firestore holding `{"segments":[{"id":1,"scene_summary":"FRESH"}]}` for `b/preprocess/analysis.json`, AND a local `/tmp/.../analysis.json` on disk holding a STALE `{"segments":[{"id":1,"scene_summary":"OLD"}]}`, assert `helpers._load_json("b","analysis.json")` returns `FRESH` (Firestore), never `OLD` (local). Second assertion: when the Firestore read raises transiently once then succeeds, it still returns `FRESH` (retry), not the stale local copy.

- [ ] **Step 2: Run → FAIL** (current code returns the stale local copy when the store read wobbles).

- [ ] **Step 3: Implement** — in `_load_json`: when a durable backend is configured (Firestore or GCS), the store read is authoritative — return its result (data, or `None` if the doc is genuinely absent); retry transient errors (keep the existing retry loop); do NOT fall through to the local file. Fall back to the local file ONLY when no durable backend is configured (local dev). Keep `write_local_preprocess` as a best-effort same-invocation cache for the PDF/generator fast path, but it is never a read authority. Update `_save_json` docstring accordingly. Do NOT change `store.*` callers otherwise.

- [ ] **Step 4: Run → PASS.** Full suite (both backends) still green.

- [ ] **Step 5: Commit** `fix(store): Firestore is authoritative — reads never serve a stale local copy`.

---

## Task 3: Provision Firestore (OWNER manual steps) + config wiring

**Files:**
- Modify: `requirements.txt` (add `google-cloud-firestore`)
- Modify: `src/config.py` (finalize `STORE_BACKEND`/`FIRESTORE_DATABASE` reading from env)

- [ ] **Step 1: OWNER manual (GCP console / gcloud), documented here):**
  - Enable the Firestore API on project `picture-book-gen`: `gcloud services enable firestore.googleapis.com --project picture-book-gen`.
  - Create a Firestore database in **Native mode**, single-region (e.g. `nam5`/`us-central`), id `(default)`.
  - Grant the existing service account Firestore access: `gcloud projects add-iam-policy-binding picture-book-gen --member="serviceAccount:vercel-storysprout@picture-book-gen.iam.gserviceaccount.com" --role="roles/datastore.user"`.
  - (Free tier: 1 GiB stored, 50k reads/20k writes per day — far above this app's use.)

- [ ] **Step 2:** Add `google-cloud-firestore` to `requirements.txt`. Confirm it installs in the Vercel Python runtime (it's pure-Python + grpc wheels; verify the build in Task 6's preview).

- [ ] **Step 3:** Vercel env: add `FIRESTORE_DATABASE=(default)`. Do NOT set `STORE_BACKEND=firestore` yet (stays `gcs` until the data is migrated in Task 4). `GCS_SA_JSON` is reused as-is.

- [ ] **Step 4: Commit** `chore(store): add google-cloud-firestore dep + Firestore config`.

---

## Task 4: One-time data migration (GCS JSON → Firestore)

**Files:**
- Create: `scripts/migrate_gcs_json_to_firestore.py` (one-shot, idempotent, run locally against prod creds)

**Interfaces:**
- Consumes: `GCS_SA_JSON`, `GCS_BUCKET` (source), Firestore (dest). Reuses `store._gcs_*` to READ and the Firestore primitives to WRITE.

- [ ] **Step 1:** Write the migration script: list every `*.json` object in the GCS bucket (`store._list_keys("")` on the gcs backend, filtered to keys ending `.json`), and for each, `firestore put_json(key, gcs_get_json(key))`. Idempotent (re-running overwrites). Print counts per book. Do NOT touch image objects (non-`.json`). Do NOT delete anything from GCS (GCS remains the rollback copy).

- [ ] **Step 2:** Dry-run: print how many JSON keys would migrate, grouped by book, with sizes; flag any doc ≥ 1 MiB (would need splitting — none expected).

- [ ] **Step 3:** Run the migration against prod. Verify: for a sample of keys (`the_happy_prince/preprocess/analysis.json`, `.../assets.json`, `.../meta.json`, `the_great_gatsby/...`), the Firestore doc `data` deep-equals the GCS object.

- [ ] **Step 4:** Verify counts: number of Firestore docs == number of `.json` objects in GCS.

- [ ] **Step 5: Commit** `chore(store): one-time GCS-JSON → Firestore migration script (data migrated)`.

---

## Task 5: Live-verify the consistency fix on a preview deploy

- [ ] **Step 1:** Deploy a PREVIEW (not production) with `STORE_BACKEND=firestore` set for the preview only (`vercel --yes`, preview env). Confirm the build installs `google-cloud-firestore` and boots.

- [ ] **Step 2:** On the preview URL, run the exact round-trip that failed before (the bug this whole migration targets):
  - `PUT /api/book/the_happy_prince/segment/2 {"scene_summary": "FSVERIFY"}` → 200
  - immediately `GET /api/book/the_happy_prince/preprocess/chapter/0/segments` → seg 2 scene_summary == `"FSVERIFY"` on the FIRST read (no stale window), repeated 8×, all fresh
  - `GET` again from a second cold request → still fresh (cross-instance consistent)
  - restore the original value; confirm it reads back immediately
- [ ] **Step 3:** Verify a special-page save (`PUT /special/book_cover`) and an asset-version flow are consistent on Firestore. Reference/selection authority check: regenerate a page → the new version appears in `/history` immediately; then manually select an OLDER version → confirm (a) the carousel shows it as current instantly, (b) `get_selected_version` returns it, and (c) a subsequent page generation that references that character/scene reads the SAME selected version (references agree across the editor — "永远用我手动选的那版").
- [ ] **If any read is stale:** the migration is not done — investigate on the preview; production still runs GCS and still (mostly) works. Do not cut over.

---

## Task 6: Cutover + soak

- [ ] **Step 1:** Merge to `main`; push.
- [ ] **Step 2:** Set production env `STORE_BACKEND=firestore` on `storysprout-web`; deploy production (`vercel --prod`).
- [ ] **Step 3:** Live-verify on `storysprout-web.vercel.app`: the segment/special save round-trip is immediately consistent; library + editor load; a real page regen works and its version shows instantly.
- [ ] **Step 4:** Soak for a day. GCS JSON objects remain as the rollback copy; rollback = set `STORE_BACKEND=gcs` + redeploy (GCS data is at most one migration-run stale — acceptable for emergency rollback; note any prod edits made after cutover would need re-migrating back).

---

## Task 7: Cleanup (only after a clean soak)

- [ ] **Step 1:** Once Firestore is confirmed stable, remove the dead OCC-emulation helpers that were only needed for GCS (the `if_generation_match` retry loop) IF no longer referenced, and delete the migration script's scratch. Keep `_gcs_*` + the `STORE_BACKEND` switch for at least one release as the rollback path; schedule their removal separately.
- [ ] **Step 2:** Update `store.py`'s module docstring (it currently says "GCS-JSON store — the single data layer") to describe Firestore as the JSON authority and GCS as image-bytes-only. Remove the stale memory note about "Mongo优先读+文件兜底".

## Self-Review / Risk
- **Single source of truth is enforced, not assumed.** Task 2B removes the stale local-`/tmp` fallback so Firestore is the only read authority in prod (the fix for "save→refresh→旧值"), and its test proves a stale local copy can never shadow a fresh Firestore value. Reference/selection state (`assets.json`) lives in Firestore, so every consumer reads the same selected version — Task 5 Step 3 verifies references agree across the editor after a manual version pick.
- **Blast radius is the 4 primitives.** Every higher function (assets, preprocess, characters, books) rides on them, so correctness of Task 1 + the Task 2 behavioural suite covers the whole surface. Task 2 explicitly re-runs the concurrency/save tests on Firestore so the exact bugs we fixed (lost updates, per-version QA) can't regress on the new backend.
- **Consistency is the whole point** — Task 5/6 verify the previously-failing save round-trip is now immediately consistent, on a preview first so a Firestore surprise never touches the live app.
- **1 MiB doc limit** is the one real Firestore constraint. Current data fits; Task 4 Step 2 flags any doc that doesn't. FUTURE (non-goal here): split `analysis.json` into per-segment documents — that also removes whole-file rewrites and gives per-segment atomic writes, but it changes the store interface, so it is intentionally out of scope for this consistency migration.
- **Rollback** is a config flip because GCS data is never deleted (Tasks 6–7), so every step before cutover is reversible and the live app is untouched until Task 6 Step 2.
- **Serverless fit:** Firestore is HTTP/gRPC with no connection pool, so the Vercel cold-start connection problems that would come with MongoDB do not apply; the client is a reused module singleton.
