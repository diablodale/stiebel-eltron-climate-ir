"""What this integration reports on Home Assistant's system information page.

Infrared is one-way. When someone reports that nothing happens, the integration's
own state cannot answer it -- what decides is which emitter was configured and
whether that emitter was there to transmit through. Those are facts a reporter
would otherwise be asked to type out, and typed answers arrive misremembered or
as "not sure".

Home Assistant finds this module by name and calls `async_register`; nothing in
`__init__.py` refers to it. What it returns is on the System information page and
in what that page's **Copy** button produces, which is what
`.github/ISSUE_TEMPLATE/bug_report.yml` asks a reporter to paste.

**The returned mapping is flat, and has to be.** The page renders one table row
per key, and a value that is a dict is rendered only when it carries a `type` of
`pending`, `failed` or `date` -- any other object leaves the cell blank rather
than expanding into a tree.

So this follows the shape core's `network` section uses: a fixed set of rows,
each holding a comma-separated list with one element per configured appliance,
every element written as ``value (state)``. The lists are built from a single
iteration of the entries, so position *n* is the same appliance in every row and
the rows can be read across.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration

from .const import CONF_EMITTER, CONF_MODEL, CONF_RECEIVER, DOMAIN
from .data import StiebelEltronIrConfigEntry
from .models import MODELS

if TYPE_CHECKING:
    # Only ever needed for the annotation. Importing another component at runtime
    # would be a dependency this integration does not otherwise have, and would
    # have to be declared in the manifest to stay honest.
    from homeassistant.components.system_health import SystemHealthRegistration

# What an element holds when the appliance has nothing to report there. Written
# rather than left empty so the lists stay the same length and the rows still
# line up -- a receiver nobody configured is a fact, not a gap.
NONE = "none (none)"


@callback
def async_register(hass: HomeAssistant, register: SystemHealthRegistration) -> None:
    """Register the callback that builds the section."""
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return the version, then one row each for models, emitters and receivers."""
    integration = await async_get_integration(hass, DOMAIN)
    info: dict[str, Any] = {"version": str(integration.version)}

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        # Three empty cells would say less than three absent rows.
        return info

    # Everything below reads `entry.data` rather than `entry.runtime_data`, so an
    # entry that failed to set up is described as fully as one that loaded. That
    # is the entry a bug is most likely to be filed against, and `runtime_data`
    # does not exist on it.
    info["models"] = _row(
        f"{_model_name(entry)} ({entry.state.value})" for entry in entries
    )
    info["emitters"] = _row(
        _entity(hass, entry.data.get(CONF_EMITTER)) for entry in entries
    )
    info["receivers"] = _row(
        _entity(hass, entry.data.get(CONF_RECEIVER)) for entry in entries
    )
    return info


def _row(elements: Iterable[str]) -> str:
    """Join one element per appliance, as the network section does."""
    return ", ".join(elements)


def _model_name(entry: StiebelEltronIrConfigEntry) -> str:
    """Name the appliance this entry drives, as the device page names it."""
    key = entry.data.get(CONF_MODEL)
    # Falls back to the raw key rather than "unknown": an entry naming a model
    # this build has dropped is exactly the case worth seeing spelled out.
    info = MODELS.get(key)
    return info.model if info is not None else str(key)


def _entity(hass: HomeAssistant, entity_id: str | None) -> str:
    """Give an entity and whether it is there to be used.

    Missing and unavailable are kept apart because they have different answers: a
    missing emitter was renamed or removed and the entry needs reconfiguring,
    while an unavailable one is configured correctly and off the network. Neither
    transmits, and neither is visible in anything the integration logs.
    """
    if entity_id is None:
        return NONE
    state = hass.states.get(entity_id)
    if state is None:
        return f"{entity_id} (missing)"
    if state.state == STATE_UNAVAILABLE:
        return f"{entity_id} (unavailable)"
    return f"{entity_id} (available)"
