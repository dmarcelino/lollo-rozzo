"""The page's icon, drawn on first use so the repo carries no binary files: the
lollo pages' look (an #7aa2f7 square) with an #0b1020 bell (full-bleed, so it
also works as maskable). Notifications show it too."""

from __future__ import annotations

from functools import cache

from lollo_common import icons

BG = (0x7A, 0xA2, 0xF7)
FG = (0x0B, 0x10, 0x20)


def _pixel(x: float, y: float) -> tuple[int, int, int]:
    # Coordinates in 0..1. A knob, a dome, flaring sides, a rim and a clapper.
    dx = abs(x - 0.5)
    if dx**2 + (y - 0.24) ** 2 <= 0.035**2:
        return FG
    if y < 0.42 and dx**2 + (y - 0.42) ** 2 <= 0.16**2:
        return FG
    if 0.42 <= y < 0.64 and dx <= 0.16 + (y - 0.42) * 0.4:
        return FG
    if 0.64 <= y <= 0.69 and dx <= 0.29:
        return FG
    if dx**2 + (y - 0.75) ** 2 <= 0.055**2:
        return FG
    return BG


@cache
def png(size: int) -> bytes:
    return icons.png(size, _pixel)
