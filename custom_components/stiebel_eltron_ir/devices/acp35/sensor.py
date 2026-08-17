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

**Anything changed in Home Assistant cancels a timer set on the remote.** Our
frames carry `timer_hours=0`, so the first one sent after the remote armed a
timer clears it. The handset does not hear that frame and goes on displaying the
timer it set, so the two disagree until the remote is used again.

That is a deliberate cost, chosen against the alternative. Replaying the value we
last heard is what the remote does, but the remote can do it correctly because it
counts down internally; we cannot see expiry at all, so replaying produces
something the remote never produces -- an expired timer re-armed, switching the
appliance off at a time nobody asked for. Cancelling loses a timer the user set.
Re-arming shuts the appliance down unbidden, for as long as nobody notices.
"""

from typing import override

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory, UnitOfTime

from ...data import StiebelEltronIrConfigEntry
from .entity import Acp35Entity


class Acp35TimerSensor(Acp35Entity, SensorEntity):
    """The hour count in `b2`, as last known. Not a countdown.

    It moves on two events, and only those: a frame heard from the remote, which
    says what timer the remote just set, and a frame of our own, which carries no
    timer and therefore cancels whatever was set. Between them the value stands
    still while the appliance counts down behind it.

    So a reading is only as good as its age, and the entity's own
    `last_reported` is what tells you that: it advances every time a frame
    confirms the count, while `last_changed` marks when the count last moved.
    A value hours old is a value about an appliance that has been counting down
    for hours.

    Zero means no timer was in the last frame we know about, which is not the
    same as no timer running: one set on the remote out of the receiver's range
    is invisible here.
    """

    _attr_translation_key = "last_known_timer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfTime.HOURS

    def __init__(self, entry: StiebelEltronIrConfigEntry) -> None:
        """Set up the entity."""
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_timer_hours"

    @property
    @override
    def native_value(self) -> int:
        """Return the hour count from the last frame we know about."""
        return self._data.state.timer_hours
