"""Regression tests over the 39 real captures in the protocol document.

Two independent kinds of check:

* :class:`TestCorpus` asserts structural properties every capture must satisfy,
  and pins the decoded bytes so a change in the decoder cannot pass unnoticed.
* :class:`TestAgainstDocumentedIntent` asserts a subset against what the
  document's prose says the button press *was*. Those expectations were written
  from the prose, not produced by the decoder, so they can disagree with it.
"""

import pytest
from acp35 import Acp35Command, Acp35Fan, Acp35Flag, Acp35Mode
from conftest import EXPECTED_CAPTURES, EXPECTED_WORDS, load_captures

# Decoded bytes for each capture, in document order. Regression pins.
GOLDEN = [
    "55320007000031C07F", "5562000D000031887D", "5560000D000031887B",
    "5562000D000031887D", "558A00100000310222", "558A00100000310222",
    "558A01100000310223", "558A02100000310224", "558A1810000031023A",
    "558A1810000031033B", "558200100000310018", "5562000D00003100F5",
    "5562000D0000318075", "5572000E000031C0C6", "5562000D000031C0B5",
    "55120003000031C05B", "55220005000031C06D", "55320007000031C07F",
    "55420009000031C091", "5552000B000031C0A3", "55820010000031C0D8",
    "55920012000031C0EA", "55A20014000031C0FC", "55B20016000031C00E",
    "55C20017000031C01F", "55D20019000031C031", "55E2001B000031C043",
    "5512000300003140DB", "5512000400003140DC", "5522000500003140ED",
    "55E2001B00003140C3", "558200100000314058", "5562000D0000218065",
    "5562000D0000118055", "5562000D0000318075", "5562000D0000338077",
    "5562000D0000128056", "5562000D0000308074", "5562000D0000318075",
]  # fmt: skip

CAPTURES = load_captures()


def ids(capture) -> str:
    """Name a parametrised case after its capture."""
    return str(capture)


class TestCorpus:
    """Properties that must hold for every capture."""

    def test_document_yields_the_expected_corpus(self):
        assert len(CAPTURES) == EXPECTED_CAPTURES

    @pytest.mark.parametrize("capture", CAPTURES, ids=ids)
    def test_capture_is_a_complete_frame(self, capture):
        # 4 header words plus 147 durations. The odd duration count is why
        # ESPHome's pair count rounds up, and is not corruption.
        assert len(capture.pronto.split()) == EXPECTED_WORDS
        assert len(capture.timings) == EXPECTED_WORDS - 4

    @pytest.mark.parametrize("capture", CAPTURES, ids=ids)
    def test_buffer_starts_with_a_space(self, capture):
        # The receive buffer begins before the first mark, which is what fixes
        # the mark/space parity and makes this pulse-distance rather than
        # pulse-width encoding.
        assert capture.timings[0] < 0

    @pytest.mark.parametrize("capture", CAPTURES, ids=ids)
    def test_decodes_with_valid_preamble_and_checksum(self, capture):
        command = Acp35Command.from_raw_timings(capture.timings)
        assert command is not None, "did not decode as an ACP 35 frame"
        state = command.to_bytes()
        assert state[0] == 0x55
        assert state[8] == sum(state[:8]) & 0xFF

    @pytest.mark.parametrize("capture", CAPTURES, ids=ids)
    def test_decodes_to_golden_bytes(self, capture):
        command = Acp35Command.from_raw_timings(capture.timings)
        assert command.to_bytes().hex().upper() == GOLDEN[capture.index]

    @pytest.mark.parametrize("capture", CAPTURES, ids=ids)
    def test_re_encodes_identically(self, capture):
        # Decode the capture, encode it with our nominal timings, decode that.
        # Real-world jitter must not survive as a difference in the frame.
        command = Acp35Command.from_raw_timings(capture.timings)
        again = Acp35Command.from_raw_timings(command.get_raw_timings())
        assert again is not None
        assert again.to_bytes() == command.to_bytes()

    @pytest.mark.parametrize("capture", CAPTURES, ids=ids)
    def test_reserved_fields_are_zero(self, capture):
        state = Acp35Command.from_raw_timings(capture.timings).to_bytes()
        assert state[4] == 0
        assert state[5] == 0
        assert not state[1] & 0x05, "b1 bits 2 and 0 are unused"
        assert not state[7] & 0x34, "b7 bits 5, 4 and 2 are unused"

    @pytest.mark.parametrize("capture", CAPTURES, ids=ids)
    def test_temperature_fields_agree(self, capture):
        """Whichever unit is displayed, the other field is its documented pair."""
        command = Acp35Command.from_raw_timings(capture.timings)
        rebuilt = (
            Acp35Command(
                power=command.power,
                mode=command.mode,
                fan=command.fan,
                celsius=command.celsius,
            )
            if Acp35Flag.CELSIUS in command.flags
            else Acp35Command(
                power=command.power,
                mode=command.mode,
                fan=command.fan,
                fahrenheit=command.fahrenheit,
            )
        )
        assert rebuilt.celsius == command.celsius
        assert rebuilt.fahrenheit == command.fahrenheit


