"""Where the shadow state is kept, and what happens when it cannot be read.

The state belongs to the config entry, not to any of its entities, so there is
one store file keyed by entry id. The mechanism this replaced was `RestoreEntity`,
which is keyed by *entity* id and therefore held one copy per entity: they could
disagree, and renaming or disabling an entity orphaned its copy.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.stiebel_eltron_ir.const import DOMAIN, MODEL_ACP35
from custom_components.stiebel_eltron_ir.devices.acp35.climate import Acp35Climate
from custom_components.stiebel_eltron_ir.devices.acp35.protocol import (
    DEFAULT_CELSIUS,
    Acp35Fan,
)
from custom_components.stiebel_eltron_ir.devices.acp35.select import (
    Acp35DisplayUnitSelect,
)
from custom_components.stiebel_eltron_ir.devices.acp35.sensor import Acp35TimerSensor
from custom_components.stiebel_eltron_ir.devices.acp35.state import (
    Acp35RestoreData,
    Acp35State,
)
from custom_components.stiebel_eltron_ir.models import MODELS

from .conftest import CLIMATE_ID, TIMER_ID, build_entry, last_command

UNIT_ID = "select.stiebel_eltron_acp_35_appliance_temperature_unit"


def store_key(entry) -> str:
    """Return the storage key for an entry, which is keyed on its id."""
    return f"{DOMAIN}.{entry.entry_id}"


def stored_file(hass_storage: dict[str, Any], entry) -> dict[str, Any] | None:
    """Return the whole store file, version envelope included."""
    return hass_storage.get(store_key(entry))


def seed(hass_storage: dict[str, Any], entry_id: str, **envelope: Any) -> None:
    """Put a payload in storage as though a previous run had written it."""
    payload = Acp35RestoreData.from_state(Acp35State()).as_dict()
    payload |= envelope.pop("data", {})
    hass_storage[f"{DOMAIN}.{entry_id}"] = {
        "version": envelope.pop("version", 1),
        "minor_version": envelope.pop("minor_version", 1),
        "key": f"{DOMAIN}.{entry_id}",
        "data": payload,
    }


class TestThereIsOneCopy:
    """The bug this replaced: one copy per entity, able to disagree."""

    async def test_the_state_is_stored_once_under_the_entry_id(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, hass_storage
    ) -> None:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 27},
            blocking=True,
        )
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        ours = [key for key in hass_storage if key.startswith(DOMAIN)]
        assert ours == [store_key(entry)], "one file, keyed by the entry"
        assert stored_file(hass_storage, entry)["data"]["celsius"] == 27

    async def test_no_entity_persists_anything(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, hass_storage
    ) -> None:
        """Nothing of ours may reach the entity-keyed restore store.

        That store is what held the duplicates: the climate entity persisted the
        display unit, which is the select's, and the select persisted the
        temperature and fan, which are the climate's.
        """
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        # Asserted at the class as well as at the file, so this cannot pass
        # merely because the restore store happened to be empty.
        for cls in (Acp35Climate, Acp35DisplayUnitSelect, Acp35TimerSensor):
            assert RestoreEntity not in cls.__mro__, cls.__name__

        restored = hass_storage.get("core.restore_state", {}).get("data", [])
        ours = [
            item
            for item in restored
            if item["state"]["entity_id"] in (CLIMATE_ID, UNIT_ID, TIMER_ID)
            and item.get("extra_data") is not None
        ]
        assert ours == []


class TestItSurvives:
    """What the store is for."""

    async def test_a_reload_keeps_the_state(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """The flush on unload is what makes this true inside the save delay."""
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_FAN_MODE: "low"},
            blocking=True,
        )
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get(CLIMATE_ID).attributes[ATTR_FAN_MODE] == "low"

    async def test_renaming_an_entity_keeps_the_state(
        self,
        hass: HomeAssistant,
        entry,
        send_command: AsyncMock,
        entity_registry: er.EntityRegistry,
    ) -> None:
        """Impossible under the entity-keyed mechanism, which keyed on this.

        Renaming changed the restore key and orphaned the copy, so the state came
        back as defaults. The store is keyed by the entry, which a rename does
        not touch.
        """
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 29},
            blocking=True,
        )
        renamed = "climate.bedroom_ac"
        entity_registry.async_update_entity(CLIMATE_ID, new_entity_id=renamed)
        await hass.async_block_till_done()

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get(renamed).attributes[ATTR_TEMPERATURE] == 29

    async def test_the_timer_read_out_deliberately_does_not(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, hass_storage
    ) -> None:
        """It reports a frame, and a frame goes stale while the process is down.

        The appliance counts a timer down without saying so, so an hour count
        written before a restart says nothing about the appliance afterwards.
        Restoring one would report a timer as current on the strength of a frame
        heard days earlier.
        """
        entry.runtime_data.state.timer_hours = 7
        entry.runtime_data.async_notify()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert "timer_hours" not in stored_file(hass_storage, entry)["data"]

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data.state.timer_hours == 0

    async def test_removing_the_entry_deletes_the_file(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, hass_storage
    ) -> None:
        """Nothing else would: the key is the entry id, and it is gone."""
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        assert store_key(entry) not in hass_storage


class TestTheStartingState:
    """What a first setup looks like, and what a stored one overrides."""

    async def test_the_display_unit_is_seeded_from_the_profile(
        self, hass: HomeAssistant, emitter: str, send_command: AsyncMock
    ) -> None:
        hass.config.units = US_CUSTOMARY_SYSTEM
        await build_entry(hass, emitter)
        assert hass.states.get(UNIT_ID).state == "fahrenheit"

    async def test_a_stored_unit_is_not_overridden_by_the_profile(
        self, hass: HomeAssistant, emitter: str, send_command: AsyncMock, hass_storage
    ) -> None:
        """Seeding runs only when there is nothing stored."""
        entry = await build_entry(hass, emitter)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        hass_storage[store_key(entry)]["data"]["display_celsius"] = False

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(UNIT_ID).state == "fahrenheit"


class TestSchemaVersions:
    """A file this build cannot read must never be quietly replaced."""

    async def test_the_model_supplies_the_version(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, hass_storage
    ) -> None:
        """Versioned per model, so one model's history cannot move another's."""
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert (
            stored_file(hass_storage, entry)["version"]
            == MODELS[MODEL_ACP35].storage_version
        )

    async def test_a_newer_version_fails_the_entry_and_keeps_the_file(
        self, hass: HomeAssistant, emitter: str, send_command: AsyncMock, hass_storage
    ) -> None:
        """The file must survive, which is the whole reason this fails.

        Store writes back after migrating, so a build that started from defaults
        here would overwrite a newer file on its first save. Someone who
        downgraded, or restored an old backup, would be reset and would recover
        nothing by upgrading again.
        """
        from tests.common import MockConfigEntry

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Stiebel Eltron ACP 35",
            data={"emitter": emitter, "model": MODEL_ACP35},
            unique_id=f"{emitter}_{MODEL_ACP35}",
        )
        config_entry.add_to_hass(hass)
        seed(hass_storage, config_entry.entry_id, version=99, data={"celsius": 19})
        before = dict(hass_storage[store_key(config_entry)])

        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        assert config_entry.state is ConfigEntryState.SETUP_ERROR
        assert hass_storage[store_key(config_entry)] == before, "the file was rewritten"
        assert hass.states.get(CLIMATE_ID) is None

    async def test_an_older_major_version_fails_rather_than_defaulting(
        self, hass: HomeAssistant, emitter: str, send_command: AsyncMock, hass_storage
    ) -> None:
        """No conversion is written, and unconverted is not the same as unreadable.

        Silently defaulting would destroy state a correct build could have read,
        and hide the missing conversion at the same time.
        """
        from tests.common import MockConfigEntry

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Stiebel Eltron ACP 35",
            data={"emitter": emitter, "model": MODEL_ACP35},
            unique_id=f"{emitter}_{MODEL_ACP35}",
        )
        config_entry.add_to_hass(hass)
        seed(hass_storage, config_entry.entry_id, version=0)

        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert config_entry.state is ConfigEntryState.SETUP_ERROR

    async def test_an_older_minor_version_loads_unchanged(
        self, hass: HomeAssistant, emitter: str, send_command: AsyncMock, hass_storage
    ) -> None:
        """Minor bumps are backward compatible by Store's own contract."""
        from tests.common import MockConfigEntry

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Stiebel Eltron ACP 35",
            data={"emitter": emitter, "model": MODEL_ACP35},
            unique_id=f"{emitter}_{MODEL_ACP35}",
        )
        config_entry.add_to_hass(hass)
        seed(
            hass_storage,
            config_entry.entry_id,
            minor_version=0,
            data={"celsius": 26, "power": True},
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE] == 26


