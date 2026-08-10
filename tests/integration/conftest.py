"""Fixtures for the Home Assistant integration tests.

These run inside the devcontainer, from ha-core's test tree, because they need
its `hass` fixture and `MockConfigEntry`. See docs/ha_ir_platform/devcontainer.md.
"""

import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from tests.common import MockConfigEntry

import tests

# Home Assistant's loader imports custom integrations as `custom_components.X`
# from the config directory, which under test is tests/testing_config. Put it on
# the path so these tests import the *same* module objects the loader will, and
# not a parallel copy under a different package name — enum identity checks such
# as `mode is Acp35Mode.COOL` would silently fail against a duplicate import.
sys.path.insert(0, str(Path(tests.__file__).parent / "testing_config"))

from custom_components.stiebel_eltron_ir.const import (
    CONF_DISPLAY_CELSIUS,
    CONF_EMITTER,
    DOMAIN,
)

EMITTER_ID = "infrared.test_emitter"
CLIMATE_ID = "climate.stiebel_eltron_acp_35"
TIMER_ID = "number.stiebel_eltron_acp_35_shutdown_timer"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the custom component under test."""


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


@pytest.fixture
async def entry(
    hass: HomeAssistant, emitter: str, send_command: AsyncMock
) -> MockConfigEntry:
    """Set up the integration against the mock emitter."""
    await async_setup_component(hass, "homeassistant", {})
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Stiebel Eltron ACP 35",
        data={CONF_EMITTER: emitter, CONF_DISPLAY_CELSIUS: True},
        unique_id=emitter,
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


def last_command(send_command: AsyncMock):
    """Return the Acp35Command from the most recent transmission."""
    assert send_command.await_count, "nothing was transmitted"
    return send_command.await_args.args[2]
