"""Question 8, the timer half: does an ordinary frame of ours cancel a timer?

Every frame the integration sends carries `b2 = 0` with `b1` bit 3 clear, because
the timer is read-only and we never transmit one. The remote emits that same byte
pattern whenever no timer is set, so the appliance certainly accepts it; whether
it *clears* an armed one was inference until this ran.

**Answered 2026-08-19: it clears it.** So any command sent from Home Assistant
cancels a timer the user set on the remote. That is a consequence of the
read-only decision rather than a fault in it -- see *The timer read-out is
read-only* in `docs/ha_ir_platform/plan.md` -- and this file exists to keep the
consequence measured rather than assumed, so it is noticed if it ever changes.

**Whether the frame was acted on is asked separately from what happened to the
timer.** A frame the appliance never received would leave the timer armed and
look like the appliance ignoring `b2`. So the transmission here also moves the
setpoint, and the setpoint is read back first: what happened to the timer means
nothing until the appliance has demonstrably obeyed the frame carrying it.

The armed timer is confirmed from the journal rather than taken on trust. The
receiver hears the remote, so the hours it actually transmitted are recorded, and
they are checked against what the person at the appliance says they set.

**The remote is not a readout.** It keeps its own model of the timer and knows
nothing about what the appliance did with our frame -- observed here: after the
cancellation it reopened at the hours it had last set. So the appliance's own
indicator lamp is the only place the answer appears.
"""

import time
from dataclasses import dataclass

import pytest
from devices.acp35.protocol import (
    MAX_CELSIUS,
    MAX_TIMER_HOURS,
    MIN_CELSIUS,
    Acp35Command,
    Acp35Fan,
    Acp35Flag,
    Acp35Mode,
)

# Long enough that the countdown cannot reach zero during the session and be
# mistaken for a cancellation, and short enough to be quick to wind on the remote.
MIN_TIMER_HOURS = 2

# Sent more than once, as everywhere else here: one missed frame would read as
# "the appliance ignored it", which is the wrong conclusion to draw from silence.
REPEATS = 3
REPEAT_GAP = 0.3

# Time for the appliance to act and its panel to settle before the question.
SETTLE = 1.5

TIMER_ANSWERS = tuple(str(hours) for hours in range(1, MAX_TIMER_HOURS + 1))

TEMPERATURE_ANSWERS = (
    *(str(celsius) for celsius in range(MIN_CELSIUS, MAX_CELSIUS + 1)),
    "none",
)


def carrier_setpoint(baseline: int) -> int:
    """Return a setpoint that is not the baseline, to prove the frame landed.

    "The setpoint moved" is how this file knows the appliance received the frame
    at all, so reusing the baseline would report a received frame as ignored.

    Above the baseline where there is room, so an accepted frame lets a cooling
    appliance idle rather than work harder.
    """
    if baseline < MAX_CELSIUS:
        return baseline + 1
    return baseline - 1


def ordinary_frame(mode: Acp35Mode, fan: Acp35Fan, celsius: int) -> Acp35Command:
    """Build the frame the integration sends for a setpoint change.

    The timer fields are left at their defaults deliberately -- `timer_hours` at
    zero and `timer_off_delay` following from it -- because that is the thing
    under test. `test_our_frames_really_do_carry_no_timer` pins it.
    """
    return Acp35Command(
        power=True,
        mode=mode,
        fan=fan,
        celsius=celsius,
        flags=Acp35Flag.CELSIUS | Acp35Flag.TEMP_CHANGED,
    )


@dataclass
class Armed:
    """What the remote transmitted when a timer was armed on it."""

    told: int
    """The hours the person at the appliance says they set."""
    heard: list[Acp35Command]
    """Every frame the receiver picked up while they were doing it."""

    @property
    def with_hours(self) -> list[Acp35Command]:
        """The frames that carried a non-zero timer."""
        return [frame for frame in self.heard if frame.timer_hours]

    @property
    def hours(self) -> int | None:
        """The hours the last such frame carried, which is what was committed."""
        return self.with_hours[-1].timer_hours if self.with_hours else None


@dataclass
class Probe:
    """One frame sent at an armed timer, and what the panel said afterwards."""

    command: Acp35Command
    acted: bool
    """Whether the setpoint moved, so the appliance demonstrably received it."""
    shown: str
    survived: bool
    """Whether the appliance's timer indicator was still lit."""


def transmit(send, journal, command: Acp35Command, label: str) -> bool:
    """Send a frame and report whether it reached the air as the bytes we built."""
    timings = command.get_raw_timings()
    sent = send(timings, label=label, count=REPEATS, gap=REPEAT_GAP)
    heard = journal.wait_for_frames(sent.after, count=1)
    return any(
        len(record.frames) == 1 and record.frames[0].to_bytes() == command.to_bytes()
        for record in heard
    )


