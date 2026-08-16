"""The sensor platform.

Home Assistant looks for a module named for the platform at the root of the
integration, so this exists to satisfy that and holds no model knowledge. What
gets added comes from the entry's model record; see `models.py`.
"""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_MODEL
from .data import Acp35ConfigEntry
from .models import MODELS

# Read-only. It never transmits, so there is nothing to serialise.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Acp35ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add this model's sensor entities."""
    entities = MODELS[entry.data[CONF_MODEL]].entities
    async_add_entities(cls(entry) for cls in entities.get(Platform.SENSOR, ()))
