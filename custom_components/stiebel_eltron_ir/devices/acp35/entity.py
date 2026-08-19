"""What the shared entity base leaves to the ACP 35.

The base owns the transmit sequence; this supplies the frame and says what
sending it did. Every ACP 35 entity builds the same frame -- only the b7 event
bit differs, according to which control the user touched.
"""

from typing import override

from ...entity import StiebelEltronIrEntity
from .protocol import (
    Acp35Command,
    Acp35Flag,
    effective_fan,
    effective_temperature,
)


class Acp35Entity(StiebelEltronIrEntity):
    """An ACP 35 entity: knows how to say this appliance's state on the wire."""

    @override
    def _build_command(self, event: Acp35Flag | None = None) -> Acp35Command:
        """Build the full-state frame, with ``event`` added to b7.

        The base passes ``None`` when a caller named no event, which for this
        protocol is `Acp35Flag.NONE` -- b7 carrying the display unit and nothing
        else, exactly what the remote sends for a fan, mode or C/F press.

        b7 carries the display unit plus an event bit naming what the user just
        changed, mirroring what the remote emits. The unit does not need the
        event bits -- measured 2026-08-19 -- so every caller could stop passing
        one. They are kept because the remote is the specification and matching
        it costs nothing, not because anything depends on them.

        Every bit but the display unit is an event, ``TIMER_UI`` included, so
        each is passed in by the setter that caused it rather than derived from
        the state. Deriving it was a bug: a capture of the remote with a timer
        counting down shows ``b7`` bit 1 clear on an ordinary fan press, so the
        bit means the entry display is open, not that a timer is pending.
        """
        state = self._data.state
        flags = Acp35Flag.NONE if event is None else event
        if state.display_celsius:
            flags |= Acp35Flag.CELSIUS

        celsius, fahrenheit = effective_temperature(
            state.mode, state.celsius, state.fahrenheit
        )
        return Acp35Command(
            power=state.power,
            mode=state.mode,
            # Dry forces low on the remote, and dry and auto pin the temperature
            # to the default. The shadow state keeps whatever the user picked so
            # it comes back when they return to a mode that uses it.
            fan=effective_fan(state.mode, state.fan),
            # Both fields go verbatim. Passing only Celsius would re-derive the
            # Fahrenheit one, which shifts a unit displaying °F by a degree
            # whenever the two mappings disagree, as they do at 63 °F.
            celsius=celsius,
            fahrenheit=fahrenheit,
            # No timer, ever. `b2` holds whole hours and counts down, and the
            # appliance acting on its own timer emits nothing, so a value heard
            # from the remote cannot be tracked to expiry -- replaying it would
            # re-arm a timer that had already fired. Sending zero cancels
            # instead, which is the deliberate cost recorded in
            # `Acp35TimerSensor`. `timer_hours=0` with no `TIMER_UI` flag also
            # clears `b1` bit 3, so this is the unambiguous cancel the remote
            # sends for TIMER twice, not the armed-at-zero shape that winding the
            # hours down produces.
            timer_hours=0,
            flags=flags,
        )

    @override
    def _apply_transmission(self, command: Acp35Command) -> None:
        """Follow the timer the frame carried, which is always no timer.

        We know the appliance has no timer left, so the read-out says so rather
        than going on reporting the value it was last told. Without this the
        sensor would show a timer this integration had just cancelled.

        The timer alone, deliberately. It is the one field `_build_command` puts
        in the frame without reading it from the state; power and the mode go out
        verbatim, and the temperature and fan are substituted per mode, so
        copying those back would replace the setpoint the user chose in cool with
        the 22 C that dry and auto transmit.
        """
        self._data.state.timer_hours = command.timer_hours
