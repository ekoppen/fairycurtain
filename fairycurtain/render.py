"""
Afbeeldingen, GIF's en tekst omzetten naar frames van width x height pixels.

Een 'frame' is een Grid: list[rows][cols] van (r, g, b).
Een animatie is een lijst van (frame, duur_in_seconden).
"""
from __future__ import annotations

import io
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence

from .layout import Grid, RGB

Frame = Tuple[Grid, float]


def _apply_levels(img: Image.Image, brightness: float, gamma: float) -> Image.Image:
    if brightness == 1.0 and gamma == 1.0:
        return img
    lut = []
    for i in range(256):
        v = (i / 255.0) ** gamma * brightness
        lut.append(max(0, min(255, int(round(v * 255)))))
    return img.point(lut * 3)


def image_to_grid(
    img: Image.Image,
    width: int,
    height: int,
    fit: str = "cover",          # cover | contain | stretch
    brightness: float = 1.0,     # 0..1 (LED's zijn fel; 0.3-0.6 is vaak prettig)
    gamma: float = 1.0,
    background: RGB = (0, 0, 0),
    resample=Image.LANCZOS,
) -> Grid:
    img = img.convert("RGB")
    if fit == "stretch":
        img = img.resize((width, height), resample)
    elif fit == "contain":
        img = ImageOps.pad(img, (width, height), method=resample, color=background)
    else:
        img = ImageOps.fit(img, (width, height), method=resample)
    img = _apply_levels(img, brightness, gamma)
    px = img.load()
    return [[tuple(px[x, y]) for x in range(width)] for y in range(height)]  # type: ignore


def bytes_to_image(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def image_bytes_to_grid(data: bytes, width: int, height: int, **kw) -> Grid:
    return image_to_grid(bytes_to_image(data), width, height, **kw)


def gif_bytes_to_frames(
    data: bytes, width: int, height: int, default_duration: float = 0.1, **kw
) -> List[Frame]:
    img = bytes_to_image(data)
    frames: List[Frame] = []
    for fr in ImageSequence.Iterator(img):
        dur = fr.info.get("duration", default_duration * 1000) / 1000.0
        frames.append((image_to_grid(fr.convert("RGB"), width, height, **kw), max(0.02, dur)))
    if not frames:
        frames.append((image_to_grid(img, width, height, **kw), default_duration))
    return frames


# ------------------------------------------------------------------ tekst ----

def _font(size: int, path: Optional[str] = None) -> ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def render_text_strip(
    text: str,
    height: int,
    color: RGB = (255, 255, 255),
    background: RGB = (0, 0, 0),
    font_path: Optional[str] = None,
    font_size: Optional[int] = None,
) -> Image.Image:
    """Tekst als brede strook van `height` pixels hoog."""
    size = font_size or max(6, int(height * 0.9))
    font = _font(size, font_path)
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    l, t, r, b = dummy.textbbox((0, 0), text, font=font)
    w = max(1, r - l)
    img = Image.new("RGB", (w + 2, height), background)
    d = ImageDraw.Draw(img)
    d.text((1 - l, (height - (b - t)) // 2 - t), text, font=font, fill=color)
    return img


def text_to_frames(
    text: str,
    width: int,
    height: int,
    color: RGB = (255, 255, 255),
    background: RGB = (0, 0, 0),
    scroll: bool = True,
    speed: float = 12.0,           # pixels per seconde
    font_path: Optional[str] = None,
    font_size: Optional[int] = None,
    brightness: float = 1.0,
) -> List[Frame]:
    strip = render_text_strip(text, height, color, background, font_path, font_size)
    strip = _apply_levels(strip, brightness, 1.0)
    if not scroll or strip.width <= width:
        canvas = Image.new("RGB", (max(width, strip.width), height), background)
        canvas.paste(strip, ((canvas.width - strip.width) // 2, 0))
        canvas = canvas.crop((0, 0, width, height))
        return [(image_to_grid(canvas, width, height, fit="stretch"), 1.0)]
    # scroll van rechts naar links: pad met `width` zwart aan beide kanten
    padded = Image.new("RGB", (strip.width + 2 * width, height), background)
    padded.paste(strip, (width, 0))
    step_dur = 1.0 / max(1.0, speed)
    frames: List[Frame] = []
    for off in range(0, padded.width - width + 1):
        win = padded.crop((off, 0, off + width, height))
        px = win.load()
        grid = [[tuple(px[x, y]) for x in range(width)] for y in range(height)]  # type: ignore
        frames.append((grid, step_dur))
    return frames


# -------------------------------------------------------------- helpers ----

def solid(width: int, height: int, color: RGB) -> Grid:
    return [[color] * width for _ in range(height)]


def hex_to_rgb(s: str) -> RGB:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def grid_to_png(grid: Grid, scale: int = 10) -> bytes:
    h, w = len(grid), len(grid[0]) if grid else 0
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = tuple(grid[y][x])
    img = img.resize((w * scale, h * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
