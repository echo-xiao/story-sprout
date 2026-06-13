"""Feedback email-to-owner is best-effort and gated on SMTP config.

The user's submission must ALWAYS succeed: a missing SMTP config (local dev, or
before the owner adds a Gmail App Password) or a mail-server failure must never
break POST /api/feedback. The note is persisted to MongoDB/file regardless; the
email is an additive notification.
"""

from __future__ import annotations

import src.routes.books as books


def test_email_is_noop_without_any_config(monkeypatch):
    for v in ("SMTP_USER", "SMTP_PASSWORD", "RESEND_API_KEY", "FEEDBACK_EMAIL_TO"):
        monkeypatch.delenv(v, raising=False)
    sent = {"called": False}

    def _boom(*a, **k):
        sent["called"] = True
        raise AssertionError("no sender must be touched when unconfigured")

    monkeypatch.setattr("smtplib.SMTP", _boom)
    monkeypatch.setattr("httpx.post", _boom)
    # No recipient + no provider → clean no-op.
    books._email_feedback_to_owner("hi", "u@x.com", "/editor")
    assert sent["called"] is False


def test_resend_preferred_when_key_set(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("FEEDBACK_EMAIL_TO", "me@gmail.com")
    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

    def _post(url, headers=None, json=None, timeout=0):
        captured.update(url=url, auth=headers["Authorization"], to=json["to"], reply=json.get("reply_to"))
        return _Resp()

    monkeypatch.setattr("httpx.post", _post)
    books._email_feedback_to_owner("hi", "user@x.com", "/editor")

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_test"
    assert captured["to"] == ["me@gmail.com"]
    assert captured["reply"] == "user@x.com"


def test_submit_feedback_succeeds_even_if_email_raises(client, monkeypatch):
    # Feedback persists; the email task is scheduled but a failure inside it
    # must not affect the user's 200.
    monkeypatch.setattr("src.core.db.save_feedback", lambda *a, **k: True)
    monkeypatch.setattr(books, "_email_feedback_to_owner",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp down")))
    resp = client.post("/api/feedback", json={"message": "great app", "email": "u@x.com"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "received"}


def test_email_sends_when_configured(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "owner@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    captured = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=0):
            captured["host"], captured["port"] = host, port
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self):
            captured["tls"] = True
        def login(self, u, p):
            captured["login"] = (u, p)
        def send_message(self, m):
            captured["to"] = m["To"]
            captured["reply_to"] = m["Reply-To"]

    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    books._email_feedback_to_owner("hello", "user@x.com", "/book/1")

    assert captured["host"] == "smtp.gmail.com"
    assert captured["login"] == ("owner@gmail.com", "app-password")
    assert captured["to"] == "owner@gmail.com"          # defaults to the sender
    assert captured["reply_to"] == "user@x.com"          # owner can reply to the user
    assert captured["tls"] is True
