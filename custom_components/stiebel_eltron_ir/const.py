"""Constants shared by every Stiebel Eltron infrared appliance.

Nothing model-specific belongs here. The Home Assistant mappings a model needs --
which HVAC modes it has, what its fan speeds are called -- are facts about that
appliance and live with it under `devices/`.
"""

from typing import Final

DOMAIN: Final = "stiebel_eltron_ir"

CONF_EMITTER: Final = "emitter"
CONF_RECEIVER: Final = "receiver"
CONF_MODEL: Final = "model"

# Which appliance an entry drives. Written into every entry even though there is
# only one value to write, because the stored shape is the expensive thing to
# change later and the picker that offers a choice is not.
MODEL_ACP35: Final = "acp35"
