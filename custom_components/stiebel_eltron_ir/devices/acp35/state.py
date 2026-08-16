"""The ACP 35's shadow state, and how it survives a restart.

The protocol carries no acknowledgement and the unit reports nothing back, so
the integration keeps a copy of what it believes the unit is doing and transmits
that whole state on every change.
"""

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Self, override

from homeassistant.helpers.restore_state import ExtraStoredData

from ...const import MODEL_ACP35
from .protocol import (
    MAX_CELSIUS,
    MAX_FAHRENHEIT,
    MAX_TIMER_HOURS,
    MIN_CELSIUS,
    MIN_FAHRENHEIT,
    Acp35Fan,
    Acp35Mode,
    celsius_to_fahrenheit,
    effective_fan,
    fahrenheit_to_celsius,
)

_LOGGER = logging.getLogger(__name__)

# The speed each mode starts on. Taken from the remote after a battery removal,
# which brings every mode back to high except dry, which returns on low.
DEFAULT_FAN_BY_MODE = {
    Acp35Mode.AUTO: Acp35Fan.HIGH,
    Acp35Mode.COOL: Acp35Fan.HIGH,
    Acp35Mode.DRY: Acp35Fan.LOW,
    Acp35Mode.FAN: Acp35Fan.HIGH,
}


@dataclass
class Acp35State:
    """What we believe the unit is currently doing.

    Defaults match the remote's own resting state: cooling, high fan, 22 °C.
    They are only used before the first restore.

    Both temperature fields are kept, rather than deriving one from the other on
    the way out, because the two mappings are not inverses: a frame from a
    remote displaying Fahrenheit can hold 63 °F with 17 °C, and re-deriving from
    17 °C would send 62 °F back and shift the unit by a degree.

    The fan speed is stored per mode because that is what the remote does: each
    mode remembers its own speed, and a mode press transmits the speed stored
    for the mode being entered. Setting cool to medium, visiting dry and coming
    back to cool returns to medium, and never to dry's low.
    """

    power: bool = False
    mode: Acp35Mode = Acp35Mode.COOL
    fan_by_mode: dict[Acp35Mode, Acp35Fan] = field(
        default_factory=DEFAULT_FAN_BY_MODE.copy
    )
    celsius: int = 22
    fahrenheit: int = 72
    timer_hours: int = 0
    # b7 bit 7: which unit the air conditioner shows on its own display. State
    # rather than configuration -- the remote's C/F button changes it and we
    # follow that, so it belongs here with everything else the unit is doing.
    # Seeded from the Home Assistant install's unit at setup.
    display_celsius: bool = True

    @property
    def fan(self) -> Acp35Fan:
        """Return the speed stored for the mode currently selected."""
        return self.fan_by_mode.get(self.mode, Acp35Fan.HIGH)

    def set_fan(self, fan: Acp35Fan) -> None:
        """Store a speed against the mode currently selected.

        Dry's slot can only ever hold low. Enforcing it here rather than only on
        the way out means no path can put a speed there that the remote could not
        have produced -- including a received frame, which is decoded from
        whatever was on the wire and is not required to be a frame the remote is
        capable of emitting.
        """
        self.fan_by_mode[self.mode] = effective_fan(self.mode, fan)

    def set_celsius(self, celsius: int) -> None:
        """Set the temperature from Celsius, repairing the Fahrenheit field."""
        self.celsius = celsius
        self.fahrenheit = celsius_to_fahrenheit(celsius)

    def set_fahrenheit(self, fahrenheit: int) -> None:
        """Set the temperature from Fahrenheit, repairing the Celsius field.

        The mirror of :meth:`set_celsius`, and needed because the two tables are
        not inverses. Whichever scale the appliance is displaying is the one the
        user is choosing on, so that field is authoritative and the other is its
        pair; deriving the wrong way round would move the displayed number.
        """
        self.fahrenheit = fahrenheit
        self.celsius = fahrenheit_to_celsius(fahrenheit)


