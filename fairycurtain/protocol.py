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

Per-pixel ("DIY"/afbeelding) modus  — BEVESTIGD (kmich, btsnoop-capture van
de officiële app + APK, fysiek getest op een 20x20-gordijn):

    * per pixel één kleurbyte: 0..180 = tint (hue/2), 254 = uit, 255 = wit
      (geen helderheid/verzadiging per pixel; wel globaal via D0)
    * index = kolom*20 + rij  (slierten van boven naar beneden)
    * volgorde:  D0(03)  D0(00)  →250ms→  6x DA (79 px, 242 bytes, 300ms apart)
                 →1.2s→  D0(04)  D0(01)
    * DA-payload = [01, idx_hi, idx_lo, kleur]*79.  Kleinere pakketten werken
      NIET: het gordijn houdt dan alleen het laatste pakket. MTU >= 245 nodig.
    * D0-payload = [richting, helderheid 0-100, snelheid 0-100, actie]
      richting: 0 stil, 1-4 omhoog/omlaag/links/rechts, 5 knipper, 6 adem,
      7-10 lus omhoog/omlaag/links/rechts.  actie 1 = plaatje tonen.

De eerdere gissingen (indexed_da met RGB, plain_rgb) staan nog als "legacy"
encoders voor experimenten; het gordijn negeert ze stil (geen ACK).
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
    "diy": None,  # type: ignore  # wordt hieronder ingevuld (bevestigd formaat)
    "legacy_indexed_da": encode_pixels_indexed_da,
    "legacy_plain_rgb": encode_pixels_plain_rgb,
}



# ----------------------------------------------------------- DIY (echt) ----

CMD_DIY_MODE = 0xD0
CMD_DIY_DATA = 0xDA
PIXEL_OFF = 0xFE
PIXEL_WHITE = 0xFF
DIY_PIXELS_PER_PACKET = 79
DIY_MIN_MTU = 245

DIRECTIONS = {"still": 0, "up": 1, "down": 2, "left": 3, "right": 4, "blink": 5, "breathe": 6,
              "loop_up": 7, "loop_down": 8, "loop_left": 9, "loop_right": 10}


def rgb_to_pixel_byte(r: int, g: int, b: int, off_below: float = 0.05, white_below: float = 0.10) -> int:
    """RGB → één DIY-kleurbyte (tint). Donker → uit, onverzadigd → wit."""
    hf, sf, vf = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if vf <= off_below:
        return PIXEL_OFF
    if sf <= white_below:
        return PIXEL_WHITE
    return min(180, max(0, int(round(hf * 360 / 2.0))))


def pixel_byte_to_rgb(c: int) -> RGB:
    """Omgekeerde (voor preview): wat het gordijn ongeveer laat zien."""
    if c == PIXEL_OFF:
        return (0, 0, 0)
    if c == PIXEL_WHITE:
        return (255, 255, 255)
    r, g, b = colorsys.hsv_to_rgb((min(180, c) * 2) / 360, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def diy_mode(action: int, direction: int = 0, brightness: int = 100, speed: int = 100) -> bytes:
    """D0: [richting, helderheid, snelheid, actie]. Capture gebruikte 00 64 64 xx."""
    return packet(CMD_DIY_MODE, bytes([direction & 0xFF, max(0, min(100, brightness)),
                                       max(0, min(100, speed)), action & 0xFF]))


def encode_pixels_diy(pixels: Sequence[RGB], mtu: int = 247) -> List[bytes]:
    """
    400 RGB-pixels (op fysieke index) → precies 6 DA-pakketten (5x242 + 1x20 bytes).
    Let op: moet in exact deze chunks; zie docstring bovenaan.
    """
    if mtu < DIY_MIN_MTU:
        raise ValueError(f"DIY-upload heeft MTU >= {DIY_MIN_MTU} nodig (nu {mtu})")
    out: List[bytes] = []
    for start in range(0, len(pixels), DIY_PIXELS_PER_PACKET):
        chunk = pixels[start:start + DIY_PIXELS_PER_PACKET]
        payload = bytes([0x01])
        for idx, (r, g, b) in enumerate(chunk, start=start):
            payload += struct.pack(">H", idx) + bytes([rgb_to_pixel_byte(r, g, b)])
        out.append(packet(CMD_DIY_DATA, payload))
    return out


def parse_info(data: bytes) -> Optional[dict]:
    """Antwoord op AA 00 00 AA: versie, aantal LED's, kolommen, rijen, DIY-type."""
    if len(data) < 4 or data[0] != PREFIX or data[1] != 0x00:
        return None
    pl = data[3:3 + data[2]]
    if len(pl) < 27:
        return None
    return {
        "firmware": pl[:20].split(b"\x00", 1)[0].decode(errors="replace"),
        "pixel_count": struct.unpack(">H", pl[20:22])[0],
        "columns": pl[22], "rows": pl[24], "diy_type": pl[26],
    }


PIXEL_ENCODERS["diy"] = encode_pixels_diy


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
