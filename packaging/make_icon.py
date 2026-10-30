"""Generate ``packaging/app.ico`` (run once; the .ico itself is committed).

Kept in the repository so the icon is reproducible instead of being an opaque
binary blob: ``python packaging/make_icon.py``.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512
BACKGROUND = (31, 122, 77, 255)
PAPER = (255, 255, 255, 255)
ACCENT = (255, 214, 102, 255)


def rounded_square(size, radius, fill):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=fill)
    return image


def build() -> Image.Image:
    base = rounded_square(SIZE, radius=112, fill=BACKGROUND)
    draw = ImageDraw.Draw(base)

    # Two parcels overlapping: a solid one and an outlined one holding the
    # 让出 (clip) shape, with the shared strip picked out in amber.
    left = [96, 160, 296, 360]
    right = [216, 280, 416, 480]
    draw.rectangle(left, fill=PAPER)
    draw.rectangle(right, outline=PAPER, width=26)

    strip = [max(left[0], right[0]), max(left[1], right[1]), min(left[2], right[2]), min(left[3], right[3])]
    draw.rectangle(strip, fill=ACCENT)
    return base


def main() -> None:
    icon = build()
    target = Path(__file__).with_name("app.ico")
    icon.save(target, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
