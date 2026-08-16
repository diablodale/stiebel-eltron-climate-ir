"""Shared base for the ACP 35 entities.

Every change transmits the unit's entire state, because the protocol has no
incremental commands. Both entities therefore build the same frame; only the b7
event bit differs, according to which control the user touched.
"""

from typing import override

from homeassistant.components.infrared import InfraredEmitterConsumerEntity
from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from ...const import DOMAIN
from ...data import StiebelEltronIrConfigEntry, StiebelEltronIrData
from .protocol import (
    Acp35Command,
    Acp35Flag,
    effective_fan,
    effective_temperature,
)
from .state import Acp35RestoreData


class Acp35Entity(InfraredEmitterConsumerEntity, RestoreEntity):
    """Base entity: owns the shared shadow state and knows how to transmit it."""

    _attr_has_entity_name = True
    _attr_assumed_state = True

    def __init__(self, entry: StiebelEltronIrConfigEntry) -> None:
        """Bind the entity to its config entry and infrared emitter."""
        self._entry = entry
        self._data: StiebelEltronIrData = entry.runtime_data
        self._listener: CALLBACK_TYPE | None = None
        self._infrared_emitter_entity_id = self._data.emitter_entity_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Stiebel Eltron",
            # Resolved at setup, so the record is not imported here. See data.py.
            model=self._data.model,
            name=entry.title,
        )

    @property
    @override
    def extra_restore_state_data(self) -> Acp35RestoreData:
        """Persist the whole shadow state, not just what this entity shows."""
        return Acp35RestoreData.from_state(self._data.state)

    async def _async_restore_shared_state(self) -> None:
        """Load the shadow state from extra data, if there is any."""
        if (extra := await self.async_get_last_extra_data()) is None:
            return
        if (restored := Acp35RestoreData.from_dict(extra.as_dict())) is not None:
            restored.apply(self._data.state)

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the shadow state, track the emitter, follow sibling changes.

        The unit cannot be asked what it is doing, so the state has to come back
        from storage. Both entities restore it: they hold the same object and
        write the same snapshot, and entity add order is not guaranteed.
        """
        await super().async_added_to_hass()
        await self._async_restore_shared_state()
        # Keep one reference to the bound method. Every `self._handle_shared_update`
        # builds a fresh bound-method object, so the identity check that stops us
        # notifying ourselves in _async_transmit would never match otherwise.
        self._listener = self._handle_shared_update
        self.async_on_remove(self._data.async_add_listener(self._listener))

    def _handle_shared_update(self) -> None:
        """Write our state when a sibling entity changed the shared state."""
        self.async_write_ha_state()

    def _build_command(self, event: Acp35Flag = Acp35Flag.NONE) -> Acp35Command:
        """Build the full-state frame, with ``event`` added to b7.

        b7 carries the display unit plus an event bit naming what the user just
        changed, mirroring what the remote emits. Whether the unit needs the
        event bits at all is untested; if it turns out not to, every caller can
        simply stop passing one.

        Every bit but the display unit is an event, ``TIMER_UI`` included, so
        each is passed in by the setter that caused it rather than derived from
        the state. Deriving it was a bug: a capture of the remote with a timer
        counting down shows ``b7`` bit 1 clear on an ordinary fan press, so the
        bit means the entry display is open, not that a timer is pending.
        """
        state = self._data.state
        flags = event
        if state.display_celsius:
            flags |= Acp35Flag.CELSIUS

        celsius, fahrenheit = effective_temperature(
            state.mode, state.celsius, state.fahrenheit
        )
        return Acp35Command(
            power=state.power,
            mode=state.mode,
            # Dry forces low on the remote, and dry and auto pin the temperature
            # to the default. The shadow state keeps whatever the user picked so
            # it comes back when they return to a mode that uses it.
            fan=effective_fan(state.mode, state.fan),
            # Both fields go verbatim. Passing only Celsius would re-derive the
            # Fahrenheit one, which shifts a unit displaying °F by a degree
            # whenever the two mappings disagree, as they do at 63 °F.
            celsius=celsius,
            fahrenheit=fahrenheit,
            timer_hours=state.timer_hours,
            flags=flags,
        )

    async def _async_transmit(self, event: Acp35Flag = Acp35Flag.NONE) -> None:
        """Send the current shared state, then refresh every entity."""
        command = self._build_command(event)
        # Note it before sending: our own receiver hears this frame and must not
        # apply it back over the state that produced it.
        self._data.async_note_transmission(command.to_bytes())
        await self._send_command(command)
        self.async_write_ha_state()
        self._data.async_notify(self._listener)
