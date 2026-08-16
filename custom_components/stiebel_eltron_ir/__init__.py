"""Control Stiebel Eltron appliances over infrared.

The protocol carries no acknowledgement and the appliances report nothing back,
so this integration keeps a shadow copy of what it believes an appliance's state
to be and transmits that whole state on every change. Entities are therefore
``assumed_state``.

Which appliance an entry drives is recorded in its data and resolved here; see
`models.py`. Everything model-specific lives under `devices/`.
"""

from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import CONF_EMITTER, CONF_MODEL, CONF_RECEIVER
from .data import Acp35ConfigEntry, Acp35Data
from .models import MODELS
from .receiver import Acp35ReceiverSync


async def async_setup_entry(hass: HomeAssistant, entry: Acp35ConfigEntry) -> bool:
    """Set up one appliance from a config entry."""
    model = entry.data.get(CONF_MODEL)
    if (info := MODELS.get(model)) is None:
        # Fails this entry rather than the integration, so the other entries on
        # the same emitter still load. Reached by an entry written before the
        # model was recorded, or by one naming a model this build has dropped.
        raise ConfigEntryError(f"Unsupported model {model!r}")

    data = entry.runtime_data = Acp35Data(
        emitter_entity_id=entry.data[CONF_EMITTER],
        receiver_entity_id=entry.data.get(CONF_RECEIVER),
        platforms=info.platforms,
        model=info.model,
    )
    # Seed the unit the air conditioner displays from this Home Assistant
    # install's own unit, which is the closest thing to the user's intent we can
    # know without asking. A restore or a frame from the remote overrides it.
    data.state.display_celsius = (
        hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS
    )
    await hass.config_entries.async_forward_entry_setups(entry, data.platforms)

    # Optional. With no receiver configured the integration is complete as it
    # stands; it just cannot notice the physical remote being used.
    if data.receiver_entity_id is not None:
        sync = Acp35ReceiverSync(hass, data, data.receiver_entity_id)
        entry.async_on_unload(sync.async_start())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: Acp35ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )
