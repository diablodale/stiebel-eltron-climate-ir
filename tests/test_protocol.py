"""Unit tests for the ACP 35 frame encoder and decoder."""

import pytest
from devices.acp35.protocol import (
    BIT_COUNT,
    BIT_MARK,
    CARRIER_HZ,
    HEADER_MARK,
    MAX_CELSIUS,
    MAX_FAHRENHEIT,
    MAX_TIMER_HOURS,
    MIN_CELSIUS,
    MIN_FAHRENHEIT,
    ONE_SPACE,
    ZERO_SPACE,
    Acp35Command,
    Acp35Fan,
    Acp35Flag,
    Acp35Mode,
    celsius_to_fahrenheit,
    fahrenheit_to_celsius,
)


def cool_high(**overrides) -> Acp35Command:
    """Build a baseline command, on / cool / high fan / 22 C."""
    return Acp35Command(
        **{
            "power": True,
            "mode": Acp35Mode.COOL,
            "fan": Acp35Fan.HIGH,
            "celsius": 22,
            **overrides,
        }
    )


class TestFramePacking:
    """The nine bytes and where each field lands."""

    def test_baseline_frame(self):
        assert cool_high().to_bytes().hex().upper() == "5562000D0000318075"

    def test_preamble_is_constant(self):
        assert cool_high().to_bytes()[0] == 0x55

    def test_checksum_is_sum_of_preceding_bytes(self):
        state = cool_high(celsius=19, timer_hours=7).to_bytes()
        assert state[8] == sum(state[:8]) & 0xFF

    def test_celsius_occupies_high_nibble_of_b1(self):
        for celsius in range(MIN_CELSIUS, MAX_CELSIUS + 1):
            assert cool_high(celsius=celsius).to_bytes()[1] >> 4 == celsius - 16

    def test_power_is_b1_bit1(self):
        assert cool_high(power=True).to_bytes()[1] & 0x02
        assert not cool_high(power=False).to_bytes()[1] & 0x02

    def test_b1_bits_2_and_0_stay_clear(self):
        assert not cool_high(timer_hours=24).to_bytes()[1] & 0x05

    def test_timer_hours_are_plain_binary_in_b2(self):
        for hours in range(MAX_TIMER_HOURS + 1):
            assert cool_high(timer_hours=hours).to_bytes()[2] == hours

    def test_fahrenheit_is_biased_by_59_in_b3(self):
        assert cool_high(celsius=19).to_bytes()[3] == 66 - 59

    def test_b4_and_b5_are_always_zero(self):
        state = cool_high(celsius=30, timer_hours=24, power=False).to_bytes()
        assert state[4] == 0
        assert state[5] == 0

    def test_fan_and_mode_share_b6(self):
        state = cool_high(fan=Acp35Fan.MEDIUM, mode=Acp35Mode.DRY).to_bytes()
        assert state[6] >> 4 == Acp35Fan.MEDIUM
        assert state[6] & 0x0F == Acp35Mode.DRY

    def test_flags_are_b7_verbatim(self):
        flags = Acp35Flag.CELSIUS | Acp35Flag.TEMP_CHANGED
        assert cool_high(flags=flags).to_bytes()[7] == 0xC0


class TestTimerDirection:
    """b1 bit 3 says which way a pending timer will switch the unit.

    It is not "a timer is set". A capture taken with the unit off and three
    hours pending has the hours in b2 and the bit clear, because that timer
    switches the unit *on*. The same state with the unit running sets it.
    """

    def test_a_timer_while_running_is_an_off_delay(self):
        assert cool_high(timer_hours=5).timer_off_delay

    def test_a_timer_while_stopped_is_not(self):
        stopped = cool_high(power=False, timer_hours=5)
        assert not stopped.timer_off_delay
        assert stopped.to_bytes()[1] & 0x08 == 0, "b1 bit 3 must be clear"
        assert stopped.to_bytes()[2] == 5, "b2 still carries the hours"

    def test_no_timer_means_no_bit_either_way(self):
        assert not cool_high(timer_hours=0).timer_off_delay
        assert not cool_high(power=False, timer_hours=0).timer_off_delay

    def test_the_entry_display_counts_as_pending(self):
        """The remote sets the bit at zero hours while the display is open."""
        opened = cool_high(timer_hours=0, flags=Acp35Flag.TIMER_UI)
        assert opened.timer_off_delay
        assert opened.to_bytes()[2] == 0
        # ...but only while running, for the same reason as above.
        assert not cool_high(
            power=False, timer_hours=0, flags=Acp35Flag.TIMER_UI
        ).timer_off_delay

    def test_the_bit_can_still_be_forced(self):
        """from_raw_timings round-trips whatever was actually on the wire."""
        state = cool_high(power=False, timer_hours=0, timer_off_delay=True).to_bytes()
        assert state[1] & 0x08
        assert state[2] == 0


