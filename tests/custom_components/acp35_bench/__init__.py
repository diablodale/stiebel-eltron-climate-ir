"""Dev-only capture bench for answering the protocol's open questions.

Records every frame a real infrared receiver hears into a newline-delimited
journal, so a session with the original TZ20160122 remote produces a file that
can be decoded afterwards instead of a terminal someone has to watch.

It exists because neither half of the infrared platform is reachable from
outside Home Assistant. ``async_subscribe_receiver`` hands signals to an
in-process callback and fires no event, and ``async_send_command`` takes a
``Command`` object rather than anything a service call could carry. A component
loaded inside Home Assistant is therefore the only way to get at either.

Deliberately dumb: it writes raw timings and never decodes them. Decoding is
``tools/hw.py``'s job, which uses the shipping ``acp35.py`` — so what validates a
capture is the same code that will encode the frame we transmit, not a second
implementation that could agree with itself while both are wrong.

Configure it in the devcontainer's ``configuration.yaml``:

```yaml
acp35_bench:
  receiver: infrared.examplekc868_ag_ir_proxy_receiver
  journal: /workspaces/acp35/tests/hardware/journal.jsonl
```

This never ships. It is loaded only by the development instance.
"""

import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.infrared import (
    InfraredReceivedSignal,
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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

DOMAIN = "acp35_bench"

CONF_RECEIVER = "receiver"
CONF_JOURNAL = "journal"

SERVICE_MARK = "mark"
ATTR_LABEL = "label"

DEFAULT_JOURNAL = "/workspaces/acp35/tests/hardware/journal.jsonl"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_RECEIVER): cv.entity_id,
                vol.Optional(CONF_JOURNAL, default=DEFAULT_JOURNAL): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

MARK_SCHEMA = vol.Schema({vol.Required(ATTR_LABEL): cv.string})


class _Journal:
    """Append-only JSONL sink, one record per line."""

    def __init__(self, hass: HomeAssistant, path: str) -> None:
        self._hass = hass
        self._path = Path(path)

    def _append(self, record: dict[str, Any]) -> None:
        """Write one record. Runs in an executor: this touches the disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    @callback
    def async_write(self, kind: str, **fields: Any) -> None:
        """Queue a record. Timestamped here so ordering reflects the event."""
        record = {
            "at": dt_util.utcnow().isoformat(timespec="milliseconds"),
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

    @callback
    def start(_event: Event) -> None:
        journal.async_write("session_start", receiver=settings[CONF_RECEIVER])
        recorder.async_start()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start)
    return True
