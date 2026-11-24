"""Generate assets/app.ico (same artwork as assistant/tray.py::paint_icon).

Pure numpy + stdlib, so it runs anywhere:  python tools/make_icon.py
"""
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

SIZES = (16, 24, 32, 48, 64, 128, 256)
BARS = (0.34, 0.62, 0.9, 0.62, 0.34)
C0 = np.array([0x8C, 0x9C, 0xFF], dtype=np.float64)
C1 = np.array([0x52, 0x62, 0xE0], dtype=np.float64)


def rounded_mask(S, x0, y0, x1, y1, r):
    ys, xs = np.mgrid[0:S, 0:S].astype(np.float64) + 0.5
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = (x1 - x0) / 2, (y1 - y0) / 2
    dx = np.maximum(np.abs(xs - cx) - (hw - r), 0)
    dy = np.maximum(np.abs(ys - cy) - (hh - r), 0)
    inside = (np.abs(xs - cx) <= hw) & (np.abs(ys - cy) <= hh)
    return inside & (np.hypot(dx, dy) <= r)


def render(size, ss=4):
    S = size * ss
    inset = size * 0.04 * ss
    tile = rounded_mask(S, inset, inset, S - inset, S - inset, size * 0.24 * ss)

    ys, xs = np.mgrid[0:S, 0:S].astype(np.float64) + 0.5
    t = ((xs + ys) / (2 * S))[..., None]
    rgb = C0 * (1 - t) + C1 * t
    white = np.zeros((S, S), dtype=bool)

    bar_w, gap = size * 0.1 * ss, size * 0.07 * ss
    total = len(BARS) * bar_w + (len(BARS) - 1) * gap
    x = (S - total) / 2
    for level in BARS:
        h = size * 0.56 * level * ss
        white |= rounded_mask(S, x, (S - h) / 2, x + bar_w, (S + h) / 2, bar_w / 2)
        x += bar_w + gap
    rgb = np.where(white[..., None], 255.0, rgb)

    alpha = tile.astype(np.float64)
    premult = rgb * alpha[..., None]
    a = alpha.reshape(size, ss, size, ss).mean(axis=(1, 3))
    p = premult.reshape(size, ss, size, ss, 3).mean(axis=(1, 3))
    out = np.zeros((size, size, 4), dtype=np.uint8)
    safe = np.maximum(a, 1e-9)[..., None]
    out[..., :3] = np.clip(p / safe, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(a * 255, 0, 255).astype(np.uint8)
    return out


def png_bytes(rgba):
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def build_ico(sizes=SIZES):
    images = [png_bytes(render(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in zip(sizes, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets" / "app.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_ico())
    print("written", target, target.stat().st_size, "bytes")
