"""Draw the PulseG Studio app icon set.

    python scripts/make_icons.py

Writes the PNG and ICO files Tauri needs into ``src-tauri/icons/``.

Drawn here rather than downloaded for two reasons. The icon is the one asset that must exist for
``tauri build`` to succeed, so it cannot depend on a third-party site being reachable at build
time. And the mark is part of the product's identity: a canary-yellow pulse line on the same
charcoal the dashboard uses, so the taskbar button and the app agree about what this program is.

The mark: a rounded charcoal tile with a single waveform crossing it. The waveform is the
"pulse" in PulseG and doubles as a throwback to a gamepad's shoulder seam at small sizes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = REPO_ROOT / "src-tauri" / "icons"

CHARCOAL = (30, 28, 33, 255)
CANARY = (250, 243, 62, 255)
MINT = (214, 248, 214, 255)
TEAL = (127, 198, 164, 255)

#: Sizes Tauri (and Windows Explorer) actually ask for.
PNG_SIZES = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
    "Square150x150Logo.png": 150,
    "Square44x44Logo.png": 44,
    "StoreLogo.png": 50,
}
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw_icon(size: int) -> Image.Image:
    """One icon at one size, drawn with supersampling so the curve stays smooth."""
    scale = 4
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    radius = int(canvas * 0.22)
    draw.rounded_rectangle([0, 0, canvas - 1, canvas - 1], radius=radius, fill=CHARCOAL)

    # The pulse: a flat line that spikes through the middle, the shape of a heartbeat monitor.
    thickness = max(2 * scale, int(canvas * 0.055))
    mid = canvas * 0.52
    left = canvas * 0.14
    right = canvas * 0.86
    step = (right - left) / 6

    points = [
        (left, mid),
        (left + step, mid),
        (left + step * 1.5, mid - canvas * 0.20),
        (left + step * 2.5, mid + canvas * 0.22),
        (left + step * 3.1, mid - canvas * 0.10),
        (left + step * 3.7, mid + canvas * 0.10),
        (left + step * 4.3, mid - canvas * 0.26),
        (left + step * 5.0, mid),
        (right, mid),
    ]
    draw.line(points, fill=CANARY, width=thickness, joint="curve")
    for point in (points[0], points[-1]):
        draw.ellipse(
            [
                point[0] - thickness / 2,
                point[1] - thickness / 2,
                point[0] + thickness / 2,
                point[1] + thickness / 2,
            ],
            fill=MINT,
        )

    # A teal far edge, so the tile reads as a "screen" rather than a flat badge.
    edge = max(1 * scale, int(canvas * 0.02))
    draw.line([(radius, canvas - edge), (canvas - radius, canvas - edge)], fill=TEAL, width=edge)

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    images = {}
    for name, size in PNG_SIZES.items():
        image = draw_icon(size)
        images[size] = image
        image.save(ICON_DIR / name)
        print(f"wrote {ICON_DIR / name} ({size}x{size})")

    ico_base = images.get(256) or draw_icon(256)
    ico_base.save(ICON_DIR / "icon.ico", sizes=[(size, size) for size in ICO_SIZES])
    print(f"wrote {ICON_DIR / 'icon.ico'} ({', '.join(str(s) for s in ICO_SIZES)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
