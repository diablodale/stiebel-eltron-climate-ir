"""Question 8: does the unit act on every command we can produce?

The captures evidence the frame *format* only. That each field means to the unit
what the protocol document says it means is inference, and a swapped mode or fan
value would ship an integration whose controls are confidently mislabelled. This
is where the mapping in `protocol.py` stops being a reading of the corpus and
becomes a measurement.

**Every stop is a blind read.** The prompt names the fields to read off the panel
and never the expected value. That is the whole point: if `MEDIUM` and `HIGH`
were transposed, a prompt asking "does it show medium?" would lead the answer,
and the one defect this test exists to catch would be confirmed by accident.

**The appliance is the instrument, and it is cooling a room.** The stops are
grouped by mode so only four mode transitions happen, cool runs first because the
unit is already there, and fan -- the only mode that stops cooling -- runs last,
immediately before the restore. The setpoint stops sit inside the cool block
because auto and dry pin the setpoint and it stops being readable there.

Three other questions are answered on the way, because the appliance is already
out and driving it twice is worse than driving it once:

- **11**, whether the unit needs the b7 event bits: steps 6 and 7 send the same
  kind of setpoint change with the bits cleared and set.
- **15**, whether a power-off frame leaves the mode running in b6: steps 15--17
  power the unit off carrying a mode it was not running, then bring it back with
  the button on the unit, which carries no mode. Whatever it comes up in is what
  it retained, and no frame of ours could have set it.

Two more are closed without measuring anything, and are recorded here so the
reasoning is not lost:

- **12**, fan `0` as an auto speed: the appliance and the remote both have three
  fan speeds, so there is no fourth for `0` to select. `Acp35Fan` has no member
  for it, and `FAN_SPEEDS` below is therefore the whole enum.
- **13**, a non-low fan in dry: the remote forces low in dry and will not let the
  fan button move it. The remote is the specification, so we never send anything
  else there -- which is why the mode/fan cross has ten cells and not twelve.
  `state_frame` routes every frame through `effective_fan` so this file cannot
  transmit one even by mistake.

Every transmission is checked against the loopback before its reading is
believed. "The unit ignored it" and "we never sent it" are different answers, and
only the first is about the appliance.
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
    celsius_to_fahrenheit,
    effective_fan,
    effective_temperature,
)

# The three speeds the appliance and the remote actually have, which is every
# member of `Acp35Fan`. Spelled out rather than derived from the enum so that
# adding a member could not silently enlarge a sweep run against an appliance.
FAN_SPEEDS = (Acp35Fan.LOW, Acp35Fan.MEDIUM, Acp35Fan.HIGH)

# Each stop is sent more than once. A single missed frame would otherwise read as
# "the unit maps this value differently", which is the wrong conclusion to draw
# from the strongest evidence available here.
REPEATS = 3
REPEAT_GAP = 0.3

# Time for the unit to act and its panel to settle before the question is asked.
SETTLE = 1.5

MODE_WORDS = tuple(mode.name.lower() for mode in Acp35Mode)
FAN_WORDS = tuple(fan.name.lower() for fan in FAN_SPEEDS)

# Every combination, so the choices list leaks nothing about what was sent while
# still guaranteeing an answer this file can parse.
MODE_FAN_ANSWERS = tuple(f"{mode} {fan}" for mode in MODE_WORDS for fan in FAN_WORDS)

# "none" is not an evasion: the panel genuinely shows no setpoint outside cool,
# and recording that is more honest than forcing a number that means nothing.
TEMPERATURE_ANSWERS = (
    *(str(celsius) for celsius in range(MIN_CELSIUS, MAX_CELSIUS + 1)),
    "none",
)

# "off" is what the panel says if the button on the unit did not start it, which
# makes the question-15 reading void rather than informative.
MODE_ANSWERS = (*MODE_WORDS, "off")

# The two setpoints question 8 names, lowest first so the unit works harder
# before it idles rather than after.
RANGE_ENDS = (MIN_CELSIUS, MAX_CELSIUS)

# Carriers for the event-bit pair. Neither is the baseline and neither is a range
# end, so each is a visible change from the stop before it.
EVENT_BIT_CARRIERS = {False: 23, True: 25}


def state_frame(mode: Acp35Mode, fan: Acp35Fan, celsius: int) -> Acp35Command:
    """Build the frame the remote sends for a mode or fan button press.

    `effective_fan` and `effective_temperature` are applied here rather than
    assumed: the remote pins the fan to low in dry and the temperature to 22 in
    dry and auto, so a frame built without them is one the remote cannot emit and
    the unit has never been asked to accept.
    """
    shown_celsius, fahrenheit = effective_temperature(
        mode, celsius, celsius_to_fahrenheit(celsius)
    )
    return Acp35Command(
        power=True,
        mode=mode,
        fan=effective_fan(mode, fan),
        celsius=shown_celsius,
        fahrenheit=fahrenheit,
        # A mode or fan press carries the display-unit bit and no event bit.
        flags=Acp35Flag.CELSIUS,
    )


def setpoint_frame(fan: Acp35Fan, celsius: int, *, event_bits: bool) -> Acp35Command:
    """Build a cool-mode setpoint change, with or without the b7 event bit.

    Only `TEMP_CHANGED` is varied. `CELSIUS` stays set in both, because it is the
    one persistent bit in b7 and clearing it would change the appliance's display
    unit -- a second variable, and question 14 already settled that the unit acts
    on it.
    """
    flags = Acp35Flag.CELSIUS
    if event_bits:
        flags |= Acp35Flag.TEMP_CHANGED
    return Acp35Command(
        power=True, mode=Acp35Mode.COOL, fan=fan, celsius=celsius, flags=flags
    )


def power_frame(mode: Acp35Mode, fan: Acp35Fan, celsius: int) -> Acp35Command:
    """Build a power-off frame carrying ``mode`` in b6, as the power button does."""
    shown_celsius, fahrenheit = effective_temperature(
        mode, celsius, celsius_to_fahrenheit(celsius)
    )
    return Acp35Command(
        power=False,
        mode=mode,
        fan=effective_fan(mode, fan),
        celsius=shown_celsius,
        fahrenheit=fahrenheit,
        flags=Acp35Flag.CELSIUS | Acp35Flag.POWER_PRESSED,
    )


@dataclass(frozen=True)
class Step:
    """One stop: what went out, and what the panel said afterwards."""

    number: int
    command: Acp35Command | None
    """None for the one step that is a physical button press rather than a frame."""
    answer: str
    confirmed: bool
    """Whether the loopback heard this frame go out as the bytes we built."""

    @property
    def mode_read(self) -> str:
        """The mode word in the answer."""
        return self.answer.split()[0]

    @property
    def fan_read(self) -> str:
        """The fan word in the answer."""
        return self.answer.split()[1]


def transmit(send, journal, command: Acp35Command, label: str) -> bool:
    """Send a frame and report whether it reached the air as the bytes we built.

    Question 10 established that the path from `get_raw_timings()` to the LED
    does not reshape a frame, so a failure here is this transmission rather than
    the mechanism, and it makes the reading that follows unusable.
    """
    timings = command.get_raw_timings()
    sent = send(timings, label=label, count=REPEATS, gap=REPEAT_GAP)
    heard = journal.wait_for_frames(sent.after, count=1)
    return any(
        len(record.frames) == 1 and record.frames[0].to_bytes() == command.to_bytes()
        for record in heard
    )


@pytest.fixture(scope="session")
def sweep(send, journal, appliance, ask) -> dict[str, Step]:
    """Drive the whole sweep once and collect what the panel showed at each stop.

    A session fixture because the appliance is an instrument that should be moved
    once however many assertions are made about the result, and because the stops
    are ordered for the room's sake -- running them per test would reorder them.
    """
    total = 17
    steps: dict[str, Step] = {}
    number = 0

    def stop(
        key: str, command: Acp35Command | None, question: str, choices: tuple[str, ...]
    ) -> None:
        nonlocal number
        number += 1
        confirmed = True
        if command is not None:
            print(f"\nStep {number} of {total}: transmitting...")
            confirmed = transmit(send, journal, command, f"question 8, step {number}")
            time.sleep(SETTLE)
        answer = ask(f"Step {number} of {total}: {question}", choices)
        steps[key] = Step(number, command, answer, confirmed)

    baseline_fan = appliance.fan
    baseline_celsius = appliance.celsius

    # Cool first: the unit is already there, so the block costs no transition.
    for fan in FAN_SPEEDS:
        stop(
            f"cool {fan.name.lower()}",
            state_frame(Acp35Mode.COOL, fan, baseline_celsius),
            "what mode and fan speed does the ACP 35 panel show?",
            MODE_FAN_ANSWERS,
        )

    # The range ends, while the unit is still in cool and the setpoint readable.
    for celsius in RANGE_ENDS:
        stop(
            f"setpoint {celsius}",
            setpoint_frame(baseline_fan, celsius, event_bits=True),
            "what temperature does the panel show?",
            TEMPERATURE_ANSWERS,
        )

    # Question 11, same kind of change twice, differing only in b7.
    for event_bits, celsius in EVENT_BIT_CARRIERS.items():
        stop(
            f"event bits {'set' if event_bits else 'cleared'}",
            setpoint_frame(baseline_fan, celsius, event_bits=event_bits),
            "what temperature does the panel show?",
            TEMPERATURE_ANSWERS,
        )

    # The remaining modes. Dry has one cell, not three: see question 13 above.
    for mode, fans in (
        (Acp35Mode.AUTO, FAN_SPEEDS),
        (Acp35Mode.DRY, (Acp35Fan.LOW,)),
        (Acp35Mode.FAN, FAN_SPEEDS),
    ):
        for fan in fans:
            stop(
                f"{mode.name.lower()} {fan.name.lower()}",
                state_frame(mode, fan, baseline_celsius),
                "what mode and fan speed does the ACP 35 panel show?",
                MODE_FAN_ANSWERS,
            )

    # Question 15. The unit is put back into a known mode, powered off by a frame
    # carrying a *different* mode, and started again from the button on the unit,
    # which carries no mode at all. Without that button the question would be
    # unanswerable: every frame we send is full state, so our own power-on frame
    # would set the mode it was meant to be reading.
    stop(
        "before power off",
        state_frame(Acp35Mode.COOL, baseline_fan, baseline_celsius),
        "what mode does the panel show?",
        MODE_ANSWERS,
    )
    stop(
        "power off",
        power_frame(Acp35Mode.FAN, baseline_fan, baseline_celsius),
        "did the ACP 35 switch off?",
        ("yes", "no"),
    )
    stop(
        "button on",
        None,
        "press the power button on the appliance itself, not the remote. "
        "What mode does the panel show?",
        MODE_ANSWERS,
    )
    return steps


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestTheUnitActsOnWhatWeSend:
    """The answer to question 8, read off the appliance's own panel."""

    def test_every_step_was_actually_transmitted(self, sweep) -> None:
        """Otherwise a silent unit and a silent emitter look the same."""
        unsent = [key for key, step in sweep.items() if not step.confirmed]
        assert not unsent, (
            f"the loopback did not hear these steps go out as the bytes we built: "
            f"{unsent}. Nothing about the appliance can be concluded from them."
        )

    @pytest.mark.parametrize("mode", list(Acp35Mode), ids=[m.name for m in Acp35Mode])
    def test_the_unit_enters_the_mode_we_named(self, sweep, mode: Acp35Mode) -> None:
        """`Acp35Mode` is a reading of the corpus; this makes it a measurement.

        A swapped pair here ships a climate entity whose mode selector is
        confidently wrong -- the user picks dry and the appliance runs fan.
        """
        word = mode.name.lower()
        readings = {
            key: step.mode_read for key, step in sweep.items() if key.startswith(word)
        }
        assert readings, f"no step exercised {word}"
        wrong = {key: read for key, read in readings.items() if read != word}
        assert not wrong, f"sent {word}, panel showed {wrong}"

    @pytest.mark.parametrize("fan", FAN_SPEEDS, ids=[f.name for f in FAN_SPEEDS])
    def test_the_unit_runs_the_fan_speed_we_named(self, sweep, fan: Acp35Fan) -> None:
        """Same for the high nibble of b6.

        Checked across every mode that carries this speed rather than in cool
        alone: a value the unit reads differently depending on mode would
        otherwise pass.
        """
        word = fan.name.lower()
        readings = {
            key: step.fan_read
            for key, step in sweep.items()
            if key.endswith(f" {word}")
        }
        assert readings, f"no step exercised fan {word}"
        wrong = {key: read for key, read in readings.items() if read != word}
        assert not wrong, f"sent fan {word}, panel showed {wrong}"

    @pytest.mark.parametrize("celsius", RANGE_ENDS)
    def test_the_setpoint_ends_are_accepted(self, sweep, celsius: int) -> None:
        """The ends are where a range check off by one would show.

        `MIN_CELSIUS` and `MAX_CELSIUS` are what the climate entity advertises to
        Home Assistant, so a value the unit refuses is one the UI offers and
        nothing happens when it is chosen.
        """
        step = sweep[f"setpoint {celsius}"]
        assert step.answer == str(celsius), (
            f"sent {celsius} C and the panel showed {step.answer}"
        )


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestWhetherTheEventBitsAreRequired:
    """Question 11, answered while the appliance is already in cool."""

    def test_a_setpoint_change_with_the_event_bit_set_is_acted_on(self, sweep) -> None:
        """This is what we ship, so this one must pass.

        `_build_command` mirrors the remote and sets `TEMP_CHANGED` on a setpoint
        change. If the unit did not act on that, the temperature control does not
        work at all.
        """
        step = sweep["event bits set"]
        expected = EVENT_BIT_CARRIERS[True]
        assert step.answer == str(expected), (
            f"sent {expected} C with the event bit set and the panel showed "
            f"{step.answer}"
        )

    def test_whether_the_bit_is_needed_at_all(self, sweep) -> None:
        """Report the answer; either result is correct behaviour to ship.

        Mirroring the remote is safe whichever way this falls, so the only
        failure worth having is an unreadable observation. What the reading buys
        is a simplification: if the setpoint moved without the bit, `b7` can
        become a constant and `_build_command` can stop tracking which button a
        change came from.
        """
        step = sweep["event bits cleared"]
        expected = EVENT_BIT_CARRIERS[False]
        assert step.answer != "none", (
            "the panel showed no setpoint, so this step says nothing about the "
            "event bits. Repeat it with the unit in cool."
        )
        if step.answer == str(expected):
            print(
                "\nThe event bits are not required: the setpoint moved with only "
                "the display-unit bit set. b7 could become a constant."
            )
        else:
            print(
                f"\nThe event bits are required: sent {expected} C without "
                f"TEMP_CHANGED and the panel showed {step.answer}. Keep mirroring "
                "the remote."
            )


