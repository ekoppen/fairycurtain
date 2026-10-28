"""
Mapping between a 2D grid (x, y) and the physical pixel index on the curtain.

An LED curtain consists of vertical strands. Usually the index runs top to
bottom along a strand and the next strand starts at the top again, but some
curtains are 'serpentine' (alternately up/down). You only know which one you
have after calibrating (see /api/test/pixel and the 'Calibrate' button in the
web editor). Everything is configurable in config.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Sequence, Tuple

RGB = Tuple[int, int, int]
Grid = List[List[RGB]]  # grid[y][x]


@dataclass
class Layout:
    width: int = 20
    height: int = 20
    order: str = "column"       # "column" (strands) or "row"
    serpentine: bool = False    # alternate direction per strand/row
    flip_x: bool = False        # first strand on the right instead of the left
    flip_y: bool = False        # index 0 at the bottom instead of the top
    pixel_count: int | None = None  # defaults to width*height

    @property
    def count(self) -> int:
        return self.pixel_count or self.width * self.height

    def index_of(self, x: int, y: int) -> int:
        if self.flip_x:
            x = self.width - 1 - x
        if self.flip_y:
            y = self.height - 1 - y
        if self.order == "column":
            major, minor, minor_len = x, y, self.height
        else:
            major, minor, minor_len = y, x, self.width
        if self.serpentine and major % 2 == 1:
            minor = minor_len - 1 - minor
        return major * minor_len + minor

    def xy_of(self, index: int) -> Tuple[int, int]:
        """Inverse of index_of (for calibration display)."""
        for y in range(self.height):
            for x in range(self.width):
                if self.index_of(x, y) == index:
                    return x, y
        raise ValueError(index)

    def grid_to_pixels(self, grid: Grid) -> List[RGB]:
        pixels: List[RGB] = [(0, 0, 0)] * self.count
        for y in range(min(self.height, len(grid))):
            row = grid[y]
            for x in range(min(self.width, len(row))):
                i = self.index_of(x, y)
                if i < len(pixels):
                    pixels[i] = tuple(int(c) & 0xFF for c in row[x])  # type: ignore
        return pixels

    def blank(self, color: RGB = (0, 0, 0)) -> Grid:
        return [[color for _ in range(self.width)] for _ in range(self.height)]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Layout":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
