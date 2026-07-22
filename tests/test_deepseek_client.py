import httpx
import pytest


def _fake_response(content: str):
    return {"choices": [{"message": {"content": content}}]}


def test_generate_json_parses_clean(monkeypatch):
    monkeypatch.setattr("src.config.DEEPSEEK_API_KEY", "sk-test", raising=False)
    import src.deepseek_client as dc

    def fake_post(url, **kwargs):
        assert url.endswith("/chat/completions")
        assert kwargs["json"]["response_format"] == {"type": "json_object"}
        req = httpx.Request("POST", url)
        return httpx.Response(200, json=_fake_response('{"a": 1, "b": "x"}'), request=req)

    monkeypatch.setattr(dc.httpx, "post", fake_post)
    assert dc.generate_json("hi", system="sys") == {"a": 1, "b": "x"}


def test_generate_json_repairs_fenced_and_trailing_comma(monkeypatch):
    monkeypatch.setattr("src.config.DEEPSEEK_API_KEY", "sk-test", raising=False)
    import src.deepseek_client as dc
    dirty = "```json\n{\"a\": 1,}\n```"

    def fake_post(url, **kwargs):
        req = httpx.Request("POST", url)
        return httpx.Response(200, json=_fake_response(dirty), request=req)

    monkeypatch.setattr(dc.httpx, "post", fake_post)
    assert dc.generate_json("hi") == {"a": 1}


def test_generate_json_missing_key_raises(monkeypatch):
    monkeypatch.setattr("src.config.DEEPSEEK_API_KEY", "", raising=False)
    import src.deepseek_client as dc
    with pytest.raises(ValueError):
        dc.generate_json("hi")


# ── JSON repair chain (ported from the old llm_client repair tests) ─────────
@pytest.fixture()
def raw(monkeypatch):
    """Stub the raw DeepSeek call; only the parse/repair chain is under test."""
    monkeypatch.setattr("src.config.DEEPSEEK_API_KEY", "test-key", raising=False)
    import src.deepseek_client as dc
    holder = {"raw": "{}"}
    monkeypatch.setattr(dc, "_call_deepseek", lambda *a, **k: holder["raw"])
    return holder


def test_repair_markdown_fenced_nested_braces(raw):
    import src.deepseek_client as dc
    raw["raw"] = '```json\n{"outer": {"inner": 1}}\n```'
    assert dc.generate_json("p") == {"outer": {"inner": 1}}


def test_repair_trailing_commas(raw):
    import src.deepseek_client as dc
    raw["raw"] = '{"a": [1, 2,], "b": {"c": 3,},}'
    assert dc.generate_json("p") == {"a": [1, 2], "b": {"c": 3}}


def test_repair_prose_around_object(raw):
    import src.deepseek_client as dc
    raw["raw"] = 'Sure! The result is {"ok": true} — let me know.'
    assert dc.generate_json("p") == {"ok": True}


def test_repair_prose_plus_trailing_comma(raw):
    import src.deepseek_client as dc
    raw["raw"] = 'Result: {"items": [1, 2,],} thanks'
    assert dc.generate_json("p") == {"items": [1, 2]}


def test_repair_garbage_raises_value_error(raw):
    import src.deepseek_client as dc
    raw["raw"] = "I could not produce JSON, sorry."
    with pytest.raises(ValueError):
        dc.generate_json("p")
