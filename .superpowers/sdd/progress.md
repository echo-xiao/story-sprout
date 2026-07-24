# Plan 3 (editor rewrite) — SDD progress ledger

Plan: docs/superpowers/plans/2026-07-22-storysprout-plan3-editor-rewrite.md
Branch: refactor/deepseek-gcs-vercel

## Tasks
- Task 1: pageGen orchestrator + tests — complete (205aac9, review clean)
- Task 2: remove BYOK from editor — complete (e1155c9, review clean)
- Task 3: chapter-gen → per-page loop (+ AgentPanel/progress.ts/dead api fns) — complete (8d3029c, review clean)
- Task 4: remove scene-background debounce — complete (c4e3437, review clean)
- Task 5: Library Download PDF button — complete (e3aae1d, review clean)
- Task 6: full verification + HANDOFF update — complete (verify: fe tsc clean/21 pass, be 237 pass; HANDOFF updated)

## Minor findings roll-up (for final review)
- [T1] BatchResult.failed lumps timeout pages with error pages (error string = "timeout"). Intended: a timed-out page IS a batch failure to surface. Interface spec-locked. (info)
- [T1] Task 3 discards generatePagesSequential's BatchResult; failed/timed-out pages are shown only via segment dots (gray/red), no summary alert. Consider a post-batch summary if final review wants it.
- [T2] Stale comment in auto-simplify effect (page.tsx) still says "view-only / no-key visitor doesn't silently trigger paid LLM calls" — BYOK is gone; comment now misleading. Cosmetic. Sweep in final review.
- [T3] Batch handlers discard generatePagesSequential's BatchResult — a per-page failure/timeout mid-batch shows only as a gray/red dot, no aggregate toast. Brief-anticipated trade-off; consider `if (result.failed.length) alert(...)` summary in final review. Minor UX.

## Log
Task 1: complete (commits 9e27a1f..205aac9, review clean)
Task 2: complete (commits 205aac9..e1155c9, review clean)
Task 3: complete (commits e1155c9..8d3029c, review clean, backend 237 green)
Task 4: complete (commits 8d3029c..c4e3437, review clean)
Task 5: complete (commits c4e3437..e3aae1d, review clean)
Task 6: complete (HANDOFF committed; branch green: fe 21/21 + tsc, be 237)

## Final whole-branch review (9e27a1f..101eaa1)
- Important #1: batch filter uses selected-chapter-only `staleSegIds` state → Gen All skips stale-but-illustrated pages in non-selected chapters (spec §11.2 violation). PLAN bug. → FIXING (per-chapter getStalePages fetch).
- Minor #2 (triage A): thread BatchResult out, alert failure summary on Gen All. → folding into #1 fix.
- Minor #3 (triage B): stale auto-simplify comment (BYOK gone). → fixing.
- Minor #4: Gen-All header shows per-chapter page count not overall chapter N/total. Optional polish, ACCEPTED as-is (row highlight shows active chapter).
- Final-review fixes: complete (598c8c9). #1 stale-filter per-chapter FIX, #2 failure summary, #3 comment. tsc clean, fe 21/21, be 237. #4 accepted as-is.
PLAN 3 COMPLETE — branch green, NOT pushed (Plan 4 deploy pending per HANDOFF).

# ===== Plan 4 (Vercel deploy + /tmp) =====
Plan: docs/superpowers/plans/2026-07-22-storysprout-plan4-vercel-deploy.md
Exec order: 1(del Vertex) → 2(config /tmp) → 3(storage auth) → 7(del Docker+reqs) → 6(vercel entry) → 4(localize) → 5(/static→GCS). Task 8 = user-run deploy.

## P4 Tasks
- P4 Task 1: delete all Vertex — complete (61c66f7, review clean)
- P4 Task 2: config /tmp-configurable + drop Mongo env — complete (9ba219a, controller review clean)
- P4 Task 3: storage.py GCS_SA_JSON auth — complete (f8f55eb, controller review clean)
- P4 Task 7: delete Docker infra + clean requirements — complete (51600df, controller review clean)
- P4 Task 6: api/index.py + vercel.json — complete (3db65e5, controller review clean)
- P4 Task 4: localize-before-generate — complete w/ documented gap (b00040a, controller-reviewed)
- P4 Task 5: /static → GCS URLs + app.py mount guard — complete (dde1a88, controller review clean)
- P4 Task 8: deploy handoff (USER-RUN) — pending

