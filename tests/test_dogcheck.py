"""Tests for dogcheck disable helper."""

from __future__ import annotations

from pathlib import Path

from undertale_extractor.dogcheck import (
    EXIT_WORD,
    MARXVEE_PATCHES,
    disable_dogcheck,
    dogcheck_likely_disabled,
)


def test_marxvee_patch_applied(tmp_path: Path):
    offset, patch = MARXVEE_PATCHES[1]  # 1.001
    data = bytearray(max(o for o, _ in MARXVEE_PATCHES) + 64)
    # Put non-zero junk so the patcher accepts it
    data[offset : offset + 4] = b"\x11\x22\x33\x44"
    path = tmp_path / "data.win"
    # Minimal FORM header so code scan doesn't explode
    path.write_bytes(b"FORM" + (8).to_bytes(4, "little") + b"GEN8" + (0).to_bytes(4, "little") + bytes(data[16:]))
    # Rewrite with full sized buffer starting with FORM
    buf = bytearray(len(data))
    buf[0:4] = b"FORM"
    buf[4:8] = (len(data) - 8).to_bytes(4, "little")
    buf[offset : offset + 4] = b"\x11\x22\x33\x44"
    path.write_bytes(buf)

    ok, msg = disable_dogcheck(path, backup=True)
    assert ok is True
    assert path.read_bytes()[offset : offset + 4] == patch
    assert path.with_suffix(".win.dogcheckbak").exists()
    assert "marxvee" in msg.lower() or "disabled" in msg.lower()


def test_already_patched(tmp_path: Path):
    offset, patch = MARXVEE_PATCHES[0]
    buf = bytearray(offset + 32)
    buf[0:4] = b"FORM"
    buf[4:8] = (len(buf) - 8).to_bytes(4, "little")
    buf[offset : offset + 4] = patch
    path = tmp_path / "data.win"
    path.write_bytes(buf)
    assert dogcheck_likely_disabled(path) is True
