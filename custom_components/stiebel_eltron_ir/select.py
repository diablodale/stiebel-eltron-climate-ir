"""Display-unit entity for the Stiebel Eltron ACP 35.

The air conditioner shows a temperature on its own display, in whichever unit
the remote's C/F button last selected, and `b7` bit 7 carries that choice in
every frame. It is the one bit of `b7` that is state rather than a per-press
event.

This is deliberately an entity rather than a config-flow field. It is not a
preference we hold about the unit, it is something the unit is doing: pressing
C/F on the physical remote changes it, and with a receiver configured we see
that and follow. A configured value would be overwritten by the first frame
that arrived and come back on the next restart, so the two would disagree.

It changes nothing Home Assistant displays. The climate entity reports Celsius
and the frontend converts to the user's own unit; this only affects the digits
on the air conditioner.
"""

from typing import override

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Acp35ConfigEntry
from .entity import Acp35Entity

PARALLEL_UPDATES = 1

CELSIUS = "celsius"
FAHRENHEIT = "fahrenheit"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Acp35ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the display-unit entity."""
    async_add_entities([Acp35DisplayUnitSelect(entry)])


class Acp35DisplayUnitSelect(Acp35Entity, SelectEntity):
    """Which unit the air conditioner shows on its own display."""

    _attr_translation_key = "appliance_temperature_unit"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [CELSIUS, FAHRENHEIT]

    def __init__(self, entry: Acp35ConfigEntry) -> None:
        """Set up the entity."""
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_display_unit"

    @property
    @override
    def current_option(self) -> str:
        """Return the unit currently selected."""
        return CELSIUS if self._data.state.display_celsius else FAHRENHEIT

    @override
    async def async_select_option(self, option: str) -> None:
        """Switch the display unit, as the remote's C/F button does.

        No event bit. The remote sends `b7` = 0x80 for a C/F press, the same as
        for fan and mode, so the unit bit changing is the whole of the message.
        """
        self._data.state.display_celsius = option == CELSIUS
        await self._async_transmit()
