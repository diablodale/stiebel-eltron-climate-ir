"""What carrier frequency does the ACP 35 hear best?

**Answered 2026-08-19: 37-39 kHz, centred on 38 kHz.** Two rounds, ascending and
descending, produced identical edges, and `CARRIER_HZ = 38000` is confirmed by
measurement for the first time. The test stays because the answer is a property
of one appliance: a second ACP 35, or another model, has to be asked again.

`CARRIER_HZ = 38000` had never been measured. The 2025 analysis recorded
"38028.9 Hz" for every capture, but that number is Pronto header word `006D`
converted back: ESPHome's `ProntoProtocol::decode()` writes
`REFERENCE_FREQUENCY / 38000` into that slot regardless of what was on the air,
so the figure is one constant reprinted, and the analyzer that validated it
against `37800 < f < 38200` was checking that constant against itself. The
protocol document has said "assumed, not measured" since the 2026 rewrite.

**The remote's carrier cannot be measured with this equipment.** Every receiver
in the chain -- the KC868-AG's and the appliance's -- is a demodulating module
that strips the carrier before anything downstream sees it. No capture through
ESPHome can report it, and nothing here pretends to.

**So this measures the actionable quantity instead: the carrier at which the
appliance responds best.** That is what sets transmit range, it is what
`CARRIER_HZ` should hold, and unlike the remote's carrier it is reachable with
the emitter we already have.

## The method

A demodulating receiver is a band-pass detector: its sensitivity peaks at the
frequency it was built for and falls away either side. Transmit the same command
at a series of carriers and the appliance acts on the ones inside its passband
and ignores the ones outside. The two frequencies where it stops responding are
the band edges, and **their midpoint estimates the centre**.

The estimator survives the one variable that ruined an earlier session. Moving
the emitter further away lowers the signal at the appliance by the same factor at
every carrier, so attenuation narrows the measured band symmetrically and does
not move its middle. Distance therefore sets the *width* of the answer, not the
answer -- which is what makes a measurement taken by hand, at a distance nobody
recorded, worth anything at all.

## Order of business, and why it is this order

1. **A range check**, one frame at 38 kHz onto a setpoint the panel is not
   showing, and one question. Nothing else runs until the appliance has
   demonstrably heard us.
2. **A loopback sweep** of the same twelve carriers, automatic, which shows the
   emitter produces each of them.
3. **Four passes** at the appliance, ascending and descending, twice.

The first run of this test had 1 and 2 the other way round and paid for it. The
loopback sweep put 78 frames in front of a marginal appliance before anything
established it could hear us, and the appliance came out of it **in auto**
although every one of those frames carried cool. See *Why the sweep is narrow*.

## Reading one number instead of twelve

Each carrier is transmitted as the appliance's own state with one field changed,
the setpoint, and every carrier in a pass gets a setpoint no other carrier uses.
Frames go out far faster than a panel can be read, so after the whole pass the
display holds the setpoint of the **last frame the appliance acted on**. An
ascending pass therefore ends on the highest carrier that worked, and a
descending pass on the lowest: one glance per pass, two glances for both edges.
This is the trick `test_header_mark.py` used, applied to a quantity that has two
edges rather than one winner.

Each pass first parks the panel, at 38 kHz, on a setpoint that is **neither the
appliance's own nor any carrier's**. That gives the single reading three
outcomes rather than two: the parking value means the appliance was listening and
no carrier worked, a carrier's setpoint names that carrier, and the appliance's
own setpoint means not even the parking frame arrived. The first run parked on
the appliance's own setpoint, which moves nothing, and so could not tell the
first outcome from the last.

Twelve carriers, a parking value, and the setpoint the appliance is already
showing come to fourteen, which is exactly how many the appliance has. The sweep
is that long because that is the whole budget.

## Why the sweep is narrow

It was 24-57 kHz in 3 kHz steps, with a second pass to refine each edge. It is
now 33-44 kHz in 1 kHz steps, which is finer, shorter, and needs no refinement.

The reason is the range check. Once 38 kHz is known to work, the appliance's
passband demonstrably contains it, so its centre is within a few kHz -- and
there is no case for putting 24 kHz or 57 kHz in front of an appliance that will
not answer to them anyway. That matters because **frames this appliance
mis-receives are obeyed rather than discarded**: it does not verify its checksum,
and cool to auto is one bit in `b6` (`0x21` to `0x20`). Measured 2026-08-19: 84
frames, every one carrying cool, left it in auto.

Note what that rules out as an explanation. Carrier distortion cannot flip a bit
here -- `ZERO_SPACE` and `ONE_SPACE` are 1447 us apart and the pulse-width shift
measured across a 22-60 kHz sweep is under 200 us. A flip needs a spurious edge,
which is a signal-strength failure. The cure is a stronger link, which is what
the range check now insists on before anything is swept.

## The tension this method does not escape

Attenuation is what brings the band edges inside a measurable range, and
attenuation is also what produces the corrupted frames above. There is no
placement that gives both a wide margin and a narrow band.

The defence is that the answer is read four times -- two directions, two rounds
-- and corruption is random, so it will not place the same wrong edge twice. A
disagreement between rounds is reported as one rather than averaged away. What
cannot be caught is a corrupted frame landing on another setpoint *in the same
pass* and arriving last; that is a known hole, and the rounds are the only thing
covering it.

## Two traps

**The third harmonic.** A 50% square wave carries a strong component at 3f, so a
carrier at a third of the receiver's centre is received *by that receiver* and
looks like a carrier that works. The sweep starts at 33 kHz, whose third harmonic
is 99 kHz, far above any infrared receiver ever built. `TestThePlan` asserts it
rather than trusting the constants to stay put.

**The emitter is in both measurements.** A carrier the ESP32 cannot generate
fails at the appliance and at the loopback alike, so the loopback sweep is the
control on it. It cannot find its *own* band edges -- measured 2026-08-19, it
heard every carrier from 22 to 60 kHz, because it sits centimetres from the LED
and its margin swamps the out-of-band rejection a receiver module has. What it
does establish is the thing actually needed: every carrier the appliance sweep
uses was emitted and arrived.

## Re-running after moving the emitter

**This test is expected to be run more than once**, because the emitter's
position is what the first run measures rather than something known in advance.
That makes `answers.toml` a hazard rather than a convenience here. It is keyed by
the question text, and a session-scoped fixture re-transmits everything on every
run, so a recorded answer is not resumed work: it is the *previous* position's
reading, asserted against frames it never saw.

**Whoever drives the session clears the recorded answers between attempts**, not
the person reading the panel. Nothing in the code can do it: the file is loaded
once when `ask` is first requested, before this module has run anything, and no
attempt has a name that could tell one run from the next.
"""

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from devices.acp35.protocol import (
    CARRIER_HZ,
    MAX_CELSIUS,
    MIN_CELSIUS,
    Acp35Command,
    Acp35Flag,
)

