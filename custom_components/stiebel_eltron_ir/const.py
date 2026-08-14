"""Constants and Home Assistant mappings for the Stiebel Eltron ACP 35."""

from typing import Final

from homeassistant.components.climate import FAN_HIGH, FAN_LOW, FAN_MEDIUM, HVACMode

from .acp35 import Acp35Fan, Acp35Mode

DOMAIN: Final = "stiebel_eltron_ir"

CONF_EMITTER: Final = "emitter"
CONF_RECEIVER: Final = "receiver"
CONF_MODEL: Final = "model"

# Which appliance an entry drives. Written into every entry even though there is
# only one value to write, because the stored shape is the expensive thing to
# change later and the picker that offers a choice is not.
MODEL_ACP35: Final = "acp35"

# The unit is cooling-only, so there is no HEAT. OFF is not a mode in the
# protocol: it clears the power bit and leaves the last mode in b6, exactly as
# the remote does, which is why it has no Acp35Mode of its own.
HVAC_TO_MODE: Final[dict[HVACMode, Acp35Mode]] = {
    HVACMode.AUTO: Acp35Mode.AUTO,
    HVACMode.COOL: Acp35Mode.COOL,
    HVACMode.DRY: Acp35Mode.DRY,
    HVACMode.FAN_ONLY: Acp35Mode.FAN,
}
MODE_TO_HVAC: Final[dict[Acp35Mode, HVACMode]] = {
    mode: hvac for hvac, mode in HVAC_TO_MODE.items()
}

# Acp35Fan.AUTO is deliberately absent. The nibble can hold it, but the remote's
# fan button only cycles high -> medium -> low and it has never been observed, so
# it is not offered until it has been tried against the unit.
FAN_TO_ACP: Final[dict[str, Acp35Fan]] = {
    FAN_LOW: Acp35Fan.LOW,
    FAN_MEDIUM: Acp35Fan.MEDIUM,
    FAN_HIGH: Acp35Fan.HIGH,
}
ACP_TO_FAN: Final[dict[Acp35Fan, str]] = {fan: name for name, fan in FAN_TO_ACP.items()}
