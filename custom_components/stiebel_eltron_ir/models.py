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

from .const import MODEL_ACP35


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


MODELS: Final[dict[str, ModelInfo]] = {
    MODEL_ACP35: ModelInfo(
        model="ACP 35",
        default_title="Stiebel Eltron ACP 35",
        platforms=(Platform.CLIMATE, Platform.SELECT, Platform.SENSOR),
    ),
}
