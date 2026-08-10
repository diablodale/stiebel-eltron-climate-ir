"""End-to-end through the real infrared platform, with no mocks.

Everything else in this directory patches `async_send_command` and inspects the
Acp35Command it was handed. That proves our logic but stops short of the
platform. Here the real `infrared` integration is set up with the `fake_ir` stub
emitter, so the chain

    climate service call -> Acp35Command -> infrared.async_send_command()
        -> InfraredEmitterEntity.async_send_command() -> get_raw_timings()

runs for real and the assertions are on the microseconds that would have gone out
of the LED. This is what would catch a command the platform refuses to carry.

It cannot validate HEADER_MARK: no capture contains it, so there is nothing to
compare the first mark against. That is a hardware bisect.
"""

from custom_components.fake_ir import DATA_SENT
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from tests.common import MockConfigEntry

from custom_components.stiebel_eltron_ir.acp35 import (
    BIT_MARK,
    CARRIER_HZ,
    HEADER_MARK,
    HEADER_SPACE,
    ONE_SPACE,
    ZERO_SPACE,
    Acp35Command,
)
from custom_components.stiebel_eltron_ir.const import (
    CONF_DISPLAY_CELSIUS,
    CONF_EMITTER,
    DOMAIN,
)

FAKE_EMITTER = "infrared.fake_ir_emitter"
CLIMATE_ID = "climate.stiebel_eltron_acp_35"


async def setup_real_chain(hass: HomeAssistant) -> MockConfigEntry:
    """Set up the real infrared platform, the stub emitter and our integration."""
    assert await async_setup_component(
        hass, "infrared", {"infrared": [{"platform": "fake_ir"}]}
    )
    await hass.async_block_till_done()
    assert hass.states.get(FAKE_EMITTER) is not None, "stub emitter did not load"

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Stiebel Eltron ACP 35",
        data={CONF_EMITTER: FAKE_EMITTER, CONF_DISPLAY_CELSIUS: True},
        unique_id=FAKE_EMITTER,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def sent(hass: HomeAssistant) -> list[dict]:
    """Return everything the stub emitter recorded."""
    return hass.data.get(DATA_SENT, [])


class TestRealPlatform:
    """The command survives the trip through Home Assistant's infrared platform."""

    async def test_a_service_call_reaches_the_emitter(
        self, hass: HomeAssistant
    ) -> None:
        await setup_real_chain(hass)
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_HVAC_MODE: HVACMode.COOL},
            blocking=True,
        )
        assert len(sent(hass)) == 1

    async def test_timings_have_the_expected_shape(self, hass: HomeAssistant) -> None:
        await setup_real_chain(hass)
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 19},
            blocking=True,
        )
        timings = sent(hass)[-1]["timings"]

        # header pair + 72 bit pairs + trailer mark
        assert len(timings) == 2 + 72 * 2 + 1
        assert timings[0] == HEADER_MARK
        assert timings[1] == -HEADER_SPACE
        assert timings[-1] == BIT_MARK
        assert all(
            (value > 0) == (index % 2 == 0) for index, value in enumerate(timings)
        ), "marks and spaces must alternate"
        assert set(timings[3::2]) <= {-ZERO_SPACE, -ONE_SPACE}

    async def test_carrier_and_repeat_reach_the_emitter(
        self, hass: HomeAssistant
    ) -> None:
        await setup_real_chain(hass)
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: CLIMATE_ID, "fan_mode": "medium"},
            blocking=True,
        )
        record = sent(hass)[-1]
        assert record["modulation"] == CARRIER_HZ
        assert record["repeat_count"] == 0, "the remote never repeats"

    async def test_emitted_timings_decode_back_to_the_intended_state(
        self, hass: HomeAssistant
    ) -> None:
        """The real proof: what went out is what we meant to send."""
        await setup_real_chain(hass)
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_HVAC_MODE: HVACMode.DRY},
            blocking=True,
        )
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 28},
            blocking=True,
        )

        decoded = Acp35Command.from_raw_timings(sent(hass)[-1]["timings"])
        assert decoded is not None, "the emitted waveform is not a valid frame"
        assert decoded.celsius == 28
        assert decoded.fahrenheit == 82
        assert decoded.mode.name == "DRY"
        assert decoded.power is True

    async def test_every_frame_carries_a_valid_checksum(
        self, hass: HomeAssistant
    ) -> None:
        await setup_real_chain(hass)
        for temperature in (17, 22, 30):
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: temperature},
                blocking=True,
            )

        assert len(sent(hass)) == 3
        for record in sent(hass):
            state = Acp35Command.from_raw_timings(record["timings"]).to_bytes()
            assert state[0] == 0x55
            assert state[8] == sum(state[:8]) & 0xFF
