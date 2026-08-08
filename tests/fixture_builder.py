"""Build a minimal GameMaker FORM / data.win for tests."""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image


def _png_bytes(color=(255, 80, 40, 255), size=(32, 32)) -> bytes:
    img = Image.new("RGBA", size, color)
    # Draw a simple pattern so crops are identifiable
    for x in range(size[0]):
        for y in range(size[1]):
            if (x // 4 + y // 4) % 2 == 0:
                img.putpixel((x, y), color)
            else:
                img.putpixel((x, y), (40, 40, 60, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _wav_bytes(duration_samples: int = 256) -> bytes:
    # Minimal 8-bit mono PCM WAV
    data = bytes([128 + ((i % 32) - 16) for i in range(duration_samples)])
    byte_rate = 22050
    block_align = 1
    bits = 8
    hdr = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        byte_rate,
        byte_rate * block_align,
        block_align,
        bits,
        b"data",
        len(data),
    )
    return hdr + data


def _align(buf: bytearray, boundary: int) -> None:
    while len(buf) % boundary:
        buf.append(0)


def _gm_string(text: str) -> bytes:
    encoded = text.encode("utf-8")
    return struct.pack("<I", len(encoded)) + encoded + b"\x00"


def build_minimal_data_win(path: str | Path) -> Path:
    """
    Construct a tiny Undertale-like data.win with:
    - 1 texture PNG
    - 1 TPAG region
    - 1 sprite (1 frame)
    - 1 background
    - 1 sound + audio WAV
    - string table
    """
    path = Path(path)
    png = _png_bytes()
    wav = _wav_bytes()

    # We assemble chunks with placeholders, then patch absolute offsets.
    # Layout plan (file offsets):
    # FORM header
    # GEN8, STRG, SOND, SPRT, BGND, TPAG, TXTR, AUDO

    strings = [
        "UNDERTALE",
        "spr_test",
        "bg_test",
        "snd_test",
        ".wav",
        "snd_test.wav",
        "fnt_test",
    ]

    # --- Build STRG chunk content ---
    # STRG content: count, offsets[], then string records
    # Absolute offsets point to character data (after length field)

    def build_archive() -> bytes:
        chunks: dict[str, bytes] = {}

        # We'll do two passes: first write with zero pointers where needed,
        # but absolute offsets require knowing final positions. So build in order
        # into a growing buffer with a FORM header placeholder.

        out = bytearray()
        out.extend(b"FORM")
        out.extend(struct.pack("<I", 0))  # patch later

        def start_chunk(tag: str) -> int:
            out.extend(tag.encode("ascii"))
            out.extend(struct.pack("<I", 0))  # size placeholder
            return len(out)

        def end_chunk(content_start: int) -> None:
            size = len(out) - content_start
            struct.pack_into("<I", out, content_start - 4, size)

        # GEN8 — minimal stub with display name pointer patched after STRG
        gen8_start = start_chunk("GEN8")
        gen8_display_name_pos = None
        # Write a simplified GEN8: fill with zeros, put display-name ptr at +100
        out.extend(b"\x00" * 100)
        gen8_display_name_pos = len(out)
        out.extend(struct.pack("<I", 0))  # display name ptr
        out.extend(b"\x00" * 40)
        end_chunk(gen8_start)

        # STRG
        strg_content_start = start_chunk("STRG")
        strg_count_pos = len(out)
        out.extend(struct.pack("<I", len(strings)))
        strg_offset_table = len(out)
        out.extend(b"\x00" * (4 * len(strings)))
        char_ptrs: list[int] = []
        for i, s in enumerate(strings):
            # length + chars + NUL; absolute ptr = address of chars
            length_pos = len(out)
            encoded = s.encode("utf-8")
            out.extend(struct.pack("<I", len(encoded)))
            char_ptr = len(out)
            out.extend(encoded)
            out.append(0)
            char_ptrs.append(char_ptr)
            struct.pack_into("<I", out, strg_offset_table + 4 * i, char_ptr)
        _align(out, 4)
        end_chunk(strg_content_start)

        # Patch GEN8 display name -> "UNDERTALE"
        struct.pack_into("<I", out, gen8_display_name_pos, char_ptrs[0])

        name_spr = char_ptrs[1]
        name_bg = char_ptrs[2]
        name_snd = char_ptrs[3]
        name_ext = char_ptrs[4]
        name_file = char_ptrs[5]

        # TPAG — one entry, texture 0, full 32x32 (will patch absolute offset of entry)
        tpag_content = start_chunk("TPAG")
        out.extend(struct.pack("<I", 1))  # count
        tpag_off_table = len(out)
        out.extend(struct.pack("<I", 0))  # entry abs offset placeholder
        tpag_entry_abs = len(out)
        # x,y,w,h, ox,oy, cw,ch, canvasw,canvash, texid
        out.extend(struct.pack("<11H", 0, 0, 16, 16, 0, 0, 16, 16, 16, 16, 0))
        struct.pack_into("<I", out, tpag_off_table, tpag_entry_abs)
        end_chunk(tpag_content)

        # TXTR — one PNG
        txtr_content = start_chunk("TXTR")
        out.extend(struct.pack("<I", 1))
        txtr_off_table = len(out)
        out.extend(struct.pack("<I", 0))
        entry_abs = len(out)
        out.extend(struct.pack("<I", 1))  # scaled / flags
        png_ptr_pos = len(out)
        out.extend(struct.pack("<I", 0))
        struct.pack_into("<I", out, txtr_off_table, entry_abs)
        _align(out, 128)
        png_abs = len(out)
        out.extend(png)
        struct.pack_into("<I", out, png_ptr_pos, png_abs)
        end_chunk(txtr_content)

        # SPRT — one sprite
        sprt_content = start_chunk("SPRT")
        out.extend(struct.pack("<I", 1))
        sprt_off_table = len(out)
        out.extend(struct.pack("<I", 0))
        spr_entry = len(out)
        out.extend(struct.pack("<I", name_spr))
        out.extend(struct.pack("<ii", 16, 16))  # w,h
        out.extend(struct.pack("<iiii", 0, 0, 0, 0))  # margins
        out.extend(struct.pack("<5i", 0, 0, 0, 0, 0))  # unknowns + bbox + sepmasks
        out.extend(struct.pack("<ii", 0, 0))  # origin
        out.extend(struct.pack("<i", 1))  # frame count
        out.extend(struct.pack("<I", tpag_entry_abs))  # frame -> TPAG
        # collision mask: ceil(16/8)*16 = 32 bytes
        out.extend(b"\xff" * 32)
        struct.pack_into("<I", out, sprt_off_table, spr_entry)
        end_chunk(sprt_content)

        # BGND
        bgnd_content = start_chunk("BGND")
        out.extend(struct.pack("<I", 1))
        bg_off_table = len(out)
        out.extend(struct.pack("<I", 0))
        bg_entry = len(out)
        out.extend(struct.pack("<I", name_bg))
        out.extend(struct.pack("<iii", 0, 0, 0))
        out.extend(struct.pack("<I", tpag_entry_abs))
        struct.pack_into("<I", out, bg_off_table, bg_entry)
        end_chunk(bgnd_content)

        # SOND
        sond_content = start_chunk("SOND")
        out.extend(struct.pack("<I", 1))
        sond_off_table = len(out)
        out.extend(struct.pack("<I", 0))
        snd_entry = len(out)
        out.extend(struct.pack("<I", name_snd))
        out.extend(struct.pack("<I", 0))  # flags
        out.extend(struct.pack("<I", name_ext))
        out.extend(struct.pack("<I", name_file))
        out.extend(struct.pack("<I", 0))  # effects
        out.extend(struct.pack("<ff", 1.0, 0.0))  # volume, pitch
        out.extend(struct.pack("<ii", 0, 0))  # group, audio id 0
        struct.pack_into("<I", out, sond_off_table, snd_entry)
        end_chunk(sond_content)

        # AUDO
        audo_content = start_chunk("AUDO")
        out.extend(struct.pack("<I", 1))
        audo_off_table = len(out)
        out.extend(struct.pack("<I", 0))
        aud_entry = len(out)
        out.extend(struct.pack("<I", len(wav)))
        out.extend(wav)
        struct.pack_into("<I", out, audo_off_table, aud_entry)
        end_chunk(audo_content)

        # Patch FORM size
        form_size = len(out) - 8
        struct.pack_into("<I", out, 4, form_size)
        return bytes(out)

    data = build_archive()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "data.win"
    build_minimal_data_win(target)
    print(f"Wrote {target} ({target.stat().st_size} bytes)")
