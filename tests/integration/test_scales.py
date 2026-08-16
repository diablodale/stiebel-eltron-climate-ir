"""The climate entity drives on the Home Assistant profile's scale.

The appliance has two scales, not one value shown two ways: 17..30 C is fourteen
steps and 62..86 F is twenty-five, and the two conversion tables are not
inverses. One of them has to be the scale the user drives on.

It is the profile's. Home Assistant converts min, max and target from the
entity's unit into the profile's unit, so matching them means no conversion and
the card holds the number the user picked. Reporting the appliance's scale
instead put a round trip in the way: 22 C converts to 71.6 F, which the protocol
cannot carry, so it shipped 72 and the card came back reading 22.2.
"""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import (
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TEMPERATURE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.stiebel_eltron_ir.devices.acp35.protocol import (
    MAX_CELSIUS,
    MAX_FAHRENHEIT,
    MIN_CELSIUS,
    MIN_FAHRENHEIT,
)

from .conftest import CLIMATE_ID, last_command
from .test_select import UNIT_ID, select


async def set_temperature(hass: HomeAssistant, value: float) -> None:
    """Ask for a temperature, in the profile's unit."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: value},
        blocking=True,
    )


def card(hass: HomeAssistant) -> dict:
    """Return what the card shows, after Home Assistant's own conversion."""
    return hass.states.get(CLIMATE_ID).attributes


class TestTheScaleFollowsTheProfile:
    """Not the appliance: matching the profile is what removes the conversion."""

    async def test_metric_profile(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        hass.config.units = METRIC_SYSTEM
        entity = hass.data["climate"].get_entity(CLIMATE_ID)
        assert entity.temperature_unit is UnitOfTemperature.CELSIUS
        assert (entity.min_temp, entity.max_temp) == (MIN_CELSIUS, MAX_CELSIUS)

    async def test_us_customary_profile(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        hass.config.units = US_CUSTOMARY_SYSTEM
        entity = hass.data["climate"].get_entity(CLIMATE_ID)
        assert entity.temperature_unit is UnitOfTemperature.FAHRENHEIT
        assert (entity.min_temp, entity.max_temp) == (MIN_FAHRENHEIT, MAX_FAHRENHEIT)

    @pytest.mark.parametrize("appliance", ["celsius", "fahrenheit"])
    async def test_the_appliance_unit_does_not_move_the_scale(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, appliance: str
    ) -> None:
        """The select drives the appliance's panel, not what HA controls in."""
        hass.config.units = METRIC_SYSTEM
        await select(hass, appliance)
        entity = hass.data["climate"].get_entity(CLIMATE_ID)
        assert entity.temperature_unit is UnitOfTemperature.CELSIUS
        assert (entity.min_temp, entity.max_temp) == (MIN_CELSIUS, MAX_CELSIUS)


class TestTheCardHoldsTheNumber:
    """Reported: a metric profile against a Fahrenheit appliance drifted."""

    @pytest.mark.parametrize("appliance", ["celsius", "fahrenheit"])
    @pytest.mark.parametrize("wanted", [17, 20, 22, 30])
    async def test_celsius_is_shown_exactly_as_asked(
        self,
        hass: HomeAssistant,
        entry,
        send_command: AsyncMock,
        appliance: str,
        wanted: int,
    ) -> None:
        """22 used to come back as 22.2, and 17 as 17.2."""
        hass.config.units = METRIC_SYSTEM
        await select(hass, appliance)
        await set_temperature(hass, wanted)

        assert card(hass)[ATTR_TEMPERATURE] == wanted
        assert last_command(send_command).celsius == wanted

    async def test_the_bounds_are_whole_degrees(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """The slider bounced because its minimum was 62 F = 16.67 C."""
        hass.config.units = METRIC_SYSTEM
        await select(hass, "fahrenheit")
        attributes = card(hass)
        assert attributes[ATTR_MIN_TEMP] == MIN_CELSIUS
        assert attributes[ATTR_MAX_TEMP] == MAX_CELSIUS

    async def test_the_minimum_is_reachable_and_stays(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Stepping down to the bottom used to land on 16.7."""
        hass.config.units = METRIC_SYSTEM
        await select(hass, "fahrenheit")
        await set_temperature(hass, MIN_CELSIUS)

        assert card(hass)[ATTR_TEMPERATURE] == MIN_CELSIUS
        assert last_command(send_command).celsius == MIN_CELSIUS

    async def test_setting_the_same_value_twice_does_not_drift(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        hass.config.units = METRIC_SYSTEM
        await select(hass, "fahrenheit")
        for _ in range(3):
            await set_temperature(hass, 22)
            assert card(hass)[ATTR_TEMPERATURE] == 22


class TestChangingTheProfileUnit:
    """Reported: switching Home Assistant to Fahrenheit relabelled 19 C as 19 F.

    The scale is read from ``hass.config.units`` and nothing re-reads it. This
    entity is ``assumed_state`` and never polls, so without listening for the
    change the card keeps the old number under the new unit's name.
    """

    async def test_the_card_moves_to_the_new_scale(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await set_temperature(hass, 19)
        assert card(hass)[ATTR_TEMPERATURE] == 19

        await hass.config.async_update(unit_system="us_customary")
        await hass.async_block_till_done()

        attributes = card(hass)
        assert attributes[ATTR_TEMPERATURE] == 66, "19 C pairs to 66 F"
        assert attributes[ATTR_MIN_TEMP] == MIN_FAHRENHEIT
        assert attributes[ATTR_MAX_TEMP] == MAX_FAHRENHEIT

    async def test_nothing_is_transmitted(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """A profile change is about display. The appliance did not move."""
        await set_temperature(hass, 19)
        send_command.reset_mock()

        await hass.config.async_update(unit_system="us_customary")
        await hass.async_block_till_done()

        assert send_command.await_count == 0

    async def test_and_back_again(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await set_temperature(hass, 19)
        await hass.config.async_update(unit_system="us_customary")
        await hass.async_block_till_done()
        await hass.config.async_update(unit_system="metric")
        await hass.async_block_till_done()

        assert card(hass)[ATTR_TEMPERATURE] == 19


class TestEveryFahrenheitStepIsReachable:
    """A Fahrenheit user has a Fahrenheit profile, and gets all 25."""

    async def test_all_twenty_five_values_can_be_sent(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        hass.config.units = US_CUSTOMARY_SYSTEM
        sent = []
        for wanted in range(MIN_FAHRENHEIT, MAX_FAHRENHEIT + 1):
            await set_temperature(hass, wanted)
            sent.append(last_command(send_command).fahrenheit)
        assert sent == list(range(MIN_FAHRENHEIT, MAX_FAHRENHEIT + 1))

    @pytest.mark.parametrize("wanted", [63, 65, 67, 69, 71, 74, 76, 78, 80, 83, 85])
    async def test_the_previously_unreachable_values(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, wanted: int
    ) -> None:
        """These eleven were impossible while the state was Celsius-first."""
        hass.config.units = US_CUSTOMARY_SYSTEM
        await set_temperature(hass, wanted)
        assert last_command(send_command).fahrenheit == wanted
        assert card(hass)[ATTR_TEMPERATURE] == wanted

    async def test_the_fahrenheit_field_is_authoritative(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """63 F pairs to 17 C, but 17 C would pair back out to 62 F."""
        hass.config.units = US_CUSTOMARY_SYSTEM
        await set_temperature(hass, 63)

        command = last_command(send_command)
        assert command.fahrenheit == 63
        assert command.celsius == 17


class TestCelsiusPairing:
    """The derived field still follows the appliance's own tables."""

    async def test_seventeen_pairs_to_sixty_two(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """The one value where the two tables disagree."""
        hass.config.units = METRIC_SYSTEM
        await set_temperature(hass, 17)
        assert last_command(send_command).fahrenheit == 62

    async def test_switching_the_appliance_unit_keeps_the_temperature(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """A C/F press changes b7 bit 7 and nothing else, as the remote does."""
        hass.config.units = METRIC_SYSTEM
        await set_temperature(hass, 22)
        before = last_command(send_command).to_bytes()

        await select(hass, "fahrenheit")
        after = last_command(send_command).to_bytes()

        assert before[:7] == after[:7], "b0..b6 unchanged"
        assert card(hass)[ATTR_TEMPERATURE] == 22, "the card is unmoved too"
        assert hass.states.get(UNIT_ID).state == "fahrenheit"