# The widest band an infrared receiver module is built for. Parts exist from
# 30 kHz to 56 kHz; nothing is centred above this. Used to place the sweep out of
# reach of its own third harmonic, not as a claim about this appliance.
PLAUSIBLE_BAND_HZ = 60_000

# The sweep. Twelve carriers around the shipped constant, at the finest step the
# setpoint budget allows: the appliance has fourteen setpoints, one is where the
# session found it and one more is the parking value.
SWEEP_LOW_HZ = 33_000
SWEEP_HIGH_HZ = 44_000
SWEEP_STEP_HZ = 1_000

# Each pass is run twice. One pass is a reading, two that agree are a
# measurement, and the disagreement is itself the result when the geometry
# shifted partway through -- somebody walking through the path is enough.
ROUNDS = 2

# Every carrier is sent more than once. A single dropped frame would read as
# "this carrier is outside the band", which is exactly the boundary being
# measured, so the one error this must not make is the cheap one.
REPEATS = 3
REPEAT_GAP = 0.3

# Between carriers. Question 9 measured that consecutive frames need no
# separation at all; this is only so a pass is legible in the journal.
BETWEEN_CARRIERS = 0.5

# After the last frame of a pass, before asking what the panel shows.
SETTLE = 1.5

# How far the measured centre may sit from the shipped constant before the
# constant is the thing that is wrong. Two steps of the sweep.
CENTRE_TOLERANCE_HZ = 2_000

