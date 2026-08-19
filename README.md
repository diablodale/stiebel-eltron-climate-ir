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
one, so **the first change made in Home Assistant clears a timer set on the
remote**. That is measured against the appliance, not inferred — a timer armed at
3 hours was gone after a single temperature change.

Two consequences worth knowing before you rely on it:

- **The handset does not notice.** It never hears our frames, so it goes on
  displaying the timer it set, and pressing TIMER reopens at that stale value. The
  appliance and the remote disagree until the remote is used again.
- **Use a Home Assistant automation instead.** It keeps accurate time, and because
  every frame carries the whole state, firing power, mode, fan and temperature
  together at the scheduled moment does more than the remote's timer can.

The alternative was rejected deliberately. The appliance acting on its own timer
emits no infrared, so a timer we heard could never be tracked to expiry;
retransmitting it would eventually re-arm one that had already fired and switch
the appliance off unbidden. Losing a timer you set is the smaller failure.

## Emitter placement matters more than you would expect

**Put the emitter where it has a clear, close line of sight to the appliance's
infrared window.** Marginal reception does not simply lose commands — it produces
wrong ones. Measured on 2026-08-19, with the emitter across the room: of sixteen
commands, six were not obeyed, and the appliance twice acted on something that was
never sent, once switching from cool to fan. Moved close, all sixteen were obeyed.

The frames themselves were confirmed correct on the air in both runs, so nothing
in Home Assistant or the emitter can detect this. A frame corrupted in flight can
still decode as a valid, different command, which means the appliance does not
appear to verify the checksum the protocol carries.

## Status

The protocol is verified against a real ACP 35, not only against captures:

- every mode, every fan speed and both ends of the temperature range are acted on
  as this integration labels them
- the frames we build reach the air unchanged — all 76 distinct frames the remote
  was ever recorded producing were transmitted and read back identically
- the header mark, which no capture contains because the receiver's buffer begins
  after it, was settled by transmitting candidates at the appliance
- the appliance acts on the display-unit flag and on the mode carried in a
  power-off frame

One item remains open: the minimum spacing between frames, and whether a single
frame is reliably enough. Details, and everything the hardware settled, are in
[plan.md](docs/ha_ir_platform/plan.md).

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
