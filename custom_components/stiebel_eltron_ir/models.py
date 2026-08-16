"""What each supported appliance is, and what Home Assistant builds for it.

Stiebel Eltron make more than one infrared appliance and this integration is
named for the manufacturer, not for a product. One record per model keeps the
facts that differ between them in a single place, so adding a model is an entry
here rather than a search for the literals that assumed there was only one.

Only the facts with a consumer today are recorded. A codec, an entity list and a
received-frame handler all belong here eventually, but guessing their shape
before a second protocol exists is how the wrong interface gets built.
"""

from dataclasses import dataclass
from typing import Final

from homeassistant.const import Platform
from homeassistant.helpers.entity import Entity

from .const import MODEL_ACP35
from .devices.acp35.climate import Acp35Climate
from .devices.acp35.select import Acp35DisplayUnitSelect
from .devices.acp35.sensor import Acp35TimerSensor


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
    ),
}
