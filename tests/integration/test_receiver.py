"""Receiver sync: following the physical remote, and coping without a receiver."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import ATTR_FAN_MODE, HVACMode
from homeassistant.components.infrared import InfraredReceivedSignal
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from tests.common import MockConfigEntry

from custom_components.stiebel_eltron_ir.acp35 import (
    Acp35Command,
    Acp35Fan,
    Acp35Flag,
    Acp35Mode,
)
from custom_components.stiebel_eltron_ir.const import (
    CONF_DISPLAY_CELSIUS,
    CONF_EMITTER,
    CONF_RECEIVER,
    DOMAIN,
)
from custom_components.stiebel_eltron_ir.receiver import Acp35ReceiverSync

from .conftest import CLIMATE_ID, EMITTER_ID, TIMER_ID

RECEIVER_ID = "infrared.test_receiver"


@pytest.fixture
async def entry_with_receiver(
    hass: HomeAssistant, emitter: str, send_command: AsyncMock
) -> MockConfigEntry:
    """Set up the integration with a receiver configured and available."""
    hass.states.async_set(RECEIVER_ID, "2026-01-01T00:00:00.000+00:00")
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Stiebel Eltron ACP 35",
        data={
            CONF_EMITTER: emitter,
            CONF_RECEIVER: RECEIVER_ID,
            CONF_DISPLAY_CELSIUS: True,
        },
        unique_id=emitter,
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


def deliver(hass: HomeAssistant, entry: MockConfigEntry, command: Acp35Command) -> None:
    """Hand a frame to the sync as though the receiver had heard it.

    Calls the handler directly rather than standing up a receiver platform: the
    subscription itself is covered separately, and this keeps the decode path
    under test rather than Home Assistant's plumbing.
    """
    sync = Acp35ReceiverSync(hass, entry.runtime_data, RECEIVER_ID)
    sync._handle_signal(
        InfraredReceivedSignal(timings=command.get_raw_timings(), modulation=38000)
    )


def remote_frame(**overrides) -> Acp35Command:
    """Build a frame as the physical remote would have sent it.

    Passing ``fahrenheit`` alone builds a frame from a remote displaying °F,
    where the Fahrenheit field is authoritative and Celsius is its pair. The
    default Celsius is dropped in that case, since supplying both would mean
    "use these verbatim" and set the °C display bit.
    """
    fields = {"power": True, "mode": Acp35Mode.COOL, "fan": Acp35Fan.HIGH, **overrides}
    if "fahrenheit" not in overrides:
        fields.setdefault("celsius", 22)
    return Acp35Command(**fields)


class TestFollowingTheRemote:
    """A decoded frame moves the shadow state and both entities."""

    async def test_power_is_followed(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        deliver(hass, entry_with_receiver, remote_frame(power=False))
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).state == HVACMode.OFF

    async def test_mode_and_fan_are_followed(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        deliver(
            hass,
            entry_with_receiver,
            remote_frame(mode=Acp35Mode.DRY, fan=Acp35Fan.LOW),
        )
        await hass.async_block_till_done()
        state = hass.states.get(CLIMATE_ID)
        assert state.state == HVACMode.DRY
        assert state.attributes[ATTR_FAN_MODE] == "low"

    async def test_temperature_is_followed(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        deliver(hass, entry_with_receiver, remote_frame(celsius=29))
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE] == 29

    async def test_timer_is_followed(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        deliver(hass, entry_with_receiver, remote_frame(timer_hours=8))
        await hass.async_block_till_done()
        assert float(hass.states.get(TIMER_ID).state) == 8

    async def test_a_fahrenheit_frame_keeps_its_own_pairing(
        self, hass: HomeAssistant, entry_with_receiver, send_command: AsyncMock
    ) -> None:
        """63 °F pairs with 17 °C, but 17 °C alone would re-derive 62 °F.

        Following the remote must not shift the unit by a degree, so both
        temperature fields are carried through verbatim.
        """
        deliver(hass, entry_with_receiver, remote_frame(fahrenheit=63))
        await hass.async_block_till_done()

        data = entry_with_receiver.runtime_data
        assert data.state.celsius == 17
        assert data.state.fahrenheit == 63
        assert data.display_celsius is False, "b7 bit 7 says the unit shows °F"

        # And the next frame we send preserves it rather than re-deriving 62.
        await hass.services.async_call(
            "climate", "turn_on", {"entity_id": CLIMATE_ID}, blocking=True
        )
        assert send_command.await_args.args[2].fahrenheit == 63

    async def test_display_unit_is_followed(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        deliver(hass, entry_with_receiver, remote_frame(fahrenheit=75))
        await hass.async_block_till_done()
        assert entry_with_receiver.runtime_data.display_celsius is False

    async def test_following_does_not_transmit(
        self, hass: HomeAssistant, entry_with_receiver, send_command: AsyncMock
    ) -> None:
        """Hearing the remote must not echo a frame back at the unit."""
        send_command.reset_mock()
        deliver(hass, entry_with_receiver, remote_frame(celsius=25))
        await hass.async_block_till_done()
        assert send_command.await_count == 0


class TestIgnoredSignals:
    """The receiver hears every remote in the room."""

    @pytest.mark.parametrize(
        ("description", "timings"),
        [
            ("empty", []),
            ("too short", [5100, -5100, 576, -481]),
            ("some other protocol", [9000, -4500] + [560, -560] * 32 + [560]),
            ("noise", [100, -100, 100, -100, 100]),
        ],
    )
    async def test_foreign_signals_are_ignored(
        self, hass: HomeAssistant, entry_with_receiver, description, timings
    ) -> None:
        before = hass.states.get(CLIMATE_ID).state
        sync = Acp35ReceiverSync(hass, entry_with_receiver.runtime_data, RECEIVER_ID)
        sync._handle_signal(InfraredReceivedSignal(timings=timings, modulation=38000))
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).state == before, description

    async def test_a_corrupt_frame_is_ignored(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        """A frame whose checksum fails must not be applied."""
        timings = remote_frame(celsius=30).get_raw_timings()
        timings[3] = -1928  # flip a bit in b0, breaking the preamble and checksum
        before = hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE]

        sync = Acp35ReceiverSync(hass, entry_with_receiver.runtime_data, RECEIVER_ID)
        sync._handle_signal(InfraredReceivedSignal(timings=timings, modulation=38000))
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE] == before

    async def test_a_capture_missing_its_leading_mark_still_decodes(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        """A real receive buffer starts at the space before the first mark."""
        timings = remote_frame(celsius=26).get_raw_timings()[1:]
        sync = Acp35ReceiverSync(hass, entry_with_receiver.runtime_data, RECEIVER_ID)
        sync._handle_signal(InfraredReceivedSignal(timings=timings, modulation=38000))
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE] == 26


class TestSubscription:
    """The subscription follows the receiver's availability."""

    async def test_a_missing_receiver_is_not_fatal(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        """A receiver that does not exist must be tolerated, not raise."""
        sync = Acp35ReceiverSync(
            hass, entry_with_receiver.runtime_data, "infrared.does_not_exist"
        )
        stop = sync.async_start()
        assert sync._unsubscribe is None
        stop()

    async def test_a_state_without_an_entity_is_refused_quietly(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        """The stub receiver here is only a state, so subscribing is refused."""
        sync = Acp35ReceiverSync(hass, entry_with_receiver.runtime_data, RECEIVER_ID)
        stop = sync.async_start()
        assert sync._unsubscribe is None
        stop()

    async def test_stop_is_idempotent(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        sync = Acp35ReceiverSync(hass, entry_with_receiver.runtime_data, RECEIVER_ID)
        stop = sync.async_start()
        stop()
        sync._async_unsubscribe()


class TestAgainstARealReceiver:
    """The whole path, with no mocks: a real receiver entity fans a signal out.

    Everything above hands frames straight to the handler. Here the signal goes
    through Home Assistant's own subscription machinery, which is what would
    catch the subscription never being made in the first place.
    """

    async def test_a_signal_from_a_real_receiver_moves_the_entity(
        self, hass: HomeAssistant
    ) -> None:
        assert await async_setup_component(
            hass, "infrared", {"infrared": [{"platform": "fake_ir"}]}
        )
        await hass.async_block_till_done()

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Stiebel Eltron ACP 35",
            data={
                CONF_EMITTER: "infrared.fake_ir_emitter",
                CONF_RECEIVER: "infrared.fake_ir_receiver",
                CONF_DISPLAY_CELSIUS: True,
            },
            unique_id="infrared.fake_ir_emitter",
        )
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        receiver = _find_receiver(hass)
        receiver.inject(remote_frame(celsius=27, fan=Acp35Fan.LOW).get_raw_timings())
        await hass.async_block_till_done()

        state = hass.states.get(CLIMATE_ID)
        assert state.attributes[ATTR_TEMPERATURE] == 27
        assert state.attributes[ATTR_FAN_MODE] == "low"

    async def test_unloading_stops_following(self, hass: HomeAssistant) -> None:
        assert await async_setup_component(
            hass, "infrared", {"infrared": [{"platform": "fake_ir"}]}
        )
        await hass.async_block_till_done()

        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_EMITTER: "infrared.fake_ir_emitter",
                CONF_RECEIVER: "infrared.fake_ir_receiver",
            },
            unique_id="infrared.fake_ir_emitter",
        )
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # runtime_data is cleared on unload, so hold the state to inspect after.
        data = config_entry.runtime_data
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()

        # Must not raise, and must not still be following the remote.
        _find_receiver(hass).inject(remote_frame(celsius=30).get_raw_timings())
        await hass.async_block_till_done()
        assert data.state.celsius != 30


