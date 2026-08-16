"""The select platform.

Home Assistant looks for a module named for the platform at the root of the
integration, so this exists to satisfy that and holds no model knowledge. What
gets added comes from the entry's model record; see `models.py`.
"""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_MODEL
from .data import StiebelEltronIrConfigEntry
from .models import MODELS

# As for climate -- selecting a display unit transmits the full state too.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StiebelEltronIrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add this model's select entities."""
    entities = MODELS[entry.data[CONF_MODEL]].entities
    async_add_entities(cls(entry) for cls in entities.get(Platform.SELECT, ()))
