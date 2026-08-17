"""What an ACP 35 frame heard on the air means.

The subscription itself is model-agnostic and lives in the integration's
`receiver.py`; this is the half that knows the protocol.
"""

import logging

from homeassistant.components.infrared import InfraredReceivedSignal

from ...data import StiebelEltronIrData
from .protocol import Acp35Command

_LOGGER = logging.getLogger(__name__)


def handle_signal(data: StiebelEltronIrData, signal: InfraredReceivedSignal) -> None:
    """Apply a received frame, ignoring anything that is not ours.

    The receiver picks up every remote in the room, so most signals are not
    ACP 35 frames. ``from_raw_timings`` returns None for those -- including
    anything that fails the preamble or checksum -- and they are dropped without
    comment.

    Our own transmissions are dropped too. The emitter and receiver are on the
    same board, so everything we send comes straight back; applying it would
    replace the shadow state with the contents of the frame, which is not the
    same thing. Switching to dry, for instance, transmits the pinned 22 °C and
    low fan, and echoing that back overwrote the setpoint and fan speed the user
    had chosen in cool.
    """
    command = Acp35Command.from_raw_timings(signal.timings)
    if command is None:
        return

    if data.async_is_own_echo(command.to_bytes()):
        _LOGGER.debug("Ignored our own transmission: %r", command)
        return

    state = data.state
    state.power = command.power
    # Mode first: the speed is stored against the mode the frame carries, so a
    # remote press changing to fan-only records that speed for fan-only and
    # leaves what cool was running at alone.
    state.mode = command.mode
    state.set_fan(command.fan)
    state.celsius = command.celsius
    state.fahrenheit = command.fahrenheit
    # Feeds the read-out only. It is never sent back: our own frames carry no
    # timer, and the next one we send cancels this. See `Acp35TimerSensor`.
    state.timer_hours = command.timer_hours

    # b7 bit 7 is the unit the air conditioner is displaying, so follow it. It is
    # shared state like everything else here, which is why the select entity
    # reads it rather than holding a copy: pressing C/F on the remote has to move
    # the select, not disagree with it.
    state.display_celsius = command.is_celsius

    _LOGGER.debug("Followed the remote: %r", command)
    data.async_notify()
