"""What the System information page reports about this integration.

The bug report form asks for the block that page copies instead of asking a
reporter to type the same facts, so what it contains is worth pinning down. The
emitter's availability is the reason the section exists: an emitter that is
missing or off the network transmits nothing, and nothing else in Home Assistant
says so in terms a bug report carries.

The mapping is asserted whole rather than by substring. Its rows are read across
-- position n is the same appliance in each -- so a row that quietly changes
length stops the section meaning what it says.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    get_system_health_info,
)

from custom_components.stiebel_eltron_ir.const import (
    CONF_EMITTER,
    CONF_MODEL,
    CONF_RECEIVER,
    DOMAIN,
    MODEL_ACP35,
)

from .conftest import EMITTER_ID, build_entry

RECEIVER_ID = "infrared.test_receiver"
TITLE = "Stiebel Eltron ACP 35"


@pytest.fixture
async def system_health(hass: HomeAssistant) -> None:
    """Set up the component that collects the sections."""
    assert await async_setup_component(hass, "system_health", {})


async def info(hass: HomeAssistant) -> dict[str, str]:
    """Return this integration's section."""
    return await get_system_health_info(hass, DOMAIN)


class TestOneRowPerKindOfFact:
    """Fixed rows, one element per appliance, as core's network section does."""

    async def test_reports_the_model_the_emitter_and_that_there_is_no_receiver(
        self, hass: HomeAssistant, system_health: None, entry
    ) -> None:
        # The version is the manifest's, not one the reporter recalls from HACS:
        # it is what the release gate checks against the tag.
        assert await info(hass) == {
            "version": _manifest_version(),
            "models": "ACP 35 (loaded)",
            "emitters": f"{EMITTER_ID} (available)",
            "receivers": "none (none)",
        }

    async def test_reports_a_configured_receiver_and_its_state(
        self, hass: HomeAssistant, system_health: None, send_command: AsyncMock
    ) -> None:
        hass.states.async_set(EMITTER_ID, "2026-01-01T00:00:00.000+00:00")
        hass.states.async_set(RECEIVER_ID, "2026-01-01T00:00:00.000+00:00")
        await _build(hass, receiver=RECEIVER_ID)

        assert (await info(hass))["receivers"] == f"{RECEIVER_ID} (available)"

    async def test_reports_nothing_but_the_version_once_the_last_entry_is_removed(
        self, hass: HomeAssistant, system_health: None, entry
    ) -> None:
        # Three empty cells would say less than three absent rows. Reached by
        # deleting the appliance while Home Assistant runs -- with none ever
        # added the integration never loads, and there is no section to read.
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

        assert await info(hass) == {"version": _manifest_version()}


class TestTheEmitterIsThePoint:
    """Whether the thing that transmits is there to transmit."""

    async def test_an_unavailable_emitter_says_so(
        self, hass: HomeAssistant, system_health: None, entry
    ) -> None:
        hass.states.async_set(EMITTER_ID, STATE_UNAVAILABLE)
        await hass.async_block_till_done()

        assert (await info(hass))["emitters"] == f"{EMITTER_ID} (unavailable)"

    async def test_a_missing_emitter_is_not_the_same_as_an_unavailable_one(
        self, hass: HomeAssistant, system_health: None, entry
    ) -> None:
        # Renamed or deleted, rather than offline. The two need different
        # answers, so the section must not collapse them into one word.
        hass.states.async_remove(EMITTER_ID)
        await hass.async_block_till_done()

        assert (await info(hass))["emitters"] == f"{EMITTER_ID} (missing)"


class TestTheRowsLineUp:
    """Position n is the same appliance in every row."""

    async def test_a_second_appliance_appears_in_the_same_position_everywhere(
        self, hass: HomeAssistant, system_health: None, send_command: AsyncMock
    ) -> None:
        second_emitter = "infrared.other_emitter"
        hass.states.async_set(EMITTER_ID, "2026-01-01T00:00:00.000+00:00")
        hass.states.async_set(second_emitter, "2026-01-01T00:00:00.000+00:00")
        hass.states.async_set(RECEIVER_ID, STATE_UNAVAILABLE)
        await _build(hass, emitter=EMITTER_ID)
        await _build(hass, emitter=second_emitter, receiver=RECEIVER_ID)

        reported = await info(hass)

        assert reported["models"] == "ACP 35 (loaded), ACP 35 (loaded)"
        assert reported["emitters"] == (
            f"{EMITTER_ID} (available), {second_emitter} (available)"
        )
        # The appliance with no receiver still holds its place, or the second
        # appliance's receiver would read as the first's.
        assert reported["receivers"] == f"none (none), {RECEIVER_ID} (unavailable)"

    async def test_an_entry_that_failed_to_load_is_described_in_full(
        self, hass: HomeAssistant, system_health: None, emitter: str
    ) -> None:
        # The state a bug report is most likely to be filed from. Everything the
        # rows carry comes from the entry's data, which survives a failed setup,
        # so a broken appliance is described rather than silently omitted.
        with patch(
            "custom_components.stiebel_eltron_ir.async_setup_entry",
            return_value=False,
        ):
            entry = MockConfigEntry(
                domain=DOMAIN,
                title=TITLE,
                data={
                    CONF_EMITTER: emitter,
                    CONF_RECEIVER: RECEIVER_ID,
                    CONF_MODEL: MODEL_ACP35,
                },
            )
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        assert await info(hass) == {
            "version": _manifest_version(),
            "models": "ACP 35 (setup_error)",
            "emitters": f"{emitter} (available)",
            "receivers": f"{RECEIVER_ID} (missing)",
        }

    async def test_a_model_this_build_does_not_know_is_named_anyway(
        self, hass: HomeAssistant, system_health: None, emitter: str
    ) -> None:
        # Written by a later build, or naming a model since dropped. Saying
        # "unknown" would hide the one thing worth knowing about it.
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=TITLE,
            data={CONF_EMITTER: emitter, CONF_MODEL: "acp99"},
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert (await info(hass))["models"] == "acp99 (setup_error)"


async def _build(
    hass: HomeAssistant, emitter: str = EMITTER_ID, receiver: str | None = None
) -> MockConfigEntry:
    """Set up one entry, optionally with a receiver."""
    if receiver is None:
        return await build_entry(hass, emitter)

    await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        data={
            CONF_EMITTER: emitter,
            CONF_RECEIVER: receiver,
            CONF_MODEL: MODEL_ACP35,
        },
        unique_id=f"{emitter}_{MODEL_ACP35}",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _manifest_version() -> str:
    """Read the version the loader will report, from the file it reads.

    Read here rather than asked of the loader, so the test fails if the two ever
    disagree instead of agreeing with itself.
    """
    manifest = (
        Path(__file__).parents[2]
        / "custom_components"
        / "stiebel_eltron_ir"
        / "manifest.json"
    )
    return json.loads(manifest.read_text())["version"]
