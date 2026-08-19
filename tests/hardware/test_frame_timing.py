"""Question 9: is one frame enough, and how close together may two be?

Two independent measurements, in two classes with their own fixtures, so either
can be run alone with `-k`. Both need a person watching the panel, and the first
is long.

**Why this could not be answered from captures.** The 10.1 ms tail on every one is
ESPHome's receive idle timeout rather than something the remote emitted, so the
true separation between two of the remote's frames has never been observed.

**Why the separation is built into one transmission rather than requested twice.**
Asking the bench to send twice puts the gap at the mercy of the path between Home
Assistant and the LED, which does not preserve it: four frames requested 150 ms
apart once reached the air with two of them 1415 us apart. A pair of frames is 295
durations, well inside the bench's 1024 limit, so both are handed over as a single
waveform and the ESP32's own timer sets the gap. That measures the appliance
rather than the transport.

It also means the two halves answer different things, and neither substitutes for
the other:

- `TestIsOneFrameEnough` asks whether the appliance reliably acts on a single
  frame, which is what `repeat_count = 0` assumes.
- `TestHowCloseTwoFramesMayBe` asks the appliance's own tolerance. Home Assistant
  issuing two service calls in quick succession -- a mode change and a temperature
  change from one script -- is the case that motivates it, but the transport sits
  between, so this bounds the problem rather than reproducing it.

**A buffer is not a frame.** Two frames close enough together arrive in one
buffer, and `all_from_raw_timings` splits them structurally. Counting buffers
would under-count what was transmitted.

**Emitter placement decides these numbers.** Question 8 lost six of sixteen
commands from across the room and none from beside the appliance, so a reliability
figure measured at a distance would describe the room. Run this with the emitter
close, as `docs/ha_ir_platform/plan.md` records.
"""

import time
from dataclasses import dataclass

import pytest
from devices.acp35.protocol import (
    MAX_CELSIUS,
    MIN_CELSIUS,
    Acp35Command,
    Acp35Fan,
    Acp35Flag,
    Acp35Mode,
)

# How many single frames to send. Each one is a separate question to a person, so
# this is the whole cost of the first half. Twenty distinguishes "never misses"
# from a rate around one in ten; ten would only catch something much worse.
SINGLE_FRAME_TRIALS = 20

# Long enough that a miss cannot be the appliance still busy with the frame
# before, which would measure the second half's question instead of this one.
BETWEEN_TRIALS = 3.0

# Separations to try, largest first, so the failures cluster at the end and the
# session can be stopped once the boundary is found. Capped at 100 ms because the
# bench refuses a single duration longer than that, and floored at 500 us because
# below a bit mark the two frames stop being separable at all.
GAPS_US = (100_000, 50_000, 20_000, 10_000, 5_000, 2_000, 1_000, 500)

# ESPHome closes a receive buffer on this much silence, so a gap at or above it
# puts the two frames in separate buffers and the loopback cannot time it. The
# gap is still exact -- we built it -- but it is worth saying which figures were
# measured and which were merely requested.
RECEIVER_IDLE_US = 10_000

SETTLE = 1.5

TEMPERATURE_ANSWERS = (
    *(str(celsius) for celsius in range(MIN_CELSIUS, MAX_CELSIUS + 1)),
    "none",
)


def setpoint_cycle(baseline: int) -> list[int]:
    """Return three setpoints that are not the baseline and not each other.

    Three rather than two. A trial's reading has to differ from whatever the
    panel showed before it, and with only two values a missed frame would leave
    the panel already showing the next trial's target -- a miss indistinguishable
    from a hit.

    Above the baseline where there is room, so an accepted frame lets a cooling
    appliance idle rather than work harder.
    """
    above = list(range(baseline + 1, MAX_CELSIUS + 1))
    below = list(range(baseline - 1, MIN_CELSIUS - 1, -1))
    chosen = (above + below)[:3]
    assert len(chosen) == 3, f"no room for three setpoints around {baseline}"
    return chosen


def frame(mode: Acp35Mode, fan: Acp35Fan, celsius: int) -> Acp35Command:
    """Build the frame the integration sends for a setpoint change."""
    return Acp35Command(
        power=True,
        mode=mode,
        fan=fan,
        celsius=celsius,
        flags=Acp35Flag.CELSIUS | Acp35Flag.TEMP_CHANGED,
    )


@dataclass(frozen=True)
class Trial:
    """One single frame, and what the panel said afterwards."""

    number: int
    celsius: int
    shown: str
    transmitted: bool
    """Whether the loopback heard it leave as the bytes we built."""

    @property
    def landed(self) -> bool:
        """Whether the appliance acted on it."""
        return self.shown == str(self.celsius)


