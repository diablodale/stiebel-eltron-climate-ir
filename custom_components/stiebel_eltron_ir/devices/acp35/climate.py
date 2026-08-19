"""Climate entity for the Stiebel Eltron ACP 35."""

import math
from typing import Any, Final, override

from homeassistant.components.climate import (
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    EVENT_CORE_CONFIG_UPDATE,
    UnitOfTemperature,
)
from homeassistant.core import Event, callback

from ...data import StiebelEltronIrConfigEntry
from .entity import Acp35Entity
from .protocol import (
    MAX_CELSIUS,
    MAX_FAHRENHEIT,
    MIN_CELSIUS,
    MIN_FAHRENHEIT,
    Acp35Fan,
    Acp35Flag,
    Acp35Mode,
    effective_fan,
    effective_temperature,
)

# The unit is cooling-only, so there is no HEAT. OFF is not a mode in the
# protocol: it clears the power bit and leaves the last mode in b6, exactly as
# the remote does, which is why it has no Acp35Mode of its own.
#
# These are facts about this appliance rather than about the integration, which
# is why they live here and not in a shared const module. A heater would need
# HEAT and would have no dry, and could not share a table with this.
HVAC_TO_MODE: Final[dict[HVACMode, Acp35Mode]] = {
    HVACMode.AUTO: Acp35Mode.AUTO,
    HVACMode.COOL: Acp35Mode.COOL,
    HVACMode.DRY: Acp35Mode.DRY,
    HVACMode.FAN_ONLY: Acp35Mode.FAN,
}
MODE_TO_HVAC: Final[dict[Acp35Mode, HVACMode]] = {
    mode: hvac for hvac, mode in HVAC_TO_MODE.items()
}

# Every speed the appliance has, so this mapping is total: `Acp35Fan` holds three
# members and all three appear here. There are no other fan speeds -- see
# the note on the nibble's unused values in `Acp35Fan`.
FAN_TO_ACP: Final[dict[str, Acp35Fan]] = {
    FAN_LOW: Acp35Fan.LOW,
    FAN_MEDIUM: Acp35Fan.MEDIUM,
    FAN_HIGH: Acp35Fan.HIGH,
}
ACP_TO_FAN: Final[dict[Acp35Fan, str]] = {fan: name for name, fan in FAN_TO_ACP.items()}