def _find_receiver(hass: HomeAssistant):
    """Return the fake receiver entity object."""
    component = hass.data["infrared"]
    for entity in component.entities:
        if entity.entity_id == "infrared.fake_ir_receiver":
            return entity
    raise AssertionError("the fake receiver did not load")


class TestWithoutAReceiver:
    """Absence of a receiver changes nothing else. This is the important one."""

    async def test_setup_succeeds(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        assert entry.data.get(CONF_RECEIVER) is None
        assert entry.runtime_data.receiver_entity_id is None
        assert hass.states.get(CLIMATE_ID).state != "unavailable"
        assert hass.states.get(TIMER_ID).state != "unavailable"

    async def test_control_is_unaffected(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": CLIMATE_ID, ATTR_TEMPERATURE: 24},
            blocking=True,
        )
        assert send_command.await_args.args[2].celsius == 24

    async def test_unloads_cleanly(self, hass: HomeAssistant, entry) -> None:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    async def test_emitter_availability_still_tracked(
        self, hass: HomeAssistant, entry, send_command: AsyncMock
    ) -> None:
        hass.states.async_set(EMITTER_ID, "unavailable")
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).state == "unavailable"


class TestFlagsAreNotFollowed:
    """Event bits describe a button press, not state, so they are not adopted."""

    async def test_event_bits_do_not_persist(
        self, hass: HomeAssistant, entry_with_receiver, send_command: AsyncMock
    ) -> None:
        deliver(
            hass,
            entry_with_receiver,
            remote_frame(celsius=23, flags=Acp35Flag.CELSIUS | Acp35Flag.TEMP_CHANGED),
        )
        await hass.async_block_till_done()
        send_command.reset_mock()

        await hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": CLIMATE_ID, ATTR_FAN_MODE: "medium"},
            blocking=True,
        )
        # A fan change carries no event bit, even though the frame we followed had one.
        assert send_command.await_args.args[2].flags == Acp35Flag.CELSIUS
