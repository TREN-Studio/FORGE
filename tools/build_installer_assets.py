from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "installer" / "assets"
ICON_PATH = ROOT / "assets" / "forge-desktop-icon.png"
WIZARD_PATH = ASSETS_DIR / "forge-wizard.bmp"
WIZARD_SMALL_PATH = ASSETS_DIR / "forge-wizard-small.bmp"


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend(
            [
                "C:/Windows/Fonts/bahnschrift.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/segoeuib.ttf",
            ]
        )
    candidates.extend(
        [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _make_gradient(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, "#050505")
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        r = int(6 + (22 - 6) * ratio)
        g = int(6 + (16 - 6) * ratio)
        b = int(8 + (12 - 8) * ratio)
        draw.line((0, y, width, y), fill=(r, g, b))

    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-42, -36, width * 0.86, height * 0.54), fill=(255, 117, 35, 118))
    glow_draw.ellipse((width * 0.25, height * 0.55, width * 1.1, height * 1.05), fill=(255, 168, 72, 42))
    glow = glow.filter(ImageFilter.GaussianBlur(24))
    return Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")


def build_wizard() -> None:
    size = (164, 314)
    image = _make_gradient(size).convert("RGBA")
    draw = ImageDraw.Draw(image)

    # Geometric frame
    draw.rounded_rectangle((18, 22, 146, 292), radius=24, outline=(255, 255, 255, 18), width=1)

    icon = Image.open(ICON_PATH).convert("RGBA").resize((82, 82))
    icon_glow = Image.new("RGBA", size, (0, 0, 0, 0))
    icon_glow.paste(icon, (41, 42), icon)
    icon_glow = icon_glow.filter(ImageFilter.GaussianBlur(10))
    image = Image.alpha_composite(image, icon_glow)
    image.alpha_composite(icon, (41, 42))

    headline_font = _load_font(24, bold=True)
    subhead_font = _load_font(11, bold=False)
    micro_font = _load_font(9, bold=False)

    draw = ImageDraw.Draw(image)
    draw.text((29, 146), "FORGE", fill="#FFE7D0", font=headline_font)
    draw.text((29, 178), "Desktop Operator", fill="#FFC180", font=subhead_font)

    body = (
        "Serious local AI runtime\n"
        "Live model routing\n"
        "Windows installer build"
    )
    draw.multiline_text((29, 208), body, fill="#D0C0B0", font=micro_font, spacing=6)

    draw.rounded_rectangle((29, 268, 135, 286), radius=9, fill=(255, 107, 26, 220))
    badge_font = _load_font(8, bold=True)
    draw.text((40, 272), "TREN STUDIO BUILD", fill="#1E1007", font=badge_font)

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(WIZARD_PATH)


def build_small() -> None:
    size = (55, 55)
    image = _make_gradient(size).convert("RGBA")
    icon = Image.open(ICON_PATH).convert("RGBA").resize((34, 34))
    image.alpha_composite(icon, (10, 10))
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(WIZARD_SMALL_PATH)


def main() -> None:
    build_wizard()
    build_small()
    print(f"Built {WIZARD_PATH}")
    print(f"Built {WIZARD_SMALL_PATH}")


if __name__ == "__main__":
    main()
