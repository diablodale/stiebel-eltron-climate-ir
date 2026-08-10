"""Config flow for the Stiebel Eltron ACP 35."""

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

from .const import CONF_DISPLAY_CELSIUS, CONF_EMITTER, CONF_RECEIVER, DOMAIN

DEFAULT_TITLE = "Stiebel Eltron ACP 35"

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
        vol.Optional(CONF_DISPLAY_CELSIUS, default=True): bool,
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

        # One entry per emitter: two config entries driving the same emitter
        # would fight over the shadow state.
        await self.async_set_unique_id(data[CONF_EMITTER])
        self._abort_if_unique_id_configured()

        return self.async_create_entry(title=title, data=data)
