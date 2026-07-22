"""run_status.json lifecycle protocol (routes/books.py).

Preprocess status used to be inferred purely from file existence, which broke
two ways: a re-POST saw the previous run's analysis.json and reported instant
"complete" while the new subprocess was still running, and a server death
mid-run left the book "processing" forever. run_status.json is now the
authoritative per-run record; these tests pin its lifecycle (running →
complete/failed), dead-pid detection, the legacy fallback for books without
one, and the content-hashed book_id.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from src.routes import books


@pytest.fixture()
def gen_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("src.routes.books.GENERATED_DIR", tmp_path)
    return tmp_path


def _run_status(gen_dir, book_id="somebook"):
    return json.loads((gen_dir / book_id / "preprocess" / "run_status.json").read_text())


def _write_run_status(gen_dir, payload, book_id="somebook"):
    pre = gen_dir / book_id / "preprocess"
    pre.mkdir(parents=True, exist_ok=True)
    (pre / "run_status.json").write_text(json.dumps(payload))
    return pre


class _FakeProc:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.pid = 4242
        self._stderr = stderr

    async def communicate(self):
        return b"", self._stderr

    def kill(self):
        pass


# ── _run_preprocess lifecycle ────────────────────────────────────


def test_success_marks_run_status_complete(monkeypatch, gen_dir):
    async def fake_exec(*a, **k):
        return _FakeProc(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(books._run_preprocess("somebook", gen_dir / "in.txt"))

    rs = _run_status(gen_dir)
    assert rs["status"] == "complete"
    assert rs["pid"] == 4242
    assert rs["started_at"] and rs["finished_at"]


def test_nonzero_exit_marks_failed_and_keeps_error_json(monkeypatch, gen_dir):
    async def fake_exec(*a, **k):
        return _FakeProc(returncode=2, stderr=b"annotation exploded")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(books._run_preprocess("somebook", gen_dir / "in.txt"))

    rs = _run_status(gen_dir)
    assert rs["status"] == "failed"
    assert "annotation exploded" in rs["error"]
    # error.json stays for backward compat
    err = json.loads((gen_dir / "somebook" / "preprocess" / "error.json").read_text())
    assert "annotation exploded" in err["error"]


def test_timeout_env_var_and_failed_status(monkeypatch, gen_dir):
    """PREPROCESS_TIMEOUT_SECONDS replaces the hardcoded 600s; on timeout the
    run is marked failed (run_status + error.json)."""
    monkeypatch.setenv("PREPROCESS_TIMEOUT_SECONDS", "0")

    class HangingProc:
        pid = 777
        returncode = None

        def __init__(self):
            self.killed = False

        async def communicate(self):
            if self.killed:
                return b"", b""
            await asyncio.sleep(60)

        def kill(self):
            self.killed = True

    async def fake_exec(*a, **k):
        return HangingProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(books._run_preprocess("somebook", gen_dir / "in.txt"))

    rs = _run_status(gen_dir)
    assert rs["status"] == "failed"
    assert "timed out after 0s" in rs["error"]
    assert (gen_dir / "somebook" / "preprocess" / "error.json").exists()


def test_spawn_crash_marks_failed(monkeypatch, gen_dir):
    async def boom(*a, **k):
        raise OSError("spawn failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    asyncio.run(books._run_preprocess("somebook", gen_dir / "in.txt"))

    rs = _run_status(gen_dir)
    assert rs["status"] == "failed"
    assert "spawn failed" in rs["error"]


# ── progress endpoint × run_status ───────────────────────────────


def _seed_complete_artifacts(gen_dir, book_id="somebook"):
    """Files a finished run leaves behind (the false-instant-complete bait)."""
    pre = gen_dir / book_id / "preprocess"
    pre.mkdir(parents=True, exist_ok=True)
    (pre / "chapters.json").write_text(json.dumps([{"title": "Ch 1"}]))
    for name in ("llm_characters.json", "alias_map.json", "cleaned_chapters.json",
                 "segments_raw.json", "analysis.json"):
        (pre / name).write_text("{}")
    return pre


def test_repost_with_stale_analysis_is_not_instant_complete(client, gen_dir):
    """run_status=running must override the file-existence heuristics — the
    previous run's analysis.json used to report 100% complete immediately."""
    _seed_complete_artifacts(gen_dir)
    _write_run_status(gen_dir, {"status": "running", "pid": os.getpid(), "started_at": "x"})

    body = client.get("/api/book/somebook/preprocess/progress").json()
    assert body["status"] == "processing"
    assert body["progress"] < 100
    assert "annotate_complete" not in body["steps_done"]