@pytest.mark.disruptive
@pytest.mark.manual
@pytest.mark.usefixtures("appliance")
class TestWhatAPowerOffFrameLeavesRunning:
    """Question 15, and the one reading no frame of ours could have produced."""

    def test_the_unit_was_in_a_known_mode_and_then_switched_off(self, sweep) -> None:
        """Both halves of the setup, since the reading is void without them."""
        before = sweep["before power off"]
        assert before.answer == "cool", (
            f"the unit was meant to be in cool before the power-off frame and the "
            f"panel showed {before.answer}; the retained mode would be ambiguous"
        )
        assert sweep["power off"].answer == "yes", (
            "the unit did not switch off, so nothing after this measures what a "
            "power-off frame leaves running"
        )

    def test_what_the_unit_came_back_in(self, sweep) -> None:
        """The off frame carried fan; the unit had been running cool.

        `fan` means the unit acted on the mode nibble of a power-off frame, which
        is what `async_set_hvac_mode(OFF)` assumes when it keeps the last mode.
        `cool` means the nibble was ignored and the unit resumed what it was
        running, in which case what we put there does not matter. Neither changes
        the code; anything else means the button forces a preset rather than
        resuming, and the step measured the button instead of the appliance.
        """
        answer = sweep["button on"].answer
        assert answer in ("fan", "cool"), (
            f"the unit came up in {answer}, which is neither the mode the "
            "power-off frame carried nor the one it had been running. The button "
            "on the unit forces a mode, so it cannot answer question 15."
        )
        if answer == "fan":
            print(
                "\nA power-off frame's mode nibble is retained: the unit came "
                "back in the mode the frame carried, not the one it was running."
            )
        else:
            print(
                "\nA power-off frame's mode nibble is ignored: the unit came back "
                "in the mode it had been running. What we send there is free."
            )
