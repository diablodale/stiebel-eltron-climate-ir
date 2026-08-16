"""Keep a subscription to an infrared receiver alive.

Entirely optional. Without a receiver the integration controls the appliance
exactly as before; it simply cannot notice that somebody picked up the remote.
Nothing outside this module may depend on a receiver existing.

Only the subscription lives here. What a received frame *means* is a question
about a protocol, so each model answers it with a handler this calls; see
`ModelInfo.handle_signal`.

The subscription is made here rather than by inheriting
``InfraredReceiverConsumerEntity`` on the climate entity: that class and
``InfraredEmitterConsumerEntity`` share a base, so inheriting both would put a
diamond on the entity. It also keeps the subscription to exactly one per config
entry rather than one per entity, so a frame is applied once and every entity
follows.
"""

import logging
from collections.abc import Callable

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

_LOGGER = logging.getLogger(__name__)

_ABSENT = (STATE_UNAVAILABLE, STATE_UNKNOWN, None)


class StiebelEltronIrReceiverSync:
    """Deliver signals from an infrared receiver to a model's handler."""

    def __init__(
        self,
        hass: HomeAssistant,
        receiver_entity_id: str,
        on_signal: Callable[[InfraredReceivedSignal], None],
    ) -> None:
        """Prepare to follow ``receiver_entity_id``, calling ``on_signal``."""
        self._hass = hass
        self._receiver_entity_id = receiver_entity_id
        self._on_signal = on_signal
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
        """Hand the signal to the model, which decides whether it is its own.

        The receiver picks up every remote in the room, so most signals belong
        to something else entirely. Recognising them is the handler's job, not
        this one's.
        """
        self._on_signal(signal)
