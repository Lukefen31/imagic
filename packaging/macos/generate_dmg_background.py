"""Generate the DMG background image for the macOS installer.

Matches the imagic brand: dark #0d0d0d base, orange #ff9800 accent,
with a smooth curved arrow and clean typography.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── Layout constants ─────────────────────────────────────────────────────────
WIDTH = 660
HEIGHT = 400

# Icon centre positions — must match create-dmg --icon coordinates
APP_X = 180
APPS_X = 480
ICON_Y = 190

BRANDING = Path(__file__).resolve().parent / "branding"
OUTPUT = BRANDING / "dmg-background.png"
OUTPUT_2X = BRANDING / "dmg-background@2x.png"

# ── Brand colours (from website/app CSS) ─────────────────────────────────────
BG_PRIMARY = (13, 13, 13)       # #0d0d0d
BG_SECONDARY = (20, 20, 20)     # #141414
BG_CARD = (26, 26, 26)          # #1a1a1a
ACCENT = (255, 152, 0)          # #ff9800
ACCENT_LIGHT = (255, 167, 38)   # #ffa726
TEXT_PRIMARY = (238, 238, 238)   # #eee
TEXT_SECONDARY = (170, 170, 170) # #aaa
BORDER = (51, 51, 51)           # #333


# ── Font helpers ─────────────────────────────────────────────────────────────
def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    if bold:
        candidates = [
            ("/System/Library/Fonts/SFNS.ttf", 0),
            ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
            ("/System/Library/Fonts/Helvetica.ttc", 1),
        ]
    else:
        candidates = [
            ("/System/Library/Fonts/SFNS.ttf", 0),
            ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
            ("/System/Library/Fonts/Helvetica.ttc", 0),
        ]
    for path, index in candidates:
        try:
            return ImageFont.truetype(path, size, index=index)
        except (OSError, IndexError):
            continue
    return ImageFont.load_default()


def _centered_text(draw: ImageDraw.ImageDraw, y: int, text: str,
                   font: ImageFont.FreeTypeFont, fill: tuple, w: int) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) // 2, y), text, fill=fill, font=font)


# ── Background ───────────────────────────────────────────────────────────────
def _draw_background(img: Image.Image, s: int) -> None:
    """Dark branded background with subtle radial glow."""
    w, h = img.size
    draw = ImageDraw.Draw(img)

    # Fill with primary dark
    draw.rectangle([0, 0, w, h], fill=BG_PRIMARY)

    # Subtle vertical gradient: slightly lighter in the middle band
    for y in range(h):
        # Ease towards lighter in the centre of the window
        dist_from_center = abs(y - h * 0.45) / (h * 0.5)
        t = max(0, 1.0 - dist_from_center)
        t = t * t * 0.4  # Subtle
        r = int(BG_PRIMARY[0] + (BG_CARD[0] - BG_PRIMARY[0]) * t)
        g = int(BG_PRIMARY[1] + (BG_CARD[1] - BG_PRIMARY[1]) * t)
        b = int(BG_PRIMARY[2] + (BG_CARD[2] - BG_PRIMARY[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # Subtle orange radial glow behind centre
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    cx, cy = w // 2, int(h * 0.43)
    for radius in range(min(w, h) // 2, 0, -1):
        alpha = int(8 * (radius / (min(w, h) // 2)))
        glow_draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(*ACCENT, alpha),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=60 * s))
    # Composite onto img
    base_rgba = img.convert("RGBA")
    img_comp = Image.alpha_composite(base_rgba, glow)
    img.paste(img_comp.convert("RGB"))


# ── Accent line at top ───────────────────────────────────────────────────────
def _draw_top_accent(draw: ImageDraw.ImageDraw, w: int, s: int) -> None:
    """Thin orange gradient bar at the top of the window."""
    bar_h = 2 * s
    for x in range(w):
        t = x / w
        # Gradient: fade in from edges, full in middle
        intensity = 1.0 - abs(t - 0.5) * 1.4
        intensity = max(0, min(1, intensity))
        r = int(ACCENT[0] * intensity)
        g = int(ACCENT[1] * intensity)
        b = int(ACCENT[2] * intensity)
        draw.line([(x, 0), (x, bar_h)], fill=(r, g, b))


# ── Curved arrow ─────────────────────────────────────────────────────────────
def _draw_curved_arrow(img: Image.Image, s: int) -> None:
    """Smooth orange curved arrow from app → Applications."""
    draw = ImageDraw.Draw(img)

    x_start = (APP_X + 52) * s
    x_end = (APPS_X - 52) * s
    cy = ICON_Y * s
    cx = ((APP_X + APPS_X) // 2) * s
    arc_height = 40 * s

    # Build bezier curve points
    points = []
    steps = 80
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * x_start + 2 * (1 - t) * t * cx + t ** 2 * x_end
        y = (1 - t) ** 2 * cy + 2 * (1 - t) * t * (cy - arc_height) + t ** 2 * cy
        points.append((x, y))

    # Draw glow behind the arrow (orange, blurred)
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for i in range(len(points) - 1):
        glow_draw.line([points[i], points[i + 1]],
                       fill=(*ACCENT, 50), width=max(8 * s, 4))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=6 * s))
    base = img.convert("RGBA")
    img.paste(Image.alpha_composite(base, glow).convert("RGB"))

    # Draw the main arrow line
    draw = ImageDraw.Draw(img)
    line_width = max(3 * s, 2)
    # Gradient along the arrow: orange to lighter orange
    for i in range(len(points) - 1):
        t = i / len(points)
        r = int(ACCENT[0] + (ACCENT_LIGHT[0] - ACCENT[0]) * t)
        g = int(ACCENT[1] + (ACCENT_LIGHT[1] - ACCENT[1]) * t)
        b = int(ACCENT[2] + (ACCENT_LIGHT[2] - ACCENT[2]) * t)
        draw.line([points[i], points[i + 1]], fill=(r, g, b), width=line_width)

    # Arrowhead
    end_x, end_y = points[-1]
    prev_x, prev_y = points[-4]
    dx = end_x - prev_x
    dy = end_y - prev_y
    length = math.sqrt(dx * dx + dy * dy)
    if length > 0:
        dx, dy = dx / length, dy / length
    head_len = 16 * s
    head_w = 10 * s
    px, py = -dy, dx

    tip_x = end_x + dx * 3 * s
    tip_y = end_y + dy * 3 * s
    left_x = tip_x - dx * head_len + px * head_w
    left_y = tip_y - dy * head_len + py * head_w
    right_x = tip_x - dx * head_len - px * head_w
    right_y = tip_y - dy * head_len - py * head_w

    draw.polygon(
        [(tip_x, tip_y), (left_x, left_y), (right_x, right_y)],
        fill=ACCENT_LIGHT,
    )


# ── Subtle border line ──────────────────────────────────────────────────────
def _draw_border(draw: ImageDraw.ImageDraw, w: int, h: int, s: int) -> None:
    """Subtle border like the app's card style."""
    draw.rectangle([0, 0, w - 1, h - 1], outline=BORDER, width=1)


