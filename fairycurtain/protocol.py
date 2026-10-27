"""
Hello Fairy BLE-protocol.

Pakketformaat (bevestigd door meerdere reverse-engineering projecten):

    AA  CMD  LEN  PAYLOAD...  CS

    CS = (som van alle voorgaande bytes) & 0xFF

Bevestigde commando's:
    aan/uit     AA 02 01 01 AE  /  AA 02 01 00 AD
    kleur (HSV) AA 03 07 01 HHHH SSSS VVVV CS   (H 0-359, S/V 0-1000, big-endian)
    preset      AA 03 04 02 PP VVVV CS         (PP 1-58, V 0-1000)
    ACK         AA 02 00 AC  /  AA 03 00 AD

Per-pixel ("DIY"/afbeelding) modus
----------------------------------
Hier bestaan twee waarnemingen van, die elkaar niet bevestigen:

  * `blecontroller` (PyPI, "getest op 20x20 Hello Fairy"):
        AA DA LEN  PAGE  [IDX_HI IDX_LO R G B]*  CS
  * een btsnoop-capture in de HA-community thread toont pakketten
        AA D1 03 00 00 96 BB  (checksum klopt NIET met de som-regel)

De eerste is als standaard-encoder geïmplementeerd ("indexed_da"). Of jouw
gordijn dit slikt, test je met `POST /api/test/pixel`. Klopt het niet, dan
sniff je de app één keer met `tools/btsnoop_writes.py` en voeg je hieronder
een encoder toe aan PIXEL_ENCODERS (zie README, hoofdstuk "Pixelprotocol").
"""
from __future__ import annotations

import colorsys
import struct
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

PREFIX = 0xAA
CMD_POWER = 0x02
CMD_COLOR = 0x03
CMD_PIXELS_DA = 0xDA

SERVICE_UUID = "49535343-fe7d-4ae5-8fa9-9fafd205e455"
WRITE_UUID = "49535343-8841-43f4-a8d4-ecbe34729bb3"
NOTIFY_UUID = "49535343-1e4d-4bd9-ba61-23c647249616"
DEVICE_NAME_PREFIX = "Hello Fairy-"

ACK_POWER = bytes([0xAA, 0x02, 0x00, 0xAC])
ACK_COLOR = bytes([0xAA, 0x03, 0x00, 0xAD])

# Init-reeks zoals de officiële app die stuurt na verbinden (uit blecontroller).
# Niet verplicht voor aan/uit/kleur; mogelijk wel voor pixel-modus.
INIT_PACKETS = [
    bytes.fromhex("AA0000AA"),
    bytes.fromhex("AA0100AB"),
    bytes.fromhex("AA2003000100CE"),
    bytes.fromhex("AA0C00B6"),
    bytes.fromhex("AAAF0059"),
]

# Namen van een aantal presets (nummers 1-58 werken allemaal).
PRESETS = {
    8: "Blue with Pink Sparkle",
    17: "Fireworks",
    18: "Xmas",
    20: "Halloween",
    39: "July 4th",
    40: "Red Gold",
    41: "Blue White Dissolve",
    46: "Valentine",
    47: "St. Patrick",
    48: "May Day",
    50: "Candy Cane",
    54: "Snow Day",
    56: "Blue Sparkle",
    57: "White Sparkle",
}


def checksum(data: Iterable[int]) -> int:
    return sum(data) & 0xFF


