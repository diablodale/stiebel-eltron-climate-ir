"""The timer read-out: diagnostic, read-only, disabled by default.

Setting the timer from Home Assistant was removed. Nothing can clear a stored
value -- the appliance acting on its own timer emits no infrared -- so replaying
one would silently re-arm a timer that had already fired. What remains is a
report of the hour count in the last frame we know about.
"""

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import CLIMATE_ID, TIMER_ID


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
