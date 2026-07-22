# StorySprout 精简版 · Plan 1：后端地基（纯新增）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入三块**纯新增**的后端地基——DeepSeek 文字客户端、GCS-JSON 数据层 `store.py`、config 增项——不改动任何现有行为，跑完全绿。

**Architecture:** 全部是新文件 + config 的追加项。现有 `llm_client.py`（Gemini 文字）、`db.py`（Mongo）、`storage.py` 一律**不动**——真正的替换与删除留给 Plan 2。这样 Plan 1 结束时旧测试一个不坏、新模块各自有单测。

**Tech Stack:** Python 3.11 · FastAPI（本 plan 不碰路由）· httpx（复用，调 DeepSeek，不引 openai SDK）· google-cloud-storage（GCS-JSON）· pytest。

## Global Constraints

- 文字引擎 = DeepSeek，模型 `deepseek-chat`，OpenAI 兼容 `POST {base}/chat/completions`，JSON 模式 `response_format={"type":"json_object"}`。
- 图片模型默认 = `gemini-3-pro-image`（Nano Banana Pro，GA）。
- 数据层 = **GCS 里的 JSON blob**，无 MongoDB、无 Firestore、无 Redis。
- GCS 鉴权：`GCS_SA_JSON`（服务账号 JSON 字符串）存在则用 `from_service_account_info`，否则退回 ADC（本地）。
- 门禁口令环境变量 `ACCESS_CODE`，默认 `Caput Draconis`。
- Plan 1 **不删除、不修改**任何现有模块/测试；只新增文件与 config 追加项。
- 每个新模块必须能在**不联真外部服务**下测试（httpx 用 monkeypatch，GCS 用内存假桶）。

---

### Task 1: config 增项（追加，不删旧）

**Files:**
- Modify: `src/config.py`（在文件末尾追加新块；保留现有所有变量不动）
- Test: `tests/test_config_additions.py`

**Interfaces:**
- Produces: `src.config.DEEPSEEK_API_KEY: str`、`DEEPSEEK_BASE_URL: str`、`DEEPSEEK_MODEL: str`、`ACCESS_CODE: str`、`GCS_SA_JSON: str`、`GEMINI_VISION_MODEL: str`；并把 `GEMINI_IMAGE_MODEL` 默认值改为 `"gemini-3-pro-image"`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config_additions.py
import importlib


def test_new_config_defaults(monkeypatch):
    # Clear so defaults apply
    for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
              "ACCESS_CODE", "GCS_SA_JSON", "GEMINI_IMAGE_MODEL", "GEMINI_VISION_MODEL"):
        monkeypatch.delenv(k, raising=False)
    import src.config as cfg
    importlib.reload(cfg)
    assert cfg.DEEPSEEK_BASE_URL == "https://api.deepseek.com"
    assert cfg.DEEPSEEK_MODEL == "deepseek-chat"
    assert cfg.ACCESS_CODE == "Caput Draconis"
    assert cfg.GEMINI_IMAGE_MODEL == "gemini-3-pro-image"
    assert cfg.GEMINI_VISION_MODEL  # non-empty
    assert cfg.GCS_SA_JSON == ""


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", "Alohomora")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
    import src.config as cfg
    importlib.reload(cfg)
    assert cfg.ACCESS_CODE == "Alohomora"
    assert cfg.DEEPSEEK_MODEL == "deepseek-reasoner"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_config_additions.py -v`
Expected: FAIL（`AttributeError: module 'src.config' has no attribute 'DEEPSEEK_BASE_URL'`）

- [ ] **Step 3: 在 `src/config.py` 末尾追加新块**

```python
# ── DeepSeek (text engine) ─────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── Access gate (single shared passcode) ───────────────────────────────────
ACCESS_CODE = os.getenv("ACCESS_CODE", "Caput Draconis")

# ── GCS auth on Vercel (no ambient GCP identity) ───────────────────────────
# Service-account JSON as a string; empty -> fall back to ADC (local dev).
GCS_SA_JSON = os.getenv("GCS_SA_JSON", "")

