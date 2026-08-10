"""Shared base for the ACP 35 entities.

Every change transmits the unit's entire state, because the protocol has no
incremental commands. Both entities therefore build the same frame; only the b7
event bit differs, according to which control the user touched.
"""

from typing import override

from homeassistant.components.infrared import InfraredEmitterConsumerEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from . import Acp35ConfigEntry, Acp35Data
from .acp35 import Acp35Command, Acp35Flag
from .const import DOMAIN


class Acp35Entity(InfraredEmitterConsumerEntity, RestoreEntity):
    """Base entity: owns the shared shadow state and knows how to transmit it."""

    _attr_has_entity_name = True
    _attr_assumed_state = True

    def __init__(self, entry: Acp35ConfigEntry) -> None:
        """Bind the entity to its config entry and infrared emitter."""
        self._entry = entry
        self._data: Acp35Data = entry.runtime_data
        self._infrared_emitter_entity_id = self._data.emitter_entity_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Stiebel Eltron",
            model="ACP 35",
            name=entry.title,
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Track the emitter and follow state changes made by sibling entities."""
        await super().async_added_to_hass()
        self.async_on_remove(self._data.async_add_listener(self._handle_shared_update))

    def _handle_shared_update(self) -> None:
        """Write our state when a sibling entity changed the shared state."""
        self.async_write_ha_state()

    def _build_command(self, event: Acp35Flag = Acp35Flag.NONE) -> Acp35Command:
        """Build the full-state frame, with ``event`` added to b7.

        b7 carries the display unit plus an event bit naming what the user just
        changed, mirroring what the remote emits. Whether the unit needs the
        event bits at all is untested; if it turns out not to, every caller can
        simply stop passing one.
        """
        state = self._data.state
        flags = event
        if self._data.display_celsius:
            flags |= Acp35Flag.CELSIUS
        if state.timer_hours:
            flags |= Acp35Flag.TIMER_UI

        return Acp35Command(
            power=state.power,
            mode=state.mode,
            fan=state.fan,
            celsius=state.celsius,
            timer_hours=state.timer_hours,
            flags=flags,
        )

    async def _async_transmit(self, event: Acp35Flag = Acp35Flag.NONE) -> None:
        """Send the current shared state, then refresh every entity."""
        await self._send_command(self._build_command(event))
        self.async_write_ha_state()
        self._data.async_notify(self._handle_shared_update)
