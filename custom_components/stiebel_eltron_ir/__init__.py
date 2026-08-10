"""Control a Stiebel Eltron ACP 35 air conditioner over infrared.

The protocol carries no acknowledgement and the unit reports nothing back, so
this integration keeps a shadow copy of what it believes the unit's state to be
and transmits that whole state on every change. Entities are therefore
``assumed_state``.

Both entities — the climate control and the timer — mutate the same shadow state
and each send the complete frame, so they share one :class:`Acp35Data` held on
the config entry.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .acp35 import Acp35Fan, Acp35Mode, celsius_to_fahrenheit
from .const import CONF_DISPLAY_CELSIUS, CONF_EMITTER, CONF_RECEIVER
from .receiver import Acp35ReceiverSync

PLATFORMS = [Platform.CLIMATE, Platform.NUMBER]

type Acp35ConfigEntry = ConfigEntry[Acp35Data]


@dataclass
class Acp35State:
    """What we believe the unit is currently doing.

    Defaults match the remote's own resting state: cooling, high fan, 22 °C.
    They are only used before the first restore.

    Both temperature fields are kept, rather than deriving one from the other on
    the way out, because the two mappings are not inverses: a frame from a
    remote displaying Fahrenheit can hold 63 °F with 17 °C, and re-deriving from
    17 °C would send 62 °F back and shift the unit by a degree.
    """

    power: bool = False
    mode: Acp35Mode = Acp35Mode.COOL
    fan: Acp35Fan = Acp35Fan.HIGH
    celsius: int = 22
    fahrenheit: int = 72
    timer_hours: int = 0

    def set_celsius(self, celsius: int) -> None:
        """Set the temperature from Celsius, repairing the Fahrenheit field."""
        self.celsius = celsius
        self.fahrenheit = celsius_to_fahrenheit(celsius)


@dataclass
class Acp35Data:
    """Everything the platforms share for one configured air conditioner."""

    emitter_entity_id: str
    receiver_entity_id: str | None
    display_celsius: bool
    state: Acp35State = field(default_factory=Acp35State)
    _listeners: list[CALLBACK_TYPE] = field(default_factory=list)

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
        display_celsius=entry.data.get(CONF_DISPLAY_CELSIUS, True),
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
