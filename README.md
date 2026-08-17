# Stiebel Eltron — infrared control for Home Assistant

Drive Stiebel Eltron appliances from Home Assistant over infrared, using the
[infrared entity platform](https://developers.home-assistant.io/blog/2026/03/30/infrared-entity-platform/)
introduced in Home Assistant 2026.6.

These appliances have no network interface. Their only input is a handheld
remote, so the integration reproduces what that remote transmits, byte for byte.

This is the infrared counterpart to Home Assistant core's `stiebel_eltron`
integration, which speaks modbus to a different range of products. The two do
not overlap and can be installed together.

## Supported devices

| model | protocol document | remote |
| ----- | ----------------- | ------ |
| ACP 35 air conditioner | [Stiebel Eltron air conditioner ACP 35.md](docs/Stiebel%20Eltron%20air%20conditioner%20ACP%2035.md) | TZ20160122 |

## What is here

| path | contents |
| ---- | -------- |
| [custom_components/stiebel_eltron_ir/](custom_components/stiebel_eltron_ir/) | the Home Assistant integration |
| [custom_components/stiebel_eltron_ir/devices/](custom_components/stiebel_eltron_ir/devices/) | one subpackage per appliance; each `protocol.py` is an encoder and decoder free of any Home Assistant import |
| [docs/](docs/) | one protocol document per model, each with every capture it was derived from |
| [docs/ha_ir_platform/plan.md](docs/ha_ir_platform/plan.md) | design decisions, open questions, and what the hardware still has to settle |
| [docs/ha_ir_platform/devcontainer.md](docs/ha_ir_platform/devcontainer.md) | running Home Assistant from source against this repo |
| [tools/](tools/) | Pronto decoding, and the capture tooling used with real hardware |

## Entities

Each configured appliance is one device. Which entities it has depends on the
model; the ACP 35 has three:

- **climate** — power, mode, fan speed and the temperature setpoint
- **Appliance temperature unit** (`select`) — which unit the air conditioner shows
  on its own display panel
- **Last known timer** (`sensor`) — diagnostic, disabled by default, read-only

Infrared is one-way, so the integration keeps a shadow copy of the appliance's
state and transmits all of it on every change. Entities are `assumed_state`. If an
infrared receiver is configured it follows the physical remote; without one the
integration works exactly the same, it just cannot notice the remote being used.

The timer is read-only in both directions: no frame this integration sends carries
one, so **the first change made in Home Assistant cancels a timer set on the
remote**. The appliance acting on its own timer emits no infrared, so a timer we
heard could never be tracked to expiry — retransmitting it would eventually re-arm
one that had already fired and switch the appliance off unbidden.

## Status

Working against the protocol as captured, and unverified against the appliance
itself. The open items are listed in [plan.md](docs/ha_ir_platform/plan.md) — the
significant one is the header mark, which no capture contains because the
receiver's buffer begins after it.

## Development

```bash
uv sync
uv run pytest          # encoder and capture tests, no Home Assistant needed
```

Integration tests need Home Assistant and run inside the devcontainer; see
[devcontainer.md](docs/ha_ir_platform/devcontainer.md).

## Trademark Legal Notices

All product names, trademarks and registered trademarks in the images in this
repository, are property of their respective owners. All images in this
repository are used for identification purposes only. The use of these names,
trademarks and brands appearing in these image files, does not imply
affiliation or endorsement