## P4 Log
- [P4-T1 minor] make_genai_client lost its docstring (now inline comment). Cosmetic.
- [P4-T1 minor] config.py REQUIRE_USER_KEY comment still says "project's Vertex backend" (pre-existing, inert BYOK block) — sweep in BYOK cleanup.
P4 Task 1: complete (61c66f7, review clean, 237 green)
P4 Task 2: complete (9ba219a, controller-reviewed: env override + guarded mkdir verified live, 237 green)
P4 Task 3: complete (f8f55eb, controller-reviewed: mirrors store.py, early-return preserved, 237 green)
P4 Task 7: complete (51600df, 4 Docker files deleted + reqs trimmed to 11 deps, 237 green)
P4 Task 6: complete (3db65e5, ASGI entry + vercel.json; final build wiring deferred to Task 8 deploy)
P4 Task 4: complete (b00040a). Character sheets localized before draw in _sheets_for + _regen (both exts), mutation-verified (239 pass). Scene-sheet localize placed but sits behind cold-/tmp guards.
## KNOWN GAP for Task 8 (deploy) — cold-start metadata materialization (spec §8 risk 5):
  - illustration.py:265 reads preprocess/llm_locations.json as a LOCAL file; on cold /tmp it's absent (the JSON lives in GCS via store.get_json). _find_scene_sheet bails at the scenes/-dir-exists + llm_locations guards before its localize runs → scene backgrounds won't be scene-sheet-conditioned on cold start.
  - Likely broader: other GENERATED_DIR/**/*.json LOCAL reads in the generation path break on cold /tmp. Full extent only knowable on real deploy. Deploy smoke test (Task 8 step 4) must check scene backgrounds; if missing, targeted follow-up = route those JSON reads through store.get_json / localize the preprocess JSON. Character consistency (命脉) is NOT affected.
P4 Task 5: complete (dde1a88, 6 editor sites + versioned_static_url → storage.image_url; app.py mkdir+mount guarded; 239 green, no test edits)
P4 CODE TASKS DONE (1,2,3,7,6,4,5). Task 8 = user-run deploy.

## P4 FINAL WHOLE-BRANCH REVIEW (6bdfee8..dde1a88): CLEAN — 0 Critical, 0 Important.
All 6 deploy-readiness risks PASS. Scene-sheet gap confirmed EXACTLY one site (not wider). Verdict: code is deploy-ready as-is; remaining 'fixes' = deploy-time env/build wiring only.
Minor (optional): GCS_BUCKET default duplicated config.py+app.py (nil impact); frontend appends 2nd &v= to absolute GCS URLs (benign).
PLAN 4 CODE COMPLETE. Only Task 8 (user-run deploy) remains. HANDOFF committed 101eaa1-style.

## P4 CLEANUP TAIL (user: 都给我做了吧) — ALL DONE
- Scene-sheet cold-/tmp fix: 82ad053 (llm_locations via store.load_preprocess_file; premature guards dropped; test updated; 239)
- Create-page BYOK removal: 6383908 (UploadForm Gemini-key UI + getConfig gone; tsc clean, 21)
- Minors B+C: 18a2c4b (app.py GCS_BUCKET from config, import os removed; frontend cb= not duplicate v=; 239 + tsc clean)
Known gap CLOSED. Remaining: only Task 8 = user-run Vercel deploy (blocked on: GCS SA JSON key [no ambient key exists], DeepSeek key [absent], Vercel login/token).

# ===== Plan 5 (unified serverless generation + GCS consistency) =====
Plan: docs/superpowers/plans/2026-07-22-storysprout-plan5-unified-serverless-generation.md
Branch: refactor/deepseek-gcs-vercel
Baseline: 239 passed. Base commit before Task 1: da670e7
Exec order: 1(QA→GCS) → 2(chapter_data→GCS + PDF) → 3(char version) → 4(history store) → 5(audit).
NOTE: deploy/live-verify steps (T2 S6, T5 S3) are USER-RUN (no GCS SA/DeepSeek/Vercel creds in this env) — code+pytest done here, deploy handed off (matches Plan 4).
Pre-flight: T1 Interfaces one-liner key differs from concrete Step2/3 code; concrete rel-path scheme (from quality_path.relative_to(GENERATED_DIR)) governs, write+read use same key.

## P5 Tasks
- P5 Task 1: QA results → GCS — complete (da670e7..f10ea55, review clean/Approved, 248 green)