@dataclass
class Acp35RestoreData(ExtraStoredData):
    """The shadow state, persisted independently of what the entities display.

    Restoring from displayed attributes cannot work here. The card hides the
    temperature outside cool and narrows the fan choices in dry, so a restart
    taken in one of those modes would read back the hidden value and overwrite
    what the user actually chose. Extra data is written from the state itself,
    so what is remembered does not depend on what is shown.

    The payload names the model that wrote it. Every field below means something
    only within this protocol -- `mode` 2 is dry here and could be anything
    elsewhere, and `celsius` is bounded by this appliance's range -- so a payload
    from another model must be refused outright rather than read field by field.
    """

    model: str
    power: bool
    mode: int
    fan_by_mode: dict[str, int]
    celsius: int
    fahrenheit: int
    timer_hours: int
    display_celsius: bool

    @classmethod
    def from_state(cls, state: Acp35State) -> Self:
        """Snapshot a shadow state.

        Every mode's fan speed is kept, not just the selected one. Storing only
        the current speed would lose the others across a restart and hand each
        mode back whatever the last-used mode was running.
        """
        return cls(
            model=MODEL_ACP35,
            power=state.power,
            mode=int(state.mode),
            # JSON object keys are strings, so the mode is written as one.
            fan_by_mode={
                str(int(mode)): int(fan) for mode, fan in state.fan_by_mode.items()
            },
            celsius=state.celsius,
            fahrenheit=state.fahrenheit,
            timer_hours=state.timer_hours,
            display_celsius=state.display_celsius,
        )

    @override
    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form Home Assistant stores."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self | None:
        """Rebuild from storage, or None if it is unusable in any respect.

        Storage outlives the code that wrote it, so a payload this build cannot
        read must leave the defaults in place rather than raise. This is read
        while the entity is being added, where Home Assistant catches the
        exception per entity and logs it, so raising would mean the entity never
        appears at all -- a far worse outcome than starting from the defaults.

        Every check here rejects the *whole* payload, never one field. A value
        out of range or an enum this build does not have says the writer was not
        this build -- corrupted storage, or a shape from an earlier one that no
        migration handled -- and every other field came from that same writer.
        Keeping the readable ones would assemble a state no build ever held, out
        of this build's defaults and another's values, with nothing to show for
        it. The defaults alone are at least coherent.
        """
        if "model" not in data:
            # No tag at all is not another model's payload, it is one written
            # before this build tagged them. With no migration to read it, that
            # is corruption like any other.
            return cls._refuse("it carries no model tag")
        if (stored_model := data["model"]) != MODEL_ACP35:
            # Not an error. Restore data is keyed by entity id, so an entry
            # re-added under a name some other model once used finds that
            # model's payload waiting. Refusing it is the whole point.
            _LOGGER.debug(
                "Ignoring restore data written by model %r, not %r",
                stored_model,
                MODEL_ACP35,
            )
            return None

        try:
            restored = cls(
                model=MODEL_ACP35,
                power=bool(data["power"]),
                mode=int(data["mode"]),
                fan_by_mode={
                    str(mode): int(fan) for mode, fan in data["fan_by_mode"].items()
                },
                celsius=int(data["celsius"]),
                fahrenheit=int(data["fahrenheit"]),
                timer_hours=int(data["timer_hours"]),
                display_celsius=bool(data["display_celsius"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            return cls._refuse(f"{type(error).__name__}: {error}")

        if restored.mode not in tuple(Acp35Mode):
            return cls._refuse(f"mode {restored.mode!r} is not one this build has")
        # Every mode must be present. A missing one is not "nothing was recorded
        # for it" -- this build records all of them every time -- so its absence
        # says the payload came from a build that held a different set.
        if set(restored.fan_by_mode) != {str(int(mode)) for mode in Acp35Mode}:
            return cls._refuse(
                f"fan_by_mode holds {sorted(restored.fan_by_mode)}, "
                f"not {sorted(str(int(mode)) for mode in Acp35Mode)}"
            )
        for mode, fan in restored.fan_by_mode.items():
            try:
                Acp35Fan(fan)
            except ValueError as error:
                return cls._refuse(f"fan_by_mode entry {mode!r}: {fan!r} ({error})")
        # Dry runs on low and the remote's fan button will not move it, and
        # `Acp35State.set_fan` enforces that on every write, so this build cannot
        # have produced any other pairing. Storage holding one did not come from
        # this build, which makes it corrupt like any other unreadable value.
        stored_dry_fan = restored.fan_by_mode[str(int(Acp35Mode.DRY))]
        if stored_dry_fan != Acp35Fan.LOW:
            return cls._refuse(
                f"dry is paired with {Acp35Fan(stored_dry_fan).name}, not LOW"
            )
        for name, value, low, high in (
            ("celsius", restored.celsius, MIN_CELSIUS, MAX_CELSIUS),
            ("fahrenheit", restored.fahrenheit, MIN_FAHRENHEIT, MAX_FAHRENHEIT),
            ("timer_hours", restored.timer_hours, 0, MAX_TIMER_HOURS),
        ):
            if not low <= value <= high:
                return cls._refuse(f"{name} {value!r} is outside {low}..{high}")
        return restored

    @classmethod
    def _refuse(cls, reason: str) -> None:
        """Log why a payload was rejected, and reject it."""
        # Worth saying out loud: without it, a refused payload looks exactly
        # like a first run, and the appliance quietly comes back on defaults.
        _LOGGER.warning(
            "Ignoring the stored state (%s); starting from the defaults instead",
            reason,
        )
        return None

    def apply(self, state: Acp35State) -> None:
        """Copy this snapshot over a shadow state.

        Every value here was checked by `from_dict`, including that a speed is
        present for every mode, so this only copies.

        Dry is pinned to low on the way back in, so storage written by an older
        build cannot reintroduce a pairing the remote cannot produce.
        """
        state.power = self.power
        state.mode = Acp35Mode(self.mode)
        for mode, fan in self.fan_by_mode.items():
            stored_mode = Acp35Mode(int(mode))
            state.fan_by_mode[stored_mode] = effective_fan(stored_mode, Acp35Fan(fan))
        state.celsius = self.celsius
        state.fahrenheit = self.fahrenheit
        state.timer_hours = self.timer_hours
        state.display_celsius = self.display_celsius
