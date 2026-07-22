"""框架性修复回归：

1. style 锚【单一来源】—— 整章 pages / 特殊页（chapter cover / back cover）都走
   `get_style_ref`（用户上传的 style reference 优先，没有才退 book cover），不再各自
   用 `_find_book_cover`（只认 book cover，无视上传）。这样单页 regen / 角色 / 场景 /
   整章 / 特殊页全部锚到同一张参考图。

2. `.png/.jpg` twin 清理 —— `save_inline_image`（唯一写盘出口）写新扩展名时删掉同 stem
   的旧 twin，含【本地/无 GCS】模式（mirror_to_gcs 在无 GCS 时早返回、从不清本地 twin
   的那个洞）。否则 checkpoint/stale/PDF 探测会挑中旧 twin，把一页误报成"已画/最新"。
"""

from __future__ import annotations

import types

import pytest


def _img_response(mime: str, data: bytes):
    part = types.SimpleNamespace(inline_data=types.SimpleNamespace(mime_type=mime, data=data))
    content = types.SimpleNamespace(parts=[part])
    return types.SimpleNamespace(candidates=[types.SimpleNamespace(content=content)])


@pytest.mark.parametrize("old_ext,new_mime,new_ext", [
    (".jpg", "image/png", ".png"),
    (".png", "image/jpeg", ".jpg"),
])
def test_save_inline_image_drops_other_extension_twin(monkeypatch, tmp_path, old_ext, new_mime, new_ext):
    from src.generation import image_utils

    # 本地/无 GCS 模式：mirror_to_gcs 早返回，twin 清理必须由 save_inline_image 自己做。
    monkeypatch.setattr("src.core.storage.GCS_BUCKET", "", raising=False)

    stale = tmp_path / f"page_001{old_ext}"
    stale.write_bytes(b"stale")

    final = image_utils.save_inline_image(_img_response(new_mime, b"fresh"), tmp_path / "page_001")

    assert final.endswith(f"page_001{new_ext}")
    assert (tmp_path / f"page_001{new_ext}").read_bytes() == b"fresh"
    assert not stale.exists(), "另一扩展名的旧 twin 必须被删除，否则会 shadow 新图"


def _assert_special_page_uses_get_style_ref(monkeypatch, call):
    import src.generation.special_pages as sp

    monkeypatch.setattr(sp, "get_style_ref", lambda bid: "/UPLOADED_STYLE_REF")
    monkeypatch.setattr(sp, "_find_book_cover", lambda bid: "/BOOK_COVER_SHOULD_NOT_BE_USED")
    cap: dict = {}
    monkeypatch.setattr(
        sp, "_generate_image_with_refs",
        lambda *a, **k: cap.update(style_ref=a[4] if len(a) > 4 else k.get("style_ref")) or "",
    )
    call(sp)
    assert cap["style_ref"] == "/UPLOADED_STYLE_REF"


def test_chapter_cover_anchors_to_get_style_ref(monkeypatch):
    _assert_special_page_uses_get_style_ref(
        monkeypatch, lambda sp: sp.generate_chapter_cover("T", 1, "summary", [], "b1"))


def test_back_cover_anchors_to_get_style_ref(monkeypatch):
    _assert_special_page_uses_get_style_ref(
        monkeypatch, lambda sp: sp.generate_back_cover("Title", "b1"))