class TestTemperaturePairing:
    """Both temperature fields always travel; one is derived from the other."""

    @pytest.mark.parametrize(
        ("celsius", "fahrenheit"),
        [
            (17, 62), (18, 64), (19, 66), (20, 68), (21, 70), (22, 72), (23, 73),
            (24, 75), (25, 77), (26, 79), (27, 81), (28, 82), (29, 84), (30, 86),
        ],
    )  # fmt: skip
    def test_celsius_to_fahrenheit(self, celsius, fahrenheit):
        assert celsius_to_fahrenheit(celsius) == fahrenheit

    def test_minimum_celsius_pins_to_minimum_fahrenheit(self):
        # Rounding alone would give 63 F; the scales' endpoints are pinned.
        assert round(17 * 9 / 5 + 32) == 63
        assert celsius_to_fahrenheit(17) == 62

    @pytest.mark.parametrize(
        ("fahrenheit", "celsius"),
        [(62, 17), (63, 17), (64, 18), (72, 22), (75, 24), (86, 30)],
    )
    def test_fahrenheit_to_celsius(self, fahrenheit, celsius):
        """The six pairings a capture confirms."""
        assert fahrenheit_to_celsius(fahrenheit) == celsius

    @pytest.mark.parametrize("fahrenheit", range(MIN_FAHRENHEIT, MAX_FAHRENHEIT + 1))
    def test_fahrenheit_table_follows_rounding(self, fahrenheit):
        """Pin the rule that generated the 19 entries no capture covers."""
        assert fahrenheit_to_celsius(fahrenheit) == round((fahrenheit - 32) * 5 / 9)

    @pytest.mark.parametrize("celsius", range(MIN_CELSIUS, MAX_CELSIUS + 1))
    def test_celsius_table_follows_rounding_except_at_the_minimum(self, celsius):
        """The Celsius table is rounding too, with one documented exception."""
        rounded = round(celsius * 9 / 5 + 32)
        if celsius == MIN_CELSIUS:
            assert celsius_to_fahrenheit(celsius) == MIN_FAHRENHEIT != rounded
        else:
            assert celsius_to_fahrenheit(celsius) == rounded

    def test_the_two_tables_are_not_inverses(self):
        """63 F pairs back to 17 C, but 17 C pairs out to 62 F."""
        assert fahrenheit_to_celsius(63) == 17
        assert celsius_to_fahrenheit(17) == 62

    def test_every_celsius_pairing_round_trips(self):
        """Each Celsius value's paired Fahrenheit maps back to it."""
        for celsius in range(MIN_CELSIUS, MAX_CELSIUS + 1):
            assert fahrenheit_to_celsius(celsius_to_fahrenheit(celsius)) == celsius

    def test_giving_celsius_sets_the_unit_flag(self):
        assert Acp35Flag.CELSIUS in cool_high(celsius=22).flags

    def test_giving_fahrenheit_clears_the_unit_flag(self):
        command = Acp35Command(
            power=True, mode=Acp35Mode.COOL, fan=Acp35Fan.HIGH, fahrenheit=72
        )
        assert Acp35Flag.CELSIUS not in command.flags

    def test_both_temperatures_stored_verbatim_when_both_given(self):
        # 63 F pairs with 17 C, but 17 C alone would derive 62 F. A frame that
        # came from the remote in Fahrenheit mode must survive unchanged.
        command = Acp35Command(
            power=True,
            mode=Acp35Mode.COOL,
            fan=Acp35Fan.HIGH,
            celsius=17,
            fahrenheit=63,
        )
        assert command.celsius == 17
        assert command.fahrenheit == 63


