"""Timer read-out for the Stiebel Eltron ACP 35.

Diagnostic and read-only, disabled by default. It reports the hour count carried
in `b2` of the last frame we know about, and nothing more.

Setting the timer from Home Assistant was removed rather than fixed. Three
reasons, and the first two are not implementation gaps:

- **Nothing can clear it.** The appliance acting on its own timer is not a button
  press and emits no infrared, so expiry is invisible to us. A stored value would
  be replayed on every later frame and silently re-arm a timer that had already
  fired.
- **`b2` holds whole hours.** Any frame sent while a timer runs has to round, so
  touching anything else moves the expiry by up to half an hour. A full-state
  protocol cannot leave a running timer alone.
- **The appliance's own panel offers more than the remote can send.** Its timer
  also sets the mode and fan to use at power-on, and pressing buttons on the
  appliance emits no infrared at all, so that has no protocol representation. A
  Home Assistant automation reproduces it exactly -- our frame is full-state, so
  firing power, mode and fan together at the scheduled moment is the same thing
  -- and keeps accurate time while doing it.

What a timer set on the physical remote does to our frames is a separate,
unsettled question; see `Acp35State.timer_hours`.
"""

from typing import override

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory, UnitOfTime

from ...data import Acp35ConfigEntry
from .entity import Acp35Entity


class Acp35TimerSensor(Acp35Entity, SensorEntity):
    """The hour count in `b2`, as last seen. Not a countdown.

    Zero means no timer was in the last frame, which is not the same as no timer
    running: if one was set on the remote out of the receiver's range, or has
    since expired, this cannot know.
    """

    _attr_translation_key = "timer_hours"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfTime.HOURS

    def __init__(self, entry: Acp35ConfigEntry) -> None:
        """Set up the entity."""
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_timer_hours"

    @property
    @override
    def native_value(self) -> int:
        """Return the hour count from the last frame we know about."""
        return self._data.state.timer_hours
