"""Shared fixtures.

The 39 IR captures in the protocol document are the regression corpus. They are
read from the document itself rather than copied into a generated fixture file,
so there is one source of truth and no chance of the two drifting apart.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest
from pronto import find_pronto_captures, parse_pronto

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_DOC = REPO_ROOT / "Stiebel Eltron air conditioner ACP 35.md"

# Every capture is 151 Pronto words: 4 header words plus 147 durations.
EXPECTED_WORDS = 151
EXPECTED_CAPTURES = 70


@dataclass(frozen=True)
class Capture:
    """One captured transmission from the original remote."""

    index: int
    label: str
    pronto: str
    timings: list[int]

    def __str__(self) -> str:
        """Return a short identifier for test output."""
        return f"#{self.index} {self.label[:48]}"


def load_captures() -> list[Capture]:
    """Read and parse every capture in the protocol document."""
    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    return [
        Capture(index=i, label=label, pronto=code, timings=parse_pronto(code))
        for i, (label, code) in enumerate(find_pronto_captures(text))
    ]


@pytest.fixture(scope="session")
def captures() -> list[Capture]:
    """Return every capture from the protocol document."""
    return load_captures()
