"""Receiver sync: following the physical remote, and coping without a receiver."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.infrared import InfraredReceivedSignal
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, SERVICE_TURN_ON
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
    CONF_EMITTER,
    CONF_MODEL,
    CONF_RECEIVER,
    DOMAIN,
    MODEL_ACP35,
)
from custom_components.stiebel_eltron_ir.receiver import Acp35ReceiverSync

from .conftest import CLIMATE_ID, EMITTER_ID, TIMER_ID, last_command

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
            CONF_MODEL: MODEL_ACP35,
        },
        unique_id=f"{emitter}_{MODEL_ACP35}",
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Power on: the remote ignores everything but power and timer while off.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CLIMATE_ID},
        blocking=True,
    )
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

    async def test_a_speed_is_stored_against_the_mode_the_frame_carries(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        """The remote stores per mode, so a followed frame must too.

        A frame carrying fan-only at low says what fan-only runs at. It says
        nothing about cool, which must still be on the speed it was left on.
        """
        state = entry_with_receiver.runtime_data.state
        state.mode = Acp35Mode.COOL
        state.set_fan(Acp35Fan.MEDIUM)

        deliver(
            hass,
            entry_with_receiver,
            remote_frame(mode=Acp35Mode.FAN, fan=Acp35Fan.LOW),
        )
        await hass.async_block_till_done()

        assert state.fan_by_mode[Acp35Mode.FAN] is Acp35Fan.LOW
        assert state.fan_by_mode[Acp35Mode.COOL] is Acp35Fan.MEDIUM

    async def test_dry_stays_low_however_the_frame_pairs_it(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        """A frame is whatever was on the wire, not what the remote can emit.

        The decoder accepts b6 = 0x22 -- dry with medium -- because nothing in
        the frame format forbids it. Following one must not put a speed in dry's
        slot that no remote press could have produced.
        """
        deliver(
            hass,
            entry_with_receiver,
            remote_frame(mode=Acp35Mode.DRY, fan=Acp35Fan.MEDIUM),
        )
        await hass.async_block_till_done()

        state = entry_with_receiver.runtime_data.state
        assert state.fan_by_mode[Acp35Mode.DRY] is Acp35Fan.LOW
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_FAN_MODE] == "low"

    async def test_temperature_is_followed(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        deliver(hass, entry_with_receiver, remote_frame(celsius=29))
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE] == 29

    @pytest.mark.usefixtures("entity_registry_enabled_by_default")
    async def test_timer_is_followed(
        self, hass: HomeAssistant, entry_with_receiver
    ) -> None:
        """The read-out is disabled by default, so this enables it."""
        deliver(hass, entry_with_receiver, remote_frame(timer_hours=8))
        await hass.async_block_till_done()
        assert float(hass.states.get(TIMER_ID).state) == 8

    async def test_a_followed_timer_rides_along_in_our_frames(
        self, hass: HomeAssistant, entry_with_receiver, send_command: AsyncMock
    ) -> None:
        """Clearing it would cancel a timer the user set on the handset.

        Both fields survive a press that is not a timer press -- that is what the
        remote does -- so a value we heard has to travel in everything we send.
        """
        deliver(hass, entry_with_receiver, remote_frame(timer_hours=8))
        await hass.async_block_till_done()

        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_FAN_MODE: "low"},
            blocking=True,
        )
        assert last_command(send_command).timer_hours == 8

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
        assert data.state.display_celsius is False, "b7 bit 7 says the unit shows °F"

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
        assert entry_with_receiver.runtime_data.state.display_celsius is False

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
                CONF_MODEL: MODEL_ACP35,
            },
            unique_id=f"infrared.fake_ir_emitter_{MODEL_ACP35}",
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
                CONF_MODEL: MODEL_ACP35,
            },
            unique_id=f"infrared.fake_ir_emitter_{MODEL_ACP35}",
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


class TestOwnEchoIsIgnored:
    """The emitter and receiver share a board, so we hear everything we send.

    Applying an echo replaces the shadow state with the contents of the frame,
    and the frame is not always the state: dry pins the temperature to 22 C and
    the fan to low. Reported from the live instance, where switching to dry
    echoed back and destroyed the setpoint and fan speed chosen in cool. No test
    caught it because the mocked emitter never transmits, so nothing echoes.
    """

    async def _set(self, hass: HomeAssistant, service: str, **data) -> None:
        await hass.services.async_call(
            CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: CLIMATE_ID, **data}, blocking=True
        )

    async def test_the_reported_sequence(
        self, hass: HomeAssistant, entry_with_receiver, send_command: AsyncMock
    ) -> None:
        """cool 20 C medium, dry, cool -> must still be 20 C and medium."""
        await self._set(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.COOL)
        await self._set(hass, SERVICE_SET_TEMPERATURE, temperature=20)
        await self._set(hass, SERVICE_SET_FAN_MODE, fan_mode="medium")

        await self._set(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.DRY)
        # Replay the frame just transmitted, exactly as the real receiver does.
        deliver(hass, entry_with_receiver, last_command(send_command))

        await self._set(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.COOL)
        attributes = hass.states.get(CLIMATE_ID).attributes
        assert attributes[ATTR_TEMPERATURE] == 20
        assert attributes[ATTR_FAN_MODE] == "medium"

    async def test_a_genuine_remote_press_is_still_applied(
        self, hass: HomeAssistant, entry_with_receiver, send_command: AsyncMock
    ) -> None:
        """The guard must not deafen us to the remote it exists to follow."""
        await self._set(hass, SERVICE_SET_TEMPERATURE, temperature=20)
        deliver(hass, entry_with_receiver, remote_frame(celsius=27))
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE] == 27

    async def test_the_window_expires(
        self, hass: HomeAssistant, entry_with_receiver, send_command: AsyncMock
    ) -> None:
        """Time-bounded, not a permanent mute on one frame's contents."""
        from custom_components.stiebel_eltron_ir import ECHO_WINDOW_SECONDS

        await self._set(hass, SERVICE_SET_TEMPERATURE, temperature=20)
        sent = last_command(send_command)
        data = entry_with_receiver.runtime_data
        # Age every remembered transmission past the window.
        data._sent = type(data._sent)(
            (frame, at - ECHO_WINDOW_SECONDS - 1) for frame, at in data._sent
        )

        deliver(hass, entry_with_receiver, sent)
        assert hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE] == 20

    async def test_an_earlier_echo_is_still_recognised(
        self, hass: HomeAssistant, entry_with_receiver, send_command: AsyncMock
    ) -> None:
        """Echoes arrive ~100 ms late, so a burst outruns a one-frame memory.

        With a single remembered frame, the first echo no longer matches by the
        time it lands and is applied as though the remote had sent it.
        """
        await self._set(hass, SERVICE_SET_TEMPERATURE, temperature=20)
        first = last_command(send_command)
        await self._set(hass, SERVICE_SET_TEMPERATURE, temperature=26)
        second = last_command(send_command)

        # Both echoes arrive after both commands were sent, oldest first.
        deliver(hass, entry_with_receiver, first)
        deliver(hass, entry_with_receiver, second)

        assert hass.states.get(CLIMATE_ID).attributes[ATTR_TEMPERATURE] == 26
