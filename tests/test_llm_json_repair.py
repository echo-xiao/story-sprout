"""llm_client.generate_json is now a thin facade over the DeepSeek client.

The JSON repair chain itself lives in (and is tested by) deepseek_client; here
we only verify the facade delegates and that a repaired result flows back.
"""

from __future__ import annotations

import src.llm_client as llm_client
import src.deepseek_client as deepseek_client


def test_generate_json_delegates_to_deepseek(monkeypatch):
    seen = {}

    def fake(prompt, system="", max_retries=3):
        seen["args"] = (prompt, system, max_retries)
        return {"ok": True}

    monkeypatch.setattr(deepseek_client, "generate_json", fake)
    assert llm_client.generate_json("hi", system="s", max_retries=2) == {"ok": True}
    assert seen["args"] == ("hi", "s", 2)


def test_repair_chain_reached_through_facade(monkeypatch):
    # A dirty raw response repaired by the DeepSeek client is returned via the facade.
    monkeypatch.setattr("src.config.DEEPSEEK_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(deepseek_client, "_call_deepseek", lambda *a, **k: '```json\n{"a": 1,}\n```')
    assert llm_client.generate_json("p") == {"a": 1}
