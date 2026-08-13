# Stiebel Eltron ACP 35 — infrared control for Home Assistant

Drive a Stiebel Eltron ACP 35 air conditioner from Home Assistant over infrared,
using the [infrared entity platform](https://developers.home-assistant.io/blog/2026/03/30/infrared-entity-platform/)
introduced in Home Assistant 2026.6.

The ACP 35 has no network interface. Its only input is the TZ20160122 handheld
remote, so the integration reproduces what that remote transmits, byte for byte.

## What is here

| path | contents |
| ---- | -------- |
| [custom_components/stiebel_eltron_ir/](custom_components/stiebel_eltron_ir/) | the Home Assistant integration |
| [custom_components/stiebel_eltron_ir/acp35.py](custom_components/stiebel_eltron_ir/acp35.py) | the protocol encoder and decoder, free of any Home Assistant import |
| [Stiebel Eltron air conditioner ACP 35.md](docs/Stiebel%20Eltron%20air%20conditioner%20ACP%2035.md) | the protocol itself, and every capture it was derived from |
| [docs/ha_ir_platform/plan.md](docs/ha_ir_platform/plan.md) | design decisions, open questions, and what the hardware still has to settle |
| [docs/ha_ir_platform/devcontainer.md](docs/ha_ir_platform/devcontainer.md) | running Home Assistant from source against this repo |
| [tools/](tools/) | Pronto decoding, and the capture tooling used with real hardware |

## Entities

One device with three entities:

- **climate** — power, mode, fan speed and the temperature setpoint
- **Appliance temperature unit** (`select`) — which unit the air conditioner shows
  on its own display panel
- **Timer** (`sensor`) — diagnostic, disabled by default, read-only

Infrared is one-way, so the integration keeps a shadow copy of the appliance's
state and transmits all of it on every change. Entities are `assumed_state`. If an
infrared receiver is configured it follows the physical remote; without one the
integration works exactly the same, it just cannot notice the remote being used.

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
