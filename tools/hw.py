"""Read and decode the capture journal written by the acp35_bench component.

acp35_bench runs inside Home Assistant and appends every infrared signal its
receiver entity delivers to a JSONL journal. This decodes those records with the
shipping ``acp35.py`` and renders selected frames as Pronto blocks for the
protocol document, which ``tests/conftest.py`` parses as the regression corpus.

    tools/hw.py entities                            # list infrared entity ids
    tools/hw.py mark "fan, cool 22C, first press"   # write a label record
    tools/hw.py journal --tail 5                    # decode recent records
    tools/hw.py pronto --since-mark                 # Pronto for the document
    tools/hw.py status                              # subscription and counts

``entities`` and ``mark`` call the Home Assistant REST API; the others read the
journal file and run with the container stopped. Set HA_URL and HA_TOKEN, or
define them in a ``.env`` beside ``pyproject.toml``.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# acp35 is imported as a bare module, not through the stiebel_eltron_ir package,
# whose __init__ pulls in homeassistant.
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "stiebel_eltron_ir"))

from acp35 import Acp35Command  # noqa: E402
from pronto import to_pronto  # noqa: E402

DEFAULT_JOURNAL = REPO_ROOT / "tests/hardware/journal.jsonl"
DEFAULT_HA_URL = "http://localhost:8123"


def load_dotenv() -> None:
    """Load a .env beside pyproject.toml; existing variables take precedence."""
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def read_journal(path: Path) -> list[dict[str, Any]]:
    """Return every record in the journal, oldest first."""
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def since_last_mark(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the records from the most recent mark onward, mark included.

    Each capture step writes a mark record before a button is pressed, so
    selecting from the last mark limits output to that step rather than the
    whole file.
    """
    for index in range(len(records) - 1, -1, -1):
        if records[index]["kind"] == "mark":
            return records[index:]
    return records


def label_of(records: list[dict[str, Any]], upto: int) -> str:
    """Return the nearest mark at or before ``upto``."""
    for index in range(upto, -1, -1):
        if records[index]["kind"] == "mark":
            return records[index]["label"]
    return "unlabelled"


def describe(record: dict[str, Any]) -> str:
    """Return the frame's duration count, decoded bytes and field values."""
    timings = record["timings"]
    command = Acp35Command.from_raw_timings(timings)
    if command is None:
        return f"{len(timings):>3} timings  NOT A VALID ACP 35 FRAME"

    state = command.to_bytes()
    checksum = "ok" if state[8] == sum(state[:8]) & 0xFF else "BAD"
    return (
        f"{len(timings):>3} timings  {state.hex(' ')}  checksum {checksum}\n"
        f"             power={'on' if command.power else 'off'} "
        f"mode={command.mode.name.lower()} fan={command.fan.name.lower()} "
        f"{command.celsius}C/{command.fahrenheit}F "
        f"timer={command.timer_hours}h flags={command.flags!r}"
    )


