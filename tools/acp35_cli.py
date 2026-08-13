#!/usr/bin/env python3
"""Decode Stiebel Eltron ACP 35 IR captures.

    # An ESPHome log line, a bare Pronto code, or raw signed timings
    ./tools/acp35_cli.py < capture.txt
    echo "0000 006D 004A 0000 00C5 0017 ..." | ./tools/acp35_cli.py

    # Every capture in the protocol document
    ./tools/acp35_cli.py --document
    ./tools/acp35_cli.py --document --format table   # markdown, for the document
    ./tools/acp35_cli.py --document --format bytes

Replaces the old decode.py, which decoded a 69-bit frame that does not exist.
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_DOC = REPO_ROOT / "docs" / "Stiebel Eltron air conditioner ACP 35.md"

# acp35 is imported as a bare module, not through the stiebel_eltron_ir package,
# whose __init__ pulls in homeassistant.
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "stiebel_eltron_ir"))

from acp35 import Acp35Command, Acp35Flag  # noqa: E402
from pronto import find_pronto_captures, parse_pronto  # noqa: E402

_SIGNED_INT = re.compile(r"-?\d+")
_PRONTO_WORD = re.compile(r"\b[0-9A-Fa-f]{4}\b")


def read_timings(text: str) -> list[list[int]]:
    """Read one or more frames of raw timings from arbitrary input text.

    Accepts ESPHome log lines, a bare Pronto code, or a list of signed
    microseconds. Raw timings are recognised by their negative values, which a
    Pronto code cannot contain.
    """
    if "-" in text:
        return [[int(value) for value in _SIGNED_INT.findall(text)]]

    captures = find_pronto_captures(text)
    if captures:
        return [parse_pronto(code) for _, code in captures]

    words = _PRONTO_WORD.findall(text)
    if words:
        return [parse_pronto(" ".join(words))]
    return []


def describe(command: Acp35Command) -> str:
    """Render a decoded command as a human-readable block."""
    unit = "C" if Acp35Flag.CELSIUS in command.flags else "F"
    events = [
        flag.name
        for flag in (
            Acp35Flag.TEMP_CHANGED,
            Acp35Flag.POWER_PRESSED,
            Acp35Flag.TIMER_UI,
        )
        if flag in command.flags
    ]
    unnamed = int(command.flags) & ~0xCA
    if unnamed:
        events.append(f"unknown 0x{unnamed:02X}")

    if command.timer_off_delay:
        timer = f"armed, {command.timer_hours} h"
    elif command.timer_hours:
        timer = f"off, but {command.timer_hours} h still in b2"
    else:
        timer = "off"

    return "\n".join(
        [
            f"  bytes       {command.to_bytes().hex(' ').upper()}",
            f"  power       {'on' if command.power else 'off'}",
            f"  mode        {command.mode.name.lower()}",
            f"  fan         {command.fan.name.lower()}",
            f"  temperature {command.celsius} C / {command.fahrenheit} F"
            f"  (displaying {unit})",
            f"  timer       {timer}",
            f"  b7          0x{int(command.flags):02X}"
            + (f"  [{', '.join(events)}]" if events else ""),
        ]
    )


def decode_stream(text: str) -> int:
    """Decode every frame found in ``text``. Returns a process exit code."""
    frames = read_timings(text)
    if not frames:
        print("no Pronto code or raw timings found in input", file=sys.stderr)
        return 2

    failures = 0
    for index, timings in enumerate(frames, start=1):
        print(f"frame {index} ({len(timings)} timings)")
        command = Acp35Command.from_raw_timings(timings)
        if command is None:
            print("  not an ACP 35 frame: bad framing, preamble or checksum")
            failures += 1
        else:
            print(describe(command))
        print()
    return 1 if failures else 0


def decode_document(path: Path, style: str) -> int:
    """Decode every capture in the protocol document."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"cannot read {path}: {error.strerror}", file=sys.stderr)
        return 2

    captures = find_pronto_captures(text)
    if not captures:
        print(f"no captures found in {path}", file=sys.stderr)
        return 2

    if style == "table":
        print("| # | bytes | state | capture |")
        print("| - | ----- | ----- | ------- |")

    failures = 0
    for index, (label, code) in enumerate(captures):
        command = Acp35Command.from_raw_timings(parse_pronto(code))
        if command is None:
            failures += 1
            if style == "table":
                print(f"| {index} | — | **failed to decode** | {label} |")
            else:
                print(f"{index:3}  failed to decode  | {label}")
            continue

        state = summarise(command)
        raw = command.to_bytes().hex(" ").upper()
        if style == "table":
            print(f"| {index} | `{raw}` | {state} | {label} |")
        elif style == "bytes":
            print(raw)
        else:
            print(f"{index:3}  {raw}  {state}  | {label}")

    return 1 if failures else 0


def summarise(command: Acp35Command) -> str:
    """Render a decoded command as one compact line."""
    unit = "C" if Acp35Flag.CELSIUS in command.flags else "F"
    temperature = command.celsius if unit == "C" else command.fahrenheit
    parts = [
        "on" if command.power else "off",
        command.mode.name.lower(),
        f"{command.fan.name.lower()} fan",
        f"{temperature}{unit}",
    ]
    if command.timer_off_delay:
        parts.append(f"timer {command.timer_hours}h")
    return ", ".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--document",
        nargs="?",
        const=str(PROTOCOL_DOC),
        metavar="PATH",
        help="decode every capture in the protocol document",
    )
    parser.add_argument(
        "--format",
        choices=("text", "table", "bytes"),
        default="text",
        help="output style for --document (default: text)",
    )
    args = parser.parse_args(argv)

    if args.document:
        return decode_document(Path(args.document), args.format)

    if sys.stdin.isatty():
        parser.print_help()
        return 2
    return decode_stream(sys.stdin.read())


if __name__ == "__main__":
    raise SystemExit(main())
