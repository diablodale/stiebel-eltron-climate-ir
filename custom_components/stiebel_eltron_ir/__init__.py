"""Control Stiebel Eltron appliances over infrared.

The protocol carries no acknowledgement and the appliances report nothing back,
so this integration keeps a shadow copy of what it believes an appliance's state
to be and transmits that whole state on every change. Entities are therefore
``assumed_state``.

That shadow state belongs to the config entry rather than to any one entity, so
it is loaded here, once, before the platforms are forwarded and before any entity
exists. See `storage.py`.

Which appliance an entry drives is recorded in its data and resolved here; see
`models.py`. Everything model-specific lives under `devices/`.
"""

from functools import partial

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, UnsupportedStorageVersionError

from .const import CONF_EMITTER, CONF_MODEL, CONF_RECEIVER
from .data import ShadowState, StiebelEltronIrConfigEntry, StiebelEltronIrData
from .models import MODELS, ModelInfo
from .receiver import StiebelEltronIrReceiverSync
from .storage import StiebelEltronIrStore


async def async_setup_entry(
    hass: HomeAssistant, entry: StiebelEltronIrConfigEntry
) -> bool:
    """Set up one appliance from a config entry."""
    model = entry.data.get(CONF_MODEL)
    if (info := MODELS.get(model)) is None:
        # Fails this entry rather than the integration, so the other entries on
        # the same emitter still load. Reached by an entry written before the
        # model was recorded, or by one naming a model this build has dropped.
        raise ConfigEntryError(f"Unsupported model {model!r}")

    store = StiebelEltronIrStore(hass, entry.entry_id, info.storage_version)
    state = await _async_load_state(store, info, hass)

    data = entry.runtime_data = StiebelEltronIrData(
        emitter_entity_id=entry.data[CONF_EMITTER],
        receiver_entity_id=entry.data.get(CONF_RECEIVER),
        platforms=info.platforms,
        model=info.model,
        state=state,
    )
    data.store = store
    data.snapshot = lambda: info.stored_state.from_state(data.state).as_dict()

    await hass.config_entries.async_forward_entry_setups(entry, data.platforms)

    # Optional. With no receiver configured the integration is complete as it
    # stands; it just cannot notice the physical remote being used.
    if data.receiver_entity_id is not None:
        sync = StiebelEltronIrReceiverSync(
            hass, data.receiver_entity_id, partial(info.handle_signal, data)
        )
        entry.async_on_unload(sync.async_start())

    return True


async def _async_load_state(
    store: StiebelEltronIrStore, info: ModelInfo, hass: HomeAssistant
) -> ShadowState:
    """Return the state to start from, stored or fresh.

    Storage this build cannot read fails the entry rather than being replaced.
    That is not fastidiousness: `Store.async_load` writes back after migrating,
    so a build that shrugged and started from defaults would overwrite the file
    on its first save. Somebody who downgraded, or restored an older backup,
    would be quietly reset and would recover nothing by upgrading again. Failing
    the entry loads no platforms, so nothing is left that could write.

    Re-raised as `ConfigEntryError` only so the reason reaches the entry card;
    both exceptions already fail the entry, but the generic handler shows no
    message and this one shows which file and which version.
    """
    try:
        stored = await store.async_load()
    except (UnsupportedStorageVersionError, NotImplementedError) as error:
        # NotImplementedError is what our own _async_migrate_func raises for a
        # version it has no conversion for; Store re-raises it when the major
        # version differs.
        raise ConfigEntryError(str(error)) from error

    state = info.new_state(hass)
    # A payload the model refuses leaves the fresh state alone, and says so in
    # the log. See `from_dict` for what counts as unusable and why.
    if stored is not None and (restored := info.stored_state.from_dict(stored)):
        restored.apply(state)
    return state


async def async_unload_entry(
    hass: HomeAssistant, entry: StiebelEltronIrConfigEntry
) -> bool:
    """Unload a config entry, writing out anything still pending.

    `Store` flushes a delayed save when Home Assistant stops, but a reload is not
    a stop: it unloads and sets up again while the process keeps running. Without
    this, a change made inside the delay window would be lost on reload.
    """
    data = entry.runtime_data
    if data.store is not None and data.snapshot is not None:
        await data.store.async_save(data.snapshot())
    return await hass.config_entries.async_unload_platforms(entry, data.platforms)


async def async_remove_entry(
    hass: HomeAssistant, entry: StiebelEltronIrConfigEntry
) -> None:
    """Delete the stored state when the entry is removed.

    Nothing else will: the file is keyed by entry id, and that id is gone.
    """
    model = entry.data.get(CONF_MODEL)
    if (info := MODELS.get(model)) is None:
        return
    await StiebelEltronIrStore(
        hass, entry.entry_id, info.storage_version
    ).async_remove()
