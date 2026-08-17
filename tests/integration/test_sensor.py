"""The timer read-out: diagnostic, read-only, disabled by default.

Setting the timer from Home Assistant was removed, and a heard one is never
replayed. The appliance acting on its own timer emits no infrared, so expiry is
invisible and replaying would silently re-arm a timer that had already fired.
What remains is a report of the hour count in the last frame we know about,
which our own frames set to zero because they carry no timer.
"""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    SERVICE_SET_FAN_MODE,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import CLIMATE_ID, TIMER_ID, last_command


class TestItIsOutOfTheWay:
    """Diagnostic and off unless asked for."""

    async def test_disabled_by_default(
        self, hass: HomeAssistant, entry, entity_registry: er.EntityRegistry
    ) -> None:
        assert hass.states.get(TIMER_ID) is None
        assert (
            entity_registry.async_get(TIMER_ID).disabled_by
            is er.RegistryEntryDisabler.INTEGRATION
        )

    async def test_it_is_diagnostic(
        self, hass: HomeAssistant, entry, entity_registry: er.EntityRegistry
    ) -> None:
        assert (
            entity_registry.async_get(TIMER_ID).entity_category
            is er.EntityCategory.DIAGNOSTIC
        )

    async def test_no_timer_can_be_set(self, hass: HomeAssistant, entry) -> None:
        """There is no number entity any more, so nothing offers to set one."""
        assert hass.states.get("number.stiebel_eltron_acp_35_timer") is None
        assert "number" not in hass.config.components or not [
            state
            for state in hass.states.async_all("number")
            if state.entity_id.startswith("number.stiebel_eltron_acp_35")
        ]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
class TestWhenEnabled:
    """What it reports once the user turns it on."""

    async def test_it_reports_the_stored_hours(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        entry.runtime_data.state.timer_hours = 6
        entry.runtime_data.async_notify()
        await hass.async_block_till_done()
        assert hass.states.get(TIMER_ID).state == "6"

    async def test_it_reads_zero_with_no_timer(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        assert hass.states.get(TIMER_ID).state == "0"

    async def test_it_is_named_for_what_it_reports(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Not "Timer": nothing here sets one, and the value can be stale."""
        assert hass.states.get(TIMER_ID).attributes[ATTR_FRIENDLY_NAME] == (
            "Stiebel Eltron ACP 35 Last known timer"
        )

    async def test_our_own_frame_cancels_it(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        """Anything changed here clears the timer, and the read-out says so.

        Leaving it reading 6 would report a timer this integration had just
        cancelled, which is worse than the stale value the old replay produced.
        """
        entry.runtime_data.state.timer_hours = 6
        entry.runtime_data.async_notify()
        await hass.async_block_till_done()
        assert hass.states.get(TIMER_ID).state == "6"

        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_FAN_MODE: "low"},
            blocking=True,
        )
        assert last_command(send_command).timer_hours == 0
        assert hass.states.get(TIMER_ID).state == "0"

    async def test_it_is_measured_in_hours(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        attributes = hass.states.get(TIMER_ID).attributes
        assert attributes["unit_of_measurement"] == "h"

    async def test_it_follows_the_emitter_availability(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        assert hass.states.get(TIMER_ID).state != "unavailable"
        assert hass.states.get(CLIMATE_ID).state != "unavailable"
