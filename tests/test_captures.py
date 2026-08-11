"""Regression tests over every real capture in the protocol document.

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
    "5562000D000031887D", "552A00050000218227", "552A0505000021822C",
    "552A0505000021832D", "55220005000021801D", "552A0705000021822E",
    "552A0705000021832F", "552A00050000218227", "552A03050000218028",
    "558A00100000310222", "558A00100000310222", "558A01100000310223",
    "558A02100000310224", "558A1810000031023A", "558A1810000031033B",
    "558200100000310018", "5562000D00003100F5", "5562000D0000318075",
    "5572000E000031C0C6", "5562000D000031C0B5", "55120003000031C05B",
    "55220005000031C06D", "55320007000031C07F", "55420009000031C091",
    "5552000B000031C0A3", "55820010000031C0D8", "55920012000031C0EA",
    "55A20014000031C0FC", "55B20016000031C00E", "55C20017000031C01F",
    "55D20019000031C031", "55E2001B000031C043", "5512000300003140DB",
    "5512000400003140DC", "5522000500003140ED", "55E2001B00003140C3",
    "558200100000314058", "5542000900002108C9", "5532000800002140F0",
    "5532000700002140EF", "5522000600002140DE", "5552000A0000214012",
    "5552000B0000214013", "5562000C0000214024", "5572000E0000214036",
    "5572000F0000214037", "558200110000214049", "55920012000021405A",
    "55A20013000021406B", "55A20014000021406C", "55B20015000021407D",
    "55B20016000021407E", "55C20017000021408F", "55C200180000214090",
    "55D2001900002140A1", "55D2001A00002140A2", "5562000D0000218065",
    "5562000D0000118055", "5562000D0000318075", "5562000D0000338077",
    "5562000D0000128056", "5562000D0000308074", "5562000D0000318075",
    "55220005000013800F", "5562000D0000128056", "5562000D0000308074",
    "55220005000031802D",
]  # fmt: skip

CAPTURES = load_captures()


def ids(capture) -> str:
    """Name a parametrised case after its capture."""
    return str(capture)


def find(fragment: str):
    """Return the one capture whose document label contains ``fragment``.

    Intent checks look captures up by label rather than position. The corpus is
    meant to grow, and inserting a capture into the middle of the document
    renumbers everything after it; a whole session's worth of expectations
    silently pointing at the wrong frames is worse than a missing one.

    Raises:
        AssertionError: if the fragment does not match exactly one capture.
    """
    matches = [c for c in CAPTURES if fragment.lower() in c.label.lower()]
    assert len(matches) == 1, (
        f"{fragment!r} matched {len(matches)} captures: {[str(m) for m in matches]}"
    )
    return matches[0]


def decode(fragment: str) -> Acp35Command:
    """Decode the capture whose label contains ``fragment``."""
    return Acp35Command.from_raw_timings(find(fragment).timings)


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
        ("label", "attribute", "expected"),
        [
            ("Remote not being used -> On", "power", True),
            ("On -> Off", "power", False),
            ("Off -> On", "power", True),
            ("kept pressing down", "celsius", 17),
            ("up once to 18c", "celsius", 18),
            ("up to 24c", "celsius", 24),
            ("up to 30c", "celsius", 30),
            ("Down to 62f", "fahrenheit", 62),
            ("Up to 63f", "fahrenheit", 63),
            ("Jump up to 86f", "fahrenheit", 86),
            ("Jump down to 75f", "fahrenheit", 75),
            # Fan button cycles high -> medium -> low
            ("High -> Medium", "fan", Acp35Fan.MEDIUM),
            ("Medium -> Low", "fan", Acp35Fan.LOW),
            ("Low -> High", "fan", Acp35Fan.HIGH),
            # Mode button cycles cool -> fan -> dry -> auto -> cool
            ("Cool -> Fan", "mode", Acp35Mode.FAN),
            ("Auto -> Cool", "mode", Acp35Mode.COOL),
            ("mode press to dry", "mode", Acp35Mode.DRY),
            ("mode press to auto", "mode", Acp35Mode.AUTO),
            ("up to 5 h", "timer_hours", 5),
            ("up to 7 h", "timer_hours", 7),
            ("24th", "timer_hours", 24),
            ("cancel method 1", "timer_hours", 0),
        ],
    )
    def test_decoded_field_matches_prose(self, label, attribute, expected):
        assert getattr(decode(label), attribute) == expected

    def test_dry_mode_pairs_with_low_fan(self):
        """The remote's stored fan speed for dry is low, in both sessions."""
        for label in ("The fan speed jumped to low", "mode press to dry"):
            command = decode(label)
            assert command.mode is Acp35Mode.DRY
            assert command.fan is Acp35Fan.LOW

    def test_unit_button_toggles_only_the_unit_flag(self):
        """The C/F button changes b7 bit 7 and nothing else."""
        to_fahrenheit = decode("Press C -> F")
        to_celsius = decode("Press F -> C")
        assert Acp35Flag.CELSIUS not in to_fahrenheit.flags
        assert Acp35Flag.CELSIUS in to_celsius.flags
        assert to_fahrenheit.to_bytes()[:7] == to_celsius.to_bytes()[:7]

    def test_timer_arms_before_hours_are_chosen(self):
        """Pressing timer arms the bit while the hour count is still zero."""
        command = decode("press timer with no timer set")
        assert command.timer_armed
        assert command.timer_hours == 0
        assert Acp35Flag.TIMER_UI in command.flags

    def test_timer_ui_bit_reports_the_display_not_a_pending_timer(self):
        """An ordinary press while a timer counts down leaves b7 bit 1 clear.

        This is what makes bit 1 an event rather than state, and it is the
        capture that caught the integration deriving it from `hours > 0`.
        """
        command = decode("fan pressed while 3 h counts down")
        assert Acp35Flag.TIMER_UI not in command.flags
        assert command.timer_armed, "b1 bit 3 must survive a non-timer press"
        assert command.timer_hours == 3, "b2 must survive a non-timer press"

    @pytest.mark.parametrize(
        "label", ["press timer with 5 h running", "press timer with 7 h running"]
    )
    def test_reopening_a_running_timer_sets_bit_zero(self, label):
        """b7 bit 0 marks a TIMER press onto an already-set timer.

        Both cases carry it at different hour counts, which is what rules out
        the bit encoding the hours.
        """
        assert decode(label).flags & 0x01

    def test_the_two_cancel_routes_disagree(self):
        """Pressing timer twice disarms; winding down to zero does not."""
        pressed_twice = decode("cancel method 1")
        wound_down = decode("cancel method 2")
        assert pressed_twice.timer_hours == wound_down.timer_hours == 0
        assert not pressed_twice.timer_armed
        assert wound_down.timer_armed, "zero hours but still armed"

    @pytest.mark.parametrize(
        "label",
        ["up button pressed onc", "22c -> 23", "23c -> 22", "up to 24c", "up to 30c"],
    )
    def test_temperature_presses_set_the_temp_changed_flag(self, label):
        assert Acp35Flag.TEMP_CHANGED in decode(label).flags

    @pytest.mark.parametrize(
        "label", ["Remote not being used -> On", "On -> Off", "Off -> On"]
    )
    def test_power_presses_set_the_power_flag(self, label):
        assert Acp35Flag.POWER_PRESSED in decode(label).flags

    @pytest.mark.parametrize(
        "label",
        [
            "High -> Medium",
            "Medium -> Low",
            "Low -> High",
            "Cool -> Fan",
            "The fan speed jumped to low",
            "The fan speed jumped to high",
            "Auto -> Cool",
        ],
    )
    def test_fan_and_mode_presses_set_no_event_flag(self, label):
        """Only the unit bit is set; bits 6 and 3 belong to other buttons."""
        assert decode(label).flags == Acp35Flag.CELSIUS

    @pytest.mark.parametrize(
        ("label", "celsius"),
        [
            ("mode press to fan", 18),
            ("mode press to dry", 22),
            ("mode press to auto", 22),
            ("mode press to cool", 18),
        ],
    )
    def test_only_cool_and_fan_carry_the_setpoint(self, label, celsius):
        """One setpoint, owned by cool; dry and auto transmit a fixed 22 C.

        Captured immediately after cool was set to 18 C, so a mode that follows
        the setpoint reports 18 and one that ignores it reports 22.
        """
        command = decode(label)
        assert command.celsius == celsius
        assert Acp35Flag.TEMP_CHANGED not in command.flags, (
            "a mode press changes the transmitted temperature without "
            "claiming the setpoint moved"
        )