# Gemini vision model used ONLY by Vision QA (text gen now goes to DeepSeek).
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", GEMINI_MODEL)
```

同一步，把现有这行的默认值改掉（仅默认值，键名不变）：

```python
# 原：GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3-pro-image")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_config_additions.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 确认没弄坏别的**

Run: `python -m pytest tests/test_gemini_backoff.py tests/test_llm_json_repair.py -q`
Expected: PASS（现有测试不受影响）

- [ ] **Step 6: 提交**

```bash
git add src/config.py tests/test_config_additions.py
git commit -m "config: add DeepSeek/ACCESS_CODE/GCS_SA_JSON, default image model gemini-3-pro-image"
```

---

### Task 2: DeepSeek 文字客户端（新文件）

**Files:**
- Create: `src/deepseek_client.py`
- Test: `tests/test_deepseek_client.py`

**Interfaces:**
- Consumes: `src.config.DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL`（Task 1）。
- Produces: `generate_json(prompt: str, system: str = "", max_retries: int = 3) -> dict` —— 调 DeepSeek 的 JSON 模式，带与旧 `llm_client` 同款的 JSON 修复兜底。Plan 2 会把 `llm_client.generate_json` 改成转调这里。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_deepseek_client.py
import json
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_deepseek_client.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.deepseek_client'`）

- [ ] **Step 3: 写实现**

```python
# src/deepseek_client.py
"""DeepSeek text client — OpenAI-compatible /chat/completions in JSON mode.

The single door for all TEXT generation (analysis, writing, simplification).
Uses httpx (already a dependency) instead of the openai SDK to keep the Vercel
function bundle small. Returns parsed JSON, with the same repair fallbacks the
old Gemini llm_client had.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _call_deepseek(prompt: str, system: str = "", timeout: float = 120.0) -> str:
    from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not set.")
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = httpx.post(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        json={
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    if not content:
        raise ValueError("DeepSeek returned empty content (blocked or truncated).")
    return content


def _extract_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        for cand in (m.group(0), re.sub(r",\s*([}\]])", r"\1", m.group(0))):
            try:
                return json.loads(cand)
            except json.JSONDecodeError:
                continue
    logger.error("DeepSeek returned invalid JSON. Raw: %s", raw[:1000])
    raise ValueError("LLM returned invalid JSON")


def generate_json(prompt: str, system: str = "", max_retries: int = 3) -> dict[str, Any]:
    """Generate JSON from DeepSeek. Retries transient HTTP errors; raises on
    a persistent failure (same contract as the old Gemini llm_client)."""
    last: Exception | None = None
    for attempt in range(max(1, max_retries)):
        try:
            return _extract_json(_call_deepseek(prompt, system))
        except ValueError:
            raise  # bad key / unparseable JSON — retrying won't help
        except httpx.HTTPError as e:
            last = e
            logger.warning("DeepSeek attempt %d/%d failed: %s", attempt + 1, max_retries, e)
    raise last or ValueError("DeepSeek generate_json failed")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_deepseek_client.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/deepseek_client.py tests/test_deepseek_client.py
git commit -m "feat: DeepSeek text client (httpx, JSON mode + repair fallback)"
```

---

### Task 3: GCS-JSON store 原语（新文件）

**Files:**
- Create: `src/core/store.py`
- Test: `tests/test_store_primitives.py`

**Interfaces:**
- Consumes: `src.config.GCS_BUCKET / GCS_SA_JSON`。
- Produces: `get_json(key: str) -> Any | None`、`put_json(key: str, data: Any) -> None`、模块级 `_bucket()`（测试通过 monkeypatch 换成内存假桶）。

- [ ] **Step 1: 写失败测试（含可复用的内存假桶）**

