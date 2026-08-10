"""Fake infrared emitter entity that records commands instead of sending them."""

from __future__ import annotations

import logging
from typing import Any, override

from homeassistant.components.infrared import (
    InfraredCommand,
    InfraredEmitterEntity,
    InfraredReceivedSignal,
    InfraredReceiverEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DATA_SENT

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the fake emitter and receiver from a YAML `infrared:` block."""
    hass.data.setdefault(DATA_SENT, [])
    async_add_entities([FakeIrEmitter(), FakeIrReceiver()])


class FakeIrEmitter(InfraredEmitterEntity):
    """An emitter that transmits nothing and remembers everything."""

    _attr_name = "Fake IR emitter"
    _attr_unique_id = "fake_ir_emitter"

    @override
    async def async_send_command(self, command: InfraredCommand) -> None:
        """Record the command rather than transmitting it."""
        record: dict[str, Any] = {
            "command": type(command).__name__,
            "modulation": command.modulation,
            "repeat_count": command.repeat_count,
            "timings": command.get_raw_timings(),
        }
        self.hass.data.setdefault(DATA_SENT, []).append(record)
        _LOGGER.info(
            "fake_ir recorded %s modulation=%d repeat=%d timings=%s",
            record["command"],
            record["modulation"],
            record["repeat_count"],
            record["timings"],
        )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the last recorded send so REST-based tests can assert on it.

        The base class owns `state` (it is the last-sent timestamp and is
        `@final`), so the payload has to travel as an attribute.
        """
        sent: list[dict[str, Any]] = self.hass.data.get(DATA_SENT, [])
        if not sent:
            return {"sent_count": 0}
        last = sent[-1]
        return {
            "sent_count": len(sent),
            "last_command": last["command"],
            "last_modulation": last["modulation"],
            "last_repeat_count": last["repeat_count"],
            "last_timings": last["timings"],
        }


class FakeIrReceiver(InfraredReceiverEntity):
    """A receiver that hears nothing until a test tells it to."""

    _attr_name = "Fake IR receiver"
    _attr_unique_id = "fake_ir_receiver"

    def inject(self, timings: list[int], modulation: int = 38000) -> None:
        """Deliver a signal to every subscriber, as real hardware would.

        `_handle_received_signal` is the hook platform implementations call when
        their device reports a signal; it is final on the base class and fans out
        to whoever subscribed via `async_subscribe_receiver`.
        """
        self._handle_received_signal(
            InfraredReceivedSignal(timings=timings, modulation=modulation)
        )
