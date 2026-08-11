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
from .acp35 import (
    MAX_CELSIUS,
    MIN_CELSIUS,
    Acp35Fan,
    Acp35Flag,
    Acp35Mode,
    effective_fan,
    effective_temperature,
)
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

    # --- UI OPTION 1 -- under evaluation, not a settled design ---------------
    # Hide what the remote does not allow: the temperature is only adjustable in
    # cool, and dry offers only low fan.

    @property
    @override
    def supported_features(self) -> ClimateEntityFeature:
        """Offer only the controls the remote would respond to.

        Cool is the one mode whose up/down buttons change the setpoint. The
        others hide the number on the remote, so the control does not belong on
        the card either.

        This is a different rule from `effective_temperature`, which pins dry and
        auto to 22 C but leaves fan alone because fan transmits cool's setpoint.
        What a frame *carries* and what the user can *change* are separate
        questions, and conflating them left the control visible in fan.
        """
        features = self._attr_supported_features
        state = self._data.state
        if not state.power:
            # Powered off, the remote responds to two buttons only: power and
            # timer. Fan, mode, temperature and the unit switch are all ignored,
            # so there is no frame for them and no control to offer. The timer
            # is a separate entity and stays available.
            return features & ~(
                ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
            )
        if state.mode is not Acp35Mode.COOL:
            features &= ~ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    @override
    def fan_modes(self) -> list[str]:
        """Offer only low in dry, where the remote forces it."""
        if self._data.state.mode is Acp35Mode.DRY:
            return [ACP_TO_FAN[Acp35Fan.LOW]]
        return self._attr_fan_modes

    def __init__(self, entry: Acp35ConfigEntry) -> None:
        """Set up the entity."""
        super().__init__(entry)
        self._attr_unique_id = entry.entry_id

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
        """Return the speed actually running, which in dry is always low.

        This has to agree with `fan_modes`, which offers only low in dry. When
        it did not, the card had a value outside its own option list and showed
        an empty selector.

        Reporting the coerced value is safe because the shadow state is
        persisted through extra data rather than read back from this attribute.
        The user's own choice is untouched and returns when they leave dry.
        """
        state = self._data.state
        return ACP_TO_FAN.get(
            effective_fan(state.mode, state.fan), self._attr_fan_modes[-1]
        )

    @property
    @override
    def target_temperature(self) -> float:
        """Return the temperature actually being transmitted.

        Dry and auto are pinned to the remote's default. The control is hidden
        in those modes so this is rarely displayed, but it must not claim a
        setpoint no frame carries.
        """
        state = self._data.state
        celsius, _ = effective_temperature(state.mode, state.celsius, state.fahrenheit)
        return celsius

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
        """Set the fan speed for the mode currently selected.

        Stored against that mode only, as the remote does: changing the speed in
        cool must not move what fan-only or auto will run at. In dry the store
        pins it to low, so the only speed the card offers there is also the only
        one that can be recorded.
        """
        if (fan := FAN_TO_ACP.get(fan_mode)) is None:
            raise ValueError(f"unsupported fan mode {fan_mode}")
        self._data.state.set_fan(fan)
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
