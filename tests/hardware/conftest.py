"""Fixtures for the tests that need the device.

Nothing here runs by default: `addopts` in `pyproject.toml` deselects the
`hardware` marker, and every test collected from this directory is given that
marker automatically, so forgetting it cannot let a test drive real hardware
during an ordinary run.

    uv run pytest -m hardware -s                 everything, prompts included
    uv run pytest -m "hardware and not manual"   device needed, no human needed

Each fixture **skips rather than fails** when what it needs is absent. A missing
token, a stopped Home Assistant, a device that is not plugged in: none of those
are test failures, and a run that reports them as such buries the one result that
is real. The output of a full run is therefore a precise list of what remains
unanswered.

Everything reaches the device through Home Assistant's REST API and the
`acp35_bench` component, never through a second connection to the ESPHome device.
See `docs/ha_ir_platform/devcontainer.md` for wiring it up, and *Open questions
the hardware must settle* in `docs/ha_ir_platform/plan.md` for what the sessions
are for.
"""

import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from devices.acp35.protocol import Acp35Command
from hw import DEFAULT_HA_URL, MIN_FRAME_DURATIONS, SIMULATED_PREFIX, load_dotenv

HERE = Path(__file__).resolve().parent

# Committed, unlike the journal beside it. A recorded answer is evidence, and
# `git log` is the record of when the device said so.
ANSWERS = HERE / "answers.toml"

JOURNAL = HERE / "journal.jsonl"

# How long to wait for a frame the bench should have journalled. A transmission
# takes about 90 ms on the wire, the receiver adds its 10 ms idle timeout, and
# the record then goes through an executor and a bind mount.
FRAME_TIMEOUT = 5.0

BENCH_DOMAIN = "acp35_bench"


