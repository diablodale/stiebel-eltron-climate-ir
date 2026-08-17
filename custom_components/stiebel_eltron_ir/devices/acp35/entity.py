"""What the shared entity base leaves to the ACP 35.

The base owns the transmit sequence; this supplies the frame. Every ACP 35 entity
builds the same one -- only the b7 event bit differs, according to which control
the user touched.
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
        changed, mirroring what the remote emits. Whether the unit needs the
        event bits at all is untested; if it turns out not to, every caller can
        simply stop passing one.

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
            timer_hours=state.timer_hours,
            flags=flags,
        )
