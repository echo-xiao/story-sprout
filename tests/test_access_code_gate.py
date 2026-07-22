import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.access_gate import AccessCodeMiddleware


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("src.config.ACCESS_CODE", "secret", raising=False)
    app = FastAPI()
    app.add_middleware(AccessCodeMiddleware)

    @app.post("/api/book/b/segment/1/regenerate")
    def regen():
        return {"ok": True}

    @app.get("/api/book/b/segment/1/regenerate")
    def read():
        return {"ok": True}

    @app.post("/api/other")
    def other():
        return {"ok": True}

    return TestClient(app)


def test_gen_without_code_is_403(client):
    assert client.post("/api/book/b/segment/1/regenerate").status_code == 403


def test_gen_with_wrong_code_is_403(client):
    r = client.post("/api/book/b/segment/1/regenerate", headers={"x-access-code": "nope"})
    assert r.status_code == 403


def test_gen_with_correct_code_passes(client):
    r = client.post("/api/book/b/segment/1/regenerate", headers={"x-access-code": "secret"})
    assert r.status_code == 200


def test_read_is_not_gated(client):
    assert client.get("/api/book/b/segment/1/regenerate").status_code == 200


def test_non_generation_post_not_gated(client):
    assert client.post("/api/other").status_code == 200


def test_empty_access_code_disables_gate(monkeypatch):
    monkeypatch.setattr("src.config.ACCESS_CODE", "", raising=False)
    app = FastAPI()
    app.add_middleware(AccessCodeMiddleware)

    @app.post("/api/x/generate")
    def gen():
        return {"ok": True}

    assert TestClient(app).post("/api/x/generate").status_code == 200
