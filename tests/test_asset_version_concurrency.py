"""asset version writes must not lose updates under concurrency.

Root cause of the "history vanishes on refresh" bug: `assets.json` is ONE shared
GCS blob per book, and add_asset_version / set_selected_version did an UNLOCKED
read-modify-write. The editor fires many concurrent /versions requests on load;
each empty asset's `_backfill_versions` writes the shared blob. Concurrent writes
raced -> a writer that read a slightly-older snapshot overwrote a freshly-added
version (old/stable versions survived; new ones were clobbered).

Fix: `_mutate_json` uses GCS optimistic concurrency (if_generation_match) and
retries on a concurrent write, so no update is ever lost.

TDD: RED before `_mutate_json` exists / add_asset_version is atomic; GREEN after.
"""

from __future__ import annotations

import src.core.store as store


def test_mutate_json_no_lost_update_on_interleaved_write():
    """A writer whose write is preceded by a concurrent write to the SAME blob
    must retry and preserve BOTH updates, not clobber the other."""
    key = "raceb/assets.json"
    saw_snapshots: list[dict] = []

    def writer_a(obj: dict):
        saw_snapshots.append(dict(obj))
        # On A's FIRST read (empty), a concurrent writer B commits first, so A's
        # write hits a stale generation and must retry.
        if len(saw_snapshots) == 1:
            store._mutate_json(key, lambda o: o.__setitem__("B", {"v": "b"}))
        obj["A"] = {"v": "a"}

    store._mutate_json(key, writer_a)

    final = store.get_json(key)
    assert final == {"B": {"v": "b"}, "A": {"v": "a"}}, final
    assert len(saw_snapshots) == 2, "A must re-read + retry after B's interleaved write"


def test_add_asset_version_retries_on_concurrent_write(monkeypatch):
    """add_asset_version must go through the optimistic-concurrency path: if a
    concurrent write bumps the blob generation mid-flight (PreconditionFailed on
    the first upload), it re-reads and retries — preserving BOTH the concurrent
    write and its own, instead of clobbering."""
    from google.api_core.exceptions import PreconditionFailed

    book = "raceb2"
    store.add_asset_version(book, "scene", "Y", "urlY", image_hash="hy", storage_key="ky")

    b = store._bucket()
    orig_blob = b.blob
    fail = {"armed": True}

    class _Wrapped:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, n):
            return getattr(self._inner, n)

        def upload_from_string(self, data, **kw):
            if fail["armed"] and self._inner._k.endswith("assets.json"):
                fail["armed"] = False
                raise PreconditionFailed("simulated concurrent write")
            return self._inner.upload_from_string(data, **kw)

    monkeypatch.setattr(b, "blob", lambda k: _Wrapped(orig_blob(k)))

    store.add_asset_version(book, "character", "X", "urlX", image_hash="hx", storage_key="kx")

    assert fail["armed"] is False, "the precondition-failure/retry path was not exercised"
    assets = store._load_assets(book)
    assert "character:X" in assets, f"lost character:X after retry; keys={list(assets)}"
    assert "scene:Y" in assets, f"lost scene:Y; keys={list(assets)}"