PANEL_ANSWERS = (
    *(str(celsius) for celsius in range(MIN_CELSIUS, MAX_CELSIUS + 1)),
    # For a panel showing something no pass sent: the unit left cool, where the
    # setpoint stops identifying anything, or it stopped responding entirely.
    "other",
)


def sweep(low: int, high: int, step: int) -> tuple[int, ...]:
    """Return the carriers from ``low`` to ``high`` inclusive, ascending."""
    return tuple(range(low, high + 1, step))


SWEEP = sweep(SWEEP_LOW_HZ, SWEEP_HIGH_HZ, SWEEP_STEP_HZ)


def probe_setpoints(baseline: int, count: int) -> list[int]:
    """Return ``count`` setpoints to identify carriers by, never the baseline.

    Above the baseline first, so a frame that is acted on makes a cooling
    appliance idle rather than work harder. The same rule `test_header_mark.py`
    and `test_checksum.py` use, restated here rather than shared: a session test
    that reaches into another session test for its plan is harder to read than
    six lines, and these files are read one at a time.
    """
    above = list(range(baseline + 1, MAX_CELSIUS + 1))
    below = list(range(baseline - 1, MIN_CELSIUS - 1, -1))
    chosen = (above + below)[:count]
    assert len(chosen) == count, f"no room for {count} setpoints around {baseline}"
    return chosen


def park_and_carriers(
    baseline: int, carriers: tuple[int, ...]
) -> tuple[int, tuple[tuple[int, int], ...]]:
    """Split the setpoints into the parking value and one per carrier."""
    setpoints = probe_setpoints(baseline, len(carriers) + 1)
    return setpoints[0], tuple(zip(carriers, setpoints[1:], strict=True))


@dataclass(frozen=True)
class Echo:
    """What our own receiver made of one carrier.

    Two questions, not one, and the first run showed why they have to be
    separated. At 24 kHz and 59 kHz a frame-length buffer came back and the
    decoder refused it: the receiver's pulse-width distortion at the edge of its
    band squeezes the spaces past `TOLERANCE`. The frame was emitted and it
    arrived -- which is all this sweep is asked to establish -- so requiring a
    decode would have failed the emitter for something the receiver did.
    """

    heard: bool
    """A buffer long enough to be a frame came back."""
    decoded: bool
    """And `from_raw_timings` accepted it as the bytes we sent."""


@dataclass(frozen=True)
class Pass:
    """One sweep put on the air, and the single reading it produced."""

    label: str
    order: tuple[tuple[int, int], ...]
    """(carrier, setpoint) in the order transmitted."""
    park: int
    """The setpoint the pass parked the panel on before sweeping. **Never the
    appliance's own setpoint**, which is what the first run of this test got
    wrong: parking on the value already displayed moves nothing, so a panel
    reading it back could mean the parking frame worked and no carrier did, or
    that nothing arrived at all -- including the parking frame."""
    baseline: int
    """Where the session found the appliance, and where the panel stays if not
    even the parking frame arrives."""
    shown: str
    unsent: tuple[int, ...]
    """Carriers the loopback did not confirm leaving the emitter."""

    @property
    def parked(self) -> bool:
        """Whether the parking frame reached the appliance.

        False means the panel never left the baseline, so this pass was
        transmitting at an appliance that was not listening -- at 38 kHz, which
        every earlier session drove it with. That is a placement result, not a
        carrier one, and the pass says nothing about any frequency.
        """
        return self.shown != str(self.baseline)

    @property
    def readable(self) -> bool:
        """Whether the panel shows something this pass can account for."""
        if self.shown == "other" or not self.parked:
            return False
        return self.shown == str(self.park) or self.shown in {
            str(setpoint) for _, setpoint in self.order
        }

    @property
    def last_obeyed(self) -> int | None:
        """The carrier of the last frame the appliance acted on, if any.

        None means the panel never left the parking value, which is this pass
        finding no carrier while the appliance was demonstrably listening. Only
        meaningful when `readable`.
        """
        if self.shown == str(self.park):
            return None
        for carrier, setpoint in self.order:
            if str(setpoint) == self.shown:
                return carrier
        return None


