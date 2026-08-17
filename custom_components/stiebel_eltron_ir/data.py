"""Runtime state shared by every entity of one configured appliance.

Held on the config entry. All of an appliance's entities mutate the same shadow
state and each transmit the complete frame, because the protocol has no
incremental commands, so they need one object between them rather than one each.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, callback

from .devices.acp35.state import Acp35RestoreData, Acp35State
from .storage import SAVE_DELAY_SECONDS, StiebelEltronIrStore

# The two types that are still one model's, named here so the rest of the
# integration can annotate against them without importing a model, and so that
# grepping for either finds exactly the places a second protocol has to revisit.
# Concrete because there is one protocol; a second decides whether these become
# type variables or shared bases, and inventing either from a single case would
# be guessing.
type ShadowState = Acp35State
type StoredState = Acp35RestoreData

# How long after transmitting an identical frame counts as our own echo. A frame
# takes about 90 ms on the wire and the receiver adds its 10 ms idle timeout, so
# this is generous; the cost of it being too long is only that a remote press
# repeating our own command exactly is not re-applied.
ECHO_WINDOW_SECONDS = 1.0

# How many recent frames to remember. Only needs to cover the echoes still in
# flight, and commands arrive in bursts of at most a handful.
ECHO_MEMORY = 8

type StiebelEltronIrConfigEntry = ConfigEntry[StiebelEltronIrData]


@dataclass
class StiebelEltronIrData:
    """Everything the platforms share for one configured appliance."""

    emitter_entity_id: str
    receiver_entity_id: str | None
    # Both resolved from the model record once at setup and kept here, rather
    # than looked up again wherever they are needed. Unload then forwards exactly
    # what setup forwarded, and nothing outside `__init__` has to import the
    # record -- which is what stops the registry and the device modules that
    # populate it from importing each other in a circle.
    platforms: tuple[Platform, ...]
    model: str
    # Required, with no default. The model builds this through `new_state`, and
    # that has to stay the only way to obtain one: a default here could only name
    # a single model's class, so the moment there are two it would hand one
    # appliance the other's state object and make the annotation above a lie.
    state: ShadowState
    # Where the state is persisted, and how to turn it into a payload. Both are
    # attached by setup rather than passed to the constructor, because the
    # snapshot function has to close over this object to read the state from it.
    store: StiebelEltronIrStore | None = None
    snapshot: Callable[[], dict[str, Any]] | None = None
    _listeners: list[CALLBACK_TYPE] = field(default_factory=list)
    _sent: deque[tuple[bytes, float]] = field(
        default_factory=lambda: deque(maxlen=ECHO_MEMORY)
    )

    @callback
    def async_note_transmission(self, frame: bytes) -> None:
        """Record a frame we are about to emit, so its echo can be recognised.

        Several are kept, not one. An echo arrives about 100 ms after the frame
        that caused it, and two service calls can easily be closer together than
        that -- a script setting the mode and the temperature, say. With a single
        slot the first echo no longer matches by the time it lands, and gets
        applied as though the remote had sent it, writing stale state.
        """
        self._sent.append((frame, time.monotonic()))

    @callback
    def async_is_own_echo(self, frame: bytes) -> bool:
        """Return whether a received frame is our own transmission coming back.

        The emitter and receiver share one board, centimetres apart, so every
        frame we send is heard by our own receiver. Applying it would overwrite
        the shadow state with whatever the frame carried, and the frame is not
        always the state: dry pins the temperature to 22 C and the fan to low, so
        switching to dry echoed back and destroyed the setpoint and fan choice
        the user had made in cool.

        Matched on content as well as time, so a genuine remote press inside the
        window is still applied unless it says exactly what we just said, in
        which case applying it would be a no-op.
        """
        now = time.monotonic()
        return any(
            sent == frame and now - at < ECHO_WINDOW_SECONDS for sent, at in self._sent
        )

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> Callable[[], None]:
        """Register a callback for when another entity changes the state."""
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def async_notify(self, source: CALLBACK_TYPE | None = None) -> None:
        """Tell every listener except ``source`` that the state moved.

        This is the one signal every change passes through, whether it came from
        a service call or from a frame the receiver heard, so it is also where
        the write is scheduled. Delayed rather than immediate: a script setting
        mode, fan and temperature in succession then writes once.
        """
        if self.store is not None and self.snapshot is not None:
            self.store.async_delay_save(self.snapshot, SAVE_DELAY_SECONDS)
        for listener in list(self._listeners):
            if listener is not source:
                listener()
