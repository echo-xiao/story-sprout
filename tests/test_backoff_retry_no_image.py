"""The shared Gemini retry must also retry a 200 response that carried NO image.

gemini-3-pro-image intermittently returns a 200 with a text-only (no-image)
response. Every image path funnels through call_gemini_with_backoff whose
`fn()` returns save_inline_image's path — or "" when the response had no image.
Previously "" was returned straight through with NO retry, so a single flaky
no-image response silently failed: a page in "gen all" failed, a scene regen
restored the old image ("点重画没反应"), a character sheet didn't save.

This makes an empty/falsy return retriable, like a transient error.
"""

from __future__ import annotations

import src.gemini_backend as gb


def test_retries_on_no_image_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "" if calls["n"] < 3 else "final.png"

    # base=0.0 => no real sleeps in the test
    result = gb.call_gemini_with_backoff(fn, max_retries=5, base=0.0, label="t")
    assert result == "final.png"
    assert calls["n"] == 3, "must retry the no-image responses until an image comes back"


def test_returns_empty_after_exhausting_no_image_retries():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return ""

    result = gb.call_gemini_with_backoff(fn, max_retries=3, base=0.0, label="t")
    assert result == "", "gives up gracefully (empty) after retries, not an exception"
    assert calls["n"] == 3, "must try exactly max_retries times"


def test_truthy_result_returns_immediately_without_extra_calls():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "got.png"

    result = gb.call_gemini_with_backoff(fn, max_retries=3, base=0.0)
    assert result == "got.png"
    assert calls["n"] == 1, "a good image must not trigger any retry"