class TestARefusedPayload:
    """Refused is not the same as failed: control has to keep working."""

    async def test_it_starts_from_defaults_without_failing_the_entry(
        self, hass: HomeAssistant, emitter: str, send_command: AsyncMock, hass_storage
    ) -> None:
        """Raising here would leave the appliance with no entities at all."""
        from tests.common import MockConfigEntry

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Stiebel Eltron ACP 35",
            data={"emitter": emitter, "model": MODEL_ACP35},
            unique_id=f"{emitter}_{MODEL_ACP35}",
        )
        config_entry.add_to_hass(hass)
        seed(hass_storage, config_entry.entry_id, data={"celsius": 99})

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        assert config_entry.state is ConfigEntryState.LOADED
        assert hass.states.get(CLIMATE_ID) is not None

        # Defaults throughout, not the 99 that was refused, and not a mixture.
        await hass.services.async_call(
            CLIMATE_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: CLIMATE_ID}, blocking=True
        )
        command = last_command(send_command)
        assert command.celsius == DEFAULT_CELSIUS
        assert command.fan is Acp35Fan.HIGH


@pytest.mark.usefixtures("send_command")
class TestSaving:
    """Every change reaches the store, whichever direction it came from."""

    async def test_a_service_call_is_saved(
        self, hass: HomeAssistant, entry, hass_storage
    ) -> None:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 18},
            blocking=True,
        )
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert stored_file(hass_storage, entry)["data"]["celsius"] == 18
