# -*- coding: utf-8 -*-
"""Sunnyday Technologies brand kit for MARB / CADCLAW figures.

Hard-coded so EVERY generated figure is consistent and legible. All figure
builders (scoreboard, scatter, charts) must import from here rather than set
their own fonts/colors/sizes.

  Display font : Nulshock Bd  (brand display; repo-local, licensed for image use)
  Body font    : Segoe UI     (clean, highly legible; the brand web font Raleway
                 is NOT installed locally and web fonts can't be served from a
                 static image, so we use the most legible installed humanist sans)
  Colors       : navy #013a73 / green #97d700 / white  (brand-guide/brand.md)

LEGIBILITY RULES (paramount for figures that explain complex topics):
  * Type is LARGE on purpose (see SIZES) — readable at feed/thumbnail scale.
  * Use as FEW words as possible. Let the data carry the message.
  * One short line per description; never a paragraph inside a figure.
"""
from pathlib import Path
import matplotlib
from matplotlib import font_manager as fm

# --- brand colors (brand-guide/brand.md) -----------------------------------
NAVY = "#013a73"; GREEN = "#97d700"; WHITE = "#ffffff"
INK = "#0e1b2a"; INK_DIM = "#33404a"; GREY = "#5b6b78"; GRID = "#e6eaee"
GREEN_DK = "#4e7a00"   # green dark enough to read as text on white
COLORS = dict(navy=NAVY, green=GREEN, white=WHITE, ink=INK, ink_dim=INK_DIM,
              grey=GREY, grid=GRID, green_dk=GREEN_DK)

# --- fonts ------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
_NUL = _REPO / "assets" / "fonts" / "Nulshock Bd.otf"
if _NUL.exists():
    fm.fontManager.addfont(str(_NUL))
    NUL = fm.FontProperties(fname=str(_NUL))           # display headings
else:                                                  # graceful fallback
    NUL = fm.FontProperties(family="sans-serif", weight="bold")
BODY = "Segoe UI"

# --- legibility sizes (deliberately large; raised floor 2026-05-29 for on-white
# article readability — small charts were hard to read) ----------------------
SIZES = dict(big=42, title=28, subtitle=18, axis=19, tick=16, label=17, note=15)

# Padded white backing for in-chart text labels. ALWAYS put this behind point
# labels/annotations so text never collides with gridlines, target lines, or
# neighboring marks — every label gets its own legibility border.
LABEL_BBOX = dict(boxstyle="round,pad=0.40", facecolor="white", edgecolor="none", alpha=0.92)

def apply_base():
    """Set legible, brand-consistent matplotlib defaults. Call once per figure."""
    matplotlib.rcParams.update({
        "font.family": BODY,
        "font.size": SIZES["label"],
        "axes.titlesize": SIZES["title"],
        "axes.labelsize": SIZES["axis"],
        "xtick.labelsize": SIZES["tick"],
        "ytick.labelsize": SIZES["tick"],
        "text.color": INK, "axes.labelcolor": INK,
        "xtick.color": GREY, "ytick.color": GREY,
        "axes.edgecolor": "#c3ccd4",
        "figure.facecolor": WHITE, "savefig.facecolor": WHITE,
    })
