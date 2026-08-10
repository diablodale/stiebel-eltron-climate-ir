"""Follow the physical remote, when an infrared receiver is available.

Entirely optional. Without a receiver the integration controls the unit exactly
as before; it simply cannot notice that somebody picked up the remote. Nothing
outside this module may depend on a receiver existing.

The subscription is made here rather than by inheriting
``InfraredReceiverConsumerEntity`` on the climate entity: that class and
``InfraredEmitterConsumerEntity`` share a base, so inheriting both would put a
diamond on the entity. It also keeps the subscription to exactly one per config
entry rather than one per entity, so a frame is applied once and both entities
follow.
"""

import logging
from typing import TYPE_CHECKING

from homeassistant.components.infrared import (
    InfraredReceivedSignal,
    async_subscribe_receiver,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event

from .acp35 import Acp35Command

if TYPE_CHECKING:
    from . import Acp35Data

_LOGGER = logging.getLogger(__name__)

_ABSENT = (STATE_UNAVAILABLE, STATE_UNKNOWN, None)


class Acp35ReceiverSync:
    """Decode frames from an infrared receiver into the shared shadow state."""

    def __init__(
        self, hass: HomeAssistant, data: Acp35Data, receiver_entity_id: str
    ) -> None:
        """Prepare to follow ``receiver_entity_id``."""
        self._hass = hass
        self._data = data
        self._receiver_entity_id = receiver_entity_id
        self._unsubscribe: CALLBACK_TYPE | None = None

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Begin following the receiver. Returns a callback that stops again.

        The receiver may not exist yet — an ESPHome device can still be
        connecting — so its state is watched and the subscription made whenever
        it appears, and dropped whenever it goes away.
        """
        remove_tracker = async_track_state_change_event(
            self._hass, [self._receiver_entity_id], self._handle_receiver_state
        )
        self._async_resubscribe()

        @callback
        def stop() -> None:
            remove_tracker()
            self._async_unsubscribe()

        return stop

    @callback
    def _handle_receiver_state(self, event: Event[EventStateChangedData]) -> None:
        """Subscribe or unsubscribe as the receiver comes and goes."""
        self._async_resubscribe()

    @callback
    def _async_resubscribe(self) -> None:
        """Make the subscription match the receiver's current availability."""
        state = self._hass.states.get(self._receiver_entity_id)
        available = state is not None and state.state not in _ABSENT

        if not available:
            self._async_unsubscribe()
            return
        if self._unsubscribe is not None:
            return

        try:
            self._unsubscribe = async_subscribe_receiver(
                self._hass, self._receiver_entity_id, self._handle_signal
            )
        except HomeAssistantError as error:
            # Not fatal: control still works, we just cannot follow the remote.
            _LOGGER.debug(
                "Cannot follow infrared receiver %s: %s",
                self._receiver_entity_id,
                error,
            )

    @callback
    def _async_unsubscribe(self) -> None:
        """Drop the subscription if there is one."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    @callback
    def _handle_signal(self, signal: InfraredReceivedSignal) -> None:
        """Apply a received frame, ignoring anything that is not ours.

        The receiver picks up every remote in the room, so most signals are not
        ACP 35 frames. ``from_raw_timings`` returns None for those — including
        anything that fails the preamble or checksum — and they are dropped
        without comment.
        """
        command = Acp35Command.from_raw_timings(signal.timings)
        if command is None:
            return

        state = self._data.state
        state.power = command.power
        state.mode = command.mode
        state.fan = command.fan
        state.celsius = command.celsius
        state.fahrenheit = command.fahrenheit
        state.timer_hours = command.timer_hours

        # b7 bit 7 is the unit the unit itself is displaying, so follow it. This
        # is deliberately not written back to the config entry: it tracks what
        # the hardware is doing now, and the configured default wins on restart.
        self._data.display_celsius = command.is_celsius

        _LOGGER.debug("Followed the remote: %r", command)
        self._data.async_notify()