class TestAgainstDocumentedIntent:
    """Spot checks written from the document's prose, not from the decoder."""

    @pytest.mark.parametrize(
        ("index", "attribute", "expected"),
        [
            # "On -> Off" and the two "-> On" captures
            (1, "power", True),
            (2, "power", False),
            (3, "power", True),
            # Temperature sweeps, labelled "up to Nc" / "to Nf"
            (15, "celsius", 17),
            (16, "celsius", 18),
            (20, "celsius", 24),
            (26, "celsius", 30),
            (27, "fahrenheit", 62),
            (28, "fahrenheit", 63),
            (30, "fahrenheit", 86),
            (31, "fahrenheit", 75),
            # Fan button cycles high -> medium -> low
            (32, "fan", Acp35Fan.MEDIUM),
            (33, "fan", Acp35Fan.LOW),
            (34, "fan", Acp35Fan.HIGH),
            # Mode button cycles cool -> fan -> dry -> auto -> cool
            (35, "mode", Acp35Mode.FAN),
            (36, "mode", Acp35Mode.DRY),
            (37, "mode", Acp35Mode.AUTO),
            (38, "mode", Acp35Mode.COOL),
            # Timer: pressed, +1h, +2h, 24h, then cancelled
            (6, "timer_hours", 1),
            (7, "timer_hours", 2),
            (8, "timer_hours", 24),
            (10, "timer_hours", 0),
        ],
    )
    def test_decoded_field_matches_prose(self, index, attribute, expected):
        command = Acp35Command.from_raw_timings(CAPTURES[index].timings)
        assert getattr(command, attribute) == expected

    def test_dry_mode_forces_the_fan_to_low(self):
        """The document notes the remote drops to low fan when entering dry."""
        command = Acp35Command.from_raw_timings(CAPTURES[36].timings)
        assert command.mode is Acp35Mode.DRY
        assert command.fan is Acp35Fan.LOW

    def test_unit_button_toggles_only_the_unit_flag(self):
        """The C/F button changes b7 bit 7 and nothing else."""
        to_fahrenheit = Acp35Command.from_raw_timings(CAPTURES[11].timings)
        to_celsius = Acp35Command.from_raw_timings(CAPTURES[12].timings)
        assert Acp35Flag.CELSIUS not in to_fahrenheit.flags
        assert Acp35Flag.CELSIUS in to_celsius.flags
        assert to_fahrenheit.to_bytes()[:7] == to_celsius.to_bytes()[:7]

    def test_timer_ui_arms_before_hours_are_chosen(self):
        """Pressing timer arms the bit while the hour count is still zero."""
        command = Acp35Command.from_raw_timings(CAPTURES[4].timings)
        assert command.timer_armed
        assert command.timer_hours == 0
        assert Acp35Flag.TIMER_UI in command.flags

    @pytest.mark.parametrize("index", [0, 13, 14, 15, 20, 26])
    def test_temperature_presses_set_the_temp_changed_flag(self, index):
        command = Acp35Command.from_raw_timings(CAPTURES[index].timings)
        assert Acp35Flag.TEMP_CHANGED in command.flags

    @pytest.mark.parametrize("index", [1, 2, 3])
    def test_power_presses_set_the_power_flag(self, index):
        command = Acp35Command.from_raw_timings(CAPTURES[index].timings)
        assert Acp35Flag.POWER_PRESSED in command.flags

    @pytest.mark.parametrize("index", [32, 33, 34, 35, 36, 37, 38])
    def test_fan_and_mode_presses_set_no_event_flag(self, index):
        """Only the unit bit is set; bits 6 and 3 belong to other buttons."""
        command = Acp35Command.from_raw_timings(CAPTURES[index].timings)
        assert command.flags == Acp35Flag.CELSIUS