def test_dead_pid_reports_failed(client, gen_dir, monkeypatch):
    _seed_complete_artifacts(gen_dir)
    _write_run_status(gen_dir, {"status": "running", "pid": 999999, "started_at": "x"})
    monkeypatch.setattr(books, "_pid_alive", lambda pid: False)

    body = client.get("/api/book/somebook/preprocess/progress").json()
    assert body["status"] == "error"
    assert "re-submit" in body["error"]


def test_pid_none_running_stays_processing(client, gen_dir):
    """Between POST and spawn the pid is unknown — must NOT be declared dead."""
    _write_run_status(gen_dir, {"status": "running", "pid": None, "started_at": "x"})
    body = client.get("/api/book/somebook/preprocess/progress").json()
    assert body["status"] == "processing"


def test_failed_run_status_reports_error(client, gen_dir):
    _write_run_status(gen_dir, {"status": "failed", "error": "gemini quota", "pid": 1})
    body = client.get("/api/book/somebook/preprocess/progress").json()
    assert body["status"] == "error"
    assert body["error"] == "gemini quota"


def test_complete_run_status_reports_complete(client, gen_dir):
    _seed_complete_artifacts(gen_dir)
    _write_run_status(gen_dir, {"status": "complete", "pid": 1, "finished_at": "x"})
    body = client.get("/api/book/somebook/preprocess/progress").json()
    assert body["status"] == "complete"
    assert body["progress"] == 100


def test_torn_run_status_degrades_to_processing(client, gen_dir):
    """A half-written run_status.json must not 500 nor report complete."""
    _seed_complete_artifacts(gen_dir)
    pre = gen_dir / "somebook" / "preprocess"
    (pre / "run_status.json").write_text('{"status": "runn')  # torn write

    resp = client.get("/api/book/somebook/preprocess/progress")
    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"


def test_legacy_book_without_run_status_keeps_file_behavior(client, gen_dir):
    """Books preprocessed before run_status existed must still report complete
    from analysis.json (and error from error.json)."""
    _seed_complete_artifacts(gen_dir)
    body = client.get("/api/book/somebook/preprocess/progress").json()
    assert body["status"] == "complete"
    assert body["progress"] == 100

    (gen_dir / "somebook" / "preprocess" / "error.json").write_text(
        json.dumps({"error": "legacy failure"}))
    body = client.get("/api/book/somebook/preprocess/progress").json()
    assert body["status"] == "error"
    assert body["error"] == "legacy failure"


# ── POST /api/generate: run_status stamping + 409 guard ──────────


SOURCE = "My Great Novel\n\nOnce upon a time there was a story to preprocess."


def test_post_stamps_running_before_task_runs(client, gen_dir, monkeypatch):
    seen = {}

    async def fake_run(book_id, dest, gemini_api_key=None):
        # The first thing the world can observe must already say "running".
        seen["rs_at_task_start"] = books._read_json_guarded(books._run_status_path(book_id))
        books._active_preprocesses.discard(book_id)

    monkeypatch.setattr(books, "_run_preprocess", fake_run)
    resp = client.post("/api/generate", json={"source_text": SOURCE})
    assert resp.status_code == 200
    book_id = resp.json()["book_id"]

    assert seen["rs_at_task_start"]["status"] == "running"
    assert seen["rs_at_task_start"]["pid"] is None
    rs = _run_status(gen_dir, book_id)
    assert rs["status"] == "running"


