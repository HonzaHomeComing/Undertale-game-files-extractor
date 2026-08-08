"""Tests for the Undertale data.win parser and asset export."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from undertale_extractor.assets import AssetKind, GameAsset, safe_filename
from undertale_extractor.binary import BinaryReader
from undertale_extractor.parser import (
    find_data_file,
    load_undertale_assets,
    scan_loose_files,
)
from tests.fixture_builder import build_minimal_data_win


@pytest.fixture()
def sample_game(tmp_path: Path) -> Path:
    data = build_minimal_data_win(tmp_path / "data.win")
    # Loose music file like Undertale ships
    (tmp_path / "mus_test.ogg").write_bytes(b"OggS\x00fake-ogg-data")
    (tmp_path / "readme.txt").write_text("ignore me")
    assert data.exists()
    return tmp_path


def test_binary_reader_basics():
    raw = b"FORM" + (12).to_bytes(4, "little") + b"GEN8" + (0).to_bytes(4, "little")
    r = BinaryReader(raw)
    assert r.read_tag() == "FORM"
    assert r.read_u32() == 12
    assert r.read_tag() == "GEN8"


def test_safe_filename():
    assert safe_filename("spr_toriel/face") == "spr_toriel_face"
    assert safe_filename("???") == "file"


def test_find_data_file(sample_game: Path):
    assert find_data_file(sample_game).name == "data.win"
    assert find_data_file(sample_game / "data.win").name == "data.win"


def test_scan_loose_files(sample_game: Path):
    loose = scan_loose_files(sample_game)
    names = {a.display_name for a in loose}
    assert any("mus_test" in n for n in names)


def test_parse_extracts_expected_kinds(sample_game: Path):
    result = load_undertale_assets(sample_game)
    kinds = {a.kind for a in result.assets}
    assert AssetKind.TEXTURE in kinds
    assert AssetKind.SPRITE in kinds
    assert AssetKind.BACKGROUND in kinds
    assert AssetKind.AUDIO in kinds
    assert AssetKind.MUSIC in kinds
    assert AssetKind.ROOM in kinds
    assert result.game_name == "UNDERTALE"

    sprites = [a for a in result.assets if a.kind == AssetKind.SPRITE]
    assert sprites
    img = sprites[0].get_image()
    assert img is not None
    assert img.size == (16, 16)

    audio = [a for a in result.assets if a.kind == AssetKind.AUDIO]
    assert audio
    assert audio[0].name == "snd_test"
    blob = audio[0].get_data()
    assert blob.startswith(b"RIFF")


def test_export_to_disk(sample_game: Path, tmp_path: Path):
    result = load_undertale_assets(sample_game)
    out = tmp_path / "exports"
    exported = []
    for asset in result.assets:
        if asset.kind in {AssetKind.SPRITE, AssetKind.AUDIO, AssetKind.MUSIC}:
            path = asset.export_to(out / asset.kind.value.lower())
            assert path.exists()
            assert path.stat().st_size > 0
            exported.append(path)
    assert exported


def test_game_asset_thumbnail():
    img = Image.new("RGBA", (64, 32), (255, 0, 0, 255))

    def image_fn() -> Image.Image:
        return img.copy()

    asset = GameAsset(
        id="t",
        name="demo",
        kind=AssetKind.SPRITE,
        extension=".png",
        size=100,
        _image_fn=image_fn,
    )
    thumb = asset.thumbnail(32)
    assert thumb is not None
    assert max(thumb.size) <= 32
    data = asset.get_data()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_textures_are_lazy(sample_game: Path):
    result = load_undertale_assets(sample_game)
    assert result.textures
    assert result._texture_image_cache == {}
    img = result.get_texture_image(0)
    assert img.size[0] > 0
    assert 0 in result._texture_image_cache


def test_missing_data_win(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_undertale_assets(tmp_path)
