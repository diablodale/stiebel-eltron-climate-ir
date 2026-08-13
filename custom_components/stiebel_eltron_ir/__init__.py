"""Control a Stiebel Eltron ACP 35 air conditioner over infrared.

The protocol carries no acknowledgement and the unit reports nothing back, so
this integration keeps a shadow copy of what it believes the unit's state to be
and transmits that whole state on every change. Entities are therefore
``assumed_state``.

Both entities — the climate control and the timer — mutate the same shadow state
and each send the complete frame, so they share one :class:`Acp35Data` held on
the config entry.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Self, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.restore_state import ExtraStoredData

from .acp35 import (
    Acp35Fan,
    Acp35Mode,
    celsius_to_fahrenheit,
    effective_fan,
    fahrenheit_to_celsius,
)
from .const import CONF_EMITTER, CONF_RECEIVER
from .receiver import Acp35ReceiverSync

PLATFORMS = [Platform.CLIMATE, Platform.SELECT, Platform.SENSOR]

# How long after transmitting an identical frame counts as our own echo. A frame
# takes about 90 ms on the wire and the receiver adds its 10 ms idle timeout, so
# this is generous; the cost of it being too long is only that a remote press
# repeating our own command exactly is not re-applied.
ECHO_WINDOW_SECONDS = 1.0

# How many recent frames to remember. Only needs to cover the echoes still in
# flight, and commands arrive in bursts of at most a handful.
ECHO_MEMORY = 8

type Acp35ConfigEntry = ConfigEntry[Acp35Data]

# The speed each mode starts on. Taken from the remote after a battery removal,
# which brings every mode back to high except dry, which returns on low.
DEFAULT_FAN_BY_MODE = {
    Acp35Mode.AUTO: Acp35Fan.HIGH,
    Acp35Mode.COOL: Acp35Fan.HIGH,
    Acp35Mode.DRY: Acp35Fan.LOW,
    Acp35Mode.FAN: Acp35Fan.HIGH,
}


@dataclass
class Acp35State:
    """What we believe the unit is currently doing.

    Defaults match the remote's own resting state: cooling, high fan, 22 °C.
    They are only used before the first restore.

    Both temperature fields are kept, rather than deriving one from the other on
    the way out, because the two mappings are not inverses: a frame from a
    remote displaying Fahrenheit can hold 63 °F with 17 °C, and re-deriving from
    17 °C would send 62 °F back and shift the unit by a degree.

    The fan speed is stored per mode because that is what the remote does: each
    mode remembers its own speed, and a mode press transmits the speed stored
    for the mode being entered. Setting cool to medium, visiting dry and coming
    back to cool returns to medium, and never to dry's low.
    """

    power: bool = False
    mode: Acp35Mode = Acp35Mode.COOL
    fan_by_mode: dict[Acp35Mode, Acp35Fan] = field(
        default_factory=DEFAULT_FAN_BY_MODE.copy
    )
    celsius: int = 22
    fahrenheit: int = 72
    timer_hours: int = 0
    # b7 bit 7: which unit the air conditioner shows on its own display. State
    # rather than configuration -- the remote's C/F button changes it and we
    # follow that, so it belongs here with everything else the unit is doing.
    # Seeded from the Home Assistant install's unit at setup.
    display_celsius: bool = True

    @property
    def fan(self) -> Acp35Fan:
        """Return the speed stored for the mode currently selected."""
        return self.fan_by_mode.get(self.mode, Acp35Fan.HIGH)

    def set_fan(self, fan: Acp35Fan) -> None:
        """Store a speed against the mode currently selected.

        Dry's slot can only ever hold low. Enforcing it here rather than only on
        the way out means no path can put a speed there that the remote could not
        have produced -- including a received frame, which is decoded from
        whatever was on the wire and is not required to be a frame the remote is
        capable of emitting.
        """
        self.fan_by_mode[self.mode] = effective_fan(self.mode, fan)

    def set_celsius(self, celsius: int) -> None:
        """Set the temperature from Celsius, repairing the Fahrenheit field."""
        self.celsius = celsius
        self.fahrenheit = celsius_to_fahrenheit(celsius)

    def set_fahrenheit(self, fahrenheit: int) -> None:
        """Set the temperature from Fahrenheit, repairing the Celsius field.

        The mirror of :meth:`set_celsius`, and needed because the two tables are
        not inverses. Whichever scale the appliance is displaying is the one the
        user is choosing on, so that field is authoritative and the other is its
        pair; deriving the wrong way round would move the displayed number.
        """
        self.fahrenheit = fahrenheit
        self.celsius = fahrenheit_to_celsius(fahrenheit)


@dataclass
class Acp35RestoreData(ExtraStoredData):
    """The shadow state, persisted independently of what the entities display.

    Restoring from displayed attributes cannot work here. The card hides the
    temperature outside cool and narrows the fan choices in dry, so a restart
    taken in one of those modes would read back the hidden value and overwrite
    what the user actually chose. Extra data is written from the state itself,
    so what is remembered does not depend on what is shown.
    """

    power: bool
    mode: int
    fan_by_mode: dict[str, int]
    celsius: int
    fahrenheit: int
    timer_hours: int
    display_celsius: bool

    @classmethod
    def from_state(cls, state: Acp35State) -> Self:
        """Snapshot a shadow state.

        Every mode's fan speed is kept, not just the selected one. Storing only
        the current speed would lose the others across a restart and hand each
        mode back whatever the last-used mode was running.
        """
        return cls(
            power=state.power,
            mode=int(state.mode),
            # JSON object keys are strings, so the mode is written as one.
            fan_by_mode={
                str(int(mode)): int(fan) for mode, fan in state.fan_by_mode.items()
            },
            celsius=state.celsius,
            fahrenheit=state.fahrenheit,
            timer_hours=state.timer_hours,
            display_celsius=state.display_celsius,
        )

    @override
    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form Home Assistant stores."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self | None:
        """Rebuild from storage, or None if it is unusable.

        Storage outlives the code that wrote it, so a missing or malformed field
        must leave the defaults in place rather than raise during setup.
        """
        try:
            return cls(
                power=bool(data["power"]),
                mode=int(data["mode"]),
                fan_by_mode={
                    str(mode): int(fan) for mode, fan in data["fan_by_mode"].items()
                },
                celsius=int(data["celsius"]),
                fahrenheit=int(data["fahrenheit"]),
                timer_hours=int(data["timer_hours"]),
                display_celsius=bool(data["display_celsius"]),
            )
        except KeyError, TypeError, ValueError, AttributeError:
            return None

    def apply(self, state: Acp35State) -> None:
        """Copy this snapshot over a shadow state, ignoring unknown enum values.

        Stored speeds are merged onto the defaults rather than replacing them, so
        a mode missing from storage keeps its default instead of disappearing.
        Dry is pinned to low on the way back in as well, so storage written by an
        older build cannot reintroduce a pairing the remote cannot produce.
        """
        state.power = self.power
        if self.mode in tuple(Acp35Mode):
            state.mode = Acp35Mode(self.mode)
        for mode, fan in self.fan_by_mode.items():
            try:
                stored_mode = Acp35Mode(int(mode))
                state.fan_by_mode[stored_mode] = effective_fan(
                    stored_mode, Acp35Fan(fan)
                )
            except ValueError:
                continue
        state.celsius = self.celsius
        state.fahrenheit = self.fahrenheit
        state.timer_hours = self.timer_hours
        state.display_celsius = self.display_celsius


@dataclass
class Acp35Data:
    """Everything the platforms share for one configured air conditioner."""

    emitter_entity_id: str
    receiver_entity_id: str | None
    state: Acp35State = field(default_factory=Acp35State)
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
        """Tell every listener except ``source`` that the state moved."""
        for listener in list(self._listeners):
            if listener is not source:
                listener()


async def async_setup_entry(hass: HomeAssistant, entry: Acp35ConfigEntry) -> bool:
    """Set up one air conditioner from a config entry."""
    data = entry.runtime_data = Acp35Data(
        emitter_entity_id=entry.data[CONF_EMITTER],
        receiver_entity_id=entry.data.get(CONF_RECEIVER),
    )
    # Seed the unit the air conditioner displays from this Home Assistant
    # install's own unit, which is the closest thing to the user's intent we can
    # know without asking. A restore or a frame from the remote overrides it.
    data.state.display_celsius = (
        hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Optional. With no receiver configured the integration is complete as it
    # stands; it just cannot notice the physical remote being used.
    if data.receiver_entity_id is not None:
        sync = Acp35ReceiverSync(hass, data, data.receiver_entity_id)
        entry.async_on_unload(sync.async_start())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: Acp35ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
