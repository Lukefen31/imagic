from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer


ICON_MASTER_SIZE = 1024
ICON_PREVIEW_SIZE = 256
ICON_PADDING_RATIO = 0.04
ICON_CORNER_RADIUS_RATIO = 0.18
ICO_SIZES = [(256, 256), (128, 128), (96, 96), (64, 64), (48, 48), (40, 40), (32, 32), (24, 24), (20, 20), (16, 16)]


def render_svg_to_png(svg_path: Path, output_path: Path, canvas_size: int) -> None:
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise RuntimeError(f"Failed to load SVG: {svg_path}")

    image = QImage(canvas_size, canvas_size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target_rect = QRectF(0, 0, canvas_size, canvas_size)
        renderer.render(painter, target_rect)
    finally:
        painter.end()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(output_path), "PNG"):
        raise RuntimeError(f"Failed to save PNG: {output_path}")


def create_windows_icon_master(rendered_png_path: Path, master_png_path: Path, canvas_size: int, padding_ratio: float, corner_radius_ratio: float) -> None:
    with Image.open(rendered_png_path) as source_image:
        source = source_image.convert("RGBA")
        inset = round(canvas_size * padding_ratio)
        inner_size = canvas_size - (inset * 2)
        resized = source.resize((inner_size, inner_size), Image.Resampling.LANCZOS)

        mask = Image.new("L", (inner_size, inner_size), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle(
            (0, 0, inner_size - 1, inner_size - 1),
            radius=round(inner_size * corner_radius_ratio),
            fill=255,
        )

        canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        canvas.paste(resized, (inset, inset), mask)
        canvas.save(master_png_path)


def create_preview_png(master_png_path: Path, preview_png_path: Path, size: int) -> None:
    with Image.open(master_png_path) as image:
        preview = image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        preview.save(preview_png_path)


def create_ico(master_png_path: Path, ico_path: Path) -> None:
    with Image.open(master_png_path) as image:
        icon = image.convert("RGBA")
        icon.save(ico_path, format="ICO", sizes=ICO_SIZES)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Windows branding assets from source SVG files.")
    parser.add_argument("--icon-svg", required=True, type=Path, help="Path to the source app icon SVG.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory to write generated branding assets to.")
    parser.add_argument(
        "--icon-padding-ratio",
        type=float,
        default=ICON_PADDING_RATIO,
        help="Transparent padding ratio to apply around the rendered icon.",
    )
    parser.add_argument(
        "--icon-corner-radius-ratio",
        type=float,
        default=ICON_CORNER_RADIUS_RATIO,
        help="Rounded corner radius ratio to apply to the finished Windows icon.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    icon_svg = args.icon_svg.resolve()
    output_dir = args.output_dir.resolve()

    if not icon_svg.is_file():
        raise FileNotFoundError(f"Icon SVG not found: {icon_svg}")

    rendered_png_path = output_dir / "imagic-app-icon-rendered.png"
    master_png_path = output_dir / "imagic-app-icon-hires.png"
    preview_png_path = output_dir / "imagic-app-icon.png"
    ico_path = output_dir / "imagic-app-icon.ico"

    render_svg_to_png(icon_svg, rendered_png_path, ICON_MASTER_SIZE)
    create_windows_icon_master(
        rendered_png_path,
        master_png_path,
        ICON_MASTER_SIZE,
        args.icon_padding_ratio,
        args.icon_corner_radius_ratio,
    )
    create_preview_png(master_png_path, preview_png_path, ICON_PREVIEW_SIZE)
    create_ico(master_png_path, ico_path)

    rendered_png_path.unlink(missing_ok=True)

    print(f"Generated {master_png_path}")
    print(f"Generated {preview_png_path}")
    print(f"Generated {ico_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - packaging utility
        print(str(exc), file=sys.stderr)
        raise