# ── Main generator ───────────────────────────────────────────────────────────
def generate(scale: int = 1) -> Image.Image:
    w, h = WIDTH * scale, HEIGHT * scale
    img = Image.new("RGB", (w, h), BG_PRIMARY)

    _draw_background(img, scale)

    draw = ImageDraw.Draw(img)
    _draw_top_accent(draw, w, scale)
    _draw_border(draw, w, h, scale)

    _draw_curved_arrow(img, scale)

    # Text
    draw = ImageDraw.Draw(img)
    title_font = _load_font(16 * scale, bold=True)
    sub_font = _load_font(11 * scale)

    _centered_text(draw, 315 * scale,
                   "Drag to Applications to install",
                   title_font, TEXT_PRIMARY, w)
    _centered_text(draw, 345 * scale,
                   "imagic v0.1.0",
                   sub_font, TEXT_SECONDARY, w)

    return img


def main() -> None:
    BRANDING.mkdir(parents=True, exist_ok=True)

    img_1x = generate(scale=1)
    img_1x.save(OUTPUT, "PNG")
    print(f"Created {OUTPUT}  ({WIDTH}x{HEIGHT})")

    img_2x = generate(scale=2)
    img_2x.save(OUTPUT_2X, "PNG")
    print(f"Created {OUTPUT_2X}  ({WIDTH * 2}x{HEIGHT * 2})")


if __name__ == "__main__":
    main()
