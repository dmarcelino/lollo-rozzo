"""App icons, drawn on first use so the repo carries no binary files: a blue
square with a white browser window (full-bleed, so it also works as maskable)."""

from __future__ import annotations

from functools import cache

from lollo_common import icons

BG = (21, 101, 192)
FG = (255, 255, 255)


def _pixel(x: float, y: float) -> tuple[int, int, int]:
    # A window outline with a title bar, in 0..1 coordinates.
    inside = 0.2 <= x <= 0.8 and 0.25 <= y <= 0.75
    if not inside:
        return BG
    border = x < 0.25 or x > 0.75 or y > 0.7 or y < 0.38
    return FG if border else BG


@cache
def png(size: int) -> bytes:
    return icons.png(size, _pixel)
