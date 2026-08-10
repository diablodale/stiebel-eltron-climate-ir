"""Shutdown-timer entity for the Stiebel Eltron ACP 35.

The protocol holds the timer in two independent fields: b1 bit 3 arms it and b2
counts the hours. The remote does emit armed-with-zero-hours while its entry UI
is open, but that is a transient UI state rather than something worth exposing,
so this collapses both into one control where 0 means off.
"""

from typing import override

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Acp35ConfigEntry
from .acp35 import MAX_TIMER_HOURS
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
    """Hours until the unit switches itself off. Zero disarms the timer."""

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

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the timer, since the unit cannot be asked for it."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or last.state in (None, "unknown", "unavailable"):
            return
        try:
            hours = int(float(last.state))
        except ValueError:
            return
        self._data.state.timer_hours = min(MAX_TIMER_HOURS, max(0, hours))

    @property
    @override
    def native_value(self) -> float:
        """Return the hours currently set."""
        return self._data.state.timer_hours

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Arm the timer for ``value`` hours, or disarm it at zero."""
        self._data.state.timer_hours = min(MAX_TIMER_HOURS, max(0, round(value)))
        # _build_command derives b1 bit 3 and the b7 timer-UI bit from the hours.
        await self._async_transmit()