def test_post_409_when_orphan_run_alive(client, gen_dir, monkeypatch):
    """Server-restart orphan: _active_preprocesses is empty but run_status
    says running and its pid is alive → 409."""
    async def fake_run(book_id, dest, gemini_api_key=None):
        books._active_preprocesses.discard(book_id)

    monkeypatch.setattr(books, "_run_preprocess", fake_run)
    book_id = books._compute_book_id(SOURCE)
    _write_run_status(gen_dir, {"status": "running", "pid": os.getpid()}, book_id=book_id)

    resp = client.post("/api/generate", json={"source_text": SOURCE})
    assert resp.status_code == 409


def test_post_allowed_when_orphan_pid_dead(client, gen_dir, monkeypatch):
    async def fake_run(book_id, dest, gemini_api_key=None):
        books._active_preprocesses.discard(book_id)

    monkeypatch.setattr(books, "_run_preprocess", fake_run)
    monkeypatch.setattr(books, "_pid_alive", lambda pid: False)
    book_id = books._compute_book_id(SOURCE)
    _write_run_status(gen_dir, {"status": "running", "pid": 999999}, book_id=book_id)

    resp = client.post("/api/generate", json={"source_text": SOURCE})
    assert resp.status_code == 200


def test_post_409_from_in_memory_claim(client, gen_dir, monkeypatch):
    async def fake_run(book_id, dest, gemini_api_key=None):
        books._active_preprocesses.discard(book_id)

    monkeypatch.setattr(books, "_run_preprocess", fake_run)
    book_id = books._compute_book_id(SOURCE)
    books._active_preprocesses.add(book_id)
    try:
        resp = client.post("/api/generate", json={"source_text": SOURCE})
        assert resp.status_code == 409
    finally:
        books._active_preprocesses.discard(book_id)


def test_repost_clears_stale_error_json(client, gen_dir, monkeypatch):
    async def fake_run(book_id, dest, gemini_api_key=None):
        books._active_preprocesses.discard(book_id)

    monkeypatch.setattr(books, "_run_preprocess", fake_run)
    book_id = books._compute_book_id(SOURCE)
    pre = gen_dir / book_id / "preprocess"
    pre.mkdir(parents=True)
    (pre / "error.json").write_text(json.dumps({"error": "old failure"}))

    resp = client.post("/api/generate", json={"source_text": SOURCE})
    assert resp.status_code == 200
    assert not (pre / "error.json").exists()


# ── book_id hashing ──────────────────────────────────────────────


def test_same_text_same_id():
    assert books._compute_book_id(SOURCE) == books._compute_book_id(SOURCE)


def test_different_text_same_first_line_distinct_ids():
    """Raw Gutenberg uploads all share the same first line — slug-only ids
    silently overwrote each other."""
    a = "The Project Gutenberg eBook\n\nText of book A."
    b = "The Project Gutenberg eBook\n\nText of a COMPLETELY different book B."
    id_a, id_b = books._compute_book_id(a), books._compute_book_id(b)
    assert id_a != id_b
    # Same human-readable slug prefix, differing hash suffix
    assert id_a.rsplit("-", 1)[0] == id_b.rsplit("-", 1)[0]


def test_book_id_shape():
    bid = books._compute_book_id(SOURCE)
    slug, digest = bid.rsplit("-", 1)
    assert slug == "my_great_novel"
    assert len(digest) == 6
    assert all(c in "0123456789abcdef" for c in digest)
    assert len(slug) <= 52


# ── delete endpoint guard ────────────────────────────────────────


def test_delete_409_while_preprocess_running(client, gen_dir):
    _write_run_status(gen_dir, {"status": "running", "pid": os.getpid()})
    resp = client.delete("/api/book/somebook")
    assert resp.status_code == 409
    assert (gen_dir / "somebook").exists()


def test_delete_proceeds_on_mongo_outage(client, gen_dir, monkeypatch):
    pre = gen_dir / "somebook" / "preprocess"
    pre.mkdir(parents=True)
    (pre / "meta.json").write_text("{}")

    async def mongo_down(book_id):
        raise RuntimeError("mongo unreachable")

    monkeypatch.setattr("src.routes.books.delete_book", mongo_down)
    resp = client.delete("/api/book/somebook")
    assert resp.status_code == 200
    assert not (gen_dir / "somebook").exists()
