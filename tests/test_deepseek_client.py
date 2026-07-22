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