class TestValidation:
    """Out-of-range input is rejected rather than silently truncated."""

    @pytest.mark.parametrize("celsius", [MIN_CELSIUS - 1, MAX_CELSIUS + 1, 0, 100])
    def test_celsius_out_of_range(self, celsius):
        with pytest.raises(ValueError, match="celsius"):
            cool_high(celsius=celsius)

    @pytest.mark.parametrize(
        "fahrenheit", [MIN_FAHRENHEIT - 1, MAX_FAHRENHEIT + 1, 0, 200]
    )
    def test_fahrenheit_out_of_range(self, fahrenheit):
        with pytest.raises(ValueError, match="fahrenheit"):
            Acp35Command(
                power=True,
                mode=Acp35Mode.COOL,
                fan=Acp35Fan.HIGH,
                fahrenheit=fahrenheit,
            )

    @pytest.mark.parametrize("hours", [-1, MAX_TIMER_HOURS + 1])
    def test_timer_hours_out_of_range(self, hours):
        with pytest.raises(ValueError, match="timer_hours"):
            cool_high(timer_hours=hours)

    def test_temperature_is_required(self):
        with pytest.raises(ValueError, match="celsius or fahrenheit"):
            Acp35Command(power=True, mode=Acp35Mode.COOL, fan=Acp35Fan.HIGH)


class TestRawTimings:
    """The wire form."""

    def test_structure_is_header_bits_trailer(self):
        timings = cool_high().get_raw_timings()
        assert len(timings) == 2 + BIT_COUNT * 2 + 1
        assert timings[0] == HEADER_MARK
        assert timings[1] < 0
        assert timings[-1] == BIT_MARK

    def test_marks_and_spaces_alternate(self):
        timings = cool_high().get_raw_timings()
        for index, value in enumerate(timings):
            assert (value > 0) == (index % 2 == 0), f"element {index} has wrong sign"

    def test_bit_values_select_the_space_length(self):
        # b0 is 0x55, so the first eight spaces alternate zero, one, zero, one...
        spaces = cool_high().get_raw_timings()[3::2][:8]
        assert spaces == [-ZERO_SPACE, -ONE_SPACE] * 4

    def test_carrier_is_38khz_and_never_repeats(self):
        command = cool_high()
        assert command.modulation == CARRIER_HZ
        assert command.repeat_count == 0


class TestDecoding:
    """from_raw_timings, including the shapes a receiver actually produces."""

    def test_round_trip(self):
        original = cool_high(
            celsius=29, mode=Acp35Mode.DRY, fan=Acp35Fan.LOW, timer_hours=13
        )
        decoded = Acp35Command.from_raw_timings(original.get_raw_timings())
        assert decoded is not None
        assert decoded.to_bytes() == original.to_bytes()

    def test_round_trip_preserves_every_field(self):
        original = cool_high(power=False, celsius=17, timer_hours=24)
        decoded = Acp35Command.from_raw_timings(original.get_raw_timings())
        assert (decoded.power, decoded.mode, decoded.fan) == (
            original.power,
            original.mode,
            original.fan,
        )
        assert (decoded.celsius, decoded.fahrenheit) == (
            original.celsius,
            original.fahrenheit,
        )
        assert (decoded.timer_hours, decoded.timer_off_delay) == (
            original.timer_hours,
            original.timer_off_delay,
        )
        assert decoded.flags == original.flags

    def test_unnamed_flag_bit_survives(self):
        # Bit 0 is unexplained but appears in a real capture as part of 0x03.
        original = cool_high(flags=0x03)
        decoded = Acp35Command.from_raw_timings(original.get_raw_timings())
        assert int(decoded.flags) == 0x03

    def test_leading_mark_may_be_missing(self):
        # A received buffer starts at the space before the first mark.
        full = cool_high().get_raw_timings()
        assert Acp35Command.from_raw_timings(full[1:]).to_bytes() == (
            Acp35Command.from_raw_timings(full).to_bytes()
        )

    def test_whole_header_may_be_missing(self):
        full = cool_high().get_raw_timings()
        assert Acp35Command.from_raw_timings(full[2:]).to_bytes() == (
            Acp35Command.from_raw_timings(full).to_bytes()
        )

    @pytest.mark.parametrize(
        ("description", "timings"),
        [
            ("empty", []),
            ("too short", [5100, -5100, 576, -481]),
            ("all marks", [576] * 200),
            ("nonsense spaces", [5100, -5100] + [576, -900] * BIT_COUNT + [576]),
        ],
    )
    def test_rejects_non_acp35_timings(self, description, timings):
        assert Acp35Command.from_raw_timings(timings) is None, description

    def test_rejects_bad_checksum(self):
        timings = cool_high().get_raw_timings()
        # Flip the last data bit, which corrupts the frame against its checksum.
        timings[2 + (BIT_COUNT - 9) * 2 + 1] = -ONE_SPACE
        assert Acp35Command.from_raw_timings(timings) is None

    def test_rejects_bad_preamble(self):
        timings = cool_high().get_raw_timings()
        timings[3] = -ONE_SPACE  # corrupt b0's top bit
        assert Acp35Command.from_raw_timings(timings) is None


