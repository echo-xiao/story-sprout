"""Lost-update protection for editor LLM endpoints (routes/editor.py).

The LLM endpoints follow read → slow LLM call → write-back. A segment edit
landing DURING the LLM call must survive: the write-back has to re-read and
merge under the book lock instead of persisting its stale pre-call snapshot
(review finding P0-3).
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import make_segment


@pytest.fixture()
def store(monkeypatch, tmp_path):
    """Disk-only _load_json/_save_json over a temp file (no MongoDB)."""
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps({"segments": [make_segment(0)]}))

    def load(book_id, filename):
        return json.loads(path.read_text()) if filename == "analysis.json" else {}

    def save(book_id, filename, data):
        assert filename == "analysis.json"
        path.write_text(json.dumps(data))

    monkeypatch.setattr("src.routes.editor._load_json", load)
    monkeypatch.setattr("src.routes.editor._save_json", save)
    monkeypatch.setattr("src.routes.editor.GENERATED_DIR", tmp_path)
    return {"load": load, "save": save}


def test_concurrent_edit_survives_simplify(client, monkeypatch, store):
    """While simplify's LLM call runs, another writer changes the segment's
    text. The simplify write-back must keep that edit."""

    def slow_simplify(scenes, age_group):
        # Simulates a PUT /segment/{id} landing mid-LLM-call.
        analysis = store["load"]("b", "analysis.json")
        analysis["segments"][0]["text"] = "EDITED DURING LLM CALL"
        store["save"]("b", "analysis.json", analysis)
        return [{"page_text": "Simple text.", "scene_direction": "a desk"}]

    monkeypatch.setattr("src.generation.text_simplifier.simplify_text", slow_simplify)

    resp = client.post("/api/book/somebook/segment/0/simplify")
    assert resp.status_code == 200
    assert resp.json()["simplified_text"] == "Simple text."

    final = store["load"]("b", "analysis.json")["segments"][0]
    assert final["simplified_text"] == "Simple text."
    assert final["scene_direction"] == "a desk"
    # the mid-flight edit must NOT have been clobbered by a stale snapshot
    assert final["text"] == "EDITED DURING LLM CALL"


def test_concurrent_edit_survives_summarize(client, monkeypatch, store):
    def slow_llm(prompt, system=""):
        analysis = store["load"]("b", "analysis.json")
        analysis["segments"][0]["simplified_text"] = "EDITED DURING LLM CALL"
        store["save"]("b", "analysis.json", analysis)
        return {"scene_summary": "A summary.", "sentiment": "tense"}

    monkeypatch.setattr("src.llm_client.generate_json", slow_llm)

    resp = client.post("/api/book/somebook/segment/0/summarize")
    assert resp.status_code == 200

    final = store["load"]("b", "analysis.json")["segments"][0]
    assert final["scene_summary"] == "A summary."
    assert final["sentiment"] == "tense"
    assert final["simplified_text"] == "EDITED DURING LLM CALL"


def test_chat_updates_sync_characters_in_scene(client, monkeypatch, store):
    """chat applies character_actions and keeps characters_in_scene in sync."""

    def fake_llm(prompt, system=""):
        return {
            "reply": "done",
            "updates": {
                "character_actions": [
                    {"name": "Nick Carraway", "action": "writes"},
                    {"name": "", "action": "ignored — empty name"},
                ],
            },
        }

    monkeypatch.setattr("src.llm_client.generate_json", fake_llm)

    resp = client.post(
        "/api/book/somebook/segment/0/chat",
        json={"message": "add Nick", "history": []},
    )
    assert resp.status_code == 200

    final = store["load"]("b", "analysis.json")["segments"][0]
    assert final["characters_in_scene"] == ["Nick Carraway"]
    assert len(final["character_actions"]) == 2
