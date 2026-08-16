"""Shared base for every appliance's entities.

The protocol has no incremental commands, so any change transmits the whole
state. That shape is the same for every model: mutate the shadow state, build one
frame, send it, and tell the appliance's other entities to redraw. What differs
is only the frame itself.

So this owns the sequence and the model owns the contents. Two members are left
for the subclass -- `_build_command`, and the restore pair that names the stored
shape. Both raise rather than being abstract in the `abc` sense, because Home
Assistant's entity classes are not abstract base classes and mixing a metaclass
in is a cost with no return here.
"""

from typing import Any, override

from homeassistant.components.infrared import InfraredEmitterConsumerEntity
from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import DOMAIN
from .data import StiebelEltronIrConfigEntry, StiebelEltronIrData


class StiebelEltronIrEntity(InfraredEmitterConsumerEntity, RestoreEntity):
    """Owns the shared shadow state and knows how to transmit it."""

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
    def extra_restore_state_data(self) -> ExtraStoredData:
        """Return the snapshot to persist. The model decides what is in it."""
        raise NotImplementedError

    async def _async_restore_shared_state(self) -> None:
        """Load the shadow state back from extra data, if there is any."""
        raise NotImplementedError

    def _build_command(self, event: Any = None) -> Any:
        """Return the frame carrying the whole of the current shadow state.

        ``event`` names what the user just touched, in whatever vocabulary the
        model's protocol uses; ``None`` means nothing in particular did. The
        model maps that to its own value, because a zero event is a fact about a
        frame format and not something this can know.
        """
        raise NotImplementedError

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the shadow state, track the emitter, follow sibling changes.

        The appliance cannot be asked what it is doing, so the state has to come
        back from storage. Every entity restores it: they hold the same object
        and write the same snapshot, and entity add order is not guaranteed.
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

    async def _async_transmit(self, event: Any = None) -> None:
        """Send the current shared state, then refresh every entity."""
        command = self._build_command(event)
        # Note it before sending: our own receiver hears this frame and must not
        # apply it back over the state that produced it.
        self._data.async_note_transmission(command.to_bytes())
        await self._send_command(command)
        self.async_write_ha_state()
        self._data.async_notify(self._listener)
