"""
Built-in gallery of 20x20 pixel-art pictures (seasonal themes and emoji).

The curtain only knows a hue per pixel (plus white and off), so the art uses fully
saturated colors; '.' is off. Most sprites are drawn as the left half of each row and
mirrored. Other layout sizes are rendered by scaling with nearest-neighbour.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

from . import render as R
from .layout import Grid

SIZE = 20
PALETTE = {
    ".": (0, 0, 0),         # off
    "R": (255, 0, 0), "O": (255, 136, 0), "Y": (255, 220, 0), "G": (0, 200, 0),
    "C": (0, 220, 220), "B": (0, 80, 255), "P": (150, 0, 255), "M": (255, 60, 160),
    "W": (255, 255, 255),
}


def _mirror(half: List[str]) -> List[str]:
    return [r + r[::-1] for r in half]


def _pad(rows: List[str]) -> List[str]:
    return rows + ["." * SIZE] * (SIZE - len(rows))


def _rainbow() -> List[str]:
    rows = []
    for y in range(SIZE):
        row = ""
        for x in range(SIZE):
            d = math.hypot(x - 9.5, y - 17)
            i = int((d - 5) / 1.6)
            if y <= 17 and 5 <= d < 14.6:
                row += "ROYGBP"[min(i, 5)]
            elif y >= 16 and (x < 4 or x > 15):
                row += "W"
            else:
                row += "."
        rows.append(row)
    return rows


_HALVES: Dict[str, tuple] = {
    # id: (title, category, half rows)
    "pumpkin": ("Pumpkin", "Halloween", [
        "..........", "........GG", ".........G", ".....OOOGG", "...OOOOOOO", "..OOOOOOOO", ".OOOOOOOOO",
        ".OOOOOOOOO", "OOO..OOOOO", "OO....OOOO", "OOOOOOOOOO", "OOOO......", "OOO.O.O.O.", "OOOOO.O.O.",
        ".OOOOOOOOO", "..OOOOOOOO", "...OOOOOOO", ".....OOOOO"]),
    "ghost": ("Ghost", "Halloween", [
        "..........", ".....WWWWW", "...WWWWWWW", "..WWWWWWWW", ".WWWWWWWWW", ".WWW..WWWW", ".WWW..WWWW",
        ".WWWWWWWWW", ".WWWWWWW..", ".WWWWWWW..", ".WWWWWWWWW", ".WWWWWWWWW", ".WWWWWWWWW", ".WWWWWWWWW",
        ".WWWWWWWWW", ".WW.WWW.WW", ".W...W...W"]),
    "bat": ("Bat", "Halloween", [
        "..........", "..........", "........P.", "........PP", "P.......PP", "PP.....PPP", "PPP...PPRP",
        "PPPP.PPPPP", "PPPPPPPPPP", "PPP.PPPPPP", "P...PPPPPP", "P....PPPPP", ".......PPP", "........PP"]),
    "skull": ("Skull", "Halloween", [
        "..........", ".....WWWWW", "...WWWWWWW", "..WWWWWWWW", ".WWWWWWWWW", ".WWWWWWWWW", ".WWW..WWWW",
        ".WWW..WWWW", ".WWWWWWWWW", ".WWWWWWWW.", ".WWWWWWWWW", "..WWWWWWWW", "..WWWWWWWW", "..W.WW.WWW",
        "..W.WW.WWW"]),
    "tree": ("Tree", "Christmas", [
        ".........Y", "........YY", ".........Y", ".........G", "........GG", ".......GGR", "......GGGG",
        ".....GBGGG", "....GGGGGY", "...GGGGGGG", "..GGRGGGGG", ".GGGGGGBGG", "GGGGGGGGGG", "........OO",
        "........OO", "........OO"]),
    "snowman": ("Snowman", "Christmas", [
        "..........", "......BBBB", "......BBBB", "....BBBBBB", "......WWWW", ".....WWWWW", ".....W.WWW",
        ".....WWWWO", ".....WWWWW", "......RRRR", "....WWWWWW", "...WWWWWWW", "...WWWWWWB", "..WWWWWWWW",
        "..WWWWWWWR", "..WWWWWWWW", "..WWWWWWWB", "...WWWWWWW", ".....WWWWW"]),
    "heart": ("Heart", "Love & spring", [
        "..........", "..........", "..RRRR....", ".RWRRRR...", "RRRRRRRRR.", "RRRRRRRRRR", "RRRRRRRRRR",
        "RRRRRRRRRR", ".RRRRRRRRR", "..RRRRRRRR", "...RRRRRRR", "....RRRRRR", ".....RRRRR", "......RRRR",
        ".......RRR", "........RR", ".........R"]),
    "egg": ("Easter egg", "Love & spring", [
        "..........", "..........", ".......MMM", "......MMMM", ".....MMMMM", ".....YYYYY", "....MMMMMM",
        "...MMMMMMM", "...CMCMCMC", "...MCMCMCM", "...MMMMMMM", "...MMMMMMM", "..YYYYYYYY", "..MMMMMMMM",
        "...MMMMMMM", "....MMMMMM", ".....MMMMM", ".......MMM"]),
    "smiley": ("Smiley", "Emoji", [
        "..........", ".....YYYYY", "...YYYYYYY", "..YYYYYYYY", ".YYYYYYYYY", "YYYYYYYYYY", "YYYY..YYYY",
        "YYYY..YYYY", "YYYYYYYYYY", "YYYYYYYYYY", "YYY.YYYYYY", "YYYY.YYYYY", "YYYYY.....", "YYYYYYYYYY",
        ".YYYYYYYYY", "..YYYYYYYY", "...YYYYYYY", ".....YYYYY"]),
    "sun": ("Sun", "Emoji", [
        ".........O", ".........O", "..O.......", "...O......", "......YYYY", "....YYYYYY", "...YYYYYYY",
        "...YYYYYYY", "...YYYYYYY", "OO.YYYYYYY", "OO.YYYYYYY", "...YYYYYYY", "...YYYYYYY", "...YYYYYYY",
        "....YYYYYY", "......YYYY", "...O......", "..O.......", ".........O", ".........O"]),
    "star": ("Star", "Emoji", [
        ".........Y", ".........Y", "........YY", "........YY", ".......YYY", "YYYYYYYYYY", ".YYYYYYYYY",
        "..YYYYYYYY", "...YYYYYYY", "....YYYYYY", "....YYYYYY", "...YYYYYYY", "..YYYYY..Y", "..YYYY....",
        ".YYY......"]),
}

_FULL: Dict[str, tuple] = {"rainbow": ("Rainbow", "Emoji", _rainbow())}


def _build() -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for gid, (title, cat, half) in _HALVES.items():
        out[gid] = {"title": title, "category": cat, "rows": _pad(_mirror(half))}
    for gid, (title, cat, rows) in _FULL.items():
        out[gid] = {"title": title, "category": cat, "rows": _pad(rows)}
    for gid, item in out.items():
        assert len(item["rows"]) == SIZE and all(len(r) == SIZE for r in item["rows"]), gid
        assert set("".join(item["rows"])) <= set(PALETTE), gid
    return out


ITEMS = _build()


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % rgb


def listing() -> List[dict]:
    """For the UI: id, title, category and the 400 hex colors (row by row)."""
    return [{"id": gid, "title": it["title"], "category": it["category"],
             "hex": [_hex(PALETTE[c]) for r in it["rows"] for c in r]} for gid, it in ITEMS.items()]


def grid(gid: str, width: int = SIZE, height: int = SIZE) -> Optional[Grid]:
    it = ITEMS.get(gid)
    if it is None:
        return None
    img = Image.new("RGB", (SIZE, SIZE))
    img.putdata([PALETTE[c] for r in it["rows"] for c in r])
    return R.image_to_grid(img, width, height, fit="contain", resample=Image.NEAREST)


# ----------------------------------------------------------- user pictures ----

USER_CATEGORY = "My pictures"
MAX_USER_ITEMS = 200
MAX_TITLE_CHARS = 40
USER_ID_RE = re.compile(r"^u_[0-9a-f]{12}$")
# Only common formats are stored; Pillow can open far more exotic ones.
ALLOWED_FORMATS = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}
FITS = ("cover", "contain", "stretch")


def clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", "".join(ch for ch in (title or "") if ch.isprintable())).strip()
    return t[:MAX_TITLE_CHARS] or "Untitled"


class UserGallery:
    """Pictures and GIFs saved by the user: files in a directory plus an index.json with
    title, fit and a 20x20 thumbnail, so listing never has to decode images."""

    def __init__(self, directory) -> None:
        self.dir = Path(directory)
        self.index_path = self.dir / "index.json"

    def _load(self) -> Dict[str, dict]:
        try:
            data = json.loads(self.index_path.read_text())
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}

    def _save(self, index: Dict[str, dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index))
        os.replace(tmp, self.index_path)

    @staticmethod
    def _public(gid: str, it: dict) -> dict:
        return {"id": gid, "title": it["title"], "category": USER_CATEGORY, "hex": it["thumb"],
                "user": True, "animated": bool(it.get("animated"))}

    def listing(self) -> List[dict]:
        idx = self._load()
        return [self._public(g, it) for g, it in sorted(idx.items(), key=lambda kv: -kv[1].get("created", 0))]

    def add(self, title: str, data: bytes, fit: str = "cover") -> dict:
        """Validate and store. Raises ValueError for bad input, OSError if the directory is not writable."""
        fit = fit if fit in FITS else "cover"
        try:  # decoding problems are the caller's fault (ValueError), unlike write errors (OSError)
            img = R.bytes_to_image(data)  # also enforces the dimension limit
            if img.format not in ALLOWED_FORMATS:
                raise ValueError("Only PNG, JPEG, GIF and WEBP images can be saved")
            animated = bool(getattr(img, "is_animated", False))
            img.seek(0)
            thumb = [_hex(px) for row in R.image_to_grid(img.convert("RGB"), SIZE, SIZE, fit=fit) for px in row]
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Not a valid image: {e}")
        idx = self._load()
        if len(idx) >= MAX_USER_ITEMS:
            raise ValueError(f"The gallery is full ({MAX_USER_ITEMS} pictures); delete some first")
        gid = "u_" + uuid.uuid4().hex[:12]
        fname = f"{gid}.{ALLOWED_FORMATS[img.format]}"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / fname).write_bytes(data)
        idx[gid] = {"title": clean_title(title), "file": fname, "fit": fit, "animated": animated,
                    "thumb": thumb, "created": time.time()}
        self._save(idx)
        return self._public(gid, idx[gid])

    def get(self, gid: str):
        """(item, bytes) or None. Only ids we generated are accepted, so no path tricks."""
        it = self._load().get(gid) if USER_ID_RE.match(gid or "") else None
        if not it:
            return None
        try:
            return it, (self.dir / it["file"]).read_bytes()
        except FileNotFoundError:
            return None

    def delete(self, gid: str) -> bool:
        idx = self._load()
        it = idx.pop(gid, None) if USER_ID_RE.match(gid or "") else None
        if not it:
            return False
        (self.dir / it["file"]).unlink(missing_ok=True)
        self._save(idx)
        return True