```python
# tests/test_store_primitives.py
import json
import pytest


class FakeBlob:
    def __init__(self, store, key):
        self._store, self._key = store, key

    def exists(self):
        return self._key in self._store

    def download_as_text(self):
        return self._store[self._key]

    def upload_from_string(self, data, content_type="application/json"):
        self._store[self._key] = data


class FakeBucket:
    def __init__(self):
        self._store = {}

    def blob(self, key):
        return FakeBlob(self._store, key)


@pytest.fixture
def fake_store(monkeypatch):
    bucket = FakeBucket()
    import src.core.store as store
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    return store


def test_put_then_get_roundtrip(fake_store):
    fake_store.put_json("book1/meta.json", {"title": "Gatsby", "n": 3})
    assert fake_store.get_json("book1/meta.json") == {"title": "Gatsby", "n": 3}


def test_get_missing_returns_none(fake_store):
    assert fake_store.get_json("nope/x.json") is None


def test_put_json_is_utf8_not_ascii_escaped(fake_store):
    fake_store.put_json("b/c.json", {"name": "李雷"})
    # stored raw should keep the CJK char, not \uXXXX
    raw = fake_store._bucket()._store["b/c.json"]
    assert "李雷" in raw
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_store_primitives.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.core.store'`）

- [ ] **Step 3: 写实现**

```python
# src/core/store.py
"""GCS-JSON store — the single data layer (replaces MongoDB src/core/db.py).

Every piece of book state (metadata, characters, segments, chapters, asset
version pointers) is a JSON object in the GCS bucket, under the book_id prefix.
Read = download+parse one object; write = overwrite one object. No database.

Auth: GCS_SA_JSON (service-account JSON string) -> from_service_account_info
(Vercel has no ambient GCP identity); empty -> ADC (local dev). In tests,
monkeypatch `_bucket` to an in-memory fake.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()


def _bucket():
    """Return the GCS bucket handle. Raises if GCS_BUCKET is unset (the store
    has no local fallback — GCS is the single source of truth)."""
    global _client
    from src.config import GCS_BUCKET, GCS_SA_JSON

    if not GCS_BUCKET:
        raise RuntimeError("GCS_BUCKET is not set — the JSON store requires it.")
    with _lock:
        if _client is None:
            from google.cloud import storage
            if GCS_SA_JSON:
                from google.oauth2 import service_account
                info = json.loads(GCS_SA_JSON)
                creds = service_account.Credentials.from_service_account_info(info)
                _client = storage.Client(project=info.get("project_id"), credentials=creds)
            else:
                _client = storage.Client()
    return _client.bucket(GCS_BUCKET)


def get_json(key: str) -> Optional[Any]:
    blob = _bucket().blob(key)
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())


def put_json(key: str, data: Any) -> None:
    _bucket().blob(key).upload_from_string(
        json.dumps(data, ensure_ascii=False),
        content_type="application/json",
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_store_primitives.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/core/store.py tests/test_store_primitives.py
git commit -m "feat: GCS-JSON store primitives (get_json/put_json, SA-json auth)"
```

---

### Task 4: store 数据层助手（书 / 角色 / 段 / 章 / preprocess 文件）

**Files:**
- Modify: `src/core/store.py`（追加助手函数）
- Test: `tests/test_store_data.py`

**Interfaces:**
- Consumes: `get_json` / `put_json`（Task 3）。
- Produces（键约定见 spec §4）：
  - `save_book(book_id, title, num_chapters, **extra) -> None` / `get_book(book_id) -> dict | None`
  - `save_characters(book_id, characters: list[dict]) -> None` / `get_characters(book_id) -> list[dict]`
  - `update_character(book_id, canonical_name, updates: dict) -> bool`
  - `save_chapter(book_id, chapter_idx, chapter_doc: dict) -> None` / `get_chapter(book_id, chapter_idx) -> dict | None`
  - `save_preprocess_file(book_id, filename, data) -> None` / `load_preprocess_file(book_id, filename) -> Any | None`
  - `list_books() -> list[dict]`（扫 `*/meta.json`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_store_data.py
from tests.test_store_primitives import FakeBucket  # reuse the in-memory fake
import pytest


@pytest.fixture
def store(monkeypatch):
    bucket = FakeBucket()
    import src.core.store as store
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    # list_books scans all meta.json — give the fake a name-prefix lister
    def list_prefix(suffix=""):
        return [k for k in bucket._store if k.endswith(suffix)]
    monkeypatch.setattr(store, "_list_keys", list_prefix, raising=False)
    return store


