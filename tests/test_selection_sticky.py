"""A manual version selection must survive later regens (add_asset_version).

Requirement: page generation must keep using the version the user picked, so a
regen (which appends a new version) must NOT auto-hijack the selection."""
import src.core.store as store


def test_add_version_autoselects_until_user_picks():
    b = "stickyb"
    v1 = store.add_asset_version(b, "character", "X", "u1", image_hash="h1", storage_key="k1")
    assert store.get_selected_version(b, "character", "X")["id"] == v1  # default: newest
    v2 = store.add_asset_version(b, "character", "X", "u2", image_hash="h2", storage_key="k2")
    assert store.get_selected_version(b, "character", "X")["id"] == v2  # still auto-newest


def test_manual_select_sticks_across_later_add():
    b = "stickyb2"
    v1 = store.add_asset_version(b, "character", "X", "u1", image_hash="h1", storage_key="k1")
    v2 = store.add_asset_version(b, "character", "X", "u2", image_hash="h2", storage_key="k2")
    assert store.set_selected_version(b, "character", "X", v1) is True   # user picks the OLD one
    store.add_asset_version(b, "character", "X", "u3", image_hash="h3", storage_key="k3")  # a later regen
    assert store.get_selected_version(b, "character", "X")["id"] == v1, "manual pick must stick"
