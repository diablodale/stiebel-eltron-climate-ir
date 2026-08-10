"""Stiebel Eltron ACP 35 air-conditioner IR protocol.

The remote sends the unit's entire state in one frame per button press; there are
no incremental commands and no repeat sequence.

Frame (pulse-distance, MSB first, byte 0 first)::

    header mark, header space
    72 bits, each a constant mark plus a short (0) or long (1) space
    trailer mark

    55  32  00  07  00  00  31  C0  7F   (on, cool, high fan, 19 C / 66 F)
    b0  b1  b2  b3  b4  b5  b6  b7  ck

    b0  constant 0x55
    b1  bits 7-4  degrees C minus 16      bit 3  timer armed
        bit 1     power on                bits 2, 0  always 0
    b2  timer hours, 0..24
    b3  degrees F minus 59
    b4  always 0
    b5  always 0
    b6  bits 7-4  fan                     bits 3-0  mode
    b7  flags, see Acp35Flag
    ck  sum(b0..b7) & 0xFF

Verified against all 39 captures in the project's protocol document: every one
decodes to nine bytes with a valid checksum and re-encodes bit-identically.

This module deliberately imports nothing from ``homeassistant`` so it can be
contributed to ``infrared-protocols`` unchanged.
"""

from enum import IntEnum, IntFlag
from typing import Self, override

from infrared_protocols.commands import Command

# --- Physical layer ---------------------------------------------------------
#
# Averaged over all 39 captures, with ESPHome's +/-20us MARK_EXCESS_MICROS
# compensation removed. The captures carry no frequency information at all:
# ESPHome's ProntoProtocol::decode() hardcodes 38000 Hz, so the "38028.9 Hz" of
# earlier analysis was that constant round-tripped through a 4-digit hex code.
CARRIER_HZ = 38000

# UNVERIFIED. Every capture's receive buffer begins at the header *space*; the
# mark before it was never recorded, and a fresh capture would very likely miss
# it the same way. Bisect this against the real unit, in order:
#   5100 (symmetric with the space), 4400, 3000, 9000, 0 (no header at all).
HEADER_MARK = 5100

HEADER_SPACE = 5100  # measured 5024..5102
BIT_MARK = 576  # measured 540..644; the trailer mark matches within tolerance
ZERO_SPACE = 481  # measured 474..500
ONE_SPACE = 1928  # measured 1904..1956
TOLERANCE = 0.4

BIT_COUNT = 72
BYTE_COUNT = 9
PREAMBLE = 0x55

MIN_CELSIUS = 17
MAX_CELSIUS = 30
MIN_FAHRENHEIT = 62
MAX_FAHRENHEIT = 86
MAX_TIMER_HOURS = 24

_CELSIUS_BIAS = 16
_FAHRENHEIT_BIAS = 59
_TIMER_ARMED_MASK = 0x08
_POWER_MASK = 0x02

# The remote always transmits both temperature fields. Whichever unit the user
# selected is authoritative and the other is its paired value.
#
# Both directions are tables, because that is what the device is: a small fixed
# mapping. They are also not inverses of each other -- 17 C pairs with 62 F, but
# 63 F pairs back to 17 C -- so deriving either side from the other by formula
# would quietly assert a symmetry that does not exist.
#
# Entries marked (v) are confirmed by a capture in the protocol document. The
# corpus test test_temperature_fields_agree checks every capture against these
# tables, so adding captures to the document verifies more entries with no code
# change. Sweeping the remote through its full Fahrenheit range would confirm
# the 19 unverified entries below.

# All 14 confirmed. Equals round(C * 9/5 + 32) at every entry except 17 C, which
# ships as 62 F rather than the 63 F rounding gives: the bottom of the Celsius
# scale is pinned to the bottom of the Fahrenheit one. Not floor() -- that would
# also change 21, 22, 26 and 27 C, and the captures show it does not.
_CELSIUS_TO_FAHRENHEIT = {
    17: 62,  # (v)
    18: 64,  # (v)
    19: 66,  # (v)
    20: 68,  # (v)
    21: 70,  # (v)
    22: 72,  # (v)
    23: 73,  # (v)
    24: 75,  # (v)
    25: 77,  # (v)
    26: 79,  # (v)
    27: 81,  # (v)
    28: 82,  # (v)
    29: 84,  # (v)
    30: 86,  # (v)
}

