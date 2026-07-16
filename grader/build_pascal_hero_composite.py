#!/usr/bin/env python3
"""Composite LinkedIn hero (1.91:1) for the MARB-A Pascal cohort.

Left: the two real Pascal renders stacked (trap on top, pass below), tightly
cropped. Right: headline + per-model scoreboard using the validated chart
palette. 2400x1254 output.
"""

from PIL import Image, ImageDraw, ImageFont

BASE = r"D:/SunnydayTech/MARB/results/figures/"
NAVY = "#12224a"
INK2 = "#5f5e57"
RULE = "#e4e3dc"

W, H = 2400, 1254
LEFT_W = 1198
PANEL_H = H // 2  # 627


def load_crop(name):
    img = Image.open(BASE + name)  # 1300x680
    # center-crop to 1147x600 (1.911 aspect), house centered
    box = (76, 40, 76 + 1147, 40 + 600)
    return img.crop(box).resize((LEFT_W, PANEL_H), Image.LANCZOS)


def font(size, bold=False):
    try:
        return ImageFont.truetype("segoeuib.ttf" if bold else "segoeui.ttf", size)
    except OSError:
        return ImageFont.load_default()


def main():
    canvas = Image.new("RGB", (W, H), "white")
    canvas.paste(load_crop("pascal_ph1_fable5_medium_01_hero3d.png"), (0, 0))
    canvas.paste(load_crop("pascal_ph1_codex55_xhigh_01_hero3d.png"), (0, PANEL_H))
    d = ImageDraw.Draw(canvas)

    # thin divider between the two renders and against the right panel
    d.rectangle([0, PANEL_H - 2, LEFT_W, PANEL_H + 2], fill="white")
    d.rectangle([LEFT_W, 0, LEFT_W + 4, H], fill="white")

    # small on-image tags
    def tag(y, text, color):
        f = font(30, bold=True)
        tw = d.textlength(text, font=f)
        d.rounded_rectangle([24, y, 24 + tw + 36, y + 54], radius=10, fill="white", outline=RULE, width=2)
        d.text((42, y + 9), text, fill=color, font=f)

    tag(20, "Claude Fable 5 — degrees", "#b45309")
    tag(PANEL_H + 22, "GPT-5.5 Codex — radians", "#1d4ed8")

    # ---- right panel ----
    x0 = LEFT_W + 64
    xmax = W - 64
    y = 78
    for line in ("Three AIs.", "Same house.", "One hidden", "units trap."):
        d.text((x0, y), line, fill=NAVY, font=font(92, bold=True))
        y += 104
    y += 26
    d.text((x0, y), "All three placed 52 elements at 0.0 mm.", fill=INK2, font=font(36))
    y += 48
    d.text((x0, y), "An undocumented rotation field did the rest.", fill=INK2, font=font(36))
    y += 82

    rows = [
        ("#2a78d6", "GPT-5.5 · Codex", "100%", "9.9 min · all gates pass"),
        ("#1baf7a", "Claude Opus 4.8 · max", "31.6%", "19.9 min · collisions fail"),
        ("#eda100", "Claude Fable 5 · medium", "31.6%", "117.8 min · collisions fail"),
    ]
    for color, name, pct, sub in rows:
        d.rounded_rectangle([x0, y + 6, x0 + 26, y + 84], radius=6, fill=color)
        d.text((x0 + 52, y), name, fill=NAVY, font=font(40, bold=True))
        d.text((x0 + 52, y + 48), sub, fill=INK2, font=font(30))
        f_big = font(64, bold=True)
        tw = d.textlength(pct, font=f_big)
        d.text((xmax - tw, y + 8), pct, fill=NAVY, font=f_big)
        y += 122

    y += 14
    d.line([x0, y, xmax, y], fill=RULE, width=3)
    y += 24
    d.text((x0, y), "MARB-A architecture lane · rendered by Pascal itself", fill=INK2, font=font(28))
    d.text((x0, y + 40), "marb.cadclaw.io/pascal", fill=NAVY, font=font(30, bold=True))

    out = BASE + "pascal_ph1_hero_composite.png"
    canvas.save(out)
    print("wrote", out, canvas.size)


if __name__ == "__main__":
    main()
