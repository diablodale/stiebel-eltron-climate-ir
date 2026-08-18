"""Question 7: does the unit accept our frame, and with which header mark?

The one unmeasured constant in the protocol. Every capture's receive buffer
begins at the header *space* -- the mark before it was never recorded -- so
`HEADER_MARK = 5100` is a guess chosen for symmetry with the space. If it is
wrong the integration cannot control the appliance at all, which is why this
blocks: no fallback exists in the code.

**The appliance is the instrument, and it is cooling a room.** Nothing here
cycles power. A frame is full state, so each candidate is sent as the appliance's
own current state with one field changed -- the setpoint -- and an accepted frame
therefore moves the temperature and nothing else. The panel then reads the answer
back: each candidate carries its own setpoint, so one glance says which one the
unit acted on. The ACP 35 does not beep, so the display is the only signal.

The current setpoint is deliberately excluded from the carrier values, so a panel
that still reads it means **no candidate worked** rather than looking like one of
them. The values sit above it, so the worst an accepted frame can do is let the
unit idle for a moment.

**Two passes, opposite order.** Frames go out faster than a panel can be read, so
if several header marks work the display shows only the last one accepted. Sending
the candidates again in reverse and reading again distinguishes "exactly one
works" -- the same setpoint both times -- from "several do", and says which.

Every transmission is checked against the loopback before its result is believed.
"The unit ignored it" and "we never sent it" are different answers, and only the
first is about the protocol.
"""

import time

import pytest
from devices.acp35.protocol import (
    HEADER_MARK,
    HEADER_SPACE,
    MAX_CELSIUS,
    MIN_CELSIUS,
    Acp35Command,
)

# In the order the protocol document nominates them: symmetric with the space
# first, then the shapes other protocols use, then no header at all. Zero is the
# hypothesis the loopback already points at -- our frames come back one duration
# longer than the remote's, and that duration is our header mark.
CANDIDATES = (5100, 4400, 3000, 9000, 0)

# Long enough that each candidate is a separate event to the appliance rather
# than part of a burst it may coalesce or ignore.
BETWEEN_CANDIDATES = 1.5

# Each candidate is sent more than once. A single missed frame would otherwise
# read as "this header mark does not work", which is the wrong conclusion from
# the strongest evidence we can gather here.
REPEATS = 3
REPEAT_GAP = 0.3


def carrier_setpoints(baseline: int, count: int) -> list[int]:
    """Return ``count`` setpoints to identify candidates by, never the baseline.

    Above the baseline first, so an accepted frame makes a cooling appliance idle
    rather than work harder, and below it only if the range runs out.
    """
    above = [t for t in range(baseline + 1, MAX_CELSIUS + 1)]
    below = [t for t in range(baseline - 1, MIN_CELSIUS - 1, -1)]
    chosen = (above + below)[:count]
    assert len(chosen) == count, f"no room for {count} setpoints around {baseline}"
    return chosen


def with_header(command: Acp35Command, mark: int) -> list[int]:
    """Return the frame's durations with ``mark`` as the header, or none at all.

    The encoder cannot do this: `HEADER_MARK` is a module constant, so the only
    way to try another value is to assemble the durations outside it. That is
    what `acp35_bench.send` taking raw durations is for.
    """
    body = command.get_raw_timings()
    if HEADER_MARK:
        body = body[2:]
    return ([mark, -HEADER_SPACE] if mark else []) + body


class Reading:
    """What one pass sent, and what the panel said afterwards."""

    def __init__(self, order: list[tuple[int, int]], shown: str, unsent: list[int]):
        self.order = order
        """(header mark, setpoint) in the order they were transmitted."""
        self.shown = shown.strip().lower()
        self.unsent = unsent
        """Candidates whose transmission the loopback did not confirm."""

    @property
    def unreadable(self) -> bool:
        """Whether the pass stopped being interpretable partway through.

        The carrier is the setpoint, and the appliance pins the setpoint in auto
        and dry, so a unit that left cool shows a number that means nothing. That
        is not hypothetical: a wrong header mark can leave the unit's bit sampling
        shifted, and a shifted decode is a different command -- observed on
        2026-08-18, when a descending pass put the unit into auto with a low fan
        although every frame sent carried cool and medium.
        """
        return self.shown == "changed"

    @property
    def winner(self) -> int | None:
        """The header mark the panel's reading identifies, if any."""
        if self.unreadable:
            return None
        for mark, setpoint in self.order:
            if setpoint == int(self.shown):
                return mark
        return None


