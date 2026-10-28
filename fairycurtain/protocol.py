"""
Hello Fairy BLE protocol.

Packet format (confirmed by several reverse-engineering projects):

    AA  CMD  LEN  PAYLOAD...  CS

    CS = (sum of all preceding bytes) & 0xFF

Confirmed commands:
    power on/offAA 02 01 01 AE  /  AA 02 01 00 AD
    color (HSV) AA 03 07 01 HHHH SSSS VVVV CS   (H 0-359, S/V 0-1000, big-endian)
    preset      AA 03 04 02 PP VVVV CS         (PP 1-58, V 0-1000)
    ACK         AA 02 00 AC  /  AA 03 00 AD

Per-pixel ("DIY"/image) mode — CONFIRMED (kmich, btsnoop capture of
the official app + APK, physically tested on a 20x20 curtain):

    * one color byte per pixel: 0..180 = hue (hue/2), 254 = off, 255 = white
      (no per-pixel brightness/saturation; brightness is global via D0)
    * index = column*20 + row  (strands run top to bottom)
    * sequence:  D0(03)  D0(00)  →250ms→  6x DA (79 px, 242 bytes, 300ms apart)
                 →1.2s→  D0(04)  D0(01)
    * DA-payload = [01, idx_hi, idx_lo, kleur]*79.  Smaller packets do NOT
      work: the curtain then keeps only the last packet. MTU >= 245 required.
    * D0-payload = [direction, brightness 0-100, speed 0-100, action]
      direction: 0 still, 1-4 up/down/left/right, 5 blink, 6 breathe,
      7-10 loop up/down/left/right.  action 1 = show picture.

The earlier guesses (indexed_da with RGB, plain_rgb) remain as "legacy"
encoders for experiments; the curtain silently ignores them (no ACK).
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

# Init sequence as sent by the official app after connecting (from blecontroller).
# Not required for power/color; possibly needed for pixel mode.
INIT_PACKETS = [
    bytes.fromhex("AA0000AA"),
    bytes.fromhex("AA0100AB"),
    bytes.fromhex("AA2003000100CE"),
    bytes.fromhex("AA0C00B6"),
    bytes.fromhex("AAAF0059"),
]

# Names of some presets (numbers 1-58 all work).
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


# ---------------------------------------------------------------- basics ----

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
    """RGB → HSV command. brightness (0-100) overrides V if given."""
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
    """AA 05 07 <ts:4> 00 00 F0 — shape derived from app capture, purpose uncertain."""
    return packet(0x05, struct.pack(">I", unix_ts) + bytes([0x00, 0x00, 0xF0]))


# ------------------------------------------------------------- pixels ----

PixelEncoder = Callable[[Sequence[RGB], int], List[bytes]]


def encode_pixels_indexed_da(pixels: Sequence[RGB], mtu: int = 247) -> List[bytes]:
    """
    AA DA LEN PAGE [IDX_HI IDX_LO R G B]* CS   — format from `blecontroller`.
    Chunk size follows the MTU (max payload 255).
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
    Alternative (unconfirmed): AA DA LEN PAGE [R G B]* CS without a per-pixel index.
    Useful as a second candidate when testing.
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
    "diy": None,  # type: ignore  # filled in below (confirmed format)
    "legacy_indexed_da": encode_pixels_indexed_da,
    "legacy_plain_rgb": encode_pixels_plain_rgb,
}



# ----------------------------------------------------------- DIY (real) ----

CMD_DIY_MODE = 0xD0
CMD_DIY_DATA = 0xDA
PIXEL_OFF = 0xFE
PIXEL_WHITE = 0xFF
DIY_PIXELS_PER_PACKET = 79
DIY_MIN_MTU = 245

DIRECTIONS = {"still": 0, "up": 1, "down": 2, "left": 3, "right": 4, "blink": 5, "breathe": 6,
              "loop_up": 7, "loop_down": 8, "loop_left": 9, "loop_right": 10}


def rgb_to_pixel_byte(r: int, g: int, b: int, off_below: float = 0.05, white_below: float = 0.10) -> int:
    """RGB → one DIY color byte (hue). Dark → off, unsaturated → white."""
    hf, sf, vf = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if vf <= off_below:
        return PIXEL_OFF
    if sf <= white_below:
        return PIXEL_WHITE
    return min(180, max(0, int(round(hf * 360 / 2.0))))


def pixel_byte_to_rgb(c: int) -> RGB:
    """Inverse (for preview): roughly what the curtain shows."""
    if c == PIXEL_OFF:
        return (0, 0, 0)
    if c == PIXEL_WHITE:
        return (255, 255, 255)
    r, g, b = colorsys.hsv_to_rgb((min(180, c) * 2) / 360, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def diy_mode(action: int, direction: int = 0, brightness: int = 100, speed: int = 100) -> bytes:
    """D0: [direction, brightness, speed, action]. Capture used 00 64 64 xx."""
    return packet(CMD_DIY_MODE, bytes([direction & 0xFF, max(0, min(100, brightness)),
                                       max(0, min(100, speed)), action & 0xFF]))


def encode_pixels_diy(pixels: Sequence[RGB], mtu: int = 247) -> List[bytes]:
    """
    400 RGB pixels (by physical index) → exactly 6 DA packets (5x242 + 1x20 bytes).
    Note: must be sent in exactly these chunks; see the docstring at the top.
    """
    if mtu < DIY_MIN_MTU:
        raise ValueError(f"DIY upload needs MTU >= {DIY_MIN_MTU} (currently {mtu})")
    out: List[bytes] = []
    for start in range(0, len(pixels), DIY_PIXELS_PER_PACKET):
        chunk = pixels[start:start + DIY_PIXELS_PER_PACKET]
        payload = bytes([0x01])
        for idx, (r, g, b) in enumerate(chunk, start=start):
            payload += struct.pack(">H", idx) + bytes([rgb_to_pixel_byte(r, g, b)])
        out.append(packet(CMD_DIY_DATA, payload))
    return out


def parse_info(data: bytes) -> Optional[dict]:
    """Reply to AA 00 00 AA: version, LED count, columns, rows, DIY type."""
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


# ---------------------------------------------------------- notifications ----

@dataclass
class Status:
    power: Optional[bool] = None
    mode: Optional[int] = None          # 1 = color, 2 = preset
    h: Optional[int] = None
    s: Optional[int] = None             # 0-1000
    v: Optional[int] = None             # 0-1000
    preset: Optional[int] = None
    preset_brightness: Optional[int] = None  # 0-1000
    raw: str = ""


def parse_notification(data: bytes) -> Optional[Status]:
    """Returns Status for status messages, None for ACKs/unknown."""
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