def _now() -> float:
    """Return a monotonic reading that is not wrong on this development host.

    `time.monotonic()` runs several percent fast here and the wall clock steps
    backwards; only the raw clock keeps time. See *Known issue: the development
    host's clock is unusable for measurement* in `docs/ha_ir_platform/plan.md`.
    """
    return time.clock_gettime(time.CLOCK_MONOTONIC_RAW)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark everything collected from this directory as `hardware`.

    Structural rather than conventional: a test that forgets the marker would
    otherwise run during `uv run pytest` and start transmitting.
    """
    for item in items:
        if HERE in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.hardware)


class HomeAssistant:
    """The REST API, with failures turned into skips."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self._token = token

    def request(self, path: str, data: dict[str, Any] | None = None) -> Any:
        """GET ``path``, or POST ``data`` to it, and return the decoded body."""
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=None if data is None else json.dumps(data).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="GET" if data is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 401:
                pytest.skip(f"Home Assistant rejected HA_TOKEN: {error}")
            raise
        except OSError as error:
            # OSError rather than URLError: a refused connection arrives as
            # ConnectionResetError, a sibling rather than a subclass.
            pytest.skip(f"cannot reach Home Assistant at {self.url}: {error}")
        return json.loads(body) if body else None

    def state(self, entity_id: str) -> dict[str, Any] | None:
        """Return one entity's state, or None if it does not exist."""
        try:
            return self.request(f"/api/states/{entity_id}")
        except urllib.error.HTTPError:
            return None

    def call(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        """Call a service."""
        return self.request(f"/api/services/{domain}/{service}", data)

    def infrared_entity(self, device_class: str) -> str:
        """Return the real device's emitter or receiver entity id.

        `fake_ir` registers one of each in the same instance, so device class
        alone is ambiguous; the stub's entity ids are excluded by prefix.
        """
        states = self.request("/api/states")
        matches = [
            state["entity_id"]
            for state in states
            if state["entity_id"].startswith("infrared.")
            and state["attributes"].get("device_class") == device_class
            and not state["entity_id"].startswith(SIMULATED_PREFIX)
        ]
        if not matches:
            pytest.skip(f"no real infrared {device_class} in Home Assistant")
        if len(matches) > 1:
            pytest.skip(f"several real infrared {device_class}s: {matches}")
        return matches[0]


@pytest.fixture(scope="session")
def ha() -> HomeAssistant:
    """Home Assistant's REST API, or a skip if it is not reachable."""
    load_dotenv()
    token = os.environ.get("HA_TOKEN")
    if not token:
        pytest.skip(
            "HA_TOKEN is not set. Create a long-lived access token in the "
            "devcontainer's Home Assistant and put it in .env"
        )
    client = HomeAssistant(os.environ.get("HA_URL", DEFAULT_HA_URL), token)
    # Proves reachability and the token once, so every later failure is about
    # the device rather than the connection.
    client.request("/api/")
    return client


@pytest.fixture(scope="session")
def emitter(ha: HomeAssistant) -> str:
    """The real device's infrared emitter entity id."""
    return ha.infrared_entity("emitter")


@pytest.fixture(scope="session")
def receiver(ha: HomeAssistant) -> str:
    """The real device's infrared receiver entity id."""
    return ha.infrared_entity("receiver")


@dataclass(frozen=True)
class Record:
    """One journal record, with the fields every kind carries."""

    seq: int
    kind: str
    raw: float
    data: dict[str, Any]

    @property
    def timings(self) -> list[int]:
        """The durations, for a `signal` or `transmit` record."""
        return self.data["timings"]

    @property
    def frames(self) -> list[Acp35Command]:
        """Every ACP 35 frame in this record; a buffer is not a frame."""
        return Acp35Command.all_from_raw_timings(self.timings)

    @property
    def is_frame(self) -> bool:
        """Whether this is a signal long enough to be a frame rather than noise."""
        return self.kind == "signal" and len(self.timings) >= MIN_FRAME_DURATIONS


class Journal:
    """What `acp35_bench` has written, and a way to wait for more.

    Ordered by `seq`, which the bench assigns as each record is created. Neither
    alternative is safe: the writes go through an executor and can reach the file
    out of order, and the wall clock steps backwards on this host.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> list[Record]:
        """Every record, oldest first."""
        if not self.path.is_file():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            records.append(
                Record(
                    seq=data.get("seq", -1),
                    kind=data["kind"],
                    raw=data.get("raw", 0.0),
                    data=data,
                )
            )
        records.sort(key=lambda record: record.seq)
        return records

    def last_seq(self) -> int:
        """The highest sequence number written so far."""
        records = self.records()
        return records[-1].seq if records else 0

    def since(self, seq: int) -> list[Record]:
        """Every record written after ``seq``."""
        return [record for record in self.records() if record.seq > seq]

    def wait_for_frames(
        self, after: int, count: int = 1, timeout: float = FRAME_TIMEOUT
    ) -> list[Record]:
        """Wait for ``count`` frame-length signals after ``seq``, or give up.

        Returns what arrived, which may be fewer than asked for -- "the unit
        answered nothing" is a result rather than an error, and it is the answer
        several of these questions are looking for.
        """
        deadline = _now() + timeout
        while True:
            found = [record for record in self.since(after) if record.is_frame]
            if len(found) >= count or _now() >= deadline:
                return found
            time.sleep(0.05)


@pytest.fixture(scope="session")
def journal(ha: HomeAssistant) -> Journal:
    """The bench's journal, or a skip if it never started recording."""
    book = Journal(Path(os.environ.get("HW_JOURNAL", JOURNAL)))
    records = book.records()
    if not records:
        pytest.skip(
            f"{book.path} is empty. Is acp35_bench in configuration.yaml, and "
            "has Home Assistant been restarted since?"
        )
    ready = [record for record in records if record.kind == "receiver_ready"]
    lost = [record for record in records if record.kind == "receiver_lost"]
    if not ready:
        pytest.skip("the bench never subscribed to a receiver")
    if lost and lost[-1].seq > ready[-1].seq:
        pytest.skip("the receiver is unavailable; is the device on the network?")
    return book


@dataclass(frozen=True)
class Sent:
    """What one `acp35_bench.send` call put on the air."""

    frames: list[Acp35Command]
    timings: list[int]
    after: int
    """The journal's last sequence number *before* the send, so what came back
    afterwards can be told from what was already there."""


@pytest.fixture
def send(
    ha: HomeAssistant, journal: Journal, emitter: str
) -> Callable[..., Sent]:
    """Transmit through the bench, and say where in the journal it began.

    Takes durations rather than a command, because the point of several of these
    questions is to send what the shipping encoder cannot build -- a different
    `HEADER_MARK` above all, which is a module constant.
    """

    def _send(timings: list[int], *, label: str | None = None, **options: Any) -> Sent:
        if label:
            ha.call(BENCH_DOMAIN, "mark", {"label": label})
        after = journal.last_seq()
        ha.call(
            BENCH_DOMAIN,
            "send",
            {"timings": list(timings), "emitter": emitter, **options},
        )
        return Sent(
            frames=Acp35Command.all_from_raw_timings(list(timings)),
            timings=list(timings),
            after=after,
        )

    return _send


def _load_answers() -> dict[str, str]:
    """Return the recorded answers, keyed by the question as asked."""
    if not ANSWERS.is_file():
        return {}
    return tomllib.loads(ANSWERS.read_text(encoding="utf-8")).get("answers", {})


def _toml_string(value: str) -> str:
    """Quote a string for TOML, escaping what a basic string may not hold."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _save_answers(answers: dict[str, str]) -> None:
    """Rewrite the answers file, sorted so a diff shows only what changed."""
    lines = [
        "# What the appliance did, recorded so nobody has to be asked twice.",
        "# Written by tests/hardware/conftest.py; committed as evidence.",
        "#",
        "# The question text is the key, so rewording a question orphans its",
        "# answer -- which is the intent: a different question needs asking again.",
        "",
        "[answers]",
    ]
    lines.extend(
        f"{_toml_string(question)} = {_toml_string(answer)}"
        for question, answer in sorted(answers.items())
    )
    ANSWERS.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(scope="session")
def ask() -> Iterator[Callable[..., str]]:
    """Resolve a question about what the appliance did.

    A hardware run cannot depend on somebody watching a terminal, and an
    assistant driving a session cannot hold one open at all. So an answer already
    recorded in `answers.toml` is asserted against non-interactively; missing with
    a tty attached prompts and records what it is told; missing with no tty skips
    and prints the question, which is what makes a full run a list of what remains
    unanswered.
    """
    answers = _load_answers()
    recorded: dict[str, str] = {}

    def _ask(question: str, choices: tuple[str, ...] | None = None) -> str:
        if question in answers:
            answer = answers[question]
            if choices and answer not in choices:
                pytest.fail(
                    f"answers.toml records {answer!r} for {question!r}, "
                    f"which is not one of {choices}"
                )
            return answer

        hint = f" [{'/'.join(choices)}]" if choices else ""
        if not sys.stdin.isatty():
            print(f"\nUNANSWERED: {question}{hint}")
            pytest.skip(f"unanswered: {question}")

        while True:
            answer = input(f"\n{question}{hint} ").strip()
            if not choices or answer in choices:
                break
            print(f"answer one of {choices}")
        answers[question] = answer
        recorded[question] = answer
        _save_answers(answers)
        return answer

    yield _ask

    if recorded:
        print(f"\nRecorded {len(recorded)} new answer(s) in {ANSWERS}")


@pytest.fixture(scope="session")
def confirm(ask: Callable[..., str]) -> Callable[[str], bool]:
    """Ask a yes/no question about what the appliance did."""

    def _confirm(question: str) -> bool:
        return ask(question, ("yes", "no")) == "yes"

    return _confirm
