"""What each supported appliance is, and what Home Assistant builds for it.

Stiebel Eltron make more than one infrared appliance and this integration is
named for the manufacturer, not for a product. One record per model keeps the
facts that differ between them in a single place, so adding a model is an entry
here rather than a search for the literals that assumed there was only one.

Only the facts with a consumer today are recorded. The codec itself is not one of
them: nothing outside a model's own modules reaches for it, and guessing the
interface a second protocol needs before seeing one is how the wrong abstraction
gets built.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from homeassistant.components.infrared import InfraredReceivedSignal
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from .const import MODEL_ACP35
from .data import ShadowState, StiebelEltronIrData, StoredState
from .devices.acp35.climate import Acp35Climate
from .devices.acp35.receiver import handle_signal as acp35_handle_signal
from .devices.acp35.select import Acp35DisplayUnitSelect
from .devices.acp35.sensor import Acp35TimerSensor
from .devices.acp35.state import Acp35RestoreData
from .devices.acp35.state import new_state as acp35_new_state


@dataclass(frozen=True)
class ModelInfo:
    """One supported appliance."""

    # Shown on the device page. The manufacturer is the same for every model, so
    # this is the part that identifies the product.
    model: str

    # The config entry's title unless the user names it something else. It stays
    # model-specific: it names the appliance, and the device name and entity ids
    # are derived from it.
    default_title: str

    # Which platforms this model exposes. A model with no display-unit switch or
    # no timer read-out simply omits them, rather than loading a platform that
    # then adds nothing.
    platforms: tuple[Platform, ...]

    # What each of those platforms adds. The platform modules at the package root
    # hold no model knowledge of their own; they exist because Home Assistant
    # requires a module named for the platform, and they read this.
    entities: dict[Platform, tuple[type[Entity], ...]]

    # What to do with a frame the receiver heard. Recognising it is part of
    # knowing the protocol, so the subscription hands every signal here and this
    # decides whether it belongs to this model at all.
    handle_signal: Callable[[StiebelEltronIrData, InfraredReceivedSignal], None]

    # The stored payload class, so setup can validate what it loaded without
    # importing a model.
    stored_state: type[StoredState]

    # Versioned per model rather than integration-wide. A store file holds
    # exactly one model's payload, so a model changing its stored shape must
    # force a conversion for that model's files and leave every other model's
    # untouched. Passed to `Store` as its version.
    storage_version: int

    # Builds the state to start from when nothing was stored. Only the model
    # knows which of its fields are worth guessing at.
    new_state: Callable[[HomeAssistant], ShadowState]


MODELS: Final[dict[str, ModelInfo]] = {
    MODEL_ACP35: ModelInfo(
        model="ACP 35",
        default_title="Stiebel Eltron ACP 35",
        platforms=(Platform.CLIMATE, Platform.SELECT, Platform.SENSOR),
        entities={
            Platform.CLIMATE: (Acp35Climate,),
            Platform.SELECT: (Acp35DisplayUnitSelect,),
            Platform.SENSOR: (Acp35TimerSensor,),
        },
        handle_signal=acp35_handle_signal,
        stored_state=Acp35RestoreData,
        storage_version=1,
        new_state=acp35_new_state,
    ),
}
