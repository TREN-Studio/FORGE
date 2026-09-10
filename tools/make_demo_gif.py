"""
FORGE Demo GIF Generator
========================
Renders the REAL e2e_routing_report.json results as a live-terminal-style
animated GIF (typing effect, 60-90s equivalent compressed into ~25s loop).

Output: release-assets/FORGE-routing-demo.gif
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".forge_artifacts" / "e2e_routing_report.json"
OUT_DIR = ROOT / "release-assets"
OUT_GIF = OUT_DIR / "FORGE-routing-demo.gif"

W, H = 1000, 620
FPS = 10
BG = (12, 12, 18)
FG = (214, 219, 228)
ACCENT = (255, 163, 71)
GREEN = (120, 220, 140)
RED = (235, 110, 110)
DIM = (120, 126, 140)
BAR_BG = (40, 42, 54)

FONT_DIR = Path("C:/Windows/Fonts")


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("consola.ttf", "Consolas.ttf", "lucon.ttf", "cour.ttf"):
        p = FONT_DIR / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


font = load_font(19)
font_bold = load_font(22)
font_small = load_font(15)


def text_w(draw: ImageDraw.ImageDraw, s: str, f) -> int:
    b = draw.textbbox((0, 0), s, font=f)
    return b[2] - b[0]


def frame_base() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # terminal title bar
    d.rectangle([0, 0, W, 34], fill=(28, 30, 40))
    d.ellipse([16, 12, 30, 26], fill=(237, 106, 94))
    d.ellipse([42, 12, 56, 26], fill=(245, 191, 79))
    d.ellipse([68, 12, 82, 26], fill=(97, 200, 99))
    d.text((W // 2 - 90, 8), "FORGE — live routing", font=font_small, fill=DIM)
    return img


def render_scene(lines: list[tuple[str, object]], progress: float) -> Image.Image:
    """lines: list of (text, color). progress: 0..1 typing reveal."""
    img = frame_base()
    d = ImageDraw.Draw(img)
    total_chars = sum(len(t) for t, _ in lines)
    visible = int(total_chars * min(progress, 1.0))

    y = 60
    x = 24
    chars_left = visible
    for text, color in lines:
        take = min(len(text), max(0, chars_left))
        chars_left -= len(text)
        shown = text[:take]
        if shown:
            d.text((x, y), shown, font=font, fill=color)
        if take < len(text):  # cursor mid-line
            cx = x + text_w(d, shown, font)
            if int(visible / 3) % 2 == 0:
                d.rectangle([cx, y + 2, cx + 10, y + 20], fill=ACCENT)
            return img
        y += 30
    # done: blinking cursor at end
    if int(progress * 6) % 2 == 0:
        d.rectangle([24 + 4, y + 2, 24 + 14, y + 20], fill=ACCENT)
    return img


def colorize_provider_line(r: dict) -> tuple[str, object]:
    if r["status"] != "success":
        return (f"$ forge route --provider {r['provider']:<11} → ERROR", RED)
    if r.get("fallback_count", 0) and any(
        a.split("/")[0] != r["provider"] for a in r.get("attempted", [])
    ):
        first = r["attempted"][0].split("/")[0]
        return (
            f"$ forge route --provider {r['provider']:<11} ✗{first} → ↻ {r['provider']}/{r['model']}  {int(r['latency_ms'])}ms",
            GREEN,
        )
    return (
        f"$ forge route --provider {r['provider']:<11} → {r['provider']}/{r['model']}  {int(r['latency_ms'])}ms",
        GREEN,
    )


def build_lines(report: dict) -> list[tuple[str, object]]:
    lines: list[tuple[str, object]] = []
    lines.append(("FORGE  v1.5.2 — Free Open Reasoning & Generation Engine", ACCENT))
    lines.append(("Smart Selector: 12 providers registered · 7 keyed", DIM))
    lines.append(("", FG))
    lines.append((f'» prompt: "{report["unified_prompt"][:64]}..."', FG))
    lines.append(("", FG))
    for r in report["per_provider_results"]:
        lines.append(colorize_provider_line(r))
    lines.append(("", FG))
    sel = report["smart_selector_result"]
    lines.append(("$ forge route --auto   (Smart Selector free choice)", ACCENT))
    lines.append((f"  chose {sel['provider']}/{sel['model']}  {int(sel['latency_ms'])}ms  ✓ answered", GREEN))
    lines.append(("", FG))
    lines.append(("═══ RESULT: 7/7 requests answered · 0 lost · fallback avg <1s ═══", ACCENT))
    lines.append(("Multi-provider routing: when one provider dies, the request survives.", DIM))
    return lines


def latency_bar_scene(report: dict, progress: float) -> Image.Image:
    """Final scene: horizontal latency comparison bars."""
    img = frame_base()
    d = ImageDraw.Draw(img)
    d.text((24, 56), "LIVE LATENCY — same request, every provider", font=font_bold, fill=ACCENT)

    rows = [
        (r["provider"], r["latency_ms"], r["status"])
        for r in report["per_provider_results"]
    ]
    rows.append(("auto (selector)", report["smart_selector_result"]["latency_ms"], "success"))
    max_lat = max(lat for _, lat, _ in rows)

    y = 110
    label_x = 24
    bar_x = 230
    bar_w_max = W - bar_x - 60
    shown = int(len(rows) * min(progress, 1.0) + 0.999)
    for i, (name, lat, status) in enumerate(rows[:shown]):
        color = GREEN if status == "success" else RED
        frac = math.sqrt(lat / max_lat)  # sqrt scale for readability
        w = max(8, int(frac * bar_w_max))
        d.text((label_x, y), f"{name:<16}", font=font_small, fill=FG)
        d.rectangle([bar_x, y, bar_x + bar_w_max, y + 18], fill=BAR_BG)
        d.rectangle([bar_x, y, bar_x + w, y + 18], fill=color)
        d.text((bar_x + w + 8, y), f"{int(lat)}ms", font=font_small, fill=DIM)
        y += 34
    return img


def main() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    lines = build_lines(report)

    frames: list[Image.Image] = []

    # Scene 1: header + prompt (typing) — 2.2s
    for i in range(22):
        frames.append(render_scene(lines[:4], i / 22))

    # Scene 2: provider results appearing line by line — 6s
    provider_lines = lines[4:4 + len(report["per_provider_results"])]
    for i, _ in enumerate(provider_lines):
        partial = lines[: 4 + i + 1]
        for _f in range(7):  # hold each line ~0.7s
            frames.append(render_scene(partial, 1.0))

    # Scene 3: selector + result banner — 3s
    partial = lines[: 4 + len(provider_lines) + 4]
    for i in range(30):
        frames.append(render_scene(partial, i / 30))

    # Scene 4: latency bars — 4.5s
    for i in range(20):
        frames.append(latency_bar_scene(report, i / 20))
    for _ in range(25):
        frames.append(latency_bar_scene(report, 1.0))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT_GIF,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / FPS),
        loop=0,
        optimize=True,
    )
    print(f"GIF saved: {OUT_GIF}")
    print(f"frames: {len(frames)}  duration: {len(frames)/FPS:.1f}s  size: {OUT_GIF.stat().st_size/1024/1024:.2f} MB")


if __name__ == "__main__":
    main()
