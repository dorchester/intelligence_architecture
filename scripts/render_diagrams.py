"""Render every Mermaid block in docs/diagrams.md to a PNG.

    npm i -g @mermaid-js/mermaid-cli     # or npx, as below
    python scripts/render_diagrams.py --out ./diagram-png

Keeps docs/diagrams.md as the single source: the markdown renders natively
on GitHub, and this produces the same figures as images for documents that
cannot render Mermaid (Word, PowerPoint, PDF).

Output files are named by the diagram's heading number and slug, e.g.
  01-the-whole-system-on-one-page.png
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HEADING = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$", re.M)
BLOCK = re.compile(r"```mermaid\n(.*?)```", re.S)


def slug(text: str) -> str:
    text = re.sub(r"\(.*?\)", "", text).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="docs/diagrams.md")
    ap.add_argument("--out", default="./diagram-png")
    ap.add_argument("--scale", default="3", help="pixel density; 3 keeps text crisp in Word")
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")

    # Pair each heading with the first mermaid block that follows it.
    sections = []
    marks = list(HEADING.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        block = BLOCK.search(text, m.end(), end)
        if block:
            sections.append((m.group(1), m.group(2), block.group(1)))

    if not sections:
        sys.exit(f"no mermaid blocks found under numbered headings in {src}")

    made = []
    for num, title, code in sections:
        stem = f"{int(num):02d}-{slug(title)}"
        mmd = out / f"{stem}.mmd"
        png = out / f"{stem}.png"
        mmd.write_text(code, encoding="utf-8")
        cmd = ["npx", "-y", "@mermaid-js/mermaid-cli@latest",
               "-i", str(mmd), "-o", str(png), "-b", "white", "-s", args.scale]
        r = subprocess.run(cmd, capture_output=True, text=True, shell=(sys.platform == "win32"))
        if png.exists():
            made.append(png.name)
            print(f"  rendered {png.name}")
        else:
            print(f"  FAILED   {stem}: {r.stderr.strip()[:200]}")
        mmd.unlink(missing_ok=True)

    print(f"\n{len(made)}/{len(sections)} diagrams rendered into {out}")
    return 0 if len(made) == len(sections) else 1


if __name__ == "__main__":
    sys.exit(main())