@dataclass(frozen=True)
class Pair:
    """Two frames sent as one waveform, and what the panel said afterwards."""

    gap_us: int
    first: int
    second: int
    shown: str
    measured_us: int | None
    """The gap as the receiver timed it, or None when it split the buffer."""
    heard: int
    """How many frames came back, across however many buffers."""

    @property
    def second_landed(self) -> bool:
        """Whether the appliance ended on the second frame, which is the point."""
        return self.shown == str(self.second)


def send_once(send, journal, timings: list[int], label: str, records: int):
    """Send a waveform once and return the frame records that came back.

    ``records`` is how many receive buffers to wait for, which is not the same as
    how many frames were sent: below the receiver's idle timeout a pair arrives in
    one buffer, above it in two. Waiting for the wrong number would return early
    and report the second frame as never transmitted.
    """
    sent = send(timings, label=label)
    return journal.wait_for_frames(sent.after, count=records)


def measure_gap(record, first_length: int) -> int | None:
    """Return the space between two frames that arrived in one buffer.

    The receiver records our leading mark, so one of our frames comes back one
    duration longer than it went out -- the trailing idle. Two frames in one
    buffer therefore put the separation immediately after the first frame's
    durations, as a negative space.

    **The buffer must hold two frames**, which is why this takes a record rather
    than timings. In a single-frame buffer the same index holds the trailing idle,
    a negative number of about 10 ms, and reading that as an inter-frame gap would
    invent a measurement for every separation the receiver split.
    """
    if len(record.frames) != 2:
        return None
    timings = record.timings
    if len(timings) <= first_length or timings[first_length] >= 0:
        return None
    return -timings[first_length]


@pytest.fixture(scope="session")
def single_frames(send, journal, appliance, ask) -> list[Trial]:
    """Send one frame at a time and ask each time whether it landed.

    Long by necessity. The protocol carries absolute state, so a missed frame in
    the middle of a burst leaves no trace -- the next frame that lands overwrites
    it. Counting misses means asking after every single one.
    """
    mode, fan = Acp35Mode.COOL, appliance.fan
    cycle = setpoint_cycle(appliance.celsius)
    trials: list[Trial] = []

    for number in range(1, SINGLE_FRAME_TRIALS + 1):
        celsius = cycle[number % len(cycle)]
        command = frame(mode, fan, celsius)
        print(f"\nTrial {number} of {SINGLE_FRAME_TRIALS}: sending one frame...")
        heard = send_once(
            send, journal, command.get_raw_timings(), f"question 9, trial {number}", 1
        )
        transmitted = any(
            len(record.frames) == 1
            and record.frames[0].to_bytes() == command.to_bytes()
            for record in heard
        )
        time.sleep(SETTLE)
        shown = ask(
            f"Trial {number} of {SINGLE_FRAME_TRIALS}: what temperature does the "
            "ACP 35 panel show?",
            TEMPERATURE_ANSWERS,
        )
        trials.append(Trial(number, celsius, shown, transmitted))
        time.sleep(BETWEEN_TRIALS)
    return trials


