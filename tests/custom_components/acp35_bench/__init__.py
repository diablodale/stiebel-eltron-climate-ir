"""Dev-only capture bench for answering the protocol's open questions.

Records every frame a real infrared receiver hears into a newline-delimited
journal, so a session with the original TZ20160122 remote produces a file that
can be decoded afterwards instead of a terminal someone has to watch. It also
transmits, which is what lets a test put a frame in front of the appliance.

It exists because neither half of the infrared platform is reachable from
outside Home Assistant. ``async_subscribe_receiver`` hands signals to an
in-process callback and fires no event, and ``async_send_command`` takes a
``Command`` object rather than anything a service call could carry. A component
loaded inside Home Assistant is therefore the only way to get at either.

Deliberately dumb in both directions: it writes raw timings and never decodes
them, and it transmits raw timings and never encodes them. Decoding is
``tools/hw.py``'s job and encoding is the caller's, both using the shipping
``devices/acp35/protocol.py`` — so what validates a capture is the same code that
builds the frames we transmit, not a second implementation that could agree with
itself while both are wrong. It is also what makes question 7 possible:
``HEADER_MARK`` is a module constant, so trying a different one means handing
over durations the encoder would not produce.

Services:

- ``acp35_bench.mark`` — write a label, so a frame can be tied to the button that
  caused it.
- ``acp35_bench.send`` — transmit ``timings``, optionally ``count`` times with
  ``gap`` seconds between.

Configure it in the devcontainer's ``configuration.yaml``:

```yaml
acp35_bench:
  receiver: infrared.examplekc868_ag_ir_proxy_receiver
  emitter: infrared.examplekc868_ag_ir_proxy_transmitter
  journal: /workspaces/acp35/tests/hardware/journal.jsonl
```

``emitter`` is optional: a session that only listens does not need one, and a
service call can always name a different one.

This never ships. It is loaded only by the development instance.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, override

import voluptuous as vol
from homeassistant.components.infrared import (
    InfraredReceivedSignal,
    async_send_command,
    async_subscribe_receiver,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    ServiceCall,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
from infrared_protocols.commands import Command as InfraredCommand

_LOGGER = logging.getLogger(__name__)

DOMAIN = "acp35_bench"

CONF_RECEIVER = "receiver"
CONF_EMITTER = "emitter"
CONF_JOURNAL = "journal"

SERVICE_MARK = "mark"
ATTR_LABEL = "label"

SERVICE_SEND = "send"
ATTR_TIMINGS = "timings"
ATTR_MODULATION = "modulation"
ATTR_REPEAT_COUNT = "repeat_count"
ATTR_COUNT = "count"
ATTR_GAP = "gap"

DEFAULT_JOURNAL = "/workspaces/acp35/tests/hardware/journal.jsonl"

# The carrier every ACP 35 capture was recorded at. Overridable per call, since
# question 7's fallback varies it.
DEFAULT_MODULATION = 38000

# Bounds on what may be transmitted. Not protocol knowledge -- the bench holds
# none -- just a guard so a typo cannot drive the emitting LED for a long time
# or queue thousands of frames at a device that answers to none of them.
MAX_DURATIONS = 1024
MAX_DURATION_US = 100_000
MAX_COUNT = 64
MAX_GAP_SECONDS = 60.0

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_RECEIVER): cv.entity_id,
                # Optional: recording needs no emitter, and a session that only
                # listens to the remote should not have to name one.
                vol.Optional(CONF_EMITTER): cv.entity_id,
                vol.Optional(CONF_JOURNAL, default=DEFAULT_JOURNAL): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

MARK_SCHEMA = vol.Schema({vol.Required(ATTR_LABEL): cv.string})


def _duration(value: Any) -> int:
    """Validate one raw timing: non-zero microseconds, marks positive."""
    duration = int(value)
    if duration == 0:
        raise vol.Invalid("a duration of 0 us is not a mark or a space")
    if abs(duration) > MAX_DURATION_US:
        raise vol.Invalid(f"|{duration}| exceeds {MAX_DURATION_US} us")
    return duration


SEND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TIMINGS): vol.All(
            cv.ensure_list, [_duration], vol.Length(min=1, max=MAX_DURATIONS)
        ),
        vol.Optional(CONF_EMITTER): cv.entity_id,
        vol.Optional(ATTR_MODULATION, default=DEFAULT_MODULATION): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1_000_000)
        ),
        vol.Optional(ATTR_REPEAT_COUNT, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=10)
        ),
        vol.Optional(ATTR_COUNT, default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_COUNT)
        ),
        vol.Optional(ATTR_GAP, default=0.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=MAX_GAP_SECONDS)
        ),
    }
)


class _RawCommand(InfraredCommand):
    """The timings the caller supplied, and nothing else.

    The bench encodes nothing, exactly as it decodes nothing. Whoever calls the
    service builds the frame -- with the shipping codec, or by hand when the
    point is to send something the codec cannot produce, which is what question 7
    needs: `HEADER_MARK` is a module constant, so the only way to try a different
    one is to assemble the durations outside the encoder.
    """

    def __init__(self, timings: list[int], *, modulation: int, repeat_count: int):
        """Hold the timings verbatim."""
        super().__init__(modulation=modulation, repeat_count=repeat_count)
        self._timings = timings

    @override
    def get_raw_timings(self) -> list[int]:
        """Return the durations exactly as given."""
        return list(self._timings)


class _Journal:
    """Append-only JSONL sink, one record per line."""

    def __init__(self, hass: HomeAssistant, path: str) -> None:
        self._hass = hass
        self._path = Path(path)
        self._seq = 0

    def _append(self, record: dict[str, Any]) -> None:
        """Write one record. Runs in an executor: this touches the disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    @callback
    def async_write(self, kind: str, **fields: Any) -> None:
        """Queue a record, stamped three ways.

        ``seq`` is the order records were created. It is assigned here, on the
        event loop, so it is the only ordering that cannot be wrong: the writes
        themselves go to an executor and could land out of order, and ``at`` is a
        wall clock that steps backwards on this development host.

        ``raw`` is `CLOCK_MONOTONIC_RAW`, the one clock here that runs at the
        right rate. Any duration measured between records has to come from it.
        See the known clock issue in `docs/ha_ir_platform/plan.md`.

        ``at`` stays because a human reading the file wants a time of day, not a
        number of seconds since an arbitrary boot.
        """
        self._seq += 1
        record = {
            "seq": self._seq,
            "at": dt_util.utcnow().isoformat(timespec="milliseconds"),
            "raw": time.clock_gettime(time.CLOCK_MONOTONIC_RAW),
            "kind": kind,
            **fields,
        }
        self._hass.async_add_executor_job(self._append, record)


