"""Draw the MineCore application icon (PNG + multi-size Windows ICO).

The icon is a section through a benched open pit above a gold ore body. It is
drawn from code so it can be regenerated; run it after a design change:

    uv run python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "python" / "minecore" / "gui" / "assets"
S = 1024  # drawing size; downsampled for every icon size

NAVY = (15, 42, 68, 255)
SKY = (27, 77, 115, 255)
ROCK = (196, 122, 44, 255)
ROCK_DARK = (150, 88, 30, 255)
ORE = (245, 197, 66, 255)
EDGE = (255, 236, 179, 255)


def draw(size: int = S) -> Image.Image:
    k = size / S
    img = Image.new("RGBA", (size, size), NAVY)
    d = ImageDraw.Draw(img)
    # Ground below the pit crest, with a darker lower band.
    top, bottom, margin = 330, S, 0
    d.rectangle((margin * k, top * k, (S - margin) * k, bottom * k), fill=ROCK)
    d.rectangle((margin * k, 760 * k, (S - margin) * k, bottom * k), fill=ROCK_DARK)
    # Benched pit: four benches each side, cut from the crest to the floor.
    steps, rise = 4, 85
    left = [(margin, top)]
    right = [(S - margin, top)]
    for i in range(steps):
        xl = margin + 120 + i * 85
        xr = S - margin - 120 - i * 85
        y = top + (i + 1) * rise
        left += [(xl, top + i * rise), (xl + 30, y)]
        right += [(xr, top + i * rise), (xr - 30, y)]
    pit = left + right[::-1]
    d.polygon([(x * k, y * k) for x, y in pit], fill=SKY)
    d.line([(x * k, y * k) for x, y in pit], fill=EDGE, width=max(1, int(14 * k)), joint="curve")
    # Ore body under the pit floor.
    floor_y = top + steps * rise
    d.ellipse((330 * k, (floor_y + 50) * k, 694 * k, (floor_y + 220) * k), fill=ORE)
    # Ground line across the crest.
    d.line(
        [(margin * k, top * k), ((S - margin) * k, top * k)], fill=EDGE, width=max(1, int(14 * k))
    )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(190 * k), fill=255
    )
    img.putalpha(mask)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.Resampling.LANCZOS).save(OUT / "minecore.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.save(OUT / "minecore.ico", sizes=sizes)
    print(f"wrote {OUT / 'minecore.png'} and {OUT / 'minecore.ico'}")


if __name__ == "__main__":
    main()
