"""Shared fixtures.

Each model's IR captures are its regression corpus. They are read from that
model's protocol document rather than copied into a generated fixture file, so
there is one source of truth and no chance of the two drifting apart.

Adding a model is an entry in `CORPORA` plus its own test module. Nothing here
knows anything about a particular appliance beyond what that entry says.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
from pronto import find_pronto_captures, parse_pronto

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every capture is 151 Pronto words: 4 header words plus 147 durations. This is a
# property of the ESPHome dumper, not of any one appliance, so it is shared.
EXPECTED_WORDS = 151


@dataclass(frozen=True)
class Corpus:
    """Where one model's captures live, and how many there should be.

    The count is a regression pin in its own right: a capture silently lost to a
    bad edit would otherwise just mean fewer assertions, not a failure.
    """

    document: Path
    expected_captures: int


CORPORA: Final[dict[str, Corpus]] = {
    "acp35": Corpus(
        document=REPO_ROOT / "docs" / "Stiebel Eltron air conditioner ACP 35.md",
        expected_captures=86,
    ),
}

# The ACP 35 is the only model with a corpus, so its entry is what the unadorned
# names refer to. A second model gets its own module rather than redefining these.
PROTOCOL_DOC = CORPORA["acp35"].document
EXPECTED_CAPTURES = CORPORA["acp35"].expected_captures


@dataclass(frozen=True)
class Capture:
    """One captured transmission from an original remote."""

    index: int
    label: str
    pronto: str
    timings: list[int]

    def __str__(self) -> str:
        """Return a short identifier for test output."""
        return f"#{self.index} {self.label[:48]}"


def load_captures(model: str = "acp35") -> list[Capture]:
    """Read and parse every capture in a model's protocol document."""
    text = CORPORA[model].document.read_text(encoding="utf-8")
    return [
        Capture(index=i, label=label, pronto=code, timings=parse_pronto(code))
        for i, (label, code) in enumerate(find_pronto_captures(text))
    ]


@pytest.fixture(scope="session")
def captures() -> list[Capture]:
    """Return every ACP 35 capture from its protocol document."""
    return load_captures()