def probe(send, journal, ask, confirm, command, label, celsius) -> Probe:
    """Transmit at an armed timer, then read both halves of the answer."""
    confirmed = transmit(send, journal, command, label)
    assert confirmed, (
        f"the loopback did not hear {label!r} go out as the bytes we built, so "
        "nothing about the appliance follows from what happens next"
    )
    time.sleep(SETTLE)
    shown = ask(
        f"{label}: what temperature does the ACP 35 panel show?",
        TEMPERATURE_ANSWERS,
    )
    survived = confirm(f"{label}: is the appliance's timer indicator still lit?")
    return Probe(command, shown == str(celsius), shown, survived)


@dataclass
class Session:
    """Everything one run of this file established."""

    armed: Armed
    ours: Probe


@pytest.fixture(scope="session")
def timer(send, journal, appliance, ask, confirm) -> Session:
    """Arm a timer on the remote, then send one ordinary frame at it.

    A session fixture: the appliance is driven once and a person is asked to work
    the remote once, however many assertions are made about the result.
    """
    mode, fan = Acp35Mode.COOL, appliance.fan
    carrier = carrier_setpoint(appliance.celsius)

    def arm(question: str) -> Armed:
        """Have a timer set on the remote, and record what it actually sent.

        The hours come from the journal rather than from the answer. The remote
        keeps its own model and reopens at whatever it last believed, so somebody
        winding "to 5" may pass through several values, and only the frames say
        which one was committed.
        """
        before = journal.last_seq()
        told = ask(question, TIMER_ANSWERS)
        return Armed(
            told=int(told),
            heard=[
                frame
                for record in journal.since(before)
                if record.is_frame
                for frame in record.frames
            ],
        )

    armed = arm(
        "On the REMOTE, with the appliance running: press TIMER, wind it to at "
        f"least {MIN_TIMER_HOURS} hours, and wait for the entry display to close. "
        "Then type the number of hours you set."
    )

    ours = probe(
        send,
        journal,
        ask,
        confirm,
        ordinary_frame(mode, fan, carrier),
        "our ordinary frame, carrying no timer",
        carrier,
    )

    print(
        "\nIf the timer is still set, cancel it on the remote: press TIMER twice. "
        "The restore frame does not clear it."
    )
    return Session(armed, ours)


class TestThePremise:
    """What the integration sends, checked without the appliance.

    Deliberately outside the `disruptive` class below: it transmits nothing and
    needs no device, so it runs on any `-m hardware` invocation and reports first
    if the premise has stopped being true.
    """

    def test_our_frames_really_do_carry_no_timer(self) -> None:
        """If this fails, everything below is measuring the wrong frame.

        `ordinary_frame` leans on `Acp35Command`'s defaults rather than stating
        the timer fields, because the point is to send what ships. That makes the
        defaults load-bearing, so they are pinned here.
        """
        frame = ordinary_frame(Acp35Mode.COOL, Acp35Fan.MEDIUM, 23)
        assert frame.timer_hours == 0
        assert frame.timer_off_delay is False
        state = frame.to_bytes()
        assert state[2] == 0, "b2 must be zero"
        assert not state[1] & 0x08, "b1 bit 3 must be clear"


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestWhatOurFramesDoToAnArmedTimer:
    """The other half of question 8."""

    def test_a_timer_was_actually_armed(self, timer) -> None:
        """The receiver heard the remote do it, rather than us taking their word.

        Without this the whole file rests on an unverified claim, and "the timer
        was never armed" would look exactly like "our frame cancelled it".
        """
        assert timer.armed.with_hours, (
            "no frame carrying a non-zero timer was heard while the remote was "
            f"being worked; {len(timer.armed.heard)} frame(s) arrived. Was the "
            "remote pointed at the receiver?"
        )
        assert timer.armed.hours == timer.armed.told, (
            f"the remote transmitted {timer.armed.hours} h but {timer.armed.told} "
            "was reported; one of the two is wrong and neither can be trusted"
        )
        # The prompt asks for this; nothing else enforces it. A one-hour timer
        # could expire mid-session and be read as a cancellation.
        assert timer.armed.hours >= MIN_TIMER_HOURS, (
            f"the timer was armed at {timer.armed.hours} h, under the "
            f"{MIN_TIMER_HOURS} h minimum: it could run out during the session "
            "and be mistaken for our frame cancelling it"
        )

    def test_the_appliance_received_our_frame(self, timer) -> None:
        """A timer surviving a frame the unit never got would prove nothing."""
        assert timer.ours.acted, (
            f"the setpoint did not move -- the panel shows {timer.ours.shown} -- so "
            "the appliance did not act on our frame, and what happened to the "
            "timer says nothing about `b2` or `b1` bit 3"
        )

    def test_an_ordinary_frame_cancels_the_timer(self, timer) -> None:
        """The answer, pinned as the appliance's measured behaviour.

        This asserts the cancellation rather than tolerating it. The integration
        does not transmit timers and is not going to, so a command from Home
        Assistant clearing one is the settled consequence of that -- what would
        be news is the appliance doing something else, and that is what this
        would report.
        """
        assert not timer.ours.survived, (
            "an ordinary frame no longer cancels a timer armed on the remote. "
            "That contradicts what was measured on 2026-08-19, so either the "
            f"appliance behaves differently from the one this was written "
            f"against, or the frame we send has changed: {timer.ours.command!r}"
        )
