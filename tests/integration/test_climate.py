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
    DEFAULT_CELSIUS,
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
        # Choose the temperature and fan in cool, where both are adjustable,
        # then move to dry. Dry offers neither control.
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 28})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "low"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get(CLIMATE_ID).state == HVACMode.DRY

        # Assert through the transmitted frame rather than the attributes: the
        # card hides the temperature outside cool, so reading it back from the
        # state would test the display rather than what survived the restart.
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        command = last_command(send_command)
        assert command.celsius == 28
        assert command.fan is Acp35Fan.LOW

    async def test_every_mode_keeps_its_own_speed(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """All four slots survive, not just the one the selected mode uses.

        Persisting only the current speed would hand every other mode whatever
        the last-used one was running the next time it is selected.
        """
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "medium"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.FAN_ONLY})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "low"})

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        for mode, expected in (
            (HVACMode.COOL, Acp35Fan.MEDIUM),
            (HVACMode.FAN_ONLY, Acp35Fan.LOW),
            (HVACMode.AUTO, Acp35Fan.HIGH),
        ):
            await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: mode})
            assert last_command(send_command).fan is expected


class TestDryForcesLowFan:
    """Dry mode forces the fan to low, as the remote does.

    Selecting dry on the TZ20160122 drops the fan to low and the fan button will
    not move it while dry is selected, so no frame the remote can emit pairs dry
    with medium or high. Whether the unit would accept one is untested, which is
    why this mirrors the remote rather than assuming the handset alone enforces
    it.
    """

    @pytest.mark.parametrize("chosen", ["low", "medium", "high"])
    async def test_dry_always_transmits_low(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, chosen: str
    ) -> None:
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: chosen})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})

        command = last_command(send_command)
        assert command.mode is Acp35Mode.DRY
        assert command.fan is Acp35Fan.LOW
        assert command.to_bytes()[6] == 0x12

    async def test_a_non_low_fan_cannot_be_selected_during_dry(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Home Assistant refuses a speed that is not in fan_modes."""
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})
        send_command.reset_mock()
        with pytest.raises(ServiceValidationError):
            await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "high"})
        assert send_command.await_count == 0

    async def test_the_reported_speed_agrees_with_the_offered_options(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """A value outside fan_modes leaves the card's selector blank."""
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "high"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})

        attributes = hass.states.get(CLIMATE_ID).attributes
        assert attributes[ATTR_FAN_MODE] == "low"
        assert attributes[ATTR_FAN_MODE] in attributes["fan_modes"]
        assert last_command(send_command).fan is Acp35Fan.LOW

    async def test_selecting_low_in_dry_does_not_overwrite_the_stored_speed(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Reported: off, cool+medium, dry, select low, cool -> must be medium.

        Low is the only option dry offers, so choosing it is not a choice. It
        must not replace the speed the other modes remember.
        """
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "medium"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "low"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})

        assert last_command(send_command).fan is Acp35Fan.MEDIUM
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_FAN_MODE] == "medium"

    async def test_leaving_dry_restores_the_chosen_speed(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """The user's pick is kept, not flattened to low by passing through dry."""
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "high"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})

        command = last_command(send_command)
        assert command.mode is Acp35Mode.COOL
        assert command.fan is Acp35Fan.HIGH
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_FAN_MODE] == "high"