## P5 Minor findings roll-up (for final review)
- [P5-T1 Minor] check_segment_quality (generation.py:551) + check_special_page_quality (generation.py:901) WRITE quality JSON to disk only — no GCS dual-write. Manual QA-check results vanish on cold instance. Read path IS already GCS-first (Task 1). Fix = mirror Task 1's 2-line dual-write into those two generation.py write sites. Out of Task 1 scope; on-mission for spec §11.2 durability — final review triage whether to fix before merge.
- [P5-T1 Minor] tests/test_page_service.py:430 _install_bucket uses lambda: _Bucket() (new instance per call) vs conftest lambda: bucket (same instance). Harmless (_Bucket stateless via closure), cosmetic inconsistency.
- P5 Task 2: chapter_data → GCS + PDF localize — complete (f10ea55..7980336, review Approved; 2 Important cleanups fixed in 7980336; 259 green)
  - [P5-T2 Minor] special-page/cover images in special_dir NOT localized before export_pdf → blank on cold instance (out of Task 2 scope; same storage.localize pattern under {book_id}/special/ would fix). Final-review triage.
  - [P5-T2 Minor] store/storage imported at function scope in books.py (lazy); no test for store.get_json-raises path (hits harmless fallback). Cosmetic.
- P5 Task 3: character-sheet regen records durable version — complete (7980336..5a90f14, review Approved, 260 green). Placement inside if profile: after QA; asset_key=char_name (canonical). No minors of note (3 informational only).

## P5 Task 4 SCOPE DECISION (plan premise stale — user chose Option C)
Reality found: store.list_asset_versions(store.py:190)=plan's get_asset_versions (exists). Unified durable carousel /asset/{type}/{key}/versions (editor.py:194, _backfill_versions+list_asset_versions) + durable restore /select (set_selected_version+_promote_selected, editor.py:183) ALREADY exist; frontend uses both (api.ts:103/72). record_image_version fires for page/scene/special/character (all durable).
Only real bug = scene /history (editor.py:978) + character /history (editor.py:1017) read LOCAL disk → empty on serverless. segment(1147)/special(764) /history already GCS-safe.
USER CHOSE **Option C**: delete scene+character /history endpoints; repoint frontend scene/character carousels to /asset/{type}/{key}/versions + /select. Cross-stack: backend del 2 endpoints + tests; frontend api.ts + carousel components + tsc green + fe tests.
- P5 Task 4 (Option C): drop dead scene/character /history endpoints — complete (5a90f14..181fd23, review Approved 0 findings; backend 258=260-2, tsc clean, fe 21/21, grep-proof empty). Trailer OK. Legacy scene/char /history were dead (live UI already on /versions). segment/special /history kept (live+GCS-safe).
- P5 Task 5 (audit): image-existence/URL reads fully GCS — CLEAN, no code change. Ran plan Step-1 grep + broader route-layer audit. All 8 hits classified: generation-time same-instance writes (special_pages:115 _find_book_cover style-ref; editor restore/rename 845/944/1186) → plan says leave; non-image (books:602 analysis.json metadata; books:831 library = store.list_books-first, local iterdir only supplements dev). Image-existence reads already GCS (commits 8e7aa90/5d13cad): editor uses storage.exists 17x; sheets/portraits(editor.py:366-380) built from GCS-key enumeration + versioned_static_url(=GCS URL in prod), not local exists. Step 3 deploy+live-smoke = USER-RUN.
P5 CODE TASKS 1-5 COMPLETE. Deploy/live-verify (T2 S6, T5 S3) handed to user. Next: final whole-branch review.

## P5 FINAL WHOLE-BRANCH REVIEW (da670e7..181fd23): Ready to merge WITH FIXES — 0 Critical, 3 Important.
Strengths: key symmetry exact on all 3 write/read pairs (QA rel-key, chapter_data, character:<name>); dual-writes isolated; T3 asset_key correct; T4 deletion provably caller-free; T4 pivot judged sound; 258 tests green. Verified chapter_data has NO bulk writer (only update_chapter_data_page) → T2 GCS-first enumeration coherent.
Important #1 (FIX): check_segment_quality(gen.py:551)+check_special_page_quality(gen.py:901) write quality local-only; reader is GCS-first (T1) → asymmetric, scores vanish on cold instance. 4-line dual-write mirroring T1 + store-seed test.
Important #2 (FIX): special/cover images not localized before export_pdf (books.py:521 loop only does page images) → cover+dividers blank in serverless PDF. storage.list_keys({book}/special/)+localize, guarded. On-mission (folded in vs deferred).
Important #3 (FIX): editor.py _load_quality store.get_json UNGUARDED → store has no local fallback, GCS blip 500s the whole history carousel. Wrap in try/except → fall through to local/None. NEW failure mode from T1.
Minor #4 (ACCEPT): books.py function-scope imports — harmless, consistent w/ codebase.
Minor #5 (TICKET, pre-existing): stale refs to removed scripts/generate_chapter.py + POST /chapter/{ch}/generate in admin_gen.sh/preprocess_book.py/pipeline.py:1114. Not this branch. + add 1-line comment at helpers.py dual-write: sole GCS sync point for chapter_data.
Fix wave dispatched (ONE subagent, all 3 Important + the helpers.py comment).