def test_book_roundtrip(store):
    store.save_book("b1", "Gatsby", 3, alias_map={"J": "Jay"})
    doc = store.get_book("b1")
    assert doc["title"] == "Gatsby" and doc["num_chapters"] == 3
    assert doc["alias_map"] == {"J": "Jay"}


def test_characters_and_update(store):
    store.save_characters("b1", [{"canonical_name": "Jay", "appearance": "suit"}])
    assert store.get_characters("b1")[0]["canonical_name"] == "Jay"
    assert store.update_character("b1", "Jay", {"appearance": "pink suit"}) is True
    assert store.get_characters("b1")[0]["appearance"] == "pink suit"
    assert store.update_character("b1", "Nobody", {"x": 1}) is False


def test_get_characters_empty_is_list(store):
    assert store.get_characters("missing") == []


def test_chapter_roundtrip(store):
    store.save_chapter("b1", 0, {"title": "Ch1", "pages": [{"page": 1}]})
    assert store.get_chapter("b1", 0)["pages"] == [{"page": 1}]


def test_preprocess_file_roundtrip(store):
    store.save_preprocess_file("b1", "analysis.json", {"segments": []})
    assert store.load_preprocess_file("b1", "analysis.json") == {"segments": []}


def test_list_books(store):
    store.save_book("b1", "A", 1)
    store.save_book("b2", "B", 2)
    titles = sorted(b["title"] for b in store.list_books())
    assert titles == ["A", "B"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_store_data.py -v`
Expected: FAIL（`AttributeError: module 'src.core.store' has no attribute 'save_book'`）

- [ ] **Step 3: 追加实现到 `src/core/store.py`**

```python
# ── list helper (overridable in tests) ─────────────────────────────────────
def _list_keys(suffix: str = "") -> list[str]:
    return [b.name for b in _bucket().list_blobs() if b.name.endswith(suffix)]


# ── Books ──────────────────────────────────────────────────────────────────
def save_book(book_id: str, title: str, num_chapters: int, **extra) -> None:
    put_json(f"{book_id}/meta.json",
             {"book_id": book_id, "title": title, "num_chapters": num_chapters, **extra})


def get_book(book_id: str) -> Optional[dict]:
    return get_json(f"{book_id}/meta.json")


def list_books() -> list[dict]:
    out = []
    for key in _list_keys("/meta.json"):
        doc = get_json(key)
        if doc:
            out.append({"book_id": doc.get("book_id", key.split("/")[0]),
                        "title": doc.get("title", ""),
                        "num_chapters": doc.get("num_chapters", 0)})
    return out


# ── Characters ─────────────────────────────────────────────────────────────
def save_characters(book_id: str, characters: list[dict]) -> None:
    put_json(f"{book_id}/characters.json", characters)


def get_characters(book_id: str) -> list[dict]:
    return get_json(f"{book_id}/characters.json") or []


def update_character(book_id: str, canonical_name: str, updates: dict) -> bool:
    chars = get_characters(book_id)
    for c in chars:
        if c.get("canonical_name") == canonical_name:
            c.update(updates)
            save_characters(book_id, chars)
            return True
    return False


# ── Chapters ───────────────────────────────────────────────────────────────
def save_chapter(book_id: str, chapter_idx: int, chapter_doc: dict) -> None:
    put_json(f"{book_id}/chapters/{chapter_idx}.json", {"chapter": chapter_idx, **chapter_doc})


def get_chapter(book_id: str, chapter_idx: int) -> Optional[dict]:
    return get_json(f"{book_id}/chapters/{chapter_idx}.json")


# ── Preprocess JSON files (analysis.json, meta.json, ...) ───────────────────
def save_preprocess_file(book_id: str, filename: str, data: Any) -> None:
    put_json(f"{book_id}/preprocess/{filename}", data)


def load_preprocess_file(book_id: str, filename: str) -> Optional[Any]:
    return get_json(f"{book_id}/preprocess/{filename}")
```

> 注：测试用 `monkeypatch.setattr(store, "_list_keys", ...)` 覆盖真实 `_list_keys`，所以 `list_books` 在假桶下也能列出。真实实现用 `_bucket().list_blobs()`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_store_data.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/core/store.py tests/test_store_data.py
git commit -m "feat: store data helpers (books/characters/chapters/preprocess)"
```

---

### Task 5: store 图片版本助手（对齐旧 db.add_asset_version 语义）

**Files:**
- Modify: `src/core/store.py`（追加版本助手）
- Test: `tests/test_store_assets.py`

**Interfaces:**
- Consumes: `get_json` / `put_json`。
- Produces（键 `book_id/assets.json`，字典 `"{asset_type}:{asset_key}" -> {versions:[...], selected_version_id}`）：
  - `add_asset_version(book_id, asset_type, asset_key, url, image_hash=None, storage_key=None) -> str`（按 hash 去重、上限 12、追加即选中）
  - `set_selected_version(book_id, asset_type, asset_key, version_id) -> bool`
  - `get_selected_version(book_id, asset_type, asset_key) -> dict | None`
  - `list_asset_versions(book_id, asset_type, asset_key) -> dict`
  - `delete_asset_versions(book_id) -> None`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_store_assets.py
from tests.test_store_primitives import FakeBucket
import pytest


@pytest.fixture
def store(monkeypatch):
    bucket = FakeBucket()
    import src.core.store as store
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    return store


def test_add_selects_latest(store):
    v1 = store.add_asset_version("b", "page", "ch0:seg1", "url1", image_hash="h1", storage_key="k1")
    v2 = store.add_asset_version("b", "page", "ch0:seg1", "url2", image_hash="h2", storage_key="k2")
    assert v1 != v2
    assert store.get_selected_version("b", "page", "ch0:seg1")["url"] == "url2"


def test_dedupe_by_hash_reselects(store):
    v1 = store.add_asset_version("b", "page", "ch0:seg1", "url1", image_hash="h1")
    store.add_asset_version("b", "page", "ch0:seg1", "url2", image_hash="h2")
    again = store.add_asset_version("b", "page", "ch0:seg1", "url1b", image_hash="h1")
    assert again == v1  # same hash -> reuse the existing version id
    assert store.get_selected_version("b", "page", "ch0:seg1")["id"] == v1
    assert len(store.list_asset_versions("b", "page", "ch0:seg1")["versions"]) == 2


def test_set_selected_and_missing(store):
    v1 = store.add_asset_version("b", "page", "ch0:seg1", "url1", image_hash="h1")
    store.add_asset_version("b", "page", "ch0:seg1", "url2", image_hash="h2")
    assert store.set_selected_version("b", "page", "ch0:seg1", v1) is True
    assert store.get_selected_version("b", "page", "ch0:seg1")["id"] == v1
    assert store.set_selected_version("b", "page", "ch0:seg1", "nope") is False


def test_cap_keeps_last_12(store):
    for i in range(15):
        store.add_asset_version("b", "page", "k", f"u{i}", image_hash=f"h{i}")
    vs = store.list_asset_versions("b", "page", "k")["versions"]
    assert len(vs) == 12
    assert vs[-1]["url"] == "u14"


def test_delete_asset_versions(store):
    store.add_asset_version("b", "page", "k", "u", image_hash="h")
    store.delete_asset_versions("b")
    assert store.list_asset_versions("b", "page", "k") == {"versions": [], "selected_version_id": None}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_store_assets.py -v`
Expected: FAIL（`AttributeError: ... has no attribute 'add_asset_version'`）

- [ ] **Step 3: 追加实现到 `src/core/store.py`**

```python
import uuid
from datetime import datetime, timezone

_MAX_ASSET_VERSIONS = 12


def _assets_key(book_id: str) -> str:
    return f"{book_id}/assets.json"


def _load_assets(book_id: str) -> dict:
    return get_json(_assets_key(book_id)) or {}


def _rec_key(asset_type: str, asset_key: str) -> str:
    return f"{asset_type}:{asset_key}"


def add_asset_version(book_id: str, asset_type: str, asset_key: str, url: str,
                      image_hash: str | None = None,
                      storage_key: str | None = None) -> str:
    assets = _load_assets(book_id)
    k = _rec_key(asset_type, asset_key)
    rec = assets.get(k) or {"versions": [], "selected_version_id": None}
    versions = rec["versions"]

    if image_hash:
        for v in versions:
            if v.get("hash") == image_hash:
                rec["selected_version_id"] = v["id"]
                assets[k] = rec
                put_json(_assets_key(book_id), assets)
                return v["id"]

    vid = uuid.uuid4().hex[:12]
    versions.append({"id": vid, "url": url, "hash": image_hash,
                     "storage_key": storage_key,
                     "created_at": datetime.now(timezone.utc).isoformat()})
    if len(versions) > _MAX_ASSET_VERSIONS:
        rec["versions"] = versions[-_MAX_ASSET_VERSIONS:]
    rec["selected_version_id"] = vid
    assets[k] = rec
    put_json(_assets_key(book_id), assets)
    return vid


def set_selected_version(book_id: str, asset_type: str, asset_key: str,
                         version_id: str) -> bool:
    assets = _load_assets(book_id)
    rec = assets.get(_rec_key(asset_type, asset_key))
    if not rec or not any(v["id"] == version_id for v in rec["versions"]):
        return False
    rec["selected_version_id"] = version_id
    assets[_rec_key(asset_type, asset_key)] = rec
    put_json(_assets_key(book_id), assets)
    return True


def get_selected_version(book_id: str, asset_type: str, asset_key: str) -> Optional[dict]:
    rec = _load_assets(book_id).get(_rec_key(asset_type, asset_key))
    if not rec:
        return None
    sel = rec.get("selected_version_id")
    versions = rec.get("versions", [])
    return next((v for v in versions if v["id"] == sel), versions[-1] if versions else None)


def list_asset_versions(book_id: str, asset_type: str, asset_key: str) -> dict:
    rec = _load_assets(book_id).get(_rec_key(asset_type, asset_key))
    if not rec:
        return {"versions": [], "selected_version_id": None}
    return {"versions": rec.get("versions", []),
            "selected_version_id": rec.get("selected_version_id")}


def delete_asset_versions(book_id: str) -> None:
    put_json(_assets_key(book_id), {})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_store_assets.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 全量回归——确认 Plan 1 没弄坏任何现有测试**

Run: `python -m pytest -q`
Expected: 现有测试全 PASS + 新增 4 个测试文件 PASS（不得有新失败）

- [ ] **Step 6: 提交**

```bash
git add src/core/store.py tests/test_store_assets.py
git commit -m "feat: store asset-version helpers (hash dedupe, cap 12, select pointer)"
```

---

## Self-Review（against spec）

- **§1 DeepSeek 文字**：Task 2 建 `deepseek_client.generate_json`（Plan 2 再把 `llm_client` 转调它）。✅
- **§2 图片模型 gemini-3-pro-image**：Task 1 改默认值。✅
- **§4 GCS-JSON 数据模型**：Task 3–5 建 `store.py`（meta/characters/chapters/preprocess/assets 键约定与 spec §4 一致）。✅
- **§6 ACCESS_CODE**：Task 1 加 config（中间件在 Plan 2/3）。✅
- **§8 风险1 GCS SA 鉴权**：Task 3 `from_service_account_info` 走 `GCS_SA_JSON`。✅
- **纯新增、不破坏**：Task 5 Step 5 全量回归门确保旧测试不坏。✅
- 占位符扫描：无 TBD/TODO；每步含真实代码与命令。✅
- 类型一致性：`add_asset_version` 签名在 Task 5 定义并与测试一致；`generate_json` 签名 Task 2 与 Plan 2 消费方对齐。✅

**留给 Plan 2 的（不属于本 plan）**：把 `llm_client.generate_json` 转调 DeepSeek；routes/generation 从 `db`→`store`；`storage.record_image_version` 改用 `store.add_asset_version`（去掉 `from src.core.db import`）；删门禁/ADK/Mongo 模块与其测试；重写 `conftest.py`。
