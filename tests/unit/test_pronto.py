"""The Pronto encoder, which is how a new capture joins the corpus.

A hardware session records raw timings over ESPHome's native API, but the
protocol document stores Pronto and `conftest.py` parses Pronto. So every new
capture goes through `to_pronto()` on its way into the document, and a bug there
would corrupt the corpus at the moment it grows — quietly, since the resulting
blocks would still look like every other block in the file.
"""

import pytest
from pronto import MARK_EXCESS_MICROS, parse_pronto, timebase_us, to_pronto


class TestRoundTrip:
    """Parsing and rendering are inverses over the real corpus."""

    def test_every_capture_re_renders_to_itself(self, captures) -> None:
        """The strong property: no capture changes by being read and written.

        Run over all 39 blocks, this pins the header words, the pair count's
        `(size + 1) // 2` rounding, the 26us timebase and the mark/space excess
        compensation simultaneously. Any one of them wrong shows up here.
        """
        for capture in captures:
            words = capture.pronto.split()
            frequency_code = int(words[1], 16)
            rendered = to_pronto(capture.timings, frequency_code=frequency_code)
            assert rendered.split() == words, f"{capture} did not round-trip"

    def test_timings_survive_a_render_and_parse(self, captures) -> None:
        """The other direction, for timings already on the grid."""
        for capture in captures:
            assert parse_pronto(to_pronto(capture.timings)) == capture.timings

    def test_the_header_says_what_esphome_would_have_said(self, captures) -> None:
        capture = captures[0]
        words = to_pronto(capture.timings).split()
        assert words[0] == "0000"
        assert words[3] == "0000"
        assert int(words[2], 16) == (len(capture.timings) + 1) // 2


class TestQuantisation:
    """An arbitrary duration has to land on the timebase grid."""

    def test_a_native_api_timing_lands_within_half_a_step(self) -> None:
        """Raw timings are not multiples of 26us, so rendering rounds them.

        Half a timebase is the worst case and the tolerance the decoder already
        works to, so this loses nothing that mattered. Asserting it keeps a
        future change to the rounding from silently widening the error.
        """
        timebase = timebase_us(0x006D)
        original = [-5100, 577, -481, 576, -1928, 555]

        recovered = parse_pronto(to_pronto(original))

        assert len(recovered) == len(original)
        for before, after in zip(original, recovered, strict=True):
            assert abs(abs(after) - abs(before)) <= timebase / 2
            assert (before > 0) == (after > 0), "a mark became a space"

    def test_compensation_is_applied_in_reverse(self) -> None:
        """A mark gives back the excess it was lent; a space is handed it."""
        timebase = timebase_us(0x006D)
        # index 0 is a space, index 1 a mark.
        units = [int(word, 16) for word in to_pronto([-1000, 1000]).split()[4:]]
        assert units[0] == round((1000 + MARK_EXCESS_MICROS) / timebase)
        assert units[1] == round((1000 - MARK_EXCESS_MICROS) / timebase)


class TestRejects:
    """Durations that Pronto cannot hold are an error, not a wrapped value."""

    @pytest.mark.parametrize("micros", [-3_000_000, 3_000_000])
    def test_an_unrepresentable_duration_raises(self, micros: int) -> None:
        with pytest.raises(ValueError, match="not representable"):
            to_pronto([micros])

    def test_a_negative_mark_would_wrap_and_is_refused(self) -> None:
        """Index 1 is a mark, so a negative value there is nonsense."""
        with pytest.raises(ValueError, match="not representable"):
            to_pronto([-1000, -1000])