@dataclass(frozen=True)
class Band:
    """The two edges one round measured, and what they say."""

    low: int
    high: int

    @property
    def centre(self) -> float:
        """The midpoint, which is the estimate of the receiver's centre."""
        return (self.low + self.high) / 2

    @property
    def width(self) -> int:
        """How wide the band came out, which is a measure of the link margin."""
        return self.high - self.low

    def __str__(self) -> str:
        """Render as kilohertz, which is how the answer will be discussed."""
        return (
            f"{self.low / 1000:g}-{self.high / 1000:g} kHz, "
            f"centre {self.centre / 1000:g} kHz"
        )


def frame(appliance: Acp35Command, celsius: int) -> Acp35Command:
    """The appliance's own state with the setpoint moved, and nothing else."""
    return Acp35Command(
        power=appliance.power,
        mode=appliance.mode,
        fan=appliance.fan,
        celsius=celsius,
        flags=Acp35Flag.CELSIUS | Acp35Flag.TEMP_CHANGED,
    )


def transmit(send, journal, command: Acp35Command, carrier: int, label: str) -> bool:
    """Send one frame at one carrier, and report whether it reached the air.

    "The appliance ignored it" and "we never sent it" are different answers and
    only the first is about the appliance -- which matters more here than
    anywhere else, because an emitter that quietly stopped at some frequency
    would draw a band edge that belongs to the ESP32.
    """
    timings = command.get_raw_timings()
    sent = send(
        timings,
        label=label,
        modulation=carrier,
        count=REPEATS,
        gap=REPEAT_GAP,
    )
    heard = journal.wait_for_frames(sent.after, count=1)
    return any(len(record.timings) == len(timings) + 1 for record in heard)


def run_pass(
    send, journal, appliance: Acp35Command, carriers: tuple[int, ...], label: str
) -> tuple[int, tuple[tuple[int, int], ...], tuple[int, ...]]:
    """Park the panel, then transmit one carrier per setpoint in order."""
    park, order = park_and_carriers(appliance.celsius, carriers)
    unsent: list[int] = []

    mapping = ", ".join(f"{c / 1000:g} kHz -> {s} C" for c, s in order)
    print(f"\n{label}: parking at {park} C, then {mapping}")

    # The parking frame goes out at the shipped carrier, which every earlier
    # session has driven the appliance with, and **onto a setpoint the panel is
    # not already showing**. Both matter. It makes "nothing in this pass worked"
    # a reading rather than an absence, since the panel would otherwise hold
    # whatever the previous pass left there; and because it moves the display, a
    # panel still reading the baseline afterwards says the appliance heard
    # nothing at all rather than that it rejected every carrier.
    if not transmit(
        send, journal, frame(appliance, park), CARRIER_HZ, f"{label}: park"
    ):
        unsent.append(CARRIER_HZ)
    time.sleep(BETWEEN_CARRIERS)

    for carrier, setpoint in order:
        if not transmit(
            send,
            journal,
            frame(appliance, setpoint),
            carrier,
            f"{label}: {carrier} Hz as {setpoint} C",
        ):
            unsent.append(carrier)
        time.sleep(BETWEEN_CARRIERS)

    return park, order, tuple(unsent)


def ask_panel(ask: Callable[..., str], appliance: Acp35Command, label: str) -> str:
    """Ask what the panel settled on after one pass."""
    time.sleep(SETTLE)
    return ask(
        f"Carrier sweep, {label}: what temperature does the ACP 35 panel show? "
        f"Answer {appliance.celsius} if it is unchanged, or 'other' if the unit "
        "is no longer in cool mode or the panel shows none of these.",
        PANEL_ANSWERS,
    )


def one_pass(
    send, journal, ask, appliance: Acp35Command, carriers: tuple[int, ...], label: str
) -> Pass:
    """Transmit a sweep and read the panel once."""
    park, order, unsent = run_pass(send, journal, appliance, carriers, label)
    shown = ask_panel(ask, appliance, label)
    return Pass(label, order, park, appliance.celsius, shown, unsent)


def band_of(passes: dict[str, Pass]) -> Band | None:
    """The band one round's two passes describe, or None if either found none."""
    high = passes["ascending"].last_obeyed
    low = passes["descending"].last_obeyed
    if high is None or low is None:
        return None
    return Band(low, high)


