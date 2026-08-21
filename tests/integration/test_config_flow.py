"""Config flow, including that the receiver really is optional."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stiebel_eltron_ir.const import (
    CONF_EMITTER,
    CONF_MODEL,
    CONF_RECEIVER,
    DOMAIN,
    MODEL_ACP35,
)

from .conftest import CLIMATE_ID, EMITTER_ID


async def start(hass: HomeAssistant):
    """Begin the user flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


class TestUserFlow:
    """Creating an entry."""

    async def test_form_is_shown(self, hass: HomeAssistant, emitter: str) -> None:
        result = await start(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

    async def test_emitter_only_creates_an_entry(
        self, hass: HomeAssistant, emitter: str
    ) -> None:
        result = await hass.config_entries.flow.async_configure(
            (await start(hass))["flow_id"], {CONF_EMITTER: emitter}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_EMITTER] == emitter
        assert CONF_RECEIVER not in result["data"]

    async def test_receiver_is_stored_when_given(
        self, hass: HomeAssistant, emitter: str
    ) -> None:
        result = await hass.config_entries.flow.async_configure(
            (await start(hass))["flow_id"],
            {CONF_EMITTER: emitter, CONF_RECEIVER: "infrared.test_receiver"},
        )
        assert result["data"][CONF_RECEIVER] == "infrared.test_receiver"

    async def test_the_display_unit_is_not_asked_for(
        self, hass: HomeAssistant, emitter: str
    ) -> None:
        """It is an entity, not configuration.

        The remote's C/F button changes it and a receiver follows that, so a
        value stored in the config entry would be overwritten by the first frame
        that arrived and then come back on the next restart.
        """
        result = await hass.config_entries.flow.async_configure(
            (await start(hass))["flow_id"],
            {CONF_EMITTER: emitter},
        )
        assert "display_celsius" not in result["data"]

    async def test_name_becomes_the_title(
        self, hass: HomeAssistant, emitter: str
    ) -> None:
        result = await hass.config_entries.flow.async_configure(
            (await start(hass))["flow_id"],
            {CONF_EMITTER: emitter, "name": "Bedroom AC"},
        )
        assert result["title"] == "Bedroom AC"
        assert "name" not in result["data"], "the name is the title, not config"


class TestOneEntryPerEmitterAndModel:
    """An emitter is claimed for one model, not outright.

    The frame carries no device address, so two appliances of the same model
    hear one emitter identically and cannot be driven apart. Two different
    models sharing one blaster is fine: each decoder rejects the other's frames.
    """

    async def test_the_unique_id_names_both(
        self, hass: HomeAssistant, emitter: str
    ) -> None:
        result = await hass.config_entries.flow.async_configure(
            (await start(hass))["flow_id"], {CONF_EMITTER: emitter}
        )
        entries = hass.config_entries.async_entries(DOMAIN)
        assert entries[0].unique_id == f"{emitter}_{MODEL_ACP35}"
        assert result["type"] is FlowResultType.CREATE_ENTRY

    async def test_the_same_emitter_and_model_aborts(
        self, hass: HomeAssistant, entry, emitter: str
    ) -> None:
        result = await hass.config_entries.flow.async_configure(
            (await start(hass))["flow_id"], {CONF_EMITTER: emitter}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"

    async def test_another_model_on_the_same_emitter_is_allowed(
        self, hass: HomeAssistant, emitter: str
    ) -> None:
        """Stands in for the second model, which does not exist yet.

        An entry holding the emitter under a different model must not block this
        one, which is the whole reason the model is in the key.
        """
        MockConfigEntry(
            domain=DOMAIN,
            data={CONF_EMITTER: emitter, CONF_MODEL: "wpl-15"},
            unique_id=f"{emitter}_wpl-15",
        ).add_to_hass(hass)

        result = await hass.config_entries.flow.async_configure(
            (await start(hass))["flow_id"], {CONF_EMITTER: emitter}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY


class TestTheModelIsRecorded:
    """Which appliance an entry drives is stored, even with nothing to choose.

    The flow asks no question -- there is one model -- but the field is written
    now so every entry already carries it by the time a second model turns it
    into a question.
    """

    async def test_the_model_lands_in_entry_data(
        self, hass: HomeAssistant, emitter: str
    ) -> None:
        result = await hass.config_entries.flow.async_configure(
            (await start(hass))["flow_id"], {CONF_EMITTER: emitter}
        )
        assert result["data"][CONF_MODEL] == MODEL_ACP35

    async def test_the_model_is_not_asked_for(
        self, hass: HomeAssistant, emitter: str
    ) -> None:
        """A one-item menu is a worse question than no question."""
        result = await start(hass)
        assert CONF_MODEL not in result["data_schema"].schema

    async def test_the_device_reports_the_model(
        self, hass: HomeAssistant, entry, device_registry: dr.DeviceRegistry
    ) -> None:
        device = device_registry.async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        )
        assert device.model == "ACP 35"
        assert device.manufacturer == "Stiebel Eltron"


class TestAnUnsupportedModel:
    """An entry naming a model this build does not have fails on its own."""

    @pytest.mark.parametrize("model", ["wpl-15", None])
    async def test_setup_fails_the_entry(
        self, hass: HomeAssistant, emitter: str, model: str | None
    ) -> None:
        data = {CONF_EMITTER: emitter}
        if model is not None:
            data[CONF_MODEL] = model
        config_entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=emitter)
        config_entry.add_to_hass(hass)

        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert config_entry.state is ConfigEntryState.SETUP_ERROR
        # Failing the entry, not the integration: nothing was half-created.
        assert hass.states.get(CLIMATE_ID) is None


class TestWithoutAReceiver:
    """Receiver sync is optional and its absence must change nothing."""

    async def test_both_entities_load(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        assert entry.data.get(CONF_RECEIVER) is None
        assert hass.states.get(CLIMATE_ID) is not None

    async def test_control_still_works(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON

        await hass.services.async_call(
            "climate",
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: CLIMATE_ID},
            blocking=True,
        )
        assert send_command.await_count == 1

    async def test_entities_are_available(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        assert hass.states.get(CLIMATE_ID).state != "unavailable"


class TestUnload:
    """Entries unload cleanly."""

    async def test_unload(self, hass: HomeAssistant, entry) -> None:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).state == "unavailable"


class TestEmitterAvailability:
    """The entities follow the emitter's availability."""

    async def test_entities_go_unavailable_with_the_emitter(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        hass.states.async_set(EMITTER_ID, "unavailable")
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).state == "unavailable"