@pytest.fixture(scope="session")
def pairs(send, journal, appliance, ask) -> list[Pair]:
    """Send two frames as one waveform at shrinking separations."""
    mode, fan = Acp35Mode.COOL, appliance.fan
    cycle = setpoint_cycle(appliance.celsius)
    results: list[Pair] = []

    for index, gap_us in enumerate(GAPS_US):
        first = cycle[index % len(cycle)]
        second = cycle[(index + 1) % len(cycle)]
        one = frame(mode, fan, first)
        two = frame(mode, fan, second)
        timings = [*one.get_raw_timings(), -gap_us, *two.get_raw_timings()]

        # One buffer below the receiver's idle timeout, two above it. Asking for
        # the wrong number returns before the second has arrived.
        expected_records = 1 if gap_us < RECEIVER_IDLE_US else 2
        print(f"\nSeparation {gap_us} us: sending two frames as one waveform...")
        heard = send_once(
            send, journal, timings, f"question 9, {gap_us} us apart", expected_records
        )
        frames = [f for record in heard for f in record.frames]
        measured = next(
            (
                gap
                for record in heard
                if (gap := measure_gap(record, len(one.get_raw_timings())))
            ),
            None,
        )
        time.sleep(SETTLE)
        shown = ask(
            f"Separation {gap_us} us: what temperature does the ACP 35 panel show?",
            TEMPERATURE_ANSWERS,
        )
        results.append(Pair(gap_us, first, second, shown, measured, len(frames)))
        time.sleep(BETWEEN_TRIALS)
    return results


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestIsOneFrameEnough:
    """Whether `repeat_count = 0` is safe, which is what the integration ships."""

    def test_every_trial_was_actually_transmitted(self, single_frames) -> None:
        """A frame that never left is not a frame the appliance missed."""
        unsent = [trial.number for trial in single_frames if not trial.transmitted]
        assert not unsent, (
            f"the loopback did not hear trials {unsent} go out as the bytes we "
            "built, so they cannot be counted either way. Re-run; if it persists "
            "the emitter is the problem, not the appliance"
        )

    def test_the_appliance_acts_on_every_single_frame(self, single_frames) -> None:
        """The result, and what it costs if it fails.

        A single miss matters more than its rate suggests: the protocol carries
        absolute state and nothing acknowledges, so a dropped frame leaves Home
        Assistant confidently showing a state the appliance is not in, until
        somebody changes something else.
        """
        missed = [
            (trial.number, trial.celsius, trial.shown)
            for trial in single_frames
            if not trial.landed
        ]
        rate = f"{len(single_frames) - len(missed)}/{len(single_frames)}"
        print(f"\nsingle frames acted on: {rate}")
        assert not missed, (
            f"the appliance acted on {rate} single frames. Missed "
            f"(trial, sent, shown): {missed}. `repeat_count = 0` is not safe -- "
            "raise it, or repeat in `_async_transmit`"
        )


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestHowCloseTwoFramesMayBe:
    """The separation below which the appliance stops acting on the second frame."""

    def test_a_lost_second_frame_was_not_simply_never_sent(self, pairs) -> None:
        """Only a row that failed needs its transmission proved.

        Checking every row would fail on our own receiver rather than on the
        appliance. Ambient infrared damages a capture often enough that one bad
        buffer says nothing -- at a 10 ms separation on 2026-08-19 the echo of
        the second frame came back with cell 29 at 707 us, between a zero and a
        one, so it did not decode. The appliance had already acted on that pair.
        The frame reached the air; the buffer beside the emitter was glitched.

        So a row whose second frame landed needs no echo to prove anything. A row
        whose second frame did not land is where "never sent" and "sent and
        ignored" are different answers, and only the second is about the
        appliance.
        """
        unproven = [
            (pair.gap_us, pair.heard)
            for pair in pairs
            if not pair.second_landed and pair.heard != 2
        ]
        assert not unproven, (
            f"(separation, frames heard) where the appliance did not act on the "
            f"second frame and the loopback did not hear two go out: {unproven}. "
            "Nothing about the appliance follows from those rows -- re-run them"
        )

    def test_the_separation_we_asked_for_is_the_one_that_went_out(self, pairs) -> None:
        """The gap is built into the waveform, so it should need no trusting.

        Only checkable below the receiver's idle timeout; above it the buffer is
        closed between the two frames and there is nothing to read. Those rows are
        reported as requested rather than measured.
        """
        measurable = [pair for pair in pairs if pair.gap_us < RECEIVER_IDLE_US]
        assert measurable, "no separation below the receiver's idle timeout was tried"
        wrong = [
            (pair.gap_us, pair.measured_us)
            for pair in measurable
            if pair.measured_us is None
            or abs(pair.measured_us - pair.gap_us) > pair.gap_us * 0.25
        ]
        assert not wrong, (
            f"(requested, measured) separations that disagree: {wrong}. The "
            "figures below are about a waveform we did not send"
        )

    def test_the_appliance_acts_on_the_second_frame(self, pairs) -> None:
        """Where it stops doing so is the number `_async_transmit` needs.

        Only the second frame matters. Home Assistant sending two changes in
        quick succession must end in the state the second one asked for; the
        appliance ignoring the first and obeying the second is the same outcome
        as obeying both.
        """
        for pair in pairs:
            measured = (
                "not timed" if pair.measured_us is None else f"{pair.measured_us} us"
            )
            print(
                f"  {pair.gap_us:>7} us requested, {measured:>12}: panel {pair.shown}"
                f" (sent {pair.first} then {pair.second})"
                f" -> {'ok' if pair.second_landed else 'SECOND FRAME LOST'}"
            )
        dropped = [pair.gap_us for pair in pairs if not pair.second_landed]
        if not dropped:
            print(
                f"\nNo minimum spacing needed down to {min(GAPS_US)} us. Two "
                "service calls in immediate succession are safe."
            )
            return
        pytest.fail(
            f"the appliance did not act on the second frame at these separations: "
            f"{dropped} us. `_async_transmit` needs a minimum spacing above "
            f"{max(dropped)} us, since Home Assistant can issue two changes in "
            "immediate succession"
        )