class TestFanSpeedIsStoredPerMode:
    """Each mode remembers its own speed, exactly as the remote does.

    A mode press transmits the speed stored for the mode being entered, not the
    speed the previous mode was running. Pulling the remote's batteries brings
    every mode back on high except dry, which returns on low, so that is where
    each slot starts.
    """

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            (HVACMode.AUTO, Acp35Fan.HIGH),
            (HVACMode.COOL, Acp35Fan.HIGH),
            (HVACMode.DRY, Acp35Fan.LOW),
            (HVACMode.FAN_ONLY, Acp35Fan.HIGH),
        ],
    )
    async def test_each_mode_starts_where_the_remote_does(
        self,
        hass: HomeAssistant,
        entry,
        send_command: AsyncMock,
        mode: HVACMode,
        expected: Acp35Fan,
    ) -> None:
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: mode})
        assert last_command(send_command).fan is expected

    async def test_a_speed_set_in_cool_does_not_follow_into_other_modes(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Reported: cool+medium then auto or fan-only must not run medium."""
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "medium"})

        for mode in (HVACMode.AUTO, HVACMode.FAN_ONLY):
            await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: mode})
            assert last_command(send_command).fan is Acp35Fan.HIGH

    async def test_returning_to_a_mode_restores_its_own_speed(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "medium"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.FAN_ONLY})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "low"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})

        assert last_command(send_command).fan is Acp35Fan.MEDIUM
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_FAN_MODE] == "medium"

        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.FAN_ONLY})
        assert last_command(send_command).fan is Acp35Fan.LOW

    @pytest.mark.parametrize("mode", [HVACMode.AUTO, HVACMode.FAN_ONLY])
    async def test_all_three_speeds_are_offered_outside_dry(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, mode: HVACMode
    ) -> None:
        """Auto and fan-only accept every speed; only dry is restricted."""
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: mode})
        assert hass.states.get(CLIMATE_ID).attributes["fan_modes"] == [
            "low",
            "medium",
            "high",
        ]

        for chosen, expected in (
            ("low", Acp35Fan.LOW),
            ("medium", Acp35Fan.MEDIUM),
            ("high", Acp35Fan.HIGH),
        ):
            await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: chosen})
            assert last_command(send_command).fan is expected


class TestOnlyCoolHasASetpoint:
    """Dry and auto are pinned to the remote's default temperature.

    The remote hides the temperature in every mode but cool and up/down will not
    change it there, so dry and auto always carry 22 C / 72 F. That 22 is
    firmware, not stored state: after the batteries were pulled the remote came
    back at 22 C in every mode, while during a session cool moved to 30 C and
    later 18 C with dry and auto unmoved.
    """

    @pytest.mark.parametrize("mode", [HVACMode.DRY, HVACMode.AUTO])
    async def test_pinned_modes_transmit_the_default(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, mode: HVACMode
    ) -> None:
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 28})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: mode})

        command = last_command(send_command)
        assert command.celsius == DEFAULT_CELSIUS
        assert command.fahrenheit == 72, "b3 must stay paired with b1"

    @pytest.mark.parametrize("mode", [HVACMode.COOL, HVACMode.FAN_ONLY])
    async def test_cool_and_fan_carry_the_setpoint(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, mode: HVACMode
    ) -> None:
        """Fan mirrors cool's setpoint on the remote, so it is not pinned."""
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 28})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: mode})
        assert last_command(send_command).celsius == 28

    async def test_leaving_a_pinned_mode_restores_the_setpoint(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 28})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})

        assert last_command(send_command).celsius == 28
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE] == 28

    async def test_the_setpoint_is_kept_while_a_pinned_mode_is_selected(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Dry transmits the default, then cool transmits the choice again."""
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 28})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})
        assert last_command(send_command).celsius == DEFAULT_CELSIUS

        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        assert last_command(send_command).celsius == 28

    async def test_a_restart_while_pinned_keeps_the_setpoint(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """The regression that reporting the pinned value would have caused."""
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 28})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "high"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        # Extra data is what makes this pass. Restoring from the displayed
        # attributes could not: the card hides the temperature in dry, so a
        # restart taken there would read back the pinned 22 and keep it.
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        command = last_command(send_command)
        assert command.celsius == 28, "the setpoint must survive a restart in dry"
        assert command.fan is Acp35Fan.HIGH, "the fan choice must survive it too"

    async def test_factory_defaults_match_the_reset_capture(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """A freshly reset remote sends cool, high fan, 22 C, °C display."""
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "high"})
        await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: DEFAULT_CELSIUS})

        state = last_command(send_command).to_bytes()
        assert state[:8].hex(" ") == "55 62 00 0d 00 00 31 c0"


class TestModeDependentControls:
    """Hide what the remote does not allow. Settled 2026-08-13.

    The temperature is adjustable only in cool. Fan mode transmits cool's
    setpoint, but the remote still hides the number there, so what a frame
    carries and what the user can change are separate questions.

    The alternative -- keeping every control and reporting the pinned value --
    was rejected. `supported_features` is a bitmask with no read-only state, so
    it would render a control that looks live and does nothing, and Home
    Assistant would stop rejecting `set_temperature` in dry before the entity
    saw it.
    """

    @pytest.mark.parametrize(
        ("mode", "adjustable"),
        [
            (HVACMode.COOL, True),
            (HVACMode.FAN_ONLY, False),
            (HVACMode.DRY, False),
            (HVACMode.AUTO, False),
        ],
    )
    async def test_temperature_control_is_offered_only_in_cool(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, mode, adjustable
    ) -> None:
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: mode})
        attributes = hass.states.get(CLIMATE_ID).attributes
        assert (ATTR_TEMPERATURE in attributes) is adjustable

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            (HVACMode.COOL, ["low", "medium", "high"]),
            (HVACMode.DRY, ["low"]),
            (HVACMode.AUTO, ["low", "medium", "high"]),
            (HVACMode.FAN_ONLY, ["low", "medium", "high"]),
        ],
    )
    async def test_only_dry_narrows_the_fan_choices(
        self, hass: HomeAssistant, entry, send_command: AsyncMock, mode, expected
    ) -> None:
        # The manual states auto takes a fan speed, so only dry is restricted.
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: mode})
        assert hass.states.get(CLIMATE_ID).attributes["fan_modes"] == expected

    async def test_setting_temperature_outside_cool_is_refused(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """A script that sets a temperature in dry fails instead of being ignored."""
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.DRY})
        send_command.reset_mock()
        with pytest.raises(ServiceValidationError):
            await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 25})
        assert send_command.await_count == 0


class TestPoweredOffIgnoresControls:
    """Off, the remote responds to power and timer only.

    Every other button is ignored, so no frame exists for a fan or temperature
    change made while off. Offering those controls invents an interaction the
    hardware does not have, and the invented value then leaks into the next
    power-on.
    """

    async def test_the_reported_sequence(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """cool+medium, off, fan=high, cool -> must still be medium."""
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "medium"})
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.OFF})

        with pytest.raises(ServiceValidationError):
            await call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "high"})

        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
        assert last_command(send_command).fan is Acp35Fan.MEDIUM

    async def test_neither_control_is_offered_while_off(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.OFF})
        attributes = hass.states.get(CLIMATE_ID).attributes
        assert ATTR_TEMPERATURE not in attributes
        assert attributes.get("fan_modes") is None

    async def test_setting_temperature_while_off_is_refused(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.OFF})
        send_command.reset_mock()
        with pytest.raises(ServiceValidationError):
            await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 25})
        assert send_command.await_count == 0

    async def test_powering_on_still_works(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Power is one of the two buttons that does respond."""
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.OFF})
        await call(hass, SERVICE_TURN_ON)
        assert last_command(send_command).power is True

    async def test_a_pending_timer_rides_along_while_off(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Both timer fields survive a press that is not a timer press.

        Home Assistant no longer sets the timer, but a value heard from the
        remote still has to travel in every frame -- clearing it would cancel a
        timer the user set on the handset.
        """
        entry.runtime_data.state.timer_hours = 5
        await call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.OFF})

        command = last_command(send_command)
        assert command.timer_hours == 5
        assert command.power is False, "the timer must not power the unit on"
