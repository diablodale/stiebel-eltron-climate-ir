"""Does the ACP 35 verify the checksum its protocol carries?

Not one of the numbered questions. It exists because a claim was made without
evidence: the first question 8 run, with the emitter across the room, left the
appliance in states nothing had transmitted, and that was written up as the
appliance ignoring its checksum. It is one explanation of several -- corruption
can land on a valid checksum, the bit sampling can shift, and an air conditioner
at its setpoint may drop its own fan -- and the observation chose between none of
them. This transmits a wrong checksum on purpose instead of inferring.

**Nothing in the integration depends on the answer.** We always send a correct
checksum, and the emitter-placement advice stands either way, because the wrong
commands were observed whatever caused them. What the answer buys is knowing
whether a corrupted frame can reach the appliance as a command, which is the
difference between "keep the emitter close so frames arrive" and "keep the
emitter close because a damaged frame is still obeyed".

**The controls are the test.** A probe that changes nothing proves the checksum
was enforced only if the appliance was listening at the time, so a valid frame is
sent before the probes and another after them. Without the second one, an
appliance that stopped responding part-way looks exactly like a checksum being
enforced.

**Every stop uses a setpoint no other stop uses.** A rejected probe leaves the
panel where the stop before it left it, so reusing a value could put the expected
reading on the panel already and make a rejection indistinguishable from a hit.
Five stops, five distinct setpoints, no collisions whatever is rejected.

**Our own decoder refuses these frames**, which is correct -- a bad checksum is
how `from_raw_timings` rejects noise -- so the loopback cannot confirm them by
decoding. They are confirmed by comparing the durations that came back against
the ones sent, which needs no decoder and is the stronger check anyway.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from devices.acp35.protocol import (
    BIT_MARK,
    HEADER_MARK,
    HEADER_SPACE,
    MAX_CELSIUS,
    MIN_CELSIUS,
    ONE_SPACE,
    ZERO_SPACE,
    Acp35Command,
    Acp35Fan,
    Acp35Flag,
    Acp35Mode,
)

# Sent more than once, as everywhere else here: one missed frame would read as
# "the appliance rejected it", which is the conclusion this test is trying to
# reach honestly rather than by accident.
REPEATS = 3
REPEAT_GAP = 0.3

SETTLE = 1.5

# How far a duration may drift between transmission and capture before the
# waveform is a different one. ESPHome's MARK_EXCESS_MICROS moves every element
# by 20 us and the ESP32 adds a little jitter; a corrupted bit would move a space
# by at least 480 us, which is the difference this has to catch.
DURATION_SLACK_US = 150

TEMPERATURE_ANSWERS = (
    *(str(celsius) for celsius in range(MIN_CELSIUS, MAX_CELSIUS + 1)),
    "none",
)


def setpoints(baseline: int, count: int) -> list[int]:
    """Return ``count`` setpoints, all distinct and none of them the baseline."""
    above = list(range(baseline + 1, MAX_CELSIUS + 1))
    below = list(range(baseline - 1, MIN_CELSIUS - 1, -1))
    chosen = (above + below)[:count]
    assert len(chosen) == count, f"no room for {count} setpoints around {baseline}"
    return chosen


def encode(state: bytes) -> list[int]:
    """Render nine bytes as durations, whatever they say.

    `Acp35Command.get_raw_timings` cannot do this. `to_bytes` computes the
    checksum from the other eight bytes, so the shipping encoder is incapable of
    producing a frame with a wrong one -- correct for shipping code, useless
    here. This is the same reason `acp35_bench.send` takes raw durations.
    """
    timings = [HEADER_MARK, -HEADER_SPACE]
    for byte in state:
        for shift in range(7, -1, -1):
            timings.append(BIT_MARK)
            timings.append(-(ONE_SPACE if (byte >> shift) & 1 else ZERO_SPACE))
    timings.append(BIT_MARK)
    return timings


def frame(fan: Acp35Fan, celsius: int) -> Acp35Command:
    """Build the cool-mode setpoint change every stop here is a variation on."""
    return Acp35Command(
        power=True,
        mode=Acp35Mode.COOL,
        fan=fan,
        celsius=celsius,
        flags=Acp35Flag.CELSIUS | Acp35Flag.TEMP_CHANGED,
    )


# How to corrupt a checksum, as transforms rather than values. **Each has to be
# applied to the frame it will be sent in**, because every stop carries its own
# setpoint and therefore its own correct checksum. Computing the wrong values
# once from a different frame is a mistake that hides itself: "one over" the
# baseline's checksum can be exactly the correct checksum for another setpoint,
# and the probe would then be a valid frame wearing the wrong label.
#
# Three rather than one, because a single value could be wrong in a way the
# appliance happens to tolerate -- a byte it masks, or compares only in part.
# Three differing in different bit positions make both clean answers meaningful.
CORRUPTIONS: tuple[tuple[str, Callable[[int], int]], ...] = (
    ("one over", lambda correct: (correct + 1) & 0xFF),
    ("bits inverted", lambda correct: correct ^ 0xFF),
    ("zero", lambda correct: 0x00),
)


@dataclass(frozen=True)
class Stop:
    """One frame put on the air, and what the panel showed afterwards."""

    label: str
    celsius: int
    state: bytes
    """The nine bytes as transmitted, checksum included."""
    checksum_valid: bool
    shown: str
    on_air: bool
    """Whether the durations that came back match the ones sent."""

    @property
    def acted(self) -> bool:
        """Whether the appliance moved to the setpoint this frame carried."""
        return self.shown == str(self.celsius)

    @property
    def checksum_really_wrong(self) -> bool:
        """Whether the byte sent actually disagrees with the sum of the rest."""
        return self.state[8] != sum(self.state[:8]) & 0xFF


def put_on_air(send, journal, timings: list[int], label: str) -> bool:
    """Transmit, and check the waveform came back as the one we built.

    By durations rather than by decoding, because half the frames here are meant
    to fail the decoder. A capture is accepted when it is one element longer than
    what went out -- the receiver appends its idle timeout -- and every element
    matches within `DURATION_SLACK_US`.
    """
    sent = send(timings, label=label, count=REPEATS, gap=REPEAT_GAP)
    for record in journal.wait_for_frames(sent.after, count=1):
        heard = record.timings
        if len(heard) != len(timings) + 1:
            continue
        if all(
            abs(abs(a) - abs(b)) <= DURATION_SLACK_US and (a < 0) == (b < 0)
            for a, b in zip(timings, heard[: len(timings)], strict=True)
        ):
            return True
    return False


def build_plan(baseline: int) -> list[tuple[str, int, Callable[[int], int] | None]]:
    """Return the stops in order: a control, every corruption, then a control.

    A function rather than inline in the fixture so `TestThePlan` can check its
    shape with no appliance present. The first version of this sliced the
    setpoints wrong -- it gave the probes `targets[1:]`, which includes the
    closing control's -- and `strict=True` turned that into an error only once a
    person had already set the run going.
    """
    targets = setpoints(baseline, len(CORRUPTIONS) + 2)
    return [
        ("control before the probes", targets[0], None),
        *(
            (f"checksum {name}", target, corrupt)
            for (name, corrupt), target in zip(CORRUPTIONS, targets[1:-1], strict=True)
        ),
        ("control after the probes", targets[-1], None),
    ]


@pytest.fixture(scope="session")
def checksum(send, journal, appliance, ask) -> list[Stop]:
    """Send a valid frame, three invalid ones, then a valid frame again."""
    fan = appliance.fan
    plan = build_plan(appliance.celsius)

    stops: list[Stop] = []
    for label, celsius, corrupt in plan:
        state = bytearray(frame(fan, celsius).to_bytes())
        if corrupt is not None:
            # Applied to this frame's own checksum, never to another's.
            state[8] = corrupt(state[8])
        timings = encode(bytes(state))
        print(f"\n{label}: sending {bytes(state).hex(' ').upper()}")
        on_air = put_on_air(send, journal, timings, f"checksum: {label}")
        time.sleep(SETTLE)
        shown = ask(
            f"Checksum test, {label}: what temperature does the ACP 35 panel show?",
            TEMPERATURE_ANSWERS,
        )
        stops.append(Stop(label, celsius, bytes(state), corrupt is None, shown, on_air))
    return stops


class TestThePlan:
    """The run's shape, checked with no appliance and nothing transmitted.

    Deliberately outside the `disruptive` class below, so it runs on any
    `-m hardware` invocation. Everything here was wrong once and none of it
    needs hardware to be right, which is the worst combination: the failure
    surfaced only after somebody had cleared their evening and started the run.
    """

    @pytest.mark.parametrize("baseline", range(MIN_CELSIUS, MAX_CELSIUS + 1))
    def test_the_plan_is_well_formed_at_every_baseline(self, baseline: int) -> None:
        """Two controls around every corruption, on setpoints nothing repeats."""
        plan = build_plan(baseline)
        assert len(plan) == len(CORRUPTIONS) + 2

        controls = [stop for stop in plan if stop[2] is None]
        assert len(controls) == 2, "a probe needs a control each side of it"
        assert plan[0][2] is None and plan[-1][2] is None, "controls must bracket"

        names = [label for label, _, corrupt in plan if corrupt is not None]
        assert names == [f"checksum {name}" for name, _ in CORRUPTIONS], (
            "every corruption must be sent, exactly once, in order"
        )

        celsius = [stop[1] for stop in plan]
        assert len(set(celsius)) == len(celsius), (
            f"setpoints repeat: {celsius}. A rejected probe leaves the panel on "
            "the stop before it, so a repeat makes rejection look like a hit"
        )
        assert baseline not in celsius, (
            "a stop reusing the baseline cannot be told from one that did nothing"
        )
        assert all(MIN_CELSIUS <= value <= MAX_CELSIUS for value in celsius)


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestWhetherTheChecksumIsVerified:
    """Transmitted rather than inferred."""

    def test_the_frames_carry_the_checksums_they_claim_to(self, checksum) -> None:
        """The premise, and the one this file got wrong on its first draft.

        Each stop carries its own setpoint and therefore its own correct
        checksum. An earlier version computed the wrong values once from the
        baseline frame and reused them, which can hand a probe the checksum that
        is *correct* for its own bytes -- a valid frame labelled invalid, and an
        answer of "the appliance obeys a bad checksum" drawn from a good one.

        Cheap to check and impossible to notice by eye, so it runs before
        anything is concluded.
        """
        wrong = [
            stop.label
            for stop in checksum
            if stop.checksum_valid and stop.checksum_really_wrong
        ]
        assert not wrong, f"controls carrying a bad checksum: {wrong}"
        right = [
            stop.label
            for stop in checksum
            if not stop.checksum_valid and not stop.checksum_really_wrong
        ]
        assert not right, (
            f"probes carrying a checksum that is actually correct: {right}. The "
            "appliance obeying these would say nothing about bad checksums"
        )

    def test_every_frame_reached_the_air_as_built(self, checksum) -> None:
        """Including the invalid ones, which is the point of checking durations.

        A probe that never left would look exactly like a probe the appliance
        rejected, and that is the answer this test is here to produce.
        """
        missing = [stop.label for stop in checksum if not stop.on_air]
        assert not missing, (
            f"the loopback did not hear these go out as the durations we built: "
            f"{missing}. Nothing about the appliance follows from them"
        )

    def test_the_appliance_was_listening_throughout(self, checksum) -> None:
        """Both controls, which is what makes a null probe mean anything.

        Without the second control, an appliance that stopped responding part-way
        through -- moved out of range, switched off, anything -- would produce
        exactly the reading that says "the checksum is enforced".
        """
        controls = [stop for stop in checksum if stop.checksum_valid]
        assert len(controls) == 2, "both controls must run"
        ignored = [
            (stop.label, stop.celsius, stop.shown)
            for stop in controls
            if not stop.acted
        ]
        assert not ignored, (
            f"a control frame with a correct checksum was not acted on: {ignored}. "
            "The appliance was not listening, so the probes say nothing"
        )

    def test_what_the_appliance_does_with_a_wrong_checksum(self, checksum) -> None:
        """The answer. Either outcome is a result; a split is the interesting one.

        All three obeyed means the checksum is not verified, and a frame damaged
        in flight can reach the appliance as a command. All three ignored means it
        is verified, and the wrong commands seen from across the room came from
        something else -- a shifted decode, or the appliance acting on its own.

        A split is neither, and would be worth more than a clean answer: it would
        mean the byte is examined but not as a sum, and which values pass would be
        the next question.
        """
        probes = [stop for stop in checksum if not stop.checksum_valid]
        obeyed = [stop.label for stop in probes if stop.acted]
        ignored = [stop.label for stop in probes if not stop.acted]
        for stop in probes:
            print(
                f"  {stop.label:<22} sent {stop.celsius} C, panel {stop.shown}"
                f" -> {'obeyed' if stop.acted else 'ignored'}"
            )
        assert not (obeyed and ignored), (
            f"the appliance obeyed {obeyed} and ignored {ignored}. The checksum "
            "byte is examined, but not as a simple sum -- worth pursuing, because "
            "neither of the two clean answers holds"
        )
        if obeyed:
            print(
                "\nThe ACP 35 does NOT verify its checksum: every frame with a "
                "deliberately wrong one was acted on. A frame damaged in flight "
                "can reach it as a command, which is why emitter placement "
                "matters beyond simply not losing frames."
            )
        else:
            print(
                "\nThe ACP 35 DOES verify its checksum: no frame with a wrong one "
                "was acted on. The wrong commands seen from across the room came "
                "from something else -- a shifted decode, or the appliance acting "
                "on its own -- and that claim must be withdrawn."
            )