# The gap seen between two frames the receiver delivered in one buffer. Inside
# ONE_SPACE's tolerance band of 1157..2699 us, which is the whole reason frames
# cannot be split by looking for a long space.
MERGED_GAP = -1415

# ESPHome's receive idle timeout closes the buffer.
IDLE_TAIL = -10000


def merged(*commands: Acp35Command) -> list[int]:
    """Build the buffer a receiver delivers for frames sent back to back."""
    timings: list[int] = []
    for command in commands:
        timings.extend(command.get_raw_timings())
        timings.append(MERGED_GAP)
    timings[-1] = IDLE_TAIL
    return timings


class TestDecodingAWholeBuffer:
    """all_from_raw_timings: a buffer is not a frame.

    A receiver closes a buffer on an idle period, so two frames closer together
    than that arrive together. Reading only the first loses the second silently.
    """

    def test_one_frame_still_reads_as_one(self):
        original = cool_high(celsius=25)
        frames = Acp35Command.all_from_raw_timings(original.get_raw_timings())
        assert [f.to_bytes() for f in frames] == [original.to_bytes()]

    def test_two_frames_in_one_buffer(self):
        first = cool_high(celsius=18)
        second = cool_high(celsius=27, fan=Acp35Fan.LOW)
        frames = Acp35Command.all_from_raw_timings(merged(first, second))
        assert [f.to_bytes() for f in frames] == [first.to_bytes(), second.to_bytes()]

    def test_three_frames_in_one_buffer(self):
        commands = [cool_high(celsius=c) for c in (17, 22, 30)]
        frames = Acp35Command.all_from_raw_timings(merged(*commands))
        assert [f.celsius for f in frames] == [17, 22, 30]

    def test_the_buffer_may_start_at_the_header_space(self):
        """A capture begins after the mark, which a merged one does too."""
        first, second = cool_high(celsius=19), cool_high(celsius=28)
        frames = Acp35Command.all_from_raw_timings(merged(first, second)[1:])
        assert [f.celsius for f in frames] == [19, 28]

    def test_a_second_frame_that_is_corrupt_is_dropped_not_the_first(self):
        first, second = cool_high(celsius=21), cool_high(celsius=26)
        timings = merged(first, second)
        timings[-4] = -900  # nonsense space inside the second frame
        frames = Acp35Command.all_from_raw_timings(timings)
        assert [f.celsius for f in frames] == [21]

    def test_trailing_noise_after_a_frame_is_ignored(self):
        original = cool_high()
        frames = Acp35Command.all_from_raw_timings(
            [*original.get_raw_timings(), -2000, 300, -300, 250]
        )
        assert len(frames) == 1

    @pytest.mark.parametrize(
        ("description", "timings"),
        [("empty", []), ("noise", [200, -300, 250, -9000]), ("all marks", [576] * 200)],
    )
    def test_nothing_decodable_yields_nothing(self, description, timings):
        assert Acp35Command.all_from_raw_timings(timings) == [], description

    def test_from_raw_timings_returns_the_first_of_several(self):
        """The single-frame entry point keeps its old meaning."""
        first, second = cool_high(celsius=20), cool_high(celsius=29)
        decoded = Acp35Command.from_raw_timings(merged(first, second))
        assert decoded.to_bytes() == first.to_bytes()