def measured_centre(rounds: list[dict[str, Pass]]) -> float:
    """The centre every round agreed on, averaged over all four readings.

    Averaging rather than taking one round: each edge was read twice, the two
    readings are independent, and there is no reason to prefer either.
    """
    bands = [band_of(passes) for passes in rounds]
    found = [band for band in bands if band is not None]
    lows = statistics.mean(band.low for band in found)
    highs = statistics.mean(band.high for band in found)
    return (lows + highs) / 2


@pytest.fixture(scope="session")
def listening(send, journal, ask, appliance) -> int:
    """Establish that the appliance can hear us at all, before anything else.

    One frame at the shipped carrier, onto a setpoint the panel is not showing,
    and one question. It costs a single reading and it is what the first run of
    this test lacked: 78 loopback frames and four passes went out, and the panel
    never moved to anything we asked for -- which said only that the emitter was
    too far away, and could have said so after the first frame.

    **Everything else depends on this**, including the loopback sweep, which is
    otherwise 24 more frames put in front of an appliance that mis-receives them
    and acts on the result.

    Skips rather than fails. Being out of range is a fact about where the emitter
    was put, not a defect in the appliance or the code, and a run that reports it
    as a failure buries the one thing it did establish.
    """
    park, _ = park_and_carriers(appliance.celsius, SWEEP)
    transmit(send, journal, frame(appliance, park), CARRIER_HZ, "range check")
    shown = ask_panel(ask, appliance, f"range check at {CARRIER_HZ} Hz")
    if shown != str(park):
        pytest.skip(
            f"the appliance did not move to {park} C at the shipped "
            f"{CARRIER_HZ} Hz -- the panel shows {shown}. It cannot hear the "
            "emitter reliably where it is, so no carrier can be measured from "
            "here, and a marginal link is what puts it in modes nothing sent. "
            "Move the emitter closer to its infrared window and run again"
        )
    return park


@pytest.fixture(scope="session")
def loopback_band(send, journal, appliance, listening) -> dict[int, Echo]:
    """Sweep the carrier past our own receiver, and record what it made of it.

    No human and no panel: the frame sent is the appliance's *current* state, so
    the appliance acting on all of them changes nothing. It still transmits, so
    the class using this carries `disruptive` like everything else here.
    """
    command = frame(appliance, appliance.celsius)
    expected = command.to_bytes()
    timings = command.get_raw_timings()

    echoes: dict[int, Echo] = {}
    for carrier in SWEEP:
        sent = send(
            timings,
            label=f"loopback carrier {carrier} Hz",
            modulation=carrier,
            count=2,
            gap=0.2,
        )
        records = journal.wait_for_frames(sent.after, count=1)
        echoes[carrier] = Echo(
            heard=bool(records),
            decoded=any(
                any(found.to_bytes() == expected for found in record.frames)
                for record in records
            ),
        )

    def listed(chosen: list[int]) -> str:
        return ", ".join(f"{carrier / 1000:g}" for carrier in chosen) + " kHz"

    print("\nLoopback heard:   " + listed([c for c, e in echoes.items() if e.heard]))
    print("Loopback decoded: " + listed([c for c, e in echoes.items() if e.decoded]))
    return echoes


@pytest.fixture(scope="session")
def passes(send, journal, ask, appliance, loopback_band) -> list[dict[str, Pass]]:
    """Run the ascending and descending passes, ``ROUNDS`` times each."""
    rounds: list[dict[str, Pass]] = []
    for number in range(1, ROUNDS + 1):
        collected = {}
        for name, carriers in (
            ("ascending", SWEEP),
            ("descending", tuple(reversed(SWEEP))),
        ):
            collected[name] = one_pass(
                send, journal, ask, appliance, carriers, f"{name} pass, round {number}"
            )
        rounds.append(collected)
    return rounds


