"""Climate entity behaviour, asserted on the frames it would transmit."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.stiebel_eltron_ir.acp35 import (
    Acp35Fan,
    Acp35Flag,
    Acp35Mode,
)

from .conftest import CLIMATE_ID, last_command


async def call(hass: HomeAssistant, service: str, **data) -> None:
    """Call a climate service on the entity under test."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        service,
        {ATTR_ENTITY_ID: CLIMATE_ID, **data},
        blocking=True,
    )


class TestSetup:
    """The entity exists and advertises the right capabilities."""

    async def test_entity_is_created(self, hass: HomeAssistant, entry) -> None:
        assert hass.states.get(CLIMATE_ID) is not None

    async def test_assumed_state(self, hass: HomeAssistant, entry) -> None:
        # IR is one way; the unit never reports back.
        assert hass.states.get(CLIMATE_ID).attributes["assumed_state"] is True

    async def test_offers_no_heat_mode(self, hass: HomeAssistant, entry) -> None:
        modes = hass.states.get(CLIMATE_ID).attributes["hvac_modes"]
        assert HVACMode.HEAT not in modes
        assert set(modes) == {
            HVACMode.OFF,
            HVACMode.AUTO,
            HVACMode.COOL,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
        }

    async def test_temperature_bounds(self, hass: HomeAssistant, entry) -> None:
        attributes = hass.states.get(CLIMATE_ID).attributes
        assert attributes["min_temp"] == 17
        assert attributes["max_temp"] == 30
        assert attributes["target_temp_step"] == 1

    async def test_fan_modes_exclude_auto(self, hass: HomeAssistant, entry) -> None:
        # Acp35Fan.AUTO is representable but never observed from the remote.
        assert hass.states.get(CLIMATE_ID).attributes["fan_modes"] == [
            "low",
            "medium",
            "high",
        ]


class TestTransmission:
    """Every change sends one complete frame."""

    async def test_setting_mode_powers_on(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        command = last_command(send_command)
        assert command.power is True
        assert command.mode is Acp35Mode.COOL

    async def test_off_clears_power_but_keeps_the_mode(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.OFF})
        command = last_command(send_command)
        assert command.power is False
        assert command.mode is Acp35Mode.DRY, "the remote keeps the mode in b6"
        assert hass.states.get(CLIMATE_ID).state == HVACMode.OFF

    async def test_turn_on_and_off(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_TURN_ON)
        assert last_command(send_command).power is True
        await call(hass, SERVICE_TURN_OFF)
        assert last_command(send_command).power is False

    @pytest.mark.parametrize(
        ("fan_mode", "expected"),
        [("low", Acp35Fan.LOW), ("medium", Acp35Fan.MEDIUM), ("high", Acp35Fan.HIGH)],
    )
    async def test_fan_modes(
        self,
        hass: HomeAssistant,
        entry,
        send_command: AsyncMock,
        fan_mode,
        expected,
    ) -> None:
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: fan_mode})
        assert last_command(send_command).fan is expected

    async def test_temperature(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 24})
        command = last_command(send_command)
        assert command.celsius == 24
        assert command.fahrenheit == 75, "the paired Fahrenheit field travels too"

    async def test_frame_is_nine_bytes_with_a_valid_checksum(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 19})
        state = last_command(send_command).to_bytes()
        assert len(state) == 9
        assert state[0] == 0x55
        assert state[8] == sum(state[:8]) & 0xFF


class TestOutOfRangeInput:
    """What reaches the encoder, and what Home Assistant stops first."""

    @pytest.mark.parametrize("requested", [5, 16.9, 30.1, 99])
    async def test_out_of_range_is_rejected_by_home_assistant(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, requested
    ) -> None:
        """Climate validates against min_temp/max_temp before the entity runs.

        So Acp35Command's own ValueError is never the thing a user sees, and
        nothing is transmitted.
        """
        send_command.reset_mock()
        with pytest.raises(ServiceValidationError, match="not valid"):
            await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: requested})
        assert send_command.await_count == 0

    @pytest.mark.parametrize(
        ("requested", "expected"),
        [(17, 17), (21.5, 22), (20.5, 21), (22.4, 22), (22.6, 23), (30, 30)],
    )
    async def test_in_range_values_are_rounded_to_whole_degrees(
        self,
        hass: HomeAssistant,
        entry,
        send_command: AsyncMock,
        requested,
        expected,
    ) -> None:
        """Halves pass Home Assistant's check, so the entity must round them.

        Rounding is half-up: round() would send 20.5 down to 20 but 21.5 up
        to 22.
        """
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: requested})
        assert last_command(send_command).celsius == expected


class TestFlags:
    """b7 names which control the user touched, mirroring the remote."""

    async def test_temperature_change_sets_temp_changed(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 23})
        assert Acp35Flag.TEMP_CHANGED in last_command(send_command).flags

    async def test_power_change_sets_power_pressed(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_TURN_ON)
        assert Acp35Flag.POWER_PRESSED in last_command(send_command).flags

    async def test_fan_change_sets_no_event_bit(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "low"})
        assert last_command(send_command).flags == Acp35Flag.CELSIUS

    async def test_mode_change_alone_sets_no_event_bit(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_TURN_ON)
        send_command.reset_mock()
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.FAN_ONLY})
        # Already on, so this is a mode change and not a power press.
        assert last_command(send_command).flags == Acp35Flag.CELSIUS

    async def test_celsius_display_bit_follows_config(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_TURN_ON)
        assert Acp35Flag.CELSIUS in last_command(send_command).flags


class TestRestore:
    """The shadow state has to survive a restart; the unit cannot be asked."""

    async def test_state_is_restored(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 28})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "low"})

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(CLIMATE_ID)
        assert state.state == HVACMode.DRY
        assert state.attributes[ATTR_TEMPERATURE] == 28
        assert state.attributes[ATTR_FAN_MODE] == "low"
