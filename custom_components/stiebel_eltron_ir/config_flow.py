"""Config flow for Stiebel Eltron infrared appliances."""

from collections.abc import Mapping
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

# The two questions both flows ask. Reconfiguring stops here; creating adds a
# name below. `name` is not offered on reconfigure because Home Assistant renames
# an entry from its own menu, and two ways to do one thing is one too many.
STEP_RECONFIGURE_SCHEMA = vol.Schema(
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
    }
)

STEP_USER_SCHEMA = STEP_RECONFIGURE_SCHEMA.extend(
    {vol.Optional("name", default=DEFAULT_TITLE): TextSelector()}
)


class StiebelEltronIrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask which infrared emitter to transmit through."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self._async_form("user", STEP_USER_SCHEMA)

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
        unique_id = f"{data[CONF_EMITTER]}_{data[CONF_MODEL]}"
        # Guards against a second flow claiming the same emitter while this one is
        # open. That one does abort, because there is no field to blame and no
        # form worth keeping when the answer arrived from somewhere else.
        await self.async_set_unique_id(unique_id)
        if self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unique_id):
            return self._async_form(
                "user",
                STEP_USER_SCHEMA,
                user_input,
                {CONF_EMITTER: "emitter_in_use"},
            )

        return self.async_create_entry(title=title, data=data)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing appliance at a different emitter or receiver.

        Deleting the entry and adding it again would do the same thing and cost
        the appliance's remembered state: `async_remove_entry` deletes the store,
        which is keyed by entry id. Reconfiguring keeps the entry, so the state,
        the entities and everything referring to them survive.
        """
        entry = self._get_reconfigure_entry()
        if user_input is None:
            return self._async_form("reconfigure", STEP_RECONFIGURE_SCHEMA, entry.data)

        data = dict(user_input)
        # Carried across rather than defaulted. The entry's model is what its
        # stored state and its entities were built for, and this form does not
        # offer it -- defaulting here would silently convert an entry the day a
        # second model exists.
        data[CONF_MODEL] = entry.data[CONF_MODEL]
        unique_id = f"{data[CONF_EMITTER]}_{data[CONF_MODEL]}"

        # Looked up by hand rather than through `_abort_if_unique_id_configured`,
        # which raises and would end the flow. Being told "no" is not a reason to
        # throw away a form the user has half-filled: an error on the offending
        # field leaves it open, still holding their choices, with the emitter the
        # only thing to change. Comparing entry ids also settles the case that
        # helper cannot -- an unchanged emitter finds *this* entry, which is not a
        # collision.
        clash = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, unique_id
        )
        if clash is not None and clash.entry_id != entry.entry_id:
            return self._async_form(
                "reconfigure",
                STEP_RECONFIGURE_SCHEMA,
                user_input,
                {CONF_EMITTER: "emitter_in_use"},
            )

        # `data=`, not `data_updates=`: clearing the receiver leaves the key out
        # of `user_input` altogether, and merging would keep the old one.
        return self.async_update_reload_and_abort(entry, unique_id=unique_id, data=data)

    def _async_form(
        self,
        step_id: str,
        schema: vol.Schema,
        values: Mapping[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Show one of the two forms, filled in with ``values``."""
        if values is not None:
            schema = self.add_suggested_values_to_schema(schema, values)
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)