class _Recorder:
    """Keeps a subscription to the receiver alive across its availability."""

    def __init__(self, hass: HomeAssistant, entity_id: str, journal: _Journal) -> None:
        self._hass = hass
        self._entity_id = entity_id
        self._journal = journal
        self._unsubscribe: CALLBACK_TYPE | None = None
        self.count = 0

    @callback
    def async_start(self) -> None:
        """Follow the receiver entity and subscribe whenever it is available.

        The entity does not exist at setup: it arrives when the ESPHome config
        entry finishes loading, and vanishes again whenever the device drops off
        the network. So this tracks state rather than subscribing once.
        """
        async_track_state_change_event(
            self._hass, [self._entity_id], self._async_state_changed
        )
        self._async_sync()

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        self._async_sync()

    @callback
    def _async_sync(self) -> None:
        state = self._hass.states.get(self._entity_id)
        available = state is not None and state.state != STATE_UNAVAILABLE

        if not available:
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
                self._journal.async_write("receiver_lost", entity_id=self._entity_id)
            return

        if self._unsubscribe is None:
            self._unsubscribe = async_subscribe_receiver(
                self._hass, self._entity_id, self._handle_signal
            )
            self._journal.async_write("receiver_ready", entity_id=self._entity_id)
            _LOGGER.info("acp35_bench recording from %s", self._entity_id)

    @callback
    def _handle_signal(self, signal: InfraredReceivedSignal) -> None:
        """Record a frame verbatim. No decoding, no filtering, no tolerance."""
        self.count += 1
        self._journal.async_write(
            "signal",
            index=self.count,
            timings=list(signal.timings),
            modulation=signal.modulation,
        )
        _LOGGER.info(
            "acp35_bench captured frame %d, %d timings", self.count, len(signal.timings)
        )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Start recording, and register the service that labels a session."""
    if DOMAIN not in config:
        return True

    settings = config[DOMAIN]
    journal = _Journal(hass, settings[CONF_JOURNAL])
    recorder = _Recorder(hass, settings[CONF_RECEIVER], journal)

    @callback
    def handle_mark(call: ServiceCall) -> None:
        """Write a label so a frame can be tied to the button that caused it.

        Without this the journal is a pile of anonymous frames. Marking before
        each press means the decode step can say "this is what the fan button
        emitted" rather than leaving it to be reconstructed from memory.
        """
        journal.async_write("mark", label=call.data[ATTR_LABEL])
        _LOGGER.info("acp35_bench mark: %s", call.data[ATTR_LABEL])

    hass.services.async_register(DOMAIN, SERVICE_MARK, handle_mark, schema=MARK_SCHEMA)

    async def handle_send(call: ServiceCall) -> None:
        """Transmit raw timings through an infrared emitter.

        The other half of the bridge. `async_send_command` takes a `Command`
        object, which no service call can carry, so nothing outside Home
        Assistant can transmit at all -- and the shipping integration only ever
        sends what its own encoder produces. Both are answered here: the caller
        supplies durations and this wraps them.

        ``count`` and ``gap`` send a burst from inside Home Assistant rather than
        from the host, which removes the tens of milliseconds of jitter that
        separate REST calls carry -- the same order as the separations question 9
        is trying to measure.

        **``gap`` spaces the service calls, not the emissions.** Measured: four
        frames requested 150 ms apart produced three receive buffers, one of them
        holding two frames 1415 us apart. Something between here and the LED --
        the ESPHome API, the device's transmit queue -- does not preserve the
        spacing, so a burst cannot be assumed to have reached the air the way it
        was asked for.

        The separation that matters is therefore always measured, never assumed,
        and the loopback is what measures it: two frames close enough together
        arrive in one buffer with the gap between them as a single duration,
        timed by the ESP32 rather than by any clock on this host. Each
        transmission also carries a `CLOCK_MONOTONIC_RAW` reading in the journal,
        which bounds the separation when the frames land in separate buffers.
        """
        emitter = call.data.get(CONF_EMITTER) or settings.get(CONF_EMITTER)
        if emitter is None:
            raise ServiceValidationError(
                f"No emitter given and none configured. Pass '{CONF_EMITTER}' in "
                f"the service call, or set it under '{DOMAIN}:' in "
                "configuration.yaml."
            )

        timings = call.data[ATTR_TIMINGS]
        command = _RawCommand(
            timings,
            modulation=call.data[ATTR_MODULATION],
            repeat_count=call.data[ATTR_REPEAT_COUNT],
        )
        count = call.data[ATTR_COUNT]
        gap = call.data[ATTR_GAP]

        for number in range(1, count + 1):
            if number > 1 and gap:
                await asyncio.sleep(gap)
            # Journalled before the send, not after, so the record's clock
            # reading is the moment the frame was handed over rather than the
            # moment it finished going out. A loopback echo lands after it, and
            # the two are told apart by `kind` rather than by order.
            journal.async_write(
                "transmit",
                emitter=emitter,
                timings=list(timings),
                modulation=command.modulation,
                repeat_count=command.repeat_count,
                number=number,
                of=count,
                requested_gap=gap,
            )
            await async_send_command(hass, emitter, command)

        _LOGGER.info(
            "acp35_bench sent %d durations x%d through %s",
            len(timings),
            count,
            emitter,
        )

    hass.services.async_register(DOMAIN, SERVICE_SEND, handle_send, schema=SEND_SCHEMA)

    @callback
    def start(_event: Event) -> None:
        journal.async_write("session_start", receiver=settings[CONF_RECEIVER])
        recorder.async_start()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start)
    return True