class TestThePlan:
    """The run's shape, checked with no appliance and nothing transmitted.

    Outside the classes below on purpose, so it runs on any `-m hardware`
    invocation. A sweep that cannot be interpreted is worth catching before
    somebody has cleared an evening, and none of this needs the device.
    """

    def test_no_carrier_can_be_heard_by_its_own_third_harmonic(self) -> None:
        """The trap that would put a phantom edge a third of the way down.

        A 50% square wave carries a third of its fundamental at 3f, so a receiver
        centred at f0 responds to a carrier at f0/3. The sweep therefore has to
        start high enough that 3f clears any receiver's band.
        """
        assert 3 * min(SWEEP) > PLAUSIBLE_BAND_HZ, (
            f"the sweep starts at {min(SWEEP)} Hz, whose third harmonic lands "
            f"inside {PLAUSIBLE_BAND_HZ} Hz. A carrier received by its own "
            "harmonic would read as a working one"
        )

    def test_the_sweep_brackets_the_carrier_the_range_check_proves(self) -> None:
        """38 kHz has to be inside it, and not at an end.

        The range check establishes that the appliance hears the shipped carrier
        before a single sweep frame goes out, and the sweep is narrow *because*
        of that. A sweep that did not contain 38 kHz, or that put it at an edge,
        would be measuring somewhere the range check said nothing about.
        """
        assert min(SWEEP) < CARRIER_HZ < max(SWEEP), (
            f"{CARRIER_HZ} Hz is not strictly inside {min(SWEEP)}-{max(SWEEP)} Hz"
        )

    def test_the_sweep_fits_the_setpoints_the_appliance_has(self) -> None:
        """Twelve carriers, a parking value, and wherever the panel already was."""
        available = MAX_CELSIUS - MIN_CELSIUS  # the baseline is spent already
        assert len(SWEEP) + 1 <= available, (
            f"{len(SWEEP)} carriers and a parking value need {len(SWEEP) + 1} "
            f"distinct setpoints and only {available} are available beside the "
            "one the appliance is already showing"
        )

    @pytest.mark.parametrize("baseline", range(MIN_CELSIUS, MAX_CELSIUS + 1))
    def test_every_carrier_gets_a_setpoint_of_its_own(self, baseline: int) -> None:
        """At every baseline, since the baseline is wherever the session found it."""
        park, order = park_and_carriers(baseline, SWEEP)
        setpoints = [setpoint for _, setpoint in order]
        assert len(set(setpoints)) == len(setpoints), f"setpoints repeat: {setpoints}"
        assert baseline not in setpoints, (
            "a carrier reusing the baseline cannot be told from a pass the "
            "appliance never heard"
        )
        assert park not in setpoints, (
            "a carrier reusing the parking value cannot be told from a pass that "
            "obeyed nothing"
        )
        assert park != baseline, (
            "parking on the value already displayed moves nothing, so the pass "
            "could not show whether the appliance was listening"
        )
        assert all(MIN_CELSIUS <= value <= MAX_CELSIUS for value in setpoints)

    @pytest.mark.parametrize("baseline", range(MIN_CELSIUS, MAX_CELSIUS + 1))
    def test_a_panel_reading_identifies_exactly_one_carrier(
        self, baseline: int
    ) -> None:
        """The whole method: one number read off the panel names one carrier.

        Checked over every reading a pass could produce -- a carrier's setpoint,
        the parking value, the baseline, and something else entirely. The
        baseline is the case the first run turned up: it has to come back as "the
        appliance heard nothing", not as "no carrier worked".
        """
        park, order = park_and_carriers(baseline, SWEEP)
        for carrier, setpoint in order:
            reading = Pass("t", order, park, baseline, str(setpoint), ())
            assert reading.parked and reading.readable
            assert reading.last_obeyed == carrier

        parked = Pass("t", order, park, baseline, str(park), ())
        assert parked.parked and parked.readable and parked.last_obeyed is None

        deaf = Pass("t", order, park, baseline, str(baseline), ())
        assert not deaf.parked, "a panel still on the baseline heard nothing"
        assert not deaf.readable

        assert not Pass("t", order, park, baseline, "other", ()).readable

    def test_the_band_reports_its_own_centre(self) -> None:
        """The estimator, which is a midpoint and nothing cleverer."""
        assert Band(34_000, 42_000).centre == 38_000
        assert Band(36_000, 39_000).centre == 37_500
        assert Band(36_000, 39_000).width == 3_000

    def test_the_centre_is_averaged_over_every_round(self) -> None:
        """Both rounds count, and neither is preferred over the other."""

        def round_of(low: int, high: int) -> dict[str, Pass]:
            park, order = park_and_carriers(23, SWEEP)
            shown = dict(order)
            return {
                "ascending": Pass("a", order, park, 23, str(shown[high]), ()),
                "descending": Pass("d", order, park, 23, str(shown[low]), ()),
            }

        assert measured_centre([round_of(35_000, 41_000)]) == 38_000
        assert (
            measured_centre([round_of(35_000, 41_000), round_of(36_000, 42_000)])
            == 38_500
        )


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestWhatTheLoopbackHears:
    """The control on the emitter. No panel of its own, but it follows the range
    check, so the whole class is `manual` even though it asks nothing.

    Its band is not the appliance's and cannot be: measured 2026-08-19 it heard
    every carrier from 22 to 60 kHz, sitting centimetres from the LED. What it
    establishes is that the emitter produces the carriers the appliance sweep
    uses, so an edge the appliance shows is the appliance's.
    """

    def test_the_emitter_produced_every_carrier_the_sweep_uses(
        self, loopback_band
    ) -> None:
        """The control this sweep is for, and all it can be for."""
        missed = [carrier for carrier in SWEEP if not loopback_band[carrier].heard]
        assert not missed, (
            f"our own receiver, centimetres from the LED, heard nothing at "
            f"{missed}. The emitter is not producing those carriers, so an edge "
            "measured across them would be the ESP32's rather than the appliance's"
        )

    def test_the_loopback_decoded_what_it_heard(self, loopback_band) -> None:
        """Every carrier here is well inside our own receiver's band.

        Unlike the 22-60 kHz sweep, where 24 kHz and 59 kHz arrived undecodable
        because the receiver's distortion at its own band edges squeezed the
        spaces past `TOLERANCE`. Across 33-44 kHz there is no such excuse, so a
        failure to decode is a real transmission fault rather than a receiver
        limit, and worth failing on.
        """
        garbled = [
            carrier
            for carrier, echo in loopback_band.items()
            if echo.heard and not echo.decoded
        ]
        assert not garbled, (
            f"frames came back at {garbled} but did not decode to the bytes sent. "
            "That is not the receiver's band edge at these frequencies; something "
            "is reshaping the waveform"
        )


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestWhichCarrierTheApplianceHearsBest:
    """The answer, read off the appliance's own panel."""

    def test_transmission_was_confirmed_wherever_it_could_be(
        self, passes, loopback_band
    ) -> None:
        """A carrier that never left would draw an edge belonging to the emitter.

        Checked against what the loopback sweep heard rather than absolutely: a
        carrier our receiver cannot hear is not a carrier we did not send, and
        the loopback test above is what decides which is which.
        """
        unsent = {
            f"round {number}, {name}": [
                carrier
                for carrier in one.unsent
                if loopback_band.get(carrier, Echo(False, False)).heard
            ]
            for number, round_of in enumerate(passes, start=1)
            for name, one in round_of.items()
        }
        missing = {name: carriers for name, carriers in unsent.items() if carriers}
        assert not missing, (
            f"the loopback did not confirm these going out: {missing}, although "
            "it heard the same carriers during its own sweep. An edge measured "
            "across them would be the emitter's, not the appliance's"
        )

    def test_every_pass_stayed_readable(self, passes) -> None:
        """A panel showing what no pass sent cannot be interpreted, and must not be.

        The appliance left cool, where the setpoint is pinned and stops
        identifying anything. That is not hypothetical: on 2026-08-19, 84 frames
        every one of which carried cool left it in auto, which is one bit in
        `b6` and a checksum it does not verify.
        """
        unreadable = [
            f"round {number}, {name}"
            for number, round_of in enumerate(passes, start=1)
            for name, one in round_of.items()
            if one.parked and not one.readable
        ]
        assert not unreadable, (
            f"the panel showed something {unreadable} never sent. The appliance "
            "acted on a command nothing transmitted, which is what a marginal "
            "link does to a receiver that checks no checksum. Put it back in "
            "cool, move the emitter closer, and run those passes again"
        )

    def test_the_appliance_was_still_listening_at_every_pass(self, passes) -> None:
        """Each pass carries its own control: the parking frame moved the panel.

        The range check establishes this once, before anything is swept. This
        establishes it four more times, because between the check and the last
        pass sits ten minutes in which the emitter can be nudged, somebody can
        stand in the path, or the appliance can be switched off at the wall.
        """
        deaf = [
            f"round {number}, {name}"
            for number, round_of in enumerate(passes, start=1)
            for name, one in round_of.items()
            if not one.parked
        ]
        assert not deaf, (
            f"the panel never left the appliance's own setpoint during {deaf}, so "
            "not even the parking frame at 38 kHz arrived. The appliance stopped "
            "hearing the emitter partway through the session; nothing about any "
            "carrier follows from those passes"
        )

    def test_the_appliance_answered_at_some_carrier(self, passes) -> None:
        """Listening but obeying nothing means the sweep missed its band entirely."""
        found = [band_of(round_of) for round_of in passes]
        assert any(band is not None for band in found), (
            "the parking frame moved the panel but no carrier in the sweep did, "
            f"so the appliance answers at {CARRIER_HZ} Hz and at nothing in "
            f"{min(SWEEP)}-{max(SWEEP)} Hz. That is a contradiction unless the "
            "link is marginal enough that three repeats were not enough; move "
            "the emitter closer"
        )

    def test_the_band_is_inside_the_swept_range(self, passes) -> None:
        """The one condition the human controls, and the one that biases nothing.

        Attenuation narrows the band symmetrically, so where the emitter sits
        changes the width of the answer and not its middle. It only has to sit
        far enough out that both edges fall inside the sweep -- and close enough
        in that the frames arrive intact, which is the tension the module
        docstring records rather than resolves.
        """
        for number, round_of in enumerate(passes, start=1):
            band = band_of(round_of)
            assert band is not None, f"round {number} found no band"
            print(f"\nround {number}: the appliance answered across {band}")
            assert band.high != max(SWEEP), (
                f"round {number} was still obeyed at the highest carrier swept "
                f"({band.high} Hz), so the upper edge is above the sweep. "
                "Attenuate: move the emitter further from the appliance or off "
                "its axis, and run again"
            )
            assert band.low != min(SWEEP), (
                f"round {number} was still obeyed at the lowest carrier swept "
                f"({band.low} Hz), so the lower edge is below the sweep. "
                "Attenuate: move the emitter further from the appliance or off "
                "its axis, and run again"
            )

    def test_the_rounds_agree(self, passes) -> None:
        """Two rounds a step apart are a measurement; further apart is drift.

        This is also the only cover for the hole the method cannot close: a
        corrupted frame landing on another setpoint in the same pass, and
        arriving last, would name the wrong carrier and look like a clean
        reading. Corruption is random, so it will not do that twice.
        """
        bands = [band_of(round_of) for round_of in passes]
        assert all(band is not None for band in bands)
        for edge in ("low", "high"):
            values = [getattr(band, edge) for band in bands]
            assert max(values) - min(values) <= SWEEP_STEP_HZ, (
                f"the rounds put the {edge} edge at {values}, more than one "
                f"{SWEEP_STEP_HZ} Hz step apart. Something moved during the "
                "session, or a frame was corrupted; neither round is the "
                "measurement"
            )

    def test_the_carrier_the_appliance_hears_best(self, passes) -> None:
        """The answer, and whether `CARRIER_HZ` is the number to ship.

        The midpoint of the edges, averaged over both rounds. It is an estimate
        of where the appliance's receiver is centred, on the assumption that its
        response falls away symmetrically either side -- true enough of a
        band-pass to place the centre within a kilohertz or two, and not a claim
        to more precision than the step size allows.
        """
        centre = measured_centre(passes)
        print(
            f"\nThe ACP 35 is centred near {centre:.0f} Hz, from {len(passes)} "
            f"rounds at {SWEEP_STEP_HZ} Hz resolution."
            f"\nShipped constant: CARRIER_HZ = {CARRIER_HZ}"
        )
        assert abs(centre - CARRIER_HZ) <= CENTRE_TOLERANCE_HZ, (
            f"the appliance is centred near {centre:.0f} Hz, not the "
            f"{CARRIER_HZ} Hz this integration transmits at. Every frame we send "
            "is off-band, which costs range at no benefit -- change CARRIER_HZ in "
            "protocol.py to the measured centre and record the measurement in the "
            "protocol document"
        )
        print(
            f"\nCARRIER_HZ = {CARRIER_HZ} is within {CENTRE_TOLERANCE_HZ} Hz of "
            "the measured centre. Record the band and the emitter position in the "
            "protocol document: the width is a property of that position, the "
            "centre is a property of the appliance."
        )
