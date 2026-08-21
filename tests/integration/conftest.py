"""Fixtures for the Home Assistant integration tests.

These need a running Home Assistant and its test fixtures, both supplied by
`pytest-homeassistant-custom-component`, which packages ha-core's own. They run
here, beside the unit tests, in the same `pytest` invocation -- so one run covers
the whole codebase and the editor's Test Explorer can drive it.

The integration is imported as `custom_components.stiebel_eltron_ir`, the same name
Home Assistant's loader uses. That matters beyond tidiness: importing it under any
other name would create a second copy of the enums, and an identity check such as
`mode is Acp35Mode.COOL` would then fail against the loader's objects for no visible
reason. `pythonpath` in pyproject puts the repository root where that name resolves.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stiebel_eltron_ir.const import (
    CONF_EMITTER,
    CONF_MODEL,
    DOMAIN,
    MODEL_ACP35,
)

EMITTER_ID = "infrared.test_emitter"
CLIMATE_ID = "climate.stiebel_eltron_acp_35"
TIMER_ID = "sensor.stiebel_eltron_acp_35_last_known_timer"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the custom component under test."""


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable entities that ship disabled, as ha-core's fixture of this name does.

    Home Assistant defines it in `tests/components/conftest.py`, which
    pytest-homeassistant-custom-component does not package -- it carries the root
    test fixtures, not the per-domain ones. The timer sensor is a diagnostic and is
    disabled by default, so its tests ask for this.
    """
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
    ):
        yield


@pytest.fixture
def emitter(hass: HomeAssistant) -> str:
    """Put an infrared emitter in the state machine.

    The consumer base class tracks the emitter's availability by watching its
    state, so it has to exist or every entity comes up unavailable.
    """
    hass.states.async_set(EMITTER_ID, "2026-01-01T00:00:00.000+00:00")
    return EMITTER_ID


@pytest.fixture
def send_command() -> Generator[AsyncMock]:
    """Capture what would have been transmitted."""
    with patch(
        "homeassistant.components.infrared.helpers.async_send_command",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


async def build_entry(hass: HomeAssistant, emitter: str) -> MockConfigEntry:
    """Set up the integration against the mock emitter, leaving it powered off.

    Separate from the ``entry`` fixture so a test can arrange something that has
    to be true *before* setup runs -- the display unit is seeded from this Home
    Assistant install's own unit, which cannot be changed after the fact.
    """
    await async_setup_component(hass, "homeassistant", {})
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Stiebel Eltron ACP 35",
        data={CONF_EMITTER: emitter, CONF_MODEL: MODEL_ACP35},
        unique_id=f"{emitter}_{MODEL_ACP35}",
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


@pytest.fixture
async def entry(
    hass: HomeAssistant, emitter: str, send_command: AsyncMock
) -> MockConfigEntry:
    """Set up the integration against the mock emitter."""
    config_entry = await build_entry(hass, emitter)

    # Power on. The shadow state starts off, and the remote ignores every button
    # but power and timer while off, so a test that sets a fan speed or a
    # temperature from the default state is exercising something the hardware
    # cannot do. Tests about the off behaviour turn it off explicitly.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CLIMATE_ID},
        blocking=True,
    )
    send_command.reset_mock()
    return config_entry


def last_command(send_command: AsyncMock):
    """Return the Acp35Command from the most recent transmission."""
    assert send_command.await_count, "nothing was transmitted"
    return send_command.await_args.args[2]
