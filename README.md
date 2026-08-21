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

## Requirements

- **Home Assistant 2026.8 or later.** The infrared entity platform arrived in
  2026.6; 2026.8 is the floor this integration is developed and tested against.
- **An infrared emitter entity**, provided by some other integration — anything that
  offers an `infrared` entity with the emitter device class. The reference hardware
  is a [KC868-AG](https://www.kincony.com/) running ESPHome 2026.7.4, which exposes
  its `ir_rf_proxy` instances to Home Assistant as infrared entities.
- **An infrared receiver entity, optionally.** With one, Home Assistant follows the
  physical remote; without one the integration is complete and simply cannot notice
  the handset being used.

## Installation

### HACS

1. HACS → the three-dot menu, top right → **Custom repositories**
2. Add `https://github.com/diablodale/stiebel-eltron-climate-ir`, type
   **Integration**, then **ADD**
3. Install the repository from HACS, then restart Home Assistant

### Manually

Copy `custom_components/stiebel_eltron_ir/` from a release into your Home Assistant
configuration directory, so that this path exists:

```text
<config>/custom_components/stiebel_eltron_ir/manifest.json
```

Restart Home Assistant afterwards. Nothing else is copied — `tests/`, `tools/` and
`docs/` are for developing the integration, not for running it.

## Configuration

Settings → Devices & services → **Add integration** → **Stiebel Eltron (infrared)**.
One form appears, with three fields:

| field | |
| ----- | - |
| **Infrared emitter** | Required. The entity to transmit through. Only emitters are offered |
| **Infrared receiver (optional)** | Leave empty if you have none |
| **Name** | Defaults to `Stiebel Eltron ACP 35`. It names the device and the entity ids are derived from it |

Then place the emitter, which is not a formality — see
[Emitter placement matters more than you would expect](#emitter-placement-matters-more-than-you-would-expect)
before deciding where it goes.

**One entry per emitter and model.** Adding a second entry for the same appliance
model on the same emitter is refused. The frame carries no device address, so two
identical appliances hearing one emitter cannot be told apart or driven separately,
whatever Home Assistant does — a second entry could only send both the same commands.
Two *different* models sharing one emitter is fine: each decoder rejects the other's
frames.

## What is here

| path | contents |
| ---- | -------- |
| [custom_components/stiebel_eltron_ir/](custom_components/stiebel_eltron_ir/) | the Home Assistant integration |
| [custom_components/stiebel_eltron_ir/devices/](custom_components/stiebel_eltron_ir/devices/) | one subpackage per appliance; each `protocol.py` is an encoder and decoder free of any Home Assistant import |
| [docs/](docs/) | one protocol document per model, each with every capture it was derived from |
| [docs/ha_ir_platform/plan.md](docs/ha_ir_platform/plan.md) | design decisions, open questions, and what the hardware still has to settle |
| [docs/ha_ir_platform/devcontainer.md](docs/ha_ir_platform/devcontainer.md) | running Home Assistant from source against this repo |
| [docs/ci_release_plan.md](docs/ci_release_plan.md) | how this repo is licensed, linted, tested in CI, versioned and released, and why each choice was made |
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
in Home Assistant or the emitter can detect this — what leaves the LED is not what
arrives three metres away, and only the appliance knows the difference.

It has no defence either: **the ACP 35 does not verify the checksum its protocol
carries.** Frames with three different deliberately wrong checksums were all acted
on, with valid frames either side as controls. Whatever reaches it is what it
does, so a frame damaged in flight is obeyed rather than discarded.

## Status

The protocol is verified against a real ACP 35, not only against captures:

- every mode, every fan speed and both ends of the temperature range are acted on
  as this integration labels them
- the frames we build reach the air unchanged — all 76 distinct frames the remote
  was ever recorded producing were transmitted and read back identically
- the header mark, which no capture contains because the receiver's buffer begins
  after it, was settled by transmitting candidates at the appliance
- the 38 kHz carrier, which no capture records either, was settled the same way:
  sweeping it a kilohertz at a time, the appliance answers across 37–39 kHz
- the appliance acts on the display-unit flag and on the mode carried in a
  power-off frame
- a single frame is enough, and two sent back to back are both acted on down to a
  separation of 500 µs, so nothing here repeats or rate-limits

**Every question the hardware had to settle is answered.** What each session
measured, and what it ruled out, is in [plan.md](docs/ha_ir_platform/plan.md).

## Development

```bash
uv sync
uv run pytest                    # every test: codecs, tooling and the integration
uv run pytest --cov              # the same, with coverage of the whole codebase
uv run pytest tests/unit -p no:homeassistant   # the fast loop, ~3s
uv run prek install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push
```

That is the whole setup, on Linux, macOS or Windows. The integration tests need no
container: Home Assistant and its test fixtures come from
`pytest-homeassistant-custom-component`, pinned to one exact Home Assistant so the
tests prove the integration against a known version. VS Code's Test Explorer drives
the same run, including **Run Tests with Coverage**.

`uv sync` keeps the environment in uv's cache and makes `.venv` a link to it, which
`pyproject.toml` asks for with uv's `centralized-project-envs`. Activation, the git
hooks and the editor all use `.venv` as usual and need to know nothing about it.

That matters on one kind of machine and is invisible on the rest. Home Assistant
brings 129 packages, and the fixtures that walk them are punishing over a filesystem
that is not native — measured on a checkout under WSL2's `/mnt/c`, with the cache on
ext4 where uv puts it by default:

| | environment beside the repo | environment in uv's cache |
| - | --------------------------- | ------------------------- |
| `uv sync` | ~20 min | 2.2 s |
| nine integration tests | 19.0 s | 3.2 s |
| whole suite | 7m34s | ~1m35s |

An older uv ignores the unknown preview name with a warning and creates an ordinary
`.venv` directory, which works exactly as it always did.

Hardware tests are deselected by default and need the KC868-AG, the appliance, or a
human; the devcontainer is still how Home Assistant itself is run against this
integration. See [devcontainer.md](docs/ha_ir_platform/devcontainer.md).

## Trademark Legal Notices

All product names, trademarks and registered trademarks in the images in this
repository, are property of their respective owners. All images in this
repository are used for identification purposes only. The use of these names,
trademarks and brands appearing in these image files, does not imply
affiliation or endorsement