class Acp35Climate(Acp35Entity, ClimateEntity):
    """The air conditioner itself."""

    _attr_name = None
    # Both of the appliance's scales are whole degrees -- 17..30 C and 62..86 F
    # -- so the step is 1 either way and only the unit and bounds move.
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [HVACMode.OFF, *HVAC_TO_MODE]
    _attr_fan_modes = list(FAN_TO_ACP)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    # The appliance has two scales, not one value shown two ways. In Celsius its
    # up/down walks 17..30, fourteen steps; in Fahrenheit it walks 62..86,
    # twenty-five. The two conversion tables are not inverses, so one of them has
    # to be the scale the user drives on and the other its paired value.
    #
    # That scale is the Home Assistant profile's, not the appliance's. Home
    # Assistant converts min, max and target from this entity's unit into the
    # profile's unit, so matching them means no conversion happens and the card
    # holds exactly the number the user picked. Reporting the appliance's scale
    # instead put a round trip in the way -- 22 C converts to 71.6 F, which is not
    # a value the protocol can carry, so it shipped 72 and the card came back
    # reading 22.2. No step or rounding fixes that; 71.6 is simply not
    # representable.
    #
    # A Fahrenheit user has a Fahrenheit profile, which is what makes them one,
    # so they still get all twenty-five steps. The select keeps its own job:
    # telling the appliance which unit to show on its panel.

    @property
    def _uses_celsius(self) -> bool:
        """Return whether this Home Assistant install drives in Celsius."""
        return self.hass.config.units.temperature_unit is UnitOfTemperature.CELSIUS

    @property
    @override
    def temperature_unit(self) -> UnitOfTemperature:
        """Return the profile's unit, so Home Assistant does not convert."""
        return self.hass.config.units.temperature_unit

    @property
    @override
    def min_temp(self) -> float:
        """Return the bottom of the scale in use.

        Whole in both, because the appliance's endpoints are pinned to each
        other: 17 C and 62 F are both its minimum, 30 C and 86 F its maximum.
        Converting one scale's bounds into the other would put the slider's end
        at 16.67, where no position is a whole degree.
        """
        return MIN_CELSIUS if self._uses_celsius else MIN_FAHRENHEIT

    @property
    @override
    def max_temp(self) -> float:
        """Return the top of the scale in use."""
        return MAX_CELSIUS if self._uses_celsius else MAX_FAHRENHEIT

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

    def __init__(self, entry: StiebelEltronIrConfigEntry) -> None:
        """Set up the entity."""
        super().__init__(entry)
        # Suffixed like every other entity rather than taking the bare entry id,
        # so no entity owns the unqualified name and a model with two climate
        # entities has somewhere to put the second.
        self._attr_unique_id = f"{entry.entry_id}_climate"

    @override
    async def async_added_to_hass(self) -> None:
        """Also follow the profile's unit, which this entity's scale tracks."""
        await super().async_added_to_hass()
        # Nothing re-reads hass.config.units on its own. This entity never polls
        # -- it is assumed_state and only writes when something changes it -- so
        # after the unit system is switched the card would keep the old number
        # under the new unit's label: 19 C relabelled as 19 F rather than 66.
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_CORE_CONFIG_UPDATE, self._handle_unit_system_change
            )
        )

    @callback
    def _handle_unit_system_change(self, event: Event) -> None:
        """Re-render on the new scale."""
        self.async_write_ha_state()

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
        """Return the temperature actually being transmitted, in the scale in use.

        Dry and auto are pinned to the remote's default. The control is hidden
        in those modes so this is rarely displayed, but it must not claim a
        setpoint no frame carries.

        The pair is read straight out of the frame's own fields rather than
        converted, so the number here is one the protocol actually carries and
        the card can hold it unchanged.
        """
        state = self._data.state
        celsius, fahrenheit = effective_temperature(
            state.mode, state.celsius, state.fahrenheit
        )
        return celsius if self._uses_celsius else fahrenheit

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
        """Set the target temperature on the profile's scale.

        The value arrives already in this entity's unit, which is the profile's,
        so no conversion has happened and it is the number the user typed. The
        field for that scale is authoritative and its pair is derived, never the
        other way round -- 63 F pairs to 17 C, but 17 C pairs back out to 62 F,
        so deriving backwards would move the value by a degree.
        """
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        state = self._data.state
        if self._uses_celsius:
            state.set_celsius(_whole(temperature, MIN_CELSIUS, MAX_CELSIUS))
        else:
            state.set_fahrenheit(_whole(temperature, MIN_FAHRENHEIT, MAX_FAHRENHEIT))
        await self._async_transmit(Acp35Flag.TEMP_CHANGED)


def _whole(temperature: float, minimum: int, maximum: int) -> int:
    """Round a requested temperature to a whole degree within the scale.

    Rounding is the part that matters. Home Assistant's climate component already
    rejects anything outside ``min_temp``..``max_temp`` with a
    ``ServiceValidationError`` before the entity is called, but it does not round:
    a half degree passes straight through, and Acp35Command only accepts whole
    ones. Converting between scales produces fractions routinely -- 20 °C arrives
    as 68.0 °F, but 21 °C arrives as 69.8 -- so this is the normal path in
    Fahrenheit rather than a guard against odd input.

    Halves go up rather than through ``round()``, whose banker's rounding would
    send 20.5 down to 20 but 21.5 up to 22.
    """
    return min(maximum, max(minimum, math.floor(temperature + 0.5)))


__all__ = ["Acp35Climate", "Acp35Fan", "Acp35Mode"]
