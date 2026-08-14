"""Config flow for Stiebel Eltron infrared appliances."""

from typing import Any

import voluptuous as vol
from homeassistant.components.infrared import DOMAIN as INFRARED_DOMAIN
from homeassistant.components.infrared import InfraredDeviceClass
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
)

from .const import CONF_EMITTER, CONF_MODEL, CONF_RECEIVER, DOMAIN, MODEL_ACP35
from .models import MODELS

# The only model there is, so the flow does not ask. When there is a second one
# this becomes a menu step; a one-item menu is worse than no question at all.
DEFAULT_MODEL = MODEL_ACP35
DEFAULT_TITLE = MODELS[DEFAULT_MODEL].default_title

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMITTER): EntitySelector(
            EntitySelectorConfig(
                domain=INFRARED_DOMAIN,
                device_class=InfraredDeviceClass.EMITTER,
            )
        ),
        # Optional. Without a receiver the integration still controls the unit;
        # it just cannot notice changes made with the physical remote.
        vol.Optional(CONF_RECEIVER): EntitySelector(
            EntitySelectorConfig(
                domain=INFRARED_DOMAIN,
                device_class=InfraredDeviceClass.RECEIVER,
            )
        ),
        vol.Optional("name", default=DEFAULT_TITLE): TextSelector(),
    }
)


class Acp35ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask which infrared emitter to transmit through."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

        data = dict(user_input)
        title = data.pop("name", DEFAULT_TITLE) or DEFAULT_TITLE
        # Recorded even though nothing chose it, so every entry already carries
        # the field by the time a second model makes it a question.
        data[CONF_MODEL] = DEFAULT_MODEL

        # One entry per emitter *and model*, not per emitter. The frame carries
        # no device address -- b0 is the constant 0x55 and b4/b5 are always zero
        # -- so two appliances of the same model hear one emitter identically and
        # cannot be driven apart, whatever Home Assistant does. Two different
        # models sharing one blaster is fine: each decoder rejects the other's
        # frames on the preamble and the checksum.
        await self.async_set_unique_id(f"{data[CONF_EMITTER]}_{data[CONF_MODEL]}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(title=title, data=data)
