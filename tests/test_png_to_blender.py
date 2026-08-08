"""Tests for png_to_blender.py"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

import png_to_blender as p2b


def _make_sprite(path: Path) -> Path:
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    for x in range(2, 6):
        for y in range(2, 6):
            img.putpixel((x, y), (220, 60, 40, 255))
    img.putpixel((3, 3), (255, 220, 80, 255))
    path.write_bytes(b"")  # ensure parent
    img.save(path)
    return path


def test_voxels_obj(tmp_path: Path):
    png = _make_sprite(tmp_path / "spr.png")
    obj = p2b.convert_png(png, tmp_path, mode="voxels", scale=0.1, depth=1.0)
    assert obj.exists()
    assert obj.with_suffix(".mtl").exists()
    text = obj.read_text()
    assert text.startswith("#")
    assert "\nv " in text or text.count("v ") >= 8
    assert "f " in text
    assert "mtllib" in text


def test_plane_obj(tmp_path: Path):
    png = _make_sprite(tmp_path / "plane.png")
    obj = p2b.convert_png(png, tmp_path, mode="plane", scale=0.1)
    assert obj.exists()
    text = obj.read_text()
    assert "vt " in text
    assert "map_Kd" in obj.with_suffix(".mtl").read_text()


def test_height_obj(tmp_path: Path):
    png = _make_sprite(tmp_path / "height.png")
    obj = p2b.convert_png(png, tmp_path, mode="height", scale=0.1, depth=2.0)
    assert obj.exists()
    assert "f " in obj.read_text()


def test_transparent_image_errors(tmp_path: Path):
    png = tmp_path / "empty.png"
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(png)
    try:
        p2b.convert_png(png, tmp_path, mode="voxels")
        assert False, "expected ValueError"
    except ValueError:
        pass