def packet(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([PREFIX, cmd, len(payload)]) + payload
    return body + bytes([checksum(body)])


# ---------------------------------------------------------------- basis ----

def power(on: bool) -> bytes:
    return packet(CMD_POWER, bytes([0x01 if on else 0x00]))


def color_hsv(h: int, s: int, v: int) -> bytes:
    """h 0-359, s 0-1000, v 0-1000."""
    h = max(0, min(359, int(h)))
    s = max(0, min(1000, int(s)))
    v = max(0, min(1000, int(v)))
    return packet(CMD_COLOR, bytes([0x01]) + struct.pack(">HHH", h, s, v))


def rgb_to_hsv1000(r: int, g: int, b: int) -> Tuple[int, int, int]:
    hf, sf, vf = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return int(round(hf * 359)), int(round(sf * 1000)), int(round(vf * 1000))


def color_rgb(r: int, g: int, b: int, brightness: Optional[int] = None) -> bytes:
    """RGB → HSV-commando. brightness (0-100) overschrijft V indien gegeven."""
    h, s, v = rgb_to_hsv1000(r, g, b)
    if brightness is not None:
        v = max(0, min(100, int(brightness))) * 10
    return color_hsv(h, s, v)


def preset(number: int, brightness: int = 100) -> bytes:
    """number 1-58, brightness 0-100."""
    number = max(1, min(58, int(number)))
    v = max(0, min(100, int(brightness))) * 10
    return packet(CMD_COLOR, bytes([0x02, number]) + struct.pack(">H", v))


def time_sync(unix_ts: int) -> bytes:
    """AA 05 07 <ts:4> 00 00 F0 — vorm afgeleid van app-capture, functie onzeker."""
    return packet(0x05, struct.pack(">I", unix_ts) + bytes([0x00, 0x00, 0xF0]))


# ------------------------------------------------------------- pixels ----

PixelEncoder = Callable[[Sequence[RGB], int], List[bytes]]


def encode_pixels_indexed_da(pixels: Sequence[RGB], mtu: int = 247) -> List[bytes]:
    """
    AA DA LEN PAGE [IDX_HI IDX_LO R G B]* CS   — formaat uit `blecontroller`.
    Chunkgrootte volgt de MTU (max payload 255).
    """
    max_payload = min(255, mtu - 7)
    per_chunk = max(1, (max_payload - 1) // 5)
    out: List[bytes] = []
    for page, start in enumerate(range(0, len(pixels), per_chunk)):
        chunk = pixels[start:start + per_chunk]
        payload = bytes([page & 0xFF])
        for idx, (r, g, b) in enumerate(chunk, start=start):
            payload += struct.pack(">H", idx) + bytes([r & 0xFF, g & 0xFF, b & 0xFF])
        out.append(packet(CMD_PIXELS_DA, payload))
    return out


def encode_pixels_plain_rgb(pixels: Sequence[RGB], mtu: int = 247) -> List[bytes]:
    """
    Alternatief (onbevestigd): AA DA LEN PAGE [R G B]* CS zonder index per pixel.
    Handig als tweede kandidaat bij het testen.
    """
    max_payload = min(255, mtu - 7)
    per_chunk = max(1, (max_payload - 1) // 3)
    out: List[bytes] = []
    for page, start in enumerate(range(0, len(pixels), per_chunk)):
        chunk = pixels[start:start + per_chunk]
        payload = bytes([page & 0xFF]) + b"".join(bytes(c) for c in chunk)
        out.append(packet(CMD_PIXELS_DA, payload))
    return out


PIXEL_ENCODERS: dict[str, PixelEncoder] = {
    "indexed_da": encode_pixels_indexed_da,
    "plain_rgb": encode_pixels_plain_rgb,
}


# ---------------------------------------------------------- notificaties ----

@dataclass
class Status:
    power: Optional[bool] = None
    mode: Optional[int] = None          # 1 = kleur, 2 = preset
    h: Optional[int] = None
    s: Optional[int] = None             # 0-1000
    v: Optional[int] = None             # 0-1000
    preset: Optional[int] = None
    preset_brightness: Optional[int] = None  # 0-1000
    raw: str = ""


def parse_notification(data: bytes) -> Optional[Status]:
    """Geeft Status terug voor statusmeldingen, None voor ACKs/onbekend."""
    if len(data) == 4 and data[0] == PREFIX and data[2] == 0:
        return None  # ACK
    if len(data) < 8 or data[0] != PREFIX:
        return None
    st = Status(raw=data.hex())
    st.power = data[6] == 1
    st.mode = data[7]
    if st.mode == 1 and len(data) >= 14:
        st.h, st.s, st.v = struct.unpack(">HHH", data[8:14])
    elif st.mode == 2 and len(data) >= 11:
        st.preset = data[8]
        st.preset_brightness = struct.unpack(">H", data[9:11])[0]
    return st


def is_ack(data: bytes) -> bool:
    return len(data) == 4 and data[0] == PREFIX and data[2] == 0
