"""Shutdown-timer entity behaviour."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.stiebel_eltron_ir.acp35 import Acp35Flag

from .conftest import CLIMATE_ID, TIMER_ID, last_command


async def set_hours(hass: HomeAssistant, value: float) -> None:
    """Set the timer to ``value`` hours."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: TIMER_ID, ATTR_VALUE: value},
        blocking=True,
    )


class TestSetup:
    """The entity exists with the protocol's bounds."""

    async def test_entity_is_created(self, hass: HomeAssistant, entry) -> None:
        assert hass.states.get(TIMER_ID) is not None

    async def test_bounds_match_the_protocol(self, hass: HomeAssistant, entry) -> None:
        attributes = hass.states.get(TIMER_ID).attributes
        assert attributes["min"] == 0
        assert attributes["max"] == 24
        assert attributes["step"] == 1


class TestTransmission:
    """Setting hours arms b1 bit 3 and fills b2."""

    @pytest.mark.parametrize("hours", [1, 2, 12, 24])
    async def test_hours_reach_b2(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, hours
    ) -> None:
        await set_hours(hass, hours)
        command = last_command(send_command)
        assert command.timer_hours == hours
        assert command.to_bytes()[2] == hours

    async def test_non_zero_hours_arm_the_timer(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await set_hours(hass, 6)
        command = last_command(send_command)
        assert command.timer_armed is True
        assert command.to_bytes()[1] & 0x08

    async def test_zero_disarms_and_clears_the_hours(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await set_hours(hass, 6)
        await set_hours(hass, 0)
        command = last_command(send_command)
        assert command.timer_armed is False
        assert command.timer_hours == 0
        assert not command.to_bytes()[1] & 0x08
        assert command.to_bytes()[2] == 0

    async def test_arming_sets_the_timer_ui_flag(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        # The remote sets b7 bit 1 on the frames it sends while the timer is set.
        await set_hours(hass, 3)
        assert Acp35Flag.TIMER_UI in last_command(send_command).flags

    async def test_disarming_clears_the_timer_ui_flag(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await set_hours(hass, 3)
        await set_hours(hass, 0)
        assert Acp35Flag.TIMER_UI not in last_command(send_command).flags

    @pytest.mark.parametrize("hours", [-1, 25, 100])
    async def test_out_of_range_is_rejected_by_home_assistant(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, hours
    ) -> None:
        send_command.reset_mock()
        with pytest.raises(ServiceValidationError):
            await set_hours(hass, hours)
        assert send_command.await_count == 0


class TestSharedState:
    """Both entities drive one shadow state and each sends the whole frame."""

    async def test_timer_frame_carries_the_climate_state(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        from homeassistant.components.climate import (
            ATTR_FAN_MODE,
            SERVICE_SET_FAN_MODE,
        )
        from homeassistant.components.climate import (
            DOMAIN as CLIMATE_DOMAIN,
        )

        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_FAN_MODE: "low"},
            blocking=True,
        )
        await set_hours(hass, 4)

        # The timer's frame must still describe the fan, not reset it.
        command = last_command(send_command)
        assert command.fan.name == "LOW"
        assert command.timer_hours == 4

    async def test_climate_frame_carries_the_timer(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        from homeassistant.components.climate import (
            ATTR_TEMPERATURE,
            SERVICE_SET_TEMPERATURE,
        )
        from homeassistant.components.climate import (
            DOMAIN as CLIMATE_DOMAIN,
        )

        await set_hours(hass, 9)
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 26},
            blocking=True,
        )

        command = last_command(send_command)
        assert command.timer_hours == 9, "a climate change must not clear the timer"
        assert command.celsius == 26


class TestRestore:
    """The timer survives a restart."""

    async def test_hours_are_restored(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await set_hours(hass, 7)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert float(hass.states.get(TIMER_ID).state) == 7
