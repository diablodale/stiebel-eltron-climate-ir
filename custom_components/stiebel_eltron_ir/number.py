"""Timer entity for the Stiebel Eltron ACP 35.

The timer runs in both directions. With the unit running it is an off-delay; with
the unit stopped it is an on-delay, and it is one of only two buttons the remote
answers in that state. One control covers both, since which one it is follows the
power state rather than anything the user selects.

The protocol holds it in two fields: b2 counts the hours and b1 bit 3 says the
pending timer will switch the unit off. The second is not "a timer is set" -- a
capture taken with the unit off and three hours pending has b2 = 3 and the bit
clear -- so ``Acp35Command`` derives it from the power state and this entity only
sets the hours. The remote emits the bit at zero hours whenever its entry display
opens, and again if the hours are wound back down to zero, but that is a display
state rather than something worth exposing, so 0 here means off.

Zero disarms rather than sending armed-with-zero. The remote has two ways to
cancel and they disagree: pressing TIMER twice clears b1 bit 3, while winding the
hours down to zero leaves it set. The disarming form is the unambiguous one.
"""

from typing import override

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Acp35ConfigEntry
from .acp35 import MAX_TIMER_HOURS, Acp35Flag
from .entity import Acp35Entity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Acp35ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the timer entity."""
    async_add_entities([Acp35TimerNumber(entry)])


class Acp35TimerNumber(Acp35Entity, NumberEntity):
    """Hours until the unit switches itself over. Zero cancels the timer.

    Which way it switches follows the power state: off if the unit is running,
    on if it is stopped.
    """

    _attr_translation_key = "timer_hours"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_TIMER_HOURS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.HOURS

    def __init__(self, entry: Acp35ConfigEntry) -> None:
        """Set up the entity."""
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_timer_hours"

    @property
    @override
    def native_value(self) -> float:
        """Return the hours currently set."""
        return self._data.state.timer_hours

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set the timer to ``value`` hours, or cancel it at zero.

        Setting sends ``TIMER_UI`` because every frame the remote emits carrying
        a new hour count has it set, with the unit running and stopped alike.
        There is no separate acceptance frame: the last frame sent while the
        entry display is open is what commits the value, so a frame without the
        bit may not register as a timer at all.

        Cancelling does not, because it reproduces the remote's TIMER-then-TIMER
        cancel: b1 bit 3 clear, b2 zero, no event bit. Winding the hours down to
        zero instead leaves the remote holding the bit at zero hours with
        ``TIMER_UI`` set, which is a second, ambiguous way to say the same thing.
        Sending the flag here would produce a frame matching neither.
        """
        self._data.state.timer_hours = min(MAX_TIMER_HOURS, max(0, round(value)))
        # b1 bit 3 follows the power state, not the hours; Acp35Command derives it.
        event = Acp35Flag.TIMER_UI if self._data.state.timer_hours else Acp35Flag.NONE
        await self._async_transmit(event)
