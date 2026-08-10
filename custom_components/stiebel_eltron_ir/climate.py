"""Climate entity for the Stiebel Eltron ACP 35."""

import math
from typing import Any, override

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Acp35ConfigEntry
from .acp35 import MAX_CELSIUS, MIN_CELSIUS, Acp35Fan, Acp35Flag, Acp35Mode
from .const import ACP_TO_FAN, FAN_TO_ACP, HVAC_TO_MODE, MODE_TO_HVAC
from .entity import Acp35Entity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Acp35ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the climate entity."""
    async_add_entities([Acp35Climate(entry)])


class Acp35Climate(Acp35Entity, ClimateEntity):
    """The air conditioner itself."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_min_temp = MIN_CELSIUS
    _attr_max_temp = MAX_CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, *HVAC_TO_MODE]
    _attr_fan_modes = list(FAN_TO_ACP)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, entry: Acp35ConfigEntry) -> None:
        """Set up the entity."""
        super().__init__(entry)
        self._attr_unique_id = entry.entry_id

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the shadow state, since the unit cannot be asked for it."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is None:
            return

        state = self._data.state
        state.power = last.state != HVACMode.OFF
        if last.state != HVACMode.OFF and last.state in HVAC_TO_MODE:
            state.mode = HVAC_TO_MODE[HVACMode(last.state)]
        if (fan := last.attributes.get("fan_mode")) in FAN_TO_ACP:
            state.fan = FAN_TO_ACP[fan]
        if (temperature := last.attributes.get(ATTR_TEMPERATURE)) is not None:
            state.set_celsius(_clamp_celsius(temperature))

    @property
    @override
    def hvac_mode(self) -> HVACMode:
        """Return the current mode, or OFF if the unit is powered down."""
        state = self._data.state
        if not state.power:
            return HVACMode.OFF
        return MODE_TO_HVAC[state.mode]

    @property
    @override
    def fan_mode(self) -> str:
        """Return the current fan speed."""
        return ACP_TO_FAN.get(self._data.state.fan, self._attr_fan_modes[-1])

    @property
    @override
    def target_temperature(self) -> float:
        """Return the target temperature in degrees Celsius."""
        return self._data.state.celsius

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the mode, or power the unit off."""
        state = self._data.state
        was_on = state.power

        if hvac_mode is HVACMode.OFF:
            # The remote clears the power bit and leaves the last mode in b6.
            state.power = False
        else:
            if (mode := HVAC_TO_MODE.get(hvac_mode)) is None:
                raise ValueError(f"unsupported hvac mode {hvac_mode}")
            state.power = True
            state.mode = mode

        # A power change is what the remote flags; a mode change is not.
        event = Acp35Flag.POWER_PRESSED if state.power != was_on else Acp35Flag.NONE
        await self._async_transmit(event)

    @override
    async def async_turn_on(self) -> None:
        """Power the unit on, keeping its last mode."""
        self._data.state.power = True
        await self._async_transmit(Acp35Flag.POWER_PRESSED)

    @override
    async def async_turn_off(self) -> None:
        """Power the unit off."""
        self._data.state.power = False
        await self._async_transmit(Acp35Flag.POWER_PRESSED)

    @override
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan speed."""
        if (fan := FAN_TO_ACP.get(fan_mode)) is None:
            raise ValueError(f"unsupported fan mode {fan_mode}")
        self._data.state.fan = fan
        await self._async_transmit()

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self._data.state.set_celsius(_clamp_celsius(temperature))
        await self._async_transmit(Acp35Flag.TEMP_CHANGED)


def _clamp_celsius(temperature: float) -> int:
    """Round a requested temperature to a whole degree in range.

    Rounding is the part that matters. Home Assistant's climate component already
    rejects anything outside ``min_temp``..``max_temp`` with a
    ``ServiceValidationError`` before the entity is called, but it does not round:
    a half degree passes straight through, and Acp35Command only accepts whole
    ones. The clamp is belt and braces, and covers the restore path, where a
    stored attribute could be out of range if the bounds ever change.

    Halves go up rather than through ``round()``, whose banker's rounding would
    send 20.5 down to 20 but 21.5 up to 22.
    """
    whole = math.floor(temperature + 0.5)
    return min(MAX_CELSIUS, max(MIN_CELSIUS, whole))


__all__ = ["Acp35Climate", "Acp35Fan", "Acp35Mode"]