def run_pass(send, journal, appliance, setpoints, marks, label):
    """Transmit one candidate per setpoint, and report what was actually emitted."""
    order = []
    unsent = []
    for mark, setpoint in zip(marks, setpoints, strict=True):
        frame = Acp35Command(
            power=appliance.power,
            mode=appliance.mode,
            fan=appliance.fan,
            celsius=setpoint,
        )
        timings = with_header(frame, mark)
        sent = send(
            timings,
            label=f"{label}: header mark {mark} as {setpoint} C",
            count=REPEATS,
            gap=REPEAT_GAP,
        )
        heard = journal.wait_for_frames(sent.after, count=1)
        if not any(len(record.timings) == len(timings) + 1 for record in heard):
            unsent.append(mark)
        order.append((mark, setpoint))
        time.sleep(BETWEEN_CANDIDATES)
    return order, unsent


@pytest.fixture(scope="session")
def bisect(send, journal, appliance, ask) -> dict[str, Reading]:
    """Run both passes and collect what the panel showed after each.

    A session fixture so the appliance is driven once, however many assertions
    are made about the result.
    """
    baseline = appliance.celsius
    setpoints = carrier_setpoints(baseline, len(CANDIDATES))
    forward = list(CANDIDATES)
    backward = list(reversed(CANDIDATES))

    readings = {}
    for name, marks in (("ascending", forward), ("descending", backward)):
        mapping = ", ".join(
            f"{mark or 'no header'} -> {setpoint} C"
            for mark, setpoint in zip(marks, setpoints, strict=True)
        )
        print(f"\nHeader-mark bisect, {name} pass. Sending: {mapping}")
        order, unsent = run_pass(
            send, journal, appliance, setpoints, marks, f"header mark, {name}"
        )
        answer = ask(
            f"Header-mark bisect, {name} pass: what temperature does the ACP 35 "
            f"panel show? Answer {baseline} if it is unchanged, or 'changed' if "
            "the unit is no longer in cool mode."
        )
        readings[name] = Reading(order, answer, unsent)
    return readings


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestWhichHeaderMarkTheUnitAccepts:
    """The answer to question 7, read off the appliance's own panel."""

    def test_every_candidate_was_actually_transmitted(self, bisect) -> None:
        """Otherwise a silent unit and a silent emitter look the same."""
        unsent = {name: reading.unsent for name, reading in bisect.items()}
        assert not any(unsent.values()), (
            f"the loopback did not confirm these header marks going out: {unsent}. "
            "Nothing about the appliance can be concluded from the passes."
        )

    def test_each_pass_stayed_readable(self, bisect) -> None:
        """A pass that left cool mode cannot be interpreted, and must not be.

        The setpoint is the carrier and the appliance pins it outside cool, so the
        panel would show a number that identifies no candidate. Failing here says
        which pass to repeat; recording the number would put a meaningless answer
        in `answers.toml` permanently.
        """
        unreadable = [name for name, reading in bisect.items() if reading.unreadable]
        assert not unreadable, (
            f"the {unreadable} pass(es) left cool mode partway through, so the "
            "setpoint stopped identifying anything. A wrong header mark can make "
            "the unit act on a command we did not send. Restore the appliance and "
            "repeat that pass, one candidate at a time if it recurs."
        )

    def test_the_unit_acts_on_one_of_our_frames(self, bisect, appliance) -> None:
        """If none works, the integration cannot drive the appliance at all.

        The remaining move is the fallback the protocol document names: vary
        `CARRIER_HZ` as well, which the loopback has already shown reaches the LED.
        """
        shown = {name: reading.shown for name, reading in bisect.items()}
        assert any(value != str(appliance.celsius) for value in shown.values()), (
            f"the panel never left {appliance.celsius} C, so the unit acted on no "
            f"candidate in {CANDIDATES}. Try the carrier fallback."
        )

    def test_the_two_passes_agree_on_one_header_mark(self, bisect) -> None:
        """Same winner from both orders means exactly one candidate works.

        Different winners is not a failure of the appliance, it is more
        information: each pass names the last candidate it accepted, so two
        different answers mean both of those work and the truth is a set rather
        than a value. Either way the number to ship is printed below.
        """
        ascending = bisect["ascending"].winner
        descending = bisect["descending"].winner
        print(f"\nascending pass identified: {ascending}")
        print(f"descending pass identified: {descending}")
        assert ascending == descending, (
            f"more than one header mark works: the ascending pass ended on "
            f"{ascending} and the descending pass on {descending}. Both are "
            "accepted by the unit; ship whichever the document prefers and record "
            "that the field is tolerant."
        )
        print(f"\nHEADER_MARK = {ascending}  <- write this into protocol.py")
