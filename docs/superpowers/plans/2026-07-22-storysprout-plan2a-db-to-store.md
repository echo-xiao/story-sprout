# StorySprout 精简版 · Plan 2a：db→store 迁移（helpers + storage）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (this project executes inline). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `src/routes/helpers.py` 的 preprocess/角色读写和 `src/core/storage.py` 的版本注册，从 MongoDB（`db.py` + MCP 双写）切到 Plan 1 的 GCS-JSON `store.py`。所有对外函数签名不变，调用方零改动。

**Architecture:** 只改 `helpers.py`（3 个函数改内部实现 + 删 1 个）与 `storage.py`（1 行 import）。GCS-JSON（store）成为权威，本地 `GENERATED_DIR` 文件降为"同一次调用内的快取/回退"。`db.py` 本 plan **不删**（`books/editor/preprocessing/adk` 仍在用它的其他函数；Mongo 不可用时 `db.py` 本就优雅退化为 None）——`db.py` 到 Plan 2e 才删。

**Tech Stack:** Python · pytest · Plan 1 的 `src/core/store.py`。

## Global Constraints

- 数据权威 = GCS-JSON `store`；无 MongoDB、无 MCP、无 Redis。
- `_load_json` / `_save_json` / `load_characters` **签名保持不变**（~45 处调用方不动）。
- store 不可用（本地无 `GCS_BUCKET`）时，读写要**优雅回退到本地文件**，不得抛异常打断路由。
- 依赖 Plan 1 已合入（`store.load_preprocess_file` / `save_preprocess_file` / `get_characters` / `add_asset_version` 存在）。

---

### Task 1: `_load_json` 改走 store（并删 `heal_if_local_fresher`）

**Files:**
- Modify: `src/routes/helpers.py`（重写 `_load_json`；删 `heal_if_local_fresher`）
- Test: `tests/test_helpers_store_migration.py`

**Interfaces:**
- Consumes: `store.load_preprocess_file(book_id, filename)`（Plan 1 Task 4）。
- Produces: `_load_json(book_id, filename, prefetched=None) -> dict|list|None`（签名不变；`prefetched` 保留但忽略）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_helpers_store_migration.py
import json
import pytest
from tests.test_store_primitives import FakeBucket


@pytest.fixture
def wired(monkeypatch, tmp_path):
    import src.core.store as store
    import src.routes.helpers as helpers
    bucket = FakeBucket()
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    monkeypatch.setattr(helpers, "GENERATED_DIR", tmp_path)
    return helpers, store, tmp_path


def test_load_json_reads_from_store(wired):
    helpers, store, _ = wired
    store.save_preprocess_file("b1", "analysis.json", {"segments": [1, 2]})
    assert helpers._load_json("b1", "analysis.json") == {"segments": [1, 2]}


def test_load_json_falls_back_to_local_file(wired):
    helpers, store, tmp = wired
    p = tmp / "b1" / "preprocess" / "meta.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"title": "Local"}), encoding="utf-8")
    assert helpers._load_json("b1", "meta.json") == {"title": "Local"}


def test_load_json_missing_returns_none(wired):
    helpers, *_ = wired
    assert helpers._load_json("b1", "nope.json") is None


def test_load_json_ignores_prefetched(wired):
    helpers, store, _ = wired
    store.save_preprocess_file("b1", "x.json", {"from": "store"})
    assert helpers._load_json("b1", "x.json", prefetched={"from": "mcp"}) == {"from": "store"}


def test_heal_if_local_fresher_removed():
    import src.routes.helpers as helpers
    assert not hasattr(helpers, "heal_if_local_fresher")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_helpers_store_migration.py -v`
Expected: FAIL（`test_load_json_reads_from_store` 读到的是 Mongo 路径 / `test_heal_if_local_fresher_removed` 因函数仍在而 FAIL）

- [ ] **Step 3: 重写 `_load_json`，删 `heal_if_local_fresher`**

把 `src/routes/helpers.py` 里的 `heal_if_local_fresher(...)` 整个函数**删除**，并把 `_load_json` 整体替换为：

```python
def _load_json(book_id: str, filename: str, prefetched=None) -> dict | list | None:
    """THE single accessor for a preprocess file. The GCS-JSON store is the
    authority; a local file under GENERATED_DIR is a same-invocation fast path
    and offline fallback.

    `prefetched` is accepted for backward compatibility (the old MongoDB-MCP
    batch-read path) and IGNORED — MCP is gone.
    """
    from src.core import store
    try:
        data = store.load_preprocess_file(book_id, filename)
    except Exception as e:  # store unconfigured (no GCS) or transient — fall back
        logger.debug("store load failed for %s/%s: %s", book_id, filename, e)
        data = None
    if data is not None:
        return data
    path = GENERATED_DIR / book_id / "preprocess" / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_helpers_store_migration.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add src/routes/helpers.py tests/test_helpers_store_migration.py
