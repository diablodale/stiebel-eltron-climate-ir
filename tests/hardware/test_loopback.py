"""Question 10: do our frames reach the air as the bytes we built?

Fully automatic, and the only test in this directory that is. Nobody has to watch
the appliance, and the appliance does not even have to be present -- this asks
about the emitter, the receiver and everything between `get_raw_timings()` and
the LED. It is worth running first for that reason: if it passes and question 7
then fails, the frame did go out correctly and the unit simply did not act on it,
which is a diagnosis rather than a guess.

What it assumes and therefore checks: that nothing between the encoder and the
LED reshapes the waveform. Home Assistant's infrared platform, the ESPHome native
API, the device's transmit queue and its RMT peripheral all sit in that path.

The corpus is the expectation. Every distinct frame the original remote was ever
recorded producing is re-encoded with our nominal timings, transmitted, heard
back and decoded. Equal bytes means our emitter reproduces the remote for that
state; the comparison needs no expectations written by hand, and it fails loudly
if the encoder and the corpus ever drift apart.

The plan expected this to skip: a receiver centimetres from the emitting LED
should saturate. It does not -- our transmissions come back cleanly -- but the
skip is kept for anyone whose hardware differs, because a decode failure there is
inconclusive rather than a bug.

**Marked `disruptive`, and answered already.** Nothing here needs the appliance,
but it sends 76 frames -- power off, dry, 17 C, 30 C, armed timers -- and an
appliance in range acts on every one of them. It was written and run with the
emitter in another room. Question 10 is settled, so re-running this is for when
the emitter or the encoder changes, not part of an ordinary session.
"""

import pytest
from conftest import load_captures
from devices.acp35.protocol import Acp35Command, Acp35Fan, Acp35Mode

# How many times to re-send before calling a frame lost. A receive glitch is not
# a transmission fault: ambient infrared splits a mark in two often enough that
# one damaged capture says nothing, and repeating costs a fifth of a second.
ATTEMPTS = 3


def distinct_frames() -> list[tuple[str, Acp35Command]]:
    """Every distinct frame in the corpus, with the capture that first held it.

    86 captures hold 76 distinct states; sending the duplicates again would add
    time and no coverage.
    """
    seen: dict[bytes, tuple[str, Acp35Command]] = {}
    for capture in load_captures():
        command = Acp35Command.from_raw_timings(capture.timings)
        assert command is not None, f"corpus capture {capture} does not decode"
        seen.setdefault(command.to_bytes(), (capture.label, command))
    return list(seen.values())


DISTINCT = distinct_frames()


def heard_cleanly(send, journal, timings: list[int], label: str):
    """Transmit, and return the record that came back, or fail saying what did.

    A clean capture of our own frame is exactly one duration longer than what we
    sent -- the receiver appends its idle timeout -- and holds exactly one frame.
    Anything else was not sent or not heard properly and must be discarded rather
    than counted as an answer: a truncated transmission means the unit got a
    broken frame and its silence says nothing.
    """
    expected = len(timings) + 1
    seen: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        sent = send(timings, label=f"loopback: {label} (attempt {attempt})")
        for record in journal.wait_for_frames(sent.after, count=1):
            frames = record.frames
            if len(record.timings) == expected and len(frames) == 1:
                return record
            seen.append(f"{len(record.timings)} durations, {len(frames)} frames")
    pytest.fail(
        f"no clean capture of {label!r} in {ATTEMPTS} attempts; "
        f"expected {expected} durations holding one frame, saw: {seen or 'nothing'}"
    )


@pytest.fixture(scope="session")
def loopback(send, journal) -> None:
    """Prove the receiver hears the emitter at all, or skip everything here.

    Once, not per test. A receiver that cannot hear its own emitter makes every
    assertion below meaningless rather than false.
    """
    probe = Acp35Command(power=True, mode=Acp35Mode.COOL, fan=Acp35Fan.HIGH, celsius=22)
    sent = send(probe.get_raw_timings(), label="loopback probe")
    if not journal.wait_for_frames(sent.after, count=1):
        pytest.skip("receiver does not hear its own emitter")


@pytest.mark.disruptive
@pytest.mark.usefixtures("loopback", "appliance")
class TestWhatWeSendIsWhatArrives:
    """The answer to question 10, one distinct corpus frame at a time."""

    @pytest.mark.parametrize(
        ("label", "command"), DISTINCT, ids=[label[:40] for label, _ in DISTINCT]
    )
    def test_a_corpus_frame_survives_the_round_trip(
        self, send, journal, label: str, command: Acp35Command
    ) -> None:
        """Our transmission decodes to exactly what the remote transmitted."""
        record = heard_cleanly(send, journal, command.get_raw_timings(), label)
        assert record.frames[0].to_bytes() == command.to_bytes()

    def test_every_field_survives_not_only_the_bytes(self, send, journal) -> None:
        """Equal bytes is the whole state, but say so in field terms once."""
        original = Acp35Command(
            power=True,
            mode=Acp35Mode.DRY,
            fan=Acp35Fan.LOW,
            fahrenheit=63,
            timer_hours=7,
        )
        record = heard_cleanly(send, journal, original.get_raw_timings(), "every field")
        heard = record.frames[0]
        assert heard.power is original.power
        assert heard.mode is original.mode
        assert heard.fan is original.fan
        # 63 F pairs with 17 C, and re-deriving either from the other would move
        # the displayed number by a degree. Both fields have to survive verbatim.
        assert (heard.celsius, heard.fahrenheit) == (
            original.celsius,
            original.fahrenheit,
        )
        assert heard.timer_hours == original.timer_hours
        assert heard.timer_off_delay is original.timer_off_delay
        assert heard.flags == original.flags


@pytest.mark.disruptive
@pytest.mark.usefixtures("loopback", "appliance")
class TestWhatTheWaveformSays:
    """Properties of the capture itself, not of the bytes it carries."""

    def test_our_frame_carries_one_duration_the_remotes_does_not(
        self, send, journal
    ) -> None:
        """Evidence on question 7, gathered for free.

        Every corpus capture is 147 durations and ours come back at 148. The
        receiver records a leading mark when there is one to record, so the extra
        one is our `HEADER_MARK` -- and the remote does not appear to send a
        header mark at all. That is the strongest evidence available before the
        appliance is present, and it points at `HEADER_MARK = 0`.
        """
        capture = load_captures()[0]
        command = Acp35Command.from_raw_timings(capture.timings)
        record = heard_cleanly(
            send, journal, command.get_raw_timings(), "header mark comparison"
        )
        assert len(record.timings) == len(capture.timings) + 1

    def test_the_carrier_can_be_chosen(self, send, journal) -> None:
        """Question 7's fallback varies the carrier, so it has to reach the LED.

        Not an assertion about what the appliance accepts -- nothing here asks the
        appliance anything. It asserts only that a frame sent at a different
        modulation still goes out and comes back, so a bisect over carriers is a
        test that can be written rather than one that silently sends 38 kHz every
        time.
        """
        command = Acp35Command(
            power=True, mode=Acp35Mode.FAN, fan=Acp35Fan.MEDIUM, celsius=26
        )
        timings = command.get_raw_timings()
        sent = send(timings, label="carrier 36 kHz", modulation=36000)
        records = journal.wait_for_frames(sent.after, count=1)
        assert records, "nothing came back at 36 kHz"
        assert records[0].frames[0].to_bytes() == command.to_bytes()