# 6 of 25 confirmed. Equals round((F - 32) * 5/9) at every entry, including all
# six confirmed ones; the remaining 19 follow that same rule and are unverified.
_FAHRENHEIT_TO_CELSIUS = {
    62: 17,  # (v)
    63: 17,  # (v)
    64: 18,  # (v)
    65: 18,
    66: 19,
    67: 19,
    68: 20,
    69: 21,
    70: 21,
    71: 22,
    72: 22,  # (v)
    73: 23,
    74: 23,
    75: 24,  # (v)
    76: 24,
    77: 25,
    78: 26,
    79: 26,
    80: 27,
    81: 27,
    82: 28,
    83: 28,
    84: 29,
    85: 29,
    86: 30,  # (v)
}


class Acp35Mode(IntEnum):
    """Operating mode, held in the low nibble of b6."""

    AUTO = 0
    COOL = 1
    DRY = 2
    FAN = 3


class Acp35Fan(IntEnum):
    """Fan speed, held in the high nibble of b6.

    AUTO is inferred from the nibble's width and has never been observed: the
    remote's fan button only cycles high -> medium -> low. Do not offer it to
    users until it has been tried against the unit.
    """

    AUTO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Acp35Flag(IntFlag):
    """b7: one state bit, the rest transient per-button-press event bits.

    b7 is the only byte that is not a pure function of the unit's state. With
    the machine in an identical state, it differs by which button produced the
    frame: 0xC0 after a temperature press, 0x80 after a fan or mode press, 0x88
    after a power press.

    Whether the unit actually requires the event bits, or acts on b1/b2/b3/b6
    regardless, is untested. Until that is known, reproduce what the remote sends.
    """

    NONE = 0
    TIMER_UI = 0x02  # remote is in its timer-entry UI
    POWER_PRESSED = 0x08  # frame came from the power button, on or off
    TEMP_CHANGED = 0x40  # frame came from a temperature up/down press
    CELSIUS = 0x80  # display unit; the one genuinely persistent bit

    # Bit 0 (0x01) appears in exactly one capture, as part of 0x03 on the first
    # press of a timer cancel. Unexplained. IntFlag keeps unnamed bits, so it
    # survives a decode/encode round trip.


def celsius_to_fahrenheit(celsius: int) -> int:
    """Convert to the Fahrenheit value the remote pairs with this Celsius one."""
    if celsius not in _CELSIUS_TO_FAHRENHEIT:
        raise ValueError(f"celsius must be {MIN_CELSIUS}..{MAX_CELSIUS}, got {celsius}")
    return _CELSIUS_TO_FAHRENHEIT[celsius]


def fahrenheit_to_celsius(fahrenheit: int) -> int:
    """Convert to the Celsius value the remote pairs with this Fahrenheit one."""
    if fahrenheit not in _FAHRENHEIT_TO_CELSIUS:
        raise ValueError(
            f"fahrenheit must be {MIN_FAHRENHEIT}..{MAX_FAHRENHEIT}, got {fahrenheit}"
        )
    return _FAHRENHEIT_TO_CELSIUS[fahrenheit]