def _request(path: str, data: dict[str, Any] | None = None) -> Any:
    """Call the Home Assistant REST API and return the decoded body.

    Raises:
        SystemExit: if HA_TOKEN is unset or Home Assistant cannot be reached.
    """
    token = os.environ.get("HA_TOKEN")
    if not token:
        raise SystemExit(
            "HA_TOKEN is not set. Create a long-lived access token in Home "
            "Assistant (profile -> security) and put it in .env as HA_TOKEN=..."
        )
    request = urllib.request.Request(
        f"{os.environ.get('HA_URL', DEFAULT_HA_URL)}{path}",
        data=None if data is None else json.dumps(data).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="GET" if data is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
    except urllib.error.HTTPError as err:
        if err.code == 401:
            raise SystemExit(
                f"Home Assistant rejected the token ({err}). Is HA_TOKEN from "
                "this instance? A token from the production instance will not work."
            ) from err
        raise SystemExit(f"Home Assistant returned {err}") from err
    except OSError as err:
        # OSError, not URLError: a refused or reset connection arrives as
        # ConnectionResetError, which is a sibling of URLError rather than a
        # subclass, and would otherwise escape as a traceback.
        raise SystemExit(
            f"Cannot reach Home Assistant at "
            f"{os.environ.get('HA_URL', DEFAULT_HA_URL)}: {err}\n"
            "Is the devcontainer up and Home Assistant running?"
        ) from err
    return json.loads(body) if body else None


def call_service(domain: str, service: str, data: dict[str, Any]) -> None:
    """POST a Home Assistant service call."""
    _request(f"/api/services/{domain}/{service}", data)


def infrared_entities(device_class: str | None = None) -> list[dict[str, Any]]:
    """Return Home Assistant's infrared entities, optionally of one device class.

    ESPHome derives entity ids from the device's ``friendly_name`` and each
    instance's ``name``, so they can be predicted from the ESPHome config. A
    rename, a second device or a manual override in Home Assistant changes an id
    without changing that config, so ids are read from Home Assistant on each
    run rather than stored in this repo or in ``.env``.
    """
    states = _request("/api/states")
    return sorted(
        (
            state
            for state in states
            if state["entity_id"].startswith("infrared.")
            and (
                device_class is None
                or state["attributes"].get("device_class") == device_class
            )
        ),
        key=lambda state: state["entity_id"],
    )


def resolve_entity(device_class: str) -> str:
    """Return the entity id of the one infrared entity of this device class.

    Raises:
        SystemExit: if there is not exactly one. Selecting from several would
            transmit through whichever entity id sorted first.
    """
    found = [state["entity_id"] for state in infrared_entities(device_class)]
    if len(found) == 1:
        return found[0]
    if not found:
        raise SystemExit(
            f"Home Assistant has no infrared {device_class}. Is the ESPHome "
            "device added, and is it exposing ir_rf_proxy?"
        )
    raise SystemExit(
        f"Home Assistant has {len(found)} infrared {device_class}s: "
        f"{', '.join(found)}. Name the one to use explicitly."
    )


def cmd_entities(args: argparse.Namespace) -> int:
    """List the infrared entities Home Assistant has."""
    infrared = infrared_entities()
    if not infrared:
        print("Home Assistant has no infrared entities.")
        print("Is the ESPHome device added, and is it exposing ir_rf_proxy?")
        return 1

    width = max(len(state["entity_id"]) for state in infrared)
    for state in infrared:
        attributes = state["attributes"]
        print(
            f"{state['entity_id']:<{width}}  "
            f"{attributes.get('device_class', '?'):<8}  "
            f"{attributes.get('friendly_name', '')}"
        )
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    """Write a label record into the journal."""
    call_service("acp35_bench", "mark", {"label": args.label})
    print(f"marked: {args.label}")
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    """Decode and print journal records."""
    records = read_journal(args.journal)
    if not records:
        print(f"nothing recorded yet in {args.journal}")
        return 0

    selected = since_last_mark(records) if args.since_mark else records
    if args.tail:
        selected = selected[-args.tail :]

    for record in selected:
        stamp = record["at"].split("T")[1]
        if record["kind"] == "signal":
            print(f"{stamp}  #{record['index']}  {describe(record)}")
        elif record["kind"] == "mark":
            print(f"{stamp}  -- {record['label']} --")
        else:
            print(f"{stamp}  ({record['kind']})")
    return 0


def cmd_pronto(args: argparse.Namespace) -> int:
    """Print captured frames as Pronto blocks for the protocol document."""
    records = read_journal(args.journal)
    selected = since_last_mark(records) if args.since_mark else records

    frames = [
        (index, record)
        for index, record in enumerate(selected)
        if record["kind"] == "signal"
    ]
    if not frames:
        print("no frames captured", file=sys.stderr)
        return 1

    for index, record in frames:
        print(f"{label_of(selected, index)}\n")
        print("```text")
        print(
            f"[remote.pronto:040]: Received Pronto: data={to_pronto(record['timings'])}"
        )
        print("```\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print the receiver subscription state and journal record counts."""
    records = read_journal(args.journal)
    if not records:
        print(f"journal {args.journal} is empty or missing")
        print("Is acp35_bench configured, and has Home Assistant been restarted?")
        return 1

    ready = [r for r in records if r["kind"] in ("receiver_ready", "receiver_lost")]
    signals = [r for r in records if r["kind"] == "signal"]
    print(f"journal      {args.journal}")
    print(f"records      {len(records)}")
    print(f"frames       {len(signals)}")
    if ready:
        print(f"receiver     {ready[-1]['kind']} at {ready[-1]['at']}")
    else:
        print("receiver     never became available")
    if signals:
        print(f"last frame   {signals[-1]['at']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch."""
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--journal",
        type=Path,
        default=DEFAULT_JOURNAL,
        help="capture journal written by the acp35_bench component",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    entities = subparsers.add_parser(
        "entities", help="list Home Assistant's infrared entities"
    )
    entities.set_defaults(func=cmd_entities)

    mark = subparsers.add_parser("mark", help="write a label record")
    mark.add_argument("label")
    mark.set_defaults(func=cmd_mark)

    journal = subparsers.add_parser("journal", help="decode and print records")
    journal.add_argument("--tail", type=int, default=0, help="only the last N records")
    journal.add_argument(
        "--since-mark",
        action="store_true",
        help="only records from the last mark onward",
    )
    journal.set_defaults(func=cmd_journal)

    pronto = subparsers.add_parser(
        "pronto", help="print Pronto blocks for the protocol document"
    )
    pronto.add_argument(
        "--since-mark",
        action="store_true",
        help="only records from the last mark onward",
    )
    pronto.set_defaults(func=cmd_pronto)

    status = subparsers.add_parser(
        "status", help="print subscription state and record counts"
    )
    status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
