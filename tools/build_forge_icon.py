from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def build_icon(size: int = 1024) -> Image.Image:
    image = Image.new("RGBA", (size, size), (8, 8, 8, 255))
    draw = ImageDraw.Draw(image)

    radius = size // 6
    margin = size // 12
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=radius,
        fill=(12, 12, 12, 255),
        outline=(40, 40, 40, 255),
        width=max(4, size // 128),
    )

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (size * 0.22, size * 0.18, size * 0.76, size * 0.72),
        fill=(255, 107, 26, 96),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=size // 14))
    image.alpha_composite(glow)

    stem_left = int(size * 0.30)
    stem_right = int(size * 0.44)
    top = int(size * 0.20)
    bottom = int(size * 0.80)
    cross_right = int(size * 0.73)
    cross_height = int(size * 0.115)

    draw.rounded_rectangle(
        (stem_left, top, stem_right, bottom),
        radius=size // 40,
        fill=(255, 107, 26, 255),
    )
    draw.rounded_rectangle(
        (stem_right - size * 0.02, top, cross_right, top + cross_height),
        radius=size // 40,
        fill=(242, 240, 236, 255),
    )
    draw.rounded_rectangle(
        (stem_right - size * 0.02, int(size * 0.44), int(size * 0.64), int(size * 0.44) + cross_height),
        radius=size // 40,
        fill=(242, 240, 236, 255),
    )
    draw.rounded_rectangle(
        (int(size * 0.22), int(size * 0.80), int(size * 0.78), int(size * 0.87)),
        radius=size // 28,
        fill=(70, 70, 70, 255),
    )
    draw.ellipse(
        (int(size * 0.57), int(size * 0.76), int(size * 0.64), int(size * 0.83)),
        fill=(255, 170, 60, 255),
    )
    return image


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    png_path = assets / "forge-desktop-icon.png"
    ico_path = assets / "forge-desktop-icon.ico"

    image = build_icon(1024)
    image.save(png_path)
    image.save(ico_path, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"Created {png_path}")
    print(f"Created {ico_path}")


if __name__ == "__main__":
    main()

