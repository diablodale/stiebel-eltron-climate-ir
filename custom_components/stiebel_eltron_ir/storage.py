"""Where an appliance's shadow state is kept between runs.

One file per config entry, holding one copy of the state every entity shares.

Not `RestoreEntity`, which was the earlier mechanism and the wrong one: it is
keyed by entity id, so a state belonging to the config entry ended up stored once
per entity. Those copies could disagree -- renaming an entity changes its key and
orphans its copy, disabling one stops it updating, and each expires on its own --
and since every entity restored separately over the shared object, one copy being
refused while another was accepted left the accepted values in place. One file
keyed by the entry makes all of that unrepresentable rather than merely unlikely.

The key is the entry id: a ULID Home Assistant assigns at creation and never
exposes for editing. Renaming an entry changes its title, not this. It is already
the device identifier and the prefix of every entity's unique id.
"""

import logging
from typing import Any, override

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# How long to wait before writing after a change. Long enough that a script
# setting mode, fan and temperature in succession writes once rather than three
# times; short enough to be irrelevant to a person. A reload flushes explicitly
# and Home Assistant stopping triggers Store's own final write, so nothing is
# lost by waiting.
SAVE_DELAY_SECONDS = 10


class StiebelEltronIrStore(Store[dict[str, Any]]):
    """The shadow state for one config entry.

    `version` is the model's, not the integration's, and is passed in by the
    caller. A file holds exactly one model's payload, so a model changing its
    stored shape must force a conversion for that model's files and leave every
    other model's alone.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, version: int) -> None:
        """Prepare the store for one config entry."""
        super().__init__(hass, version, f"{DOMAIN}.{entry_id}")

    @override
    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert a payload written by an older version.

        There are no older versions yet, so every call raises. `NotImplementedError`
        is the signal `Store` expects, and it is what makes the two cases behave
        differently without either being written here:

        - a differing *minor* version with the same major: `Store` catches this
          and uses the payload unchanged, because minor bumps are required to be
          backward compatible;
        - an older *major*: `Store` re-raises, and the entry fails.

        Failing is deliberate rather than a gap. This exists armed instead of
        empty so that bumping a model's `storage_version` without writing the
        conversion fails the first time it is exercised. Discarding instead would
        be worse than it looks: unreadable data loses nothing recoverable, but
        data that is merely *unconverted* is state a correct build could have
        read, and replacing it with defaults would destroy it and hide the
        omission at once.
        """
        raise NotImplementedError(
            f"No conversion from storage version "
            f"{old_major_version}.{old_minor_version} to {self.version}."
            f"{self.minor_version}"
        )
