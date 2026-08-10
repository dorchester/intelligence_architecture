"""Trim and redact console screenshots so they are fit to publish.

    pip install pillow
    python scripts/prepare_screenshots.py --src ./raw-shots --out ./ie-screenshots \
        --redact 123456789012

Two jobs, both of which matter before a screenshot goes into a document that
leaves the account:

  crop    Console pages are mostly empty canvas. Every image is trimmed to
          its actual content (background colour sampled from the border, so
          this works on both the light AWS console and the dark product
          console), with a small margin left back.

  redact  Any string given with --redact is blacked out wherever it appears.
          Location is found by template-matching the string as rendered text
          at several sizes, so it catches the account badge, breadcrumbs and
          bucket names alike - not just one known corner.

Redaction here is a backstop. The cleaner path, when you still have the page
open, is to mask the text in the DOM before capturing; that cannot miss an
occurrence because it never renders one.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MARGIN = 14
DIFF = 26          # per-pixel channel-sum difference that counts as "content"
MIN_KEEP = 40_000  # images smaller than this after cropping are broken renders


def background(img: Image.Image) -> tuple[int, int, int]:
    """Most common colour around the border - the page's canvas colour."""
    w, h = img.size
    px = img.load()
    counts: dict = {}
    for x in range(0, w, 3):
        for y in (0, 1, 2, h - 3, h - 2, h - 1):
            counts[px[x, y]] = counts.get(px[x, y], 0) + 1
    for y in range(0, h, 3):
        for x in (0, 1, 2, w - 3, w - 2, w - 1):
            counts[px[x, y]] = counts.get(px[x, y], 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def content_box(img: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of real content, ignoring chrome at the very edges.

    The inset matters: a scrollbar or a 1px border hugging the frame counts as
    content and silently defeats the whole crop, which is why several pages
    came back uncropped the first time.
    """
    import numpy as np

    bg = background(img)
    arr = np.asarray(img, dtype=np.int16)
    h, w, _ = arr.shape
    inset = 8
    if h <= 2 * inset or w <= 2 * inset:
        return None
    core = arr[inset:h - inset, inset:w - inset]
    diff = np.abs(core - np.array(bg, dtype=np.int16)).sum(axis=2)
    mask = diff > DIFF
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return (max(0, int(xs.min()) + inset - MARGIN), max(0, int(ys.min()) + inset - MARGIN),
            min(w, int(xs.max()) + inset + MARGIN), min(h, int(ys.max()) + inset + MARGIN))


def find_text(img: Image.Image, needle: str, threshold: float = 0.90) -> list[tuple[int, int, int, int]]:
    """Locate `needle` by matching the column-ink profile of the rendered string.

    Comparing total ink is far too loose - any dense block of text matches, and
    the first version of this blacked out half of every page. What identifies a
    *specific* string is the shape of its profile: where strokes fall across
    the width. This renders the string at each plausible UI size, then scores
    every candidate window by normalised cross-correlation against that shape,
    keeping only strong, non-overlapping peaks.

    Vectorised, because the naive loop is minutes per image.
    """
    import numpy as np

    gray = np.asarray(img.convert("L"), dtype=np.float32)
    ink = 255.0 - gray
    h, w = ink.shape
    hits: list[tuple[int, int, int, int]] = []

    # Vertical running sums so a window's column profile is one subtraction.
    cum = np.zeros((h + 1, w), dtype=np.float32)
    np.cumsum(ink, axis=0, out=cum[1:])

    # Console UI, terminal output and page body use different faces; a probe
    # rendered in the wrong one correlates poorly, so try the plausible set.
    faces = ["arial.ttf", "segoeui.ttf", "tahoma.ttf", "verdana.ttf",
             "consola.ttf", "cour.ttf", "lucon.ttf"]
    for face, size in ((f, s) for f in faces for s in range(9, 17)):
        try:
            font = ImageFont.truetype(face, size)
        except OSError:
            continue
        bbox = font.getbbox(needle)
        pw, ph = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if pw < 20 or ph < 5 or pw >= w or ph >= h:
            continue
        probe = Image.new("L", (pw, ph), 255)
        ImageDraw.Draw(probe).text((-bbox[0], -bbox[1]), needle, font=font, fill=0)
        sig = (255.0 - np.asarray(probe, dtype=np.float32)).sum(axis=0)
        sig_c = sig - sig.mean()
        sig_n = float(np.sqrt((sig_c ** 2).sum()))
        if sig_n < 1e-6:
            continue
        sig_total = float(sig.sum())

        cols = cum[ph:] - cum[:-ph]                       # (h-ph+1, w) column ink
        win = np.lib.stride_tricks.sliding_window_view(cols, pw, axis=1)
        wm = win.mean(axis=2, keepdims=True)
        wc = win - wm
        num = (wc * sig_c).sum(axis=2)
        den = np.sqrt((wc ** 2).sum(axis=2)) * sig_n
        with np.errstate(divide="ignore", invalid="ignore"):
            score = np.where(den > 0, num / den, 0.0)

        total = win.sum(axis=2)
        ok = (score >= threshold) & (total > sig_total * 0.5) & (total < sig_total * 2.0)
        for y, x in zip(*np.nonzero(ok)):
            hits.append((int(x) - 2, int(y) - 2, int(x) + pw + 2, int(y) + ph + 2))

    # Collapse overlapping detections of the same occurrence.
    merged: list[tuple[int, int, int, int]] = []
    for b in sorted(hits, key=lambda t: (t[1], t[0])):
        placed = False
        for i, m in enumerate(merged):
            if not (b[2] < m[0] or b[0] > m[2] or b[3] < m[1] or b[1] > m[3]):
                merged[i] = (min(m[0], b[0]), min(m[1], b[1]),
                             max(m[2], b[2]), max(m[3], b[3]))
                placed = True
                break
        if not placed:
            merged.append(b)
    return merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--redact", action="append", default=[],
                    help="string to black out; repeatable")
    ap.add_argument("--no-crop", action="store_true")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for f in sorted(src.iterdir()):
        if f.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        img = Image.open(f).convert("RGB")
        before = img.size

        if not args.no_crop:
            box = content_box(img)
            if box and (box[2] - box[0]) > 80 and (box[3] - box[1]) > 60:
                img = img.crop(box)

        if img.size[0] * img.size[1] < MIN_KEEP:
            print(f"  skipped {f.name}: {img.size} - broken/near-empty render")
            continue

        draw = ImageDraw.Draw(img)
        marks = 0
        for needle in args.redact:
            for box in find_text(img, needle):
                draw.rectangle(box, fill=(0, 0, 0))
                marks += 1

        dest = out / f.name
        img.save(dest, quality=92)
        note = f"  {f.name}: {before[0]}x{before[1]} -> {img.size[0]}x{img.size[1]}"
        print(note + (f", {marks} redaction(s)" if marks else ""))

    print(f"\nprepared into {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
