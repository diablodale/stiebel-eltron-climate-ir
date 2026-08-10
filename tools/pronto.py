"""Read ESPHome Pronto captures into raw signed timings.

Pronto CCF is a header of four 16-bit words followed by durations expressed in
units of a timebase derived from the carrier:

    word 0  format, 0000 = raw with modulation
    word 1  frequency code
    word 2  number of pairs in the intro sequence
    word 3  number of pairs in the repeat sequence

Two ESPHome details matter when reading a capture back:

* ``ProntoProtocol::decode()`` hardcodes 38000 Hz and dumps every element of the
  receive buffer, writing the pair count as ``(size + 1) // 2``. An odd-length
  buffer therefore rounds the pair count *up*, which is not corruption.
* Durations are compensated by ``MARK_EXCESS_MICROS``: marks are shortened by
  20us and spaces lengthened by 20us. Undo it to recover what was on the wire.

The receive buffer starts at the space before the first mark, so element 0 of a
capture is a space and the marks sit at the odd indices.
"""

import re

# Pronto's fixed reference, and ESPHome's integer timebase arithmetic. Going
# code -> carrier -> timebase entirely in integers recovers the exact 26us
# ESPHome used: 4145146 // 109 = 38028, and 1000000 // 38028 = 26.
REFERENCE_FREQUENCY = 4145146
MARK_EXCESS_MICROS = 20

_WORD = re.compile(r"[0-9A-Fa-f]{4}")
_ESPHOME_LOG_LINE = re.compile(r"remote\.pronto:\d+\]:\s*(.*)")


def carrier_hz(frequency_code: int) -> int:
    """Return the carrier the Pronto frequency code encodes."""
    return REFERENCE_FREQUENCY // frequency_code


def timebase_us(frequency_code: int) -> int:
    """Return the microseconds-per-unit ESPHome used for this frequency code."""
    return 1_000_000 // carrier_hz(frequency_code)


def parse_pronto(text: str, *, mark_excess_us: int = MARK_EXCESS_MICROS) -> list[int]:
    """Convert one Pronto code to signed microseconds; marks +, spaces -.

    Set ``mark_excess_us=0`` for a code that did not come from an ESPHome
    receiver and so was never compensated.
    """
    words = [int(w, 16) for w in _WORD.findall(text)]
    if len(words) < 4:
        raise ValueError(f"Pronto code too short: {len(words)} words")

    timebase = timebase_us(words[1])
    durations = words[4:]

    timings = []
    for index, units in enumerate(durations):
        micros = units * timebase
        # Element 0 is a space, so spaces are the even indices.
        if index % 2:
            timings.append(micros + mark_excess_us)  # mark
        else:
            timings.append(-(micros - mark_excess_us))  # space
    return timings


def find_pronto_captures(text: str) -> list[tuple[str, str]]:
    """Pull every ESPHome Pronto capture out of a log or markdown document.

    Returns ``(label, pronto_code)`` pairs. A capture may be wrapped across
    several log lines, each re-prefixed; they are stitched back together and a
    new capture is recognised by the leading ``0000`` format word. The label is
    the nearest preceding line of prose, which in the protocol document says
    what button was pressed.
    """
    captures: list[tuple[str, str]] = []
    words: list[str] = []
    label = ""
    pending_label = ""

    def flush() -> None:
        if words:
            captures.append((label, " ".join(words)))

    for line in text.splitlines():
        match = _ESPHOME_LOG_LINE.search(line)
        if not match:
            stripped = line.strip()
            # Remember prose, but not fences or loose bit strings.
            if (
                stripped
                and not stripped.startswith("```")
                and not re.fullmatch(r"[01\s]+", stripped)
            ):
                pending_label = stripped
            continue

        for word in _WORD.findall(match.group(1)):
            # A new capture starts at the format word, but only once the current
            # one is past its own four header words.
            if word == "0000" and len(words) > 4:
                flush()
                words = []
                label = pending_label
            if not words:
                label = pending_label
            words.append(word)

    flush()
    return captures
