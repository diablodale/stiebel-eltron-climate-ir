"""Config flow, including that the receiver really is optional."""

from unittest.mock import AsyncMock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.stiebel_eltron_ir.const import (
    CONF_EMITTER,
    CONF_RECEIVER,
    DOMAIN,
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

    async def test_one_entry_per_emitter(
        self, hass: HomeAssistant, entry, emitter: str
    ) -> None:
        # Two entries on one emitter would fight over the shadow state.
        result = await hass.config_entries.flow.async_configure(
            (await start(hass))["flow_id"], {CONF_EMITTER: emitter}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


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