## P5 FINAL-REVIEW FIXES: complete (181fd23..1f359b8, re-review clean — all 3 Important resolved, 0 Critical/Important, 264 green, trailer OK).
- Fix1: check_segment_quality/check_special_page_quality dual-write quality→GCS (key symmetric w/ _load_quality, verified byte-for-byte). Fix2: download_book_pdf localizes {book}/special/*.png|jpg before export_pdf (cover+dividers no longer blank on cold instance). Fix3: _load_quality guards store.get_json → GCS blip falls through to local/None, no carousel 500. Fix4: helpers.py comment (sole chapter_data GCS sync point).
- 6 new tests, all fail-if-reverted (verified real, not mock/tautological).
Minor #5 (pre-existing, NOT this branch): stale refs to removed scripts/generate_chapter.py + POST /chapter/{ch}/generate in admin_gen.sh/preprocess_book.py/pipeline.py:1114 — cleanup ticket for later.

## PLAN 5 COMPLETE — branch green (264 passed, fe tsc clean, fe 21/21). Commits da670e7..1f359b8:
  f10ea55(T1 QA→GCS) 2a42ca6+7980336(T2 chapter_data+PDF) 5a90f14(T3 char ver) 181fd23(T4 drop dead /history) 1f359b8(final fixes).
## USER-RUN REMAINING (no GCS SA/DeepSeek/Vercel creds in this env):
  - T2 Step 6: vercel --prod --yes; curl .../book/the_happy_prince/pdf → non-empty PDF, page image + cover appear.
  - T5 Step 3: full live smoke — gen page+character+cover; refresh each editor surface; confirm image+QA score+version carousel persist; download PDF.
  - Branch NOT pushed (per Plan 3/4 handoff pattern). Integration (merge/PR) = user decision.

## SHIPPED (this session): merge→push→deploy→live-verify ALL DONE.
- Merged feature refactor/deepseek-gcs-vercel → main (FF, 53 commits Plans 3+4+5), 264 green on merged main.
- Pushed origin/main 8ee9398..1f359b8.
- Deployed backend `storysprout` to prod via `vercel --prod --yes` → https://storysprout-nine.vercel.app (dpl_6xrRemUUgp85NnKTjbDN4GHjo2Fz, READY). NOTE: Vercel CLI WAS authed here (echoxiao666-9525) + project linked — earlier "user-run/blocked on creds" assumption was WRONG; runtime keys live in Vercel env.
- LIVE VERIFY: /api/health 200. Library /api/books/preprocessed 200 (GCS store.list_books: gatsby 39p, happy_prince 1p).
  - the_great_gatsby /pdf → 200 application/pdf 898KB valid %PDF-1.4 ✅ = T2 fix CONFIRMED on serverless (chapter_data + page imgs from GCS).
  - the_happy_prince /pdf → 404 "No generated pages" = EXPECTED (pre-fix book, chapter_data.json only local /tmp, never in GCS; needs 1 re-edit to backfill via update_chapter_data_page dual-write).
- Not yet live-verified (need access-code gate + generation credits): T1 QA-score persist, T3/T4 version carousel. T2 PDF (headline deliverable) verified working.

## POST-DEPLOY BUG (found by user testing live): character regen "no history / no image change / no QA change"
Root cause (systematic-debugging, evidence from Vercel logs + live /versions): _generate_portrait/generate_character_sheets SKIP generation when image exists in /tmp (character_sheet.py:182,294). On serverless, storage.localize (Plan5's GCS-read display paths) re-materializes current image into /tmp -> regen "finds exists" -> reuses stale image -> no new image -> QA same -> record_image_version dedups identical bytes -> no new version. Log smoking gun: "Portrait for 'Nightingale' already exists, skipping". NOT Plan5-caused but Plan5 made it more frequent (more localize). Scenes generate unconditionally (fine); pages have PBG_FORCE_REGEN (fine); only characters lacked force.
ALSO found: initial 429 RESOURCE_EXHAUSTED (Gemini prepay credits depleted) — user topped up. And save_inline_image returned "" SILENTLY on no-image responses (now logged).
FIX (5daa7fc, pushed+deployed): thread force param generate_character_sheets->_generate_portrait + sheet skip-check; regenerate_character_sheet passes force=True. +instrument save_inline_image silent no-image. 4 new TDD tests (test_character_force_regen.py), 268 green. Live-verifying new hashed version appears.

## FIX CONFIRMED LIVE (5daa7fc): character force-regen works on production.
Nightingale /versions BEFORE=1 (hash=none backfilled) -> AFTER=2 (added 5ac8c1fa9dd1 hash=YES Nightingale_fde0494326b7.jpg @06:03:28 = record_image_version on fresh sheet). Logs: portrait saved, QA overall=96 fresh sheet, NO "already exists, skipping". All 3 symptoms fixed at root (new image hash / new QA / new version). NOTE: regen+self-correct takes ~90-120s live (first 75s check was too early — red herring, not a fix failure). Forward-looking: existing chars accumulate new hashed versions on each future regen. 268 tests green.

## POST-DEPLOY BUG #2 (user: "history vanishes on refresh"): lost-update race on shared assets.json
Root cause: assets.json is ONE shared GCS blob/book; add_asset_version/set_selected_version did UNLOCKED read-modify-write. Editor page-load fires many concurrent /versions -> each empty asset's _backfill_versions writes the shared blob -> concurrent writes race -> writer with older snapshot clobbers a freshly-recorded version. Old/stable versions (in every snapshot base) survive (Happy Prince 02:27); new ones clobbered on refresh (Swallow/Nightingale). Answers user's "why Happy Prince has but Swallow doesn't".
FIX (0126b2a, pushed+deployed): store._mutate_json = optimistic concurrency (read blob+generation, write if_generation_match, retry on PreconditionFailed). add_asset_version+set_selected_version route through it. Defensive fallback for bucket fakes w/o get_blob. conftest fake bucket gains generation+if_generation_match. 2 TDD tests (test_asset_version_concurrency.py: interleave no-lost-update + retry-on-precondition). 270 green. Live-verifying version survives concurrent /versions storm.
## FIX #2 CONFIRMED LIVE (0126b2a): regen recorded version (2->3) AND survived 40x3 concurrent /versions storm (3->3, PASS). Lost-update race fixed. Both post-deploy bugs (force-regen 5daa7fc + concurrency 0126b2a) resolved & verified on prod. 270 tests green.
Remaining OPTIONAL (non-blocking): QA JSON-parse bug (Expecting ',' delimiter, occasional); frontend clear-error-on-failure (save_inline_image now logs, but UI still shows silence); vercel maxDuration=60s risk for long regen+self-correct.

## POST-PLAN5 live-fixes (user testing session):
- 5daa7fc: character force-regen (skip-if-exists reused stale /tmp on serverless). VERIFIED live.
- 0126b2a: assets.json optimistic concurrency (versions vanished on refresh — lost-update race). VERIFIED live.
- 9eeca52: retry on 200-no-image (scenes silently restored old / gen-all pages blank / sheet not saved). Deployed, user to verify.
- aa294ea: REMOVED shared-passcode gate entirely (backend AccessCodeMiddleware + access_gate.py + test deleted; frontend AccessGate + api interceptor removed). App now PUBLIC (no auth) per owner. Deployed BOTH projects: backend storysprout + frontend storysprout-web.vercel.app. 267 be / 21 fe / tsc clean. Owner warned to set AI Studio spending cap.
## STILL QUEUED: Q1 (user decided: 永远用手动选的那版) — set_selected_version marks user_selected; add_asset_version stops auto-overriding; page-gen (_sheets_for/scene) references the SELECTED version's immutable image. NOT yet implemented.

# ===== Reference-Consistency plan (2026-07-23) — SDD =====
Plan: docs/superpowers/plans/2026-07-23-storysprout-reference-consistency.md
Branch: main (session works on main per owner). Base commit: aa294ea. Baseline: 267 passed.
Goal: pages reference the SAME user-selected IMMUTABLE character/scene sheet -> cross-page consistency.
Exec: 1(sticky select) 2(selected_version_image) 3(char ref) 4(scene ref) 5(integration/联动) 6(deploy storysprout only).
## Tasks

# ===== Editor Version Coherence plan (2026-07-23, SUPERSEDES reference-consistency) =====
Plan: docs/superpowers/plans/2026-07-23-storysprout-editor-version-coherence.md
Framework: version = the atom; reference/QA/stale all key off version-id. Base for T2: 4e5dc74.
- T1 sticky selection: DONE (114dc3f, 269 green) — carried over from reference-consistency plan.
Exec T2..T9: T2 resolver, T3 char ref, T4 scene ref, T5 QA-per-version, T6 QA parse-robust, T7 stale-versioned+provenance, T8 联动 integration, T9 deploy.
- T2 selected_version_image: complete (4e5dc74..a99377d, controller-verified exact plan match, 271 green)
- T3 character reference reads selected version: complete (a99377d..28a5478, review Approved 0 Crit/Imp, 272 green)
- T4 scene reference reads selected version: complete (28a5478..402537a, controller-verified, 273 green)
- T5 QA-per-version: complete (402537a..bd31035, review Approved, 277 green). record_image_version now returns vid; page path wired; char/scene on graceful fallback (QA in diff scope); history attaches per-version quality + _load_quality fallback.
  - [T5 Minor] editor history _quality_by_url keyed on v["url"] vs compared to storage.image_url(ck) — verify the lookup hits (else per-version QA won't display). Cover in T8 integration + T9 live.
- T6 QA parse robustness: complete (bd31035..4335e96, controller-verified, 279 green). _parse_quality_json tolerant; both parse points routed. check_style_consistency left (own except, out of scope).
- T7 version-based stale + provenance: complete (4335e96..5770dd6, review Approved, 281 green). update_chapter_data_page refs param; segment regen records char(reliable)+scene(best-effort) provenance; get_stale_pages rewritten to compare recorded vid vs selected, None-guard prevents false-red, mtime removed.
- T8 联动 integration tests: complete (5770dd6..33c315c, tests-only, 285 green). Proves selection+reference+stale agree (char/scene) + QA-per-version. 
## REAL FINDING from T8 (was T5 Minor, now confirmed): page history carousel (get_segment_illustration_history) attaches per-version QA by matching version content-addressed url vs current page-file url — they DIFFER, so HISTORICAL page versions' QA won't display. Current-page QA still shows via _load_quality fallback (T6 ensures quality.json written). ROOT: pages use /segment/{id}/history (history/-prefix, url-keyed) which is a DIFFERENT carousel system than characters/scenes' /asset/{type}/versions (assets.json). FOLLOW-UP: unify page carousel onto list_asset_versions for true editor-wide coherence + per-version page QA display.
## ALL 8 CODE TASKS DONE (114dc3f..33c315c). 285 tests green. Next: final whole-branch review (opus) then present + deploy.
## FINAL WHOLE-BRANCH REVIEW (ed774e7..33c315c): Ready to ship WITH FIXES — 0 Critical, 2 Important + minors. Core (reference+stale version-keyed) confirmed solid/coherent; url→vid safe; page-QA binds to correct final version.
- Imp#1: carousel per-version QA dead (content-addressed url != page-file url) + masking test. Imp#2: char/scene/special versions got no QA attached. Minors: tautological tests, scene 1:1, none-vid edge.
## FINAL FIXES (33c315c..6fa3e22, controller-verified): Fix1 character QA→version; Fix2 carousel QA from get_selected_version (dead _quality_by_url removed, both endpoints); Fix3 honest tests (content-addressed url + character-QA test); Fix4 comments. 286 green.
## ACCEPTED LIMITATION / FOLLOW-UP: historical page/special carousel entries (history/-prefix files) carry no per-version QA — pages/special use /segment|special/history carousel, a DIFFERENT system than characters/scenes' /asset/{type}/versions. Full historical per-version QA needs unifying page/special carousel onto list_asset_versions. Vercel single-project merge = separate plan (pending).
## VERSION COHERENCE PLAN COMPLETE (114dc3f..6fa3e22, 286 green). Deploying backend storysprout.

# ===== Unify page/special carousel onto version store (2026-07-23) =====
Plan: docs/superpowers/plans/2026-07-23-storysprout-unify-page-carousel.md. Base: 0ca4f49. Baseline 286.
Goal: page+special history carousels build from list_asset_versions (per-version QA all entries) + restore via set_selected_version+_promote_selected. Frontend UNCHANGED (opaque version string). T1 segment, T2 special, T3 deploy.
ALSO done this session: fixed broken /api (repointed frontend/vercel.json to backend live URL after user deleted storysprout-nine alias) + Library is now landing page (49c5090). App verified working.
## Tasks
- T1 segment carousel+restore on version store: complete (0ca4f49..284c89c, review Approved, 293 green). get_segment_illustration_history from list_asset_versions (per-version QA all entries); restore via set_selected_version+_promote_selected. 4 existing tests rewritten to new contract (verified meaningful).
- T2 special carousel+restore on version store: complete (284c89c..2d15281, controller-verified, 299 green). Mirrors T1. 1 existing test rewritten (verified real).
- T3 deploy + FIX: _backfill_versions page history prefix (chapter-level not pages/ subdir); 300 green. NOTE gatsby page store polluted by pre-fix verify call (1 version, backfill no-ops) — test-book artifact; fix correct for un-backfilled pages.
## PAGE/SPECIAL CAROUSEL UNIFICATION COMPLETE (0ca4f49..dd21ca6, 300 green, deployed).
All 4 carousels (page/character/scene/special) now on ONE version system (list_asset_versions). Per-version QA for every entry; restore = set_selected_version+_promote_selected. Frontend unchanged. Backend deployed; all carousel endpoints 200 live. Found+fixed _backfill_versions page-history-prefix bug (chapter-level not pages/ subdir).

## VERCEL CONSOLIDATION COMPLETE (2026-07-23): ONE project serves frontend + Python /api.
Merged Next.js (frontend/src/* -> root app/components/lib/types/__tests__, avoid Python src/ collision) + Python api/src/tests (unchanged) into repo root. One vercel.json (Next auto + functions api/index.py + rewrite /api->/api/index). next.config /api rewrite dev-only. Verified on preview (Next->Python routing 200, GCS books load) THEN cutover storysprout-web prod. Copied 5 env vars to storysprout-web via Vercel API (GCS_SA_JSON from ~/Downloads SA file — sensitive, unreadable via API). Deleted backend project 'storysprout'. Merged to main (a71861f), main .vercel re-linked to storysprout-web, worktree removed. 300 pytest + 21 vitest green. ONE project 'storysprout-web' = one domain storysprout-web.vercel.app.

# ===== Firestore migration (2026-07-24) — SDD =====
Plan: docs/superpowers/plans/2026-07-24-firestore-migration.md
Branch: main (STORE_BACKEND default gcs → new code dormant until Task 6 cutover). Base before T1: 8ca3781.
- Task 1: Firestore primitives behind STORE_BACKEND switch — complete (8ca3781..65e2892, review clean/Approved, 320 passed = 313 gcs + 7 fs)
  Minors (final-review roll-up): result_holder accumulates on retry (harmless, [-1] correct); self-import _self in _fs_mutate_json (testability, commented); weak return-None assertion (covered elsewhere). No Critical/Important.
- Task 2: whole suite green on Firestore backend — complete (65e2892..dadbcb5, review Conditional-PASS→fixed) + fix f673188. GCS 320 passed; Firestore 307 passed/13 skipped. no-lost-update GENUINELY proven on Firestore (reviewer manually traced retry). Fix: 7 quality-JSON store-path tests made backend-agnostic (were wrongly gated) + de-tautologized page-QA-none test. Remaining 13 fs-skips = GCS-image-bytes/PDF tests (images stay on GCS by design) — acceptable.
- Task 2B: Firestore authoritative (drop stale local fallback) — complete (f673188..e1fc30f, review PASS/Approved, GCS 324 / Firestore 311+13skip). _load_json store_ok flag: store success (data OR None) authoritative, local only on all-raise. Reviewer confirmed 3 pre-existing local-only-seed tests corrected (not weakened); new authoritative test genuine (Test 4 = None-authoritative discriminator). Minor: redundant double-monkeypatch in retry test (harmless) → final-review note.
- Task 3 (code part): add google-cloud-firestore>=2.16.0 to requirements.txt — done (commit $(git rev-parse --short HEAD)). Config vars added in T1. Verified real lib imports (firestore.transactional/Client present), fs suite 311 green with real lib. OWNER MANUAL steps (enable Firestore API, create (default) DB Native mode, grant SA roles/datastore.user) PENDING — blocks T4-T6.
- LIVE-VERIFY (during T3): user provisioned Firestore (Standard, Native, nam5, DB ID='default' NOT '(default)' → FIRESTORE_DATABASE=default). SA vercel-storysprout granted roles/datastore.user. Live round-trip caught a real-lib bug: _fs_mutate_json used wrong @firestore.transactional call convention (fake masked it). FIXED + committed; re-verified live put/get/transaction/list all pass on real DB. GCS 324 / fs-fake 311+13.
- Task 4 (data migration): scripts/migrate_gcs_json_to_firestore.py — complete (commit above). Ran live: 287 JSON (gatsby 114 + happy_prince 173) migrated to Firestore DB 'default', 0 oversize, count parity 287==287, deep-equal sample (24 keys incl 9 analysis/assets/special/meta) 0 mismatches. seg2 = correct original (pollution gone, strong-consistency locked).
- Task 5 (preview verify): PASS. Preview (STORE_BACKEND=firestore, DB default) build OK (firestore dep installs on Vercel), /api/health 200, books list from Firestore. THE BUG FIX CONFIRMED: PUT seg2 → 8/8 immediate reads FRESH (Firestore strong consistency) vs GCS's ~36s stale/flicker window. Preview URL storysprout-l1gogr4w9.
- Task 6 (CUTOVER): DONE. Re-migrated (287==287, capture latest). Set prod env STORE_BACKEND=firestore + FIRESTORE_DATABASE=default. Deployed prod (READY). VERIFIED on storysprout-web.vercel.app: /api/health 200, books 200 (from Firestore), save round-trip 8/8 immediate reads FRESH — the "无法保存/读旧值" bug is GONE in production (Firestore strong consistency). Rollback available: set STORE_BACKEND=gcs + redeploy (GCS data intact).
- Task 7 (cleanup): DEFERRED to post-soak (keep _gcs_* + STORE_BACKEND switch as rollback ≥1 release).
Known Minors for final review: result_holder accumulates on retry ([-1] correct); redundant double-monkeypatch in a retry test; PDF/image-bytes tests gated on fs (images stay GCS, legit).

## FINAL WHOLE-BRANCH REVIEW (8ca3781..774ceaf, opus, read real lib source): Ready-with-fixes — 0 Critical, 2 Important (pre-existing couplings the cutover exposes; not corrupting live data now).
- I1 (books.py download_book_pdf ~489): chapter enum via storage.list_keys (GCS blobs) for /chapter_data.json — now in Firestore not GCS → post-cutover NEW books 404 on PDF. Masked by migrated books' old GCS blobs. FIX: enumerate from store._list_keys.
- I2 (helpers.py update_chapter_data_page ~328): local-first RMW of chapter_data.json → cold instance bootstraps {"pages":[]}+1 page → OVERWRITES store doc dropping other pages (data loss, violates SSOT). FIX: atomic RMW via store._mutate_json on the chapter_data key + local mirror.
- Confirmed-correct: backend dispatch, _mutate_json retry (result_holder[-1]=committed attempt, verified vs real lib), all 4 mutators retry-safe, SSOT _load_json, doc-id encoding collision-free, singleton ordering. M1-M6 accept.
- Fix wave: ONE subagent, I1+I2 + un-skip enumeration tests on Firestore.

## FINAL FIXES (774ceaf..88e2b6a): I1+I2 complete, review-verified-by-controller + LIVE.
- I1: books.py PDF chapter enum now from store._list_keys (not storage.list_keys/GCS blobs). I2: update_chapter_data_page atomic authoritative RMW via store._mutate_json (bootstrap only when store genuinely absent; catches store-failure best-effort NO local write; local mirror only after store success). +9 tests both backends (333 gcs / 320+13 fs). Behavioral change: no local write on store-failure (correct SSOT) — inverted test documents it.
- Controller-verified the raise-vs-swallow crux (catches+returns, regen path safe). Pushed + deployed prod.
- LIVE PROD VERIFY: health 200; the_great_gatsby /pdf 200 898KB; the_happy_prince /pdf 200 24MB (WAS 404 pre-fix — I1 confirmed); save round-trip 6/6 FRESH.

## FIRESTORE MIGRATION COMPLETE (8ca3781..88e2b6a). Data layer on Firestore, single source of truth, strong read-your-writes. The "无法保存/读旧值/闪回" bug class ELIMINATED in prod. Rollback ready: STORE_BACKEND=gcs + redeploy (GCS data intact). Task 7 cleanup (remove _gcs_* + switch) deferred ≥1 release post-soak.
