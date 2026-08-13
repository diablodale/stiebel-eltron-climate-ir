"""Display-unit entity behaviour.

`b7` bit 7 is the unit the air conditioner shows on its own display. It is the
only bit of `b7` that is state rather than a per-press event, which is why it is
an entity here and not a config-flow field: the remote's C/F button moves it, and
with a receiver configured we follow.
"""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.select import (
    ATTR_OPTION,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import (
    METRIC_SYSTEM,
    US_CUSTOMARY_SYSTEM,
    UnitSystem,
)

from custom_components.stiebel_eltron_ir.acp35 import Acp35Flag

from .conftest import build_entry, last_command

UNIT_ID = "select.stiebel_eltron_acp_35_appliance_temperature_unit"


async def select(hass: HomeAssistant, option: str) -> None:
    """Choose a display unit."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: UNIT_ID, ATTR_OPTION: option},
        blocking=True,
    )


class TestSetup:
    """The entity exists and offers exactly the two units."""

    async def test_entity_is_created(self, hass: HomeAssistant, entry) -> None:
        assert hass.states.get(UNIT_ID) is not None

    async def test_options_are_the_two_units(self, hass: HomeAssistant, entry) -> None:
        assert hass.states.get(UNIT_ID).attributes["options"] == [
            "celsius",
            "fahrenheit",
        ]


class TestInitialValue:
    """It starts on whatever unit this Home Assistant install uses."""

    @pytest.mark.parametrize(
        ("units", "expected"),
        [(METRIC_SYSTEM, "celsius"), (US_CUSTOMARY_SYSTEM, "fahrenheit")],
    )
    async def test_seeded_from_home_assistant(
        self,
        hass: HomeAssistant,
        emitter: str,
        send_command: AsyncMock,
        units: UnitSystem,
        expected: str,
    ) -> None:
        hass.config.units = units
        await build_entry(hass, emitter)
        assert hass.states.get(UNIT_ID).state == expected


class TestTransmission:
    """Selecting a unit sets b7 bit 7 and nothing else."""

    async def test_celsius_sets_the_bit(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await select(hass, "celsius")
        command = last_command(send_command)
        assert command.is_celsius is True
        assert command.to_bytes()[7] & 0x80

    async def test_fahrenheit_clears_the_bit(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await select(hass, "fahrenheit")
        command = last_command(send_command)
        assert command.is_celsius is False
        assert not command.to_bytes()[7] & 0x80

    async def test_no_event_bit_is_sent(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """The remote sends b7 = 0x80 for a C/F press, as it does for fan."""
        await select(hass, "celsius")
        assert last_command(send_command).flags == Acp35Flag.CELSIUS

    async def test_the_rest_of_the_frame_is_unchanged(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Only the unit bit moves; a C/F press is not a state change."""
        await select(hass, "celsius")
        before = last_command(send_command).to_bytes()
        await select(hass, "fahrenheit")
        after = last_command(send_command).to_bytes()

        assert before[:7] == after[:7], "b0..b6 must be identical"
        assert before[7] ^ after[7] == 0x80, "only bit 7 differs"


class TestSharedState:
    """One value, so the select and the transmitted frame cannot disagree."""

    async def test_a_climate_change_keeps_the_unit(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        from homeassistant.components.climate import (
            ATTR_TEMPERATURE,
            SERVICE_SET_TEMPERATURE,
        )
        from homeassistant.components.climate import (
            DOMAIN as CLIMATE_DOMAIN,
        )

        from .conftest import CLIMATE_ID

        await select(hass, "fahrenheit")
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 25},
            blocking=True,
        )
        assert last_command(send_command).is_celsius is False
        assert hass.states.get(UNIT_ID).state == "fahrenheit"

    async def test_it_survives_a_restart(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await select(hass, "fahrenheit")
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(UNIT_ID).state == "fahrenheit"