git commit -m "refactor(helpers): _load_json reads GCS-JSON store; drop Mongo heal/MCP"
```

---

### Task 2: `_save_json` 改走 store（保留本地写，删 Mongo 双写）

**Files:**
- Modify: `src/routes/helpers.py`（重写 `_save_json`）
- Test: `tests/test_helpers_store_migration.py`（追加）

**Interfaces:**
- Consumes: `store.save_preprocess_file(book_id, filename, data)`。
- Produces: `_save_json(book_id, filename, data) -> None`（签名不变）。

- [ ] **Step 1: 追加失败测试**

```python
def test_save_json_writes_store_and_local(wired):
    helpers, store, tmp = wired
    helpers._save_json("b1", "meta.json", {"title": "Both"})
    assert store.load_preprocess_file("b1", "meta.json") == {"title": "Both"}
    local = tmp / "b1" / "preprocess" / "meta.json"
    assert json.loads(local.read_text(encoding="utf-8")) == {"title": "Both"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_helpers_store_migration.py::test_save_json_writes_store_and_local -v`
Expected: FAIL（当前 `_save_json` 走 Mongo，假桶里查不到 `b1/preprocess/meta.json`）

- [ ] **Step 3: 重写 `_save_json`**

把 `src/routes/helpers.py` 里的 `_save_json` 整体替换为：

```python
def _save_json(book_id: str, filename: str, data: Any) -> None:
    """Persist a preprocess file to the GCS-JSON store (authority) and a local
    GENERATED_DIR copy (same-invocation fast path for the generators / PDF)."""
    from src.core import store
    try:
        store.save_preprocess_file(book_id, filename, data)
    except Exception as e:
        logger.warning("store save failed for %s/%s: %s", book_id, filename, e)
    path = GENERATED_DIR / book_id / "preprocess" / filename
    lock = _get_lock(f"{book_id}/{filename}")
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_helpers_store_migration.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/routes/helpers.py tests/test_helpers_store_migration.py
git commit -m "refactor(helpers): _save_json writes store + local; drop Mongo dual-write"
```

---

### Task 3: `load_characters` 改走 store（文件兜底保留）

**Files:**
- Modify: `src/routes/helpers.py`（重写 `load_characters`）
- Test: `tests/test_helpers_store_migration.py`（追加）

**Interfaces:**
- Consumes: `store.get_characters(book_id)`、`_load_json`（文件兜底）。
- Produces: `load_characters(book_id) -> list[dict]`（签名不变；`load_character_profiles` 不动，它转调本函数）。

- [ ] **Step 1: 追加失败测试**

```python
def test_load_characters_from_store(wired):
    helpers, store, _ = wired
    store.save_characters("b1", [{"canonical_name": "Jay"}])
    assert helpers.load_characters("b1")[0]["canonical_name"] == "Jay"


def test_load_characters_file_fallback(wired):
    helpers, store, _ = wired
    # No characters.json -> store.get_characters returns []; fall back to the
    # llm_characters.json preprocess file (also served through the store here).
    store.save_preprocess_file("b1", "llm_characters.json",
                               {"characters": [{"canonical_name": "Nick"}]})
    assert helpers.load_characters("b1")[0]["canonical_name"] == "Nick"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_helpers_store_migration.py -k load_characters -v`
Expected: FAIL（当前 `load_characters` 走 `db.get_characters`）

- [ ] **Step 3: 重写 `load_characters`**

把 `src/routes/helpers.py` 里的 `load_characters` 整体替换为：

```python
def load_characters(book_id: str) -> list[dict]:
    """Character profiles — the GCS-JSON store's characters.json is the single
    source of truth; the preprocess llm_characters.json is a last-resort
    fallback (a failed re-preprocess can leave it blank, so never primary)."""
    from src.core import store
    try:
        chars = store.get_characters(book_id)
        if chars:
            return chars
    except Exception as e:
        logger.debug("store.get_characters failed for %s: %s", book_id, e)
    data = _load_json(book_id, "llm_characters.json")
    return data.get("characters", []) if isinstance(data, dict) else []
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_helpers_store_migration.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add src/routes/helpers.py tests/test_helpers_store_migration.py
git commit -m "refactor(helpers): load_characters reads store; keep file fallback"
```

---

### Task 4: `storage.record_image_version` 改用 store 的版本注册

**Files:**
- Modify: `src/core/storage.py`（第 184 行 `from src.core.db import add_asset_version` → store）
- Test: `tests/test_record_image_version_store.py`

**Interfaces:**
- Consumes: `store.add_asset_version(book_id, asset_type, asset_key, url, image_hash=, storage_key=)`（Plan 1 Task 5）。
- Produces: `record_image_version(book_id, asset_type, asset_key, data, content_type="image/png") -> str`（签名不变）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_record_image_version_store.py
import pytest
from tests.test_store_primitives import FakeBucket


def test_record_image_version_registers_in_store(monkeypatch, tmp_path):
    import src.core.store as store
    import src.core.storage as storage
    bucket = FakeBucket()
    monkeypatch.setattr(store, "_bucket", lambda: bucket)
    # Force storage.put_image down its LOCAL path (no real GCS in the test).
    monkeypatch.setattr(storage, "GCS_BUCKET", "")
    monkeypatch.setattr(storage, "GENERATED_DIR", tmp_path)

    url = storage.record_image_version("b1", "page", "ch0:seg1",
                                       b"\x89PNG\r\n\x1a\n", content_type="image/png")
    assert url.startswith("/static/")
    sel = store.get_selected_version("b1", "page", "ch0:seg1")
    assert sel is not None
    assert sel["storage_key"].startswith("b1/pages/")
    assert sel["url"] == url
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_record_image_version_store.py -v`
Expected: FAIL（当前从 `db.add_asset_version`；Mongo 不可用 → 假桶里查不到版本，`get_selected_version` 返回 None）

- [ ] **Step 3: 改 import 来源**

在 `src/core/storage.py` 的 `record_image_version` 里，把这一行：

```python
    from src.core.db import add_asset_version
```

改为：

```python
    from src.core.store import add_asset_version
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_record_image_version_store.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 全量回归门**

Run: `python -m pytest -q`
Expected: 新增/迁移测试 PASS；**现有测试里凡直接断言"Mongo 双写/heal"的会开始失败——这些属于 Plan 2e 的删除/迁移清单**（`test_load_json_freshness` / `test_load_preprocess_freshness` / `test_mcp_hub_sync` / `test_character_single_source` 等）。记录下失败清单，确认都在 2e 清单内，不得有"计划外"失败。

- [ ] **Step 6: 提交**

```bash
git add src/core/storage.py tests/test_record_image_version_store.py
git commit -m "refactor(storage): record_image_version registers via store, not db"
```

---

## Self-Review（against spec §7 + 迁移安全）

- **spec §7「保留但大改」helpers/storage 去 Mongo**：Task 1–4 覆盖 `_load_json`/`_save_json`/`load_characters`/`record_image_version` + 删 `heal_if_local_fresher`。✅
- **签名不变、调用方零改**：三函数签名保留（`prefetched` 保留但忽略）；`record_image_version` 签名不变。✅
- **优雅回退**：store 异常 → try/except → 本地文件，不打断路由。✅
- **`db.py` 不在本 plan 删**：`books/editor/preprocessing/adk` 的其他 db 用法留给 2c/2e；`db.py` 在 Mongo 不可用时退化为 None，过渡期可跑。✅
- 占位符扫描：无。类型一致：`add_asset_version` 关键字参数与 Plan 1 Task 5 定义一致。✅
- **已知过渡态**：Task 4 Step 5 明确——本 plan 后，测"Mongo 双写/freshness/MCP"的旧测试会红，它们在 **Plan 2e 删除/迁移清单**内；执行 2e 前不修它们（避免给要删的代码补丁）。

**留给后续**：2b（`/tmp` 物化）、2c（逐页端点 + QA 合并 + stale 时间戳 + 删 ADK）、2d（门禁替换）、2e（`llm_client`→DeepSeek + 删 `db.py`/`mcp_client.py` + 测试大清理 + 重写 `conftest`）。