class Acp35Command(Command):
    """A full-state Stiebel Eltron ACP 35 command."""

    def __init__(
        self,
        *,
        power: bool,
        mode: Acp35Mode,
        fan: Acp35Fan,
        celsius: int | None = None,
        fahrenheit: int | None = None,
        timer_hours: int = 0,
        timer_armed: bool | None = None,
        flags: Acp35Flag | int | None = None,
        modulation: int = CARRIER_HZ,
    ) -> None:
        """Build a command.

        Give exactly one of ``celsius`` or ``fahrenheit`` and the other is
        derived, matching how the remote pairs the two fields; the unit flag in
        b7 follows the one you gave. Passing both stores them verbatim, which is
        what :meth:`from_raw_timings` uses to round-trip a captured frame.

        ``timer_armed`` defaults to ``timer_hours > 0``. They are separate bits
        in the protocol and the remote does emit armed-with-zero-hours while its
        timer UI is open, so it can be forced.

        ``flags`` is the whole of b7. Left as ``None`` it is just the unit bit.
        """
        if celsius is None and fahrenheit is None:
            raise ValueError("give celsius or fahrenheit")
        is_celsius = celsius is not None
        if celsius is None:
            celsius = fahrenheit_to_celsius(fahrenheit)
        elif fahrenheit is None:
            fahrenheit = celsius_to_fahrenheit(celsius)
        if not MIN_CELSIUS <= celsius <= MAX_CELSIUS:
            raise ValueError(
                f"celsius must be {MIN_CELSIUS}..{MAX_CELSIUS}, got {celsius}"
            )
        if not MIN_FAHRENHEIT <= fahrenheit <= MAX_FAHRENHEIT:
            raise ValueError(
                f"fahrenheit must be {MIN_FAHRENHEIT}..{MAX_FAHRENHEIT}, "
                f"got {fahrenheit}"
            )
        if not 0 <= timer_hours <= MAX_TIMER_HOURS:
            raise ValueError(
                f"timer_hours must be 0..{MAX_TIMER_HOURS}, got {timer_hours}"
            )

        super().__init__(modulation=modulation, repeat_count=0)
        self.power = power
        self.mode = Acp35Mode(mode)
        self.fan = Acp35Fan(fan)
        self.celsius = celsius
        self.fahrenheit = fahrenheit
        self.timer_hours = timer_hours
        self.timer_armed = timer_hours > 0 if timer_armed is None else timer_armed
        if flags is None:
            flags = Acp35Flag.CELSIUS if is_celsius else Acp35Flag.NONE
        self.flags = Acp35Flag(flags)

    @property
    def is_celsius(self) -> bool:
        """Return whether the unit is displaying Celsius (b7 bit 7)."""
        return Acp35Flag.CELSIUS in self.flags

    def to_bytes(self) -> bytes:
        """Render the nine frame bytes, checksum included."""
        b1 = (self.celsius - _CELSIUS_BIAS) << 4
        if self.timer_armed:
            b1 |= _TIMER_ARMED_MASK
        if self.power:
            b1 |= _POWER_MASK

        state = [
            PREAMBLE,
            b1,
            self.timer_hours,
            self.fahrenheit - _FAHRENHEIT_BIAS,
            0x00,
            0x00,
            (self.fan << 4) | self.mode,
            int(self.flags),
        ]
        state.append(sum(state) & 0xFF)
        return bytes(state)

    @override
    def get_raw_timings(self) -> list[int]:
        """Render the frame as microseconds; positive marks, negative spaces."""
        # HEADER_MARK of 0 is the "no header at all" hypothesis: the 5100us
        # element seen in captures would then be the idle gap before the frame,
        # which a transmitter does not emit.
        timings: list[int] = [HEADER_MARK, -HEADER_SPACE] if HEADER_MARK else []

        for byte in self.to_bytes():
            for shift in range(7, -1, -1):
                timings.append(BIT_MARK)
                timings.append(-(ONE_SPACE if (byte >> shift) & 1 else ZERO_SPACE))

        timings.append(BIT_MARK)  # trailer
        return timings

    @classmethod
    def from_raw_timings(cls, timings: list[int]) -> Self | None:
        """Decode raw timings, or return None if they are not an ACP 35 frame.

        Tolerates a missing leading mark. Home Assistant receives a buffer that
        starts at the space *before* the first mark, so a captured frame has one
        fewer element than one we transmit.
        """
        data = list(timings)

        # A leading space is the pre-frame gap, or the header space of a frame
        # whose header mark was never recorded.
        if data and data[0] < 0:
            data = data[1:]
        # A leading mark far longer than a bit mark is a header; drop its pair.
        if len(data) >= 2 and data[0] > BIT_MARK * 2 and data[1] < 0:
            data = data[2:]

        if len(data) < BIT_COUNT * 2:
            return None

        value = 0
        for i in range(BIT_COUNT):
            bit = cls._decode_bit(data[2 * i], -data[2 * i + 1])
            if bit is None:
                return None
            value = (value << 1) | bit

        state = value.to_bytes(BYTE_COUNT, "big")
        if state[0] != PREAMBLE or sum(state[:-1]) & 0xFF != state[-1]:
            return None

        return cls(
            power=bool(state[1] & _POWER_MASK),
            mode=Acp35Mode(state[6] & 0x0F),
            fan=Acp35Fan(state[6] >> 4),
            celsius=(state[1] >> 4) + _CELSIUS_BIAS,
            fahrenheit=state[3] + _FAHRENHEIT_BIAS,
            timer_hours=state[2],
            timer_armed=bool(state[1] & _TIMER_ARMED_MASK),
            flags=Acp35Flag(state[7]),
        )

    @staticmethod
    def _is_close(actual: int, expected: int) -> bool:
        margin = expected * TOLERANCE
        return expected - margin <= actual <= expected + margin

    @classmethod
    def _decode_bit(cls, mark_us: int, space_us: int) -> int | None:
        if not cls._is_close(mark_us, BIT_MARK):
            return None
        if cls._is_close(space_us, ZERO_SPACE):
            return 0
        if cls._is_close(space_us, ONE_SPACE):
            return 1
        return None

    def __repr__(self) -> str:
        """Return a debugging representation."""
        return (
            f"{type(self).__name__}("
            f"power={self.power}, mode={self.mode.name}, fan={self.fan.name}, "
            f"celsius={self.celsius}, fahrenheit={self.fahrenheit}, "
            f"timer_hours={self.timer_hours}, timer_armed={self.timer_armed}, "
            f"flags={self.flags!r}) "
            f"[{self.to_bytes().hex(' ').upper()}]"
        )
