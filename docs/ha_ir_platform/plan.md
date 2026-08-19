# Stiebel Eltron ACP 35 → Home Assistant infrared platform

## Step 0 — Save this plan

First action: save this plan **unchanged** to `docs/ha_ir_platform/plan.md`.

## Context

The ACP 35 is IR-only. Last year the IR protocol was reverse-engineered from 39 Pronto
captures and documented in [Stiebel Eltron air conditioner ACP 35.md](../Stiebel%20Eltron%20air%20conditioner%20ACP%2035.md).
The goal now is to drive the unit from Home Assistant via the new
[infrared entity platform](https://developers.home-assistant.io/blog/2026/03/30/infrared-entity-platform/)
(HA 2026.6+), where an emitter integration (ESPHome on the KC868-AG) exposes an
`InfraredEmitterEntity` and a consumer integration builds commands with
`infrared_protocols.commands.Command` and sends them via `infrared.async_send_command()`.

Before writing any integration the protocol was re-reviewed — and it was wrong.

Throughout this document, **frame** means one complete IR transmission: header, 72 bits,
trailer. The remote sends exactly one frame per button press and never repeats.

## The documented 69-bit frame is an artifact; the real frame is 72 bits / 9 bytes

Two bugs in `pronto_analyzer.py` compounded:

1. `decode_to_binary()` paired timings as `(timings[i], timings[i+1])` from a hardcoded
   `start_idx = 8` and classified each pair by its **sum**. That straddles the mark/space
   boundary, so it recovers bit values only by luck — it drops leading bits and silently
   merges a bit wherever two short elements are adjacent. The `10101` "preamble" and the
   split-nibble field layout are both products of this misalignment.
2. The `Sequence length validation: FAILED — expected 152, got 151` warning was the real
   clue and was dismissed. It is not corruption: ESPHome's `ProntoProtocol::decode()`
   writes `dump_number_((data.size() + 1) / 2)` as the pair count and then dumps **every**
   element of the raw buffer, so an odd-length buffer rounds the pair count up.
   `pronto_analyzer.py`'s `expected = 4 + pairs*2` assumption is what's wrong. All 39
   captures are 151 words = 147 durations = a complete frame.

147 is odd, and the last duration is the receive-idle timeout, which is necessarily a
space — so buffer index 0 is a **space** and marks sit at the odd indices. That makes this
ordinary pulse-distance encoding (constant mark; `0`/`1` carried by the space length), and
the variable elements at indices 2, 4, … 144 give exactly **72 bits** on all 39 captures.
The frame is byte-aligned:

```text
55  32  00  07  00  00  31  C0  7F      (power on, cool, high fan, 19 °C / 66 °F)
b0  b1  b2  b3  b4  b5  b6  b7  ck
```

| byte | field |
| ---- | ----- |
| `b0` | constant `0x55` |
| `b1` | bits 7-4 = °C − 16 (1..14 → 17..30 °C); bit 3 = pending timer will switch the unit off; bit 1 = power on; bits 2,0 = 0 |
| `b2` | timer hours, plain binary 0..24 |
| `b3` | °F − 59 (3..27 → 62..86 °F) |
| `b4` | 0 (never observed non-zero) |
| `b5` | 0 (never observed non-zero) |
| `b6` | bits 7-4 = fan (1 = low, 2 = med, 3 = high); bits 3-0 = mode (0 = auto, 1 = cool, 2 = dry, 3 = fan) |
| `b7` | one state bit + several per-press event bits — see below |
| `ck` | `sum(b0..b7) & 0xFF` |

**`ck` validates on 39/39 captures.** The doc's "initialise the sum with `0x55`" was just
the constant byte `b0` being excluded from the sum and re-added as a magic seed.

Both temperature fields are always populated. **Corrected during Phase 3** — the
clamp originally written here does no work: `clamp(round(17 × 9/5 + 32), 62, 86)`
is 63, but the capture for 17 °C carries 62 °F. The actual behaviour is two
lookup tables, and they are *not* inverses of each other:

- **°C → °F** is `round(°C × 9/5 + 32)` at all 14 values except 17 °C, which
  ships as 62 °F. The scales' endpoints are pinned: 17 °C / 62 °F are both the
  remote's minimum, 30 °C / 86 °F both its maximum. Not `floor()` — that would
  also change 21, 22, 26 and 27 °C, and the captures show it does not.
- **°F → °C** is `round((°F − 32) × 5/9)` at all 25 values.

So 17 °C pairs out to 62 °F, while 63 °F pairs back to 17 °C. Whichever unit the
user selected is authoritative and the other is derived from it. A clamp is a
no-op in both directions over the whole valid domain, and no input lands on an
exact `.5`, so rounding mode never matters.

### `b7` — one state bit, the rest are per-press event bits

This is the only byte whose value is not a pure function of the machine's state. The
proof: with the machine in an identical state (22 °C, cool, high fan, °C display), `b7`
differs purely by which button produced the frame — `0xC0` after a temperature press,
`0x80` after a fan or mode press, `0x88` after a power press.

| bit | kind | meaning |
| --- | ---- | ------- |
| 7 `0x80` | **state** | display unit: `1` = °C, `0` = °F. Persists across every frame. |
| 6 `0x40` | **event** | `1` only in the frame caused by a temperature up/down press; `0` in every other frame. Not set by up/down while in the timer UI, so it tracks the temperature *value* changing, not the button. (n = 17) |
| 3 `0x08` | **event** | `1` only in frames caused by the power button, for both on and off. (n = 3) |
| 1 `0x02` | **event** | `1` while the remote is in its timer-entry UI. |
| 0 `0x01` | unknown | set in exactly one capture (`0x03`, first press of a timer cancel). Unexplained. |
| 5,4,2 | — | never observed set. |

So bits 6, 3 and 1 are never "always 1" or "always 0" — they are `1` in the one frame that
button produces and `0` everywhere else. Their *purpose* is untested; most likely they
tell the indoor unit which field changed so it can flash the right segment on its display.

**Open question, deferred to testing:** does the unit require them, or does it act on
`b1`/`b2`/`b3`/`b6` regardless? Until that is answered the encoder reproduces the remote's
behaviour byte-for-byte, which the captures fully specify.

## Physical layer

Pulse-distance, MSB first, `b0` first, no repeat sequence, 74 pairs = header + 72 bit
pairs + trailer.

**Carrier: use 38000 Hz.** The captures contain *no* frequency information —
`ProntoProtocol::decode()` hardcodes `uint16_t frequency = 38000U`, so the `006D` in every
capture is just `REFERENCE_FREQUENCY / 38000` written back out. The "38028.9 Hz" in the
current doc is that constant round-tripped through 4-digit hex, not a measurement. Keep it
a named module constant so it is trivially tunable if the unit turns out to be fussy.

Timings below are averaged over all 39 captures using ESPHome's actual integer timebase
(`to_timebase_(38000) = 1000000 / 38000 = 26 µs`, not 26.296) with the ±20 µs
`MARK_EXCESS_MICROS` compensation removed (`true_mark = printed + 20`,
`true_space = printed − 20`):

| element | true µs | spread | n |
| ------- | ------- | ------ | - |
| header mark | **unknown — see below** | | |
| header space | 5100 | 5024–5102 | 39 |
| bit mark | 576 | 540–644 | 2808 |
| space = `0` | 481 | 474–500 | 2097 |
| space = `1` | 1928 | 1904–1956 | 711 |
| trailer mark | 555 | 540–566 | 39 |
| trailing gap | not a protocol value | exactly 9990 in all 39 | 39 |

The trailing gap is identical to the bit in every capture because it is ESPHome's default
`idle: 10ms` receive timeout, not something the remote emits. A real protocol gap would
jitter. Use ≥ 10 ms between frames and don't treat it as measured.

Bit mark and trailer mark agree within tolerance; use one constant for both.

## The one unknown, and why re-capturing does not fix it

Every capture's buffer *begins* at the header space. The mark that must precede it was
never recorded — ESPHome's receive buffer starts at the first edge it can measure from, so
the leading mark is lost before the dumper ever sees it. **A fresh `dump: raw` capture
would very likely be missing the same element**, which is why re-capturing is not worth
doing: it would confirm the mark/space assignment that parity already proves, and still
leave the header mark unknown.

Instead, treat the header mark as the single tunable constant and resolve it during the
transmit testing that has to happen anyway. `HEADER_MARK` gets an ordered candidate list —
`5100` (symmetric with the space, the common case), then `4400`, `3000`, `9000`, and `0`
(no header mark at all) — and the first value the unit responds to wins. That is about
five button presses in HA developer tools, and unlike a capture it proves what the *unit*
accepts rather than what the remote emits.

Everything else a re-capture campaign would have chased (`b7` policy, fan-auto, dry/fan
interaction) also needs the unit to respond, not a capture, so it folds into
**Verification** below.

> **Phases 1–5 are the original plan, kept as the record of what was intended
> and why.** Their file paths are no longer the as-built ones: model code moved
> under `devices/<model>/` and the codec is `devices/acp35/protocol.py`. For the
> layout and rules as they stand, read "Adding another device" and "Settled
> design decisions" below instead.

## Phase 1 — Rewrite the protocol doc and tooling

**[Stiebel Eltron air conditioner ACP 35.md](../Stiebel%20Eltron%20air%20conditioner%20ACP%2035.md)**

- Replace *IR protocol analysis* → *Mode* with the corrected 72-bit / 9-byte spec, the
  `b7` state-vs-event table, and the physical-layer table above.
- Correct the carrier claim: 38 kHz assumed, not measured.
- Keep every raw capture block verbatim — they are the regression fixtures.
- Add an appendix **"Superseded 69-bit interpretation"** recording the old table, the two
  analyzer bugs, and the `(data.size() + 1) / 2` rounding that explains `151 != 152`, so
  the provenance of the correction is clear.
- Update *Equipment* to name the HA infrared platform as the target.

**Delete** `pronto_analyzer.py` and `checksum.py` (git history preserves them); their jobs
are done and their logic is wrong.

**Replace [requirements.txt](requirements.txt) with `pyproject.toml`.** The current file
exists only to feed `pronto_analyzer.py` (`matplotlib`, `scikit-learn`, and a pinned `pip`),
so nothing in it survives. `pyproject.toml` then carries dependencies, the pytest markers
and lint config in one place:

```toml
[project]
name = "stiebel-eltron-acp35-ir"
requires-python = ">=3.14"          # infrared-protocols needs 3.14; ha-core needs 3.14.2
dependencies = ["infrared-protocols>=9"]

[dependency-groups]
dev = ["pytest"]                    # no group for the hardware tests; see below

[tool.pytest.ini_options]
markers = [
  "hardware: needs the KC868-AG, the ACP 35, or the original remote",
  "manual: needs a human to press a button or observe the unit",
]
addopts = "-m 'not hardware'"
```

**Python version is a real constraint, not a formality.** This machine's `python3` is
**3.12.3**, but `infrared-protocols` 9.0.0 requires `>=3.14` and ha-core requires
`>=3.14.2`, so the encoder cannot even import under the system interpreter. `uv` is already
installed at `~/.local/bin/uv`, which solves it without touching the system Python:

```bash
uv python install 3.14
uv sync
uv run pytest
```

`infrared-protocols` itself has zero runtime dependencies, so this stays a light install.

**New `tools/acp35_cli.py`** — one correct tool replacing both scripts:

- `--extract` : pull every Pronto capture block out of the `.md`, decode each to 9 bytes,
  and write `tests/captures.jsonl` (pronto text, label, bytes, decoded state).
- default stdin mode: Pronto or raw in → decoded human-readable state out (the useful half
  of the old [decode.py](decode.py)).
- Imports the shared encoder/decoder from Phase 3 rather than re-implementing bit slicing.

## Phase 2 — Local HA development environment

Set up a Home Assistant core devcontainer so the integration can be developed and tested
without touching the real AC. Host is Windows + Docker Desktop + WSL2; follow
[setup_devcontainer_environment](https://developers.home-assistant.io/docs/setup_devcontainer_environment/).

This repo stays where it is on `C:` and is bind-mounted into the container. Confirmed
constraints of that choice, to design around rather than rediscover: `/mnt/c` is **9p**
(`cache=0x5`, `msize=65536`), and **inotify does not fire for Windows-hosted files under
WSL2**. So: no HA auto-reload on edit, no `pytest-watch`, and slow bulk I/O. Restart HA
manually after edits, and run pytest explicitly. Only *this repo* pays that cost — ha-core
itself lives on ext4, where the heavy dependency install and core test runs happen.

1. Node LTS is already installed in WSL via nvm — `/home/dale/.nvm/versions/node/v24.19.0/bin/npx`.
   One gotcha: nvm is sourced from `~/.bashrc`, so a **non-interactive** shell falls back to
   the Windows Node at `/mnt/c/Program Files/nodejs/npx`, which would hand Windows paths to
   a Linux workspace. Any scripted invocation must run under `bash -lic`, or
   `source ~/.nvm/nvm.sh` first. Verify with `command -v npx` → must not be under `/mnt/c`.
2. Clone the fork onto ext4 (945 GB free), *not* under `/mnt/c`:

   ```bash
   git clone https://github.com/diablodale/ha-core.git ~/src/ha-core
   cd ~/src/ha-core && git remote add upstream https://github.com/home-assistant/core.git
   ```

3. Bring the container up, bind-mounting this repo:

   ```bash
   npx --yes @devcontainers/cli up --workspace-folder ~/src/ha-core \
     --mount type=bind,source=/mnt/c/njs/stiebel-eltron-climate-ir,target=/workspaces/acp35
   ```

   `up` already runs the container's `postCreateCommand`
   (`git config --global --add safe.directory … && script/setup`) and `postStartCommand`
   (`script/bootstrap`), so there is no separate setup step to invoke — just expect the
   first `up` to take a while. The container forwards `appPort` 8123, so the HA the
   REST-based tests talk to is `http://localhost:8123`, and its interpreter is
   `/home/vscode/.local/ha-venv/bin/python`.

   **Smoke-test the mount before going further** — Docker Desktop has to translate the
   `/mnt/c` path back to a Windows path, and it either works or it doesn't:

   ```bash
   npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
     sh -c 'ls /workspaces/acp35 && touch /workspaces/acp35/.mnt-probe'
   ```

   If the mount fails or is unusably slow, fall back to developing inside
   `~/src/ha-core/config/custom_components/` and `rsync` back to this repo before
   committing. Decide this once, up front; don't fight it later.

4. Symlink `config/custom_components/stiebel_eltron_ir` → `/workspaces/acp35/custom_components/stiebel_eltron_ir`
   so `hass -c config` (the devcontainer's *Run Home Assistant* task) loads the live code.
5. Confirm the environment: run HA, check the `infrared` integration is present, and run a
   subset of core's suite (`pytest tests/components/infrared`) to prove the harness works.
6. **Write a dev-only stub emitter**, `config/custom_components/fake_ir/` — roughly 30
   lines exposing one `InfraredEmitterEntity` whose `async_send_command()` just logs
   `command.modulation` and `command.get_raw_timings()` and stashes them for assertions.
   This is what makes hardware-free testing possible: the entire chain (climate service
   call → `Acp35Command` → `infrared.async_send_command()` → emitter) runs in the
   devcontainer and we can assert the exact µs list. It never ships; it lives only in the
   devcontainer's `config/`.

Three test tiers follow from this:

- **Encoder tests** (Phase 3) are plain `pytest` in this repo with no HA import — they run
  on the host in seconds via `uv run pytest` on the uv-managed 3.14, since the system
  Python 3.12 cannot import `infrared-protocols`.
- **Integration tests** (Phase 4) need HA's fixtures and run inside the devcontainer.
- **Live-HA smoke tests** drive the real HA UI in the devcontainer against `fake_ir`,
  proving config flow, entity behaviour and emitted timings without any hardware.

If the integration is ever upstreamed, the fork is already in place to move
`custom_components/stiebel_eltron_ir` to `homeassistant/components/` and open a core PR.

## Phase 3 — The encoder (`Acp35Command`)

**New `custom_components/stiebel_eltron_ir/acp35.py`** — deliberately free of any
`homeassistant` import so it can be lifted into
[home-assistant-libs/infrared-protocols](https://github.com/home-assistant-libs/infrared-protocols)
as a PR later without rework.

Model it on `infrared_protocols/commands/general_electric.py` (module-level timing
constants, `get_raw_timings()`, `from_raw_timings()` classmethod, `_is_close` /
`_decode_bit` helpers) and on `commands/panasonic_ac.py` (full-state AC: an `IntEnum` per
field, a state byte list, checksum appended last, validation in `__init__`):

```python
CARRIER_HZ  = 38000    # not measured; ESPHome hardcodes 38 kHz when dumping Pronto
HEADER_MARK = 5100     # UNVERIFIED. candidates: 5100, 4400, 3000, 9000, 0 (none)
HEADER_SPACE, BIT_MARK, ZERO_SPACE, ONE_SPACE = 5100, 576, 481, 1928

class Acp35Mode(IntEnum):   AUTO = 0; COOL = 1; DRY = 2; FAN = 3
class Acp35Fan(IntEnum):    AUTO = 0; LOW = 1; MEDIUM = 2; HIGH = 3   # AUTO unverified

class Acp35Flag(IntFlag):   CELSIUS = 0x80; TEMP_CHANGED = 0x40; POWER_PRESSED = 0x08
                            TIMER_UI = 0x02

class Acp35Command(Command):          # infrared_protocols.commands.Command
    def __init__(self, *, power: bool, mode, fan, celsius: int,
                 timer_hours: int = 0, flags: Acp35Flag = Acp35Flag.CELSIUS,
                 modulation: int = CARRIER_HZ) -> None: ...
    def get_raw_timings(self) -> list[int]: ...
    @classmethod
    def from_raw_timings(cls, timings: list[int]) -> Self | None: ...
```

- `__init__` validates 17 ≤ °C ≤ 30 and 0 ≤ timer_hours ≤ 24, derives `b3` from `b1` with
  the clamped conversion, writes `flags` to `b7` verbatim, appends `ck`.
- `flags` carries the whole of `b7`, so the caller decides the event-bit policy in one
  place and it can be reduced to just `CELSIUS` if testing shows the rest are ignored.
- `repeat_count = 0` — the remote never repeats, confirmed across all 39 captures.
- `from_raw_timings()` tolerates a missing leading header mark, so it decodes both our own
  transmissions and ESPHome-captured buffers. It powers Phase 5 and gives a free
  round-trip test.

**New `tests/test_acp35.py`** — regression over `tests/captures.jsonl`:

- every capture decodes to the expected 9 bytes and its checksum validates;
- `Acp35Command(**decoded).get_raw_timings()` re-encodes to the same 72 bits;
- `from_raw_timings(get_raw_timings())` round-trips across a spread of states;
- boundaries: 17 °C → 62 °F, 30 °C → 86 °F, out-of-range raises `ValueError`.

## Phase 4 — The Home Assistant custom integration

**New `custom_components/stiebel_eltron_ir/`**

| file | contents |
| ---- | -------- |
| `manifest.json` | `"domain": "stiebel_eltron_ir"`, `"dependencies": ["infrared"]`, `"config_flow": true`, `"iot_class": "assumed_state"` |
| `const.py` | domain, config keys, HA↔protocol enum maps |
| `config_flow.py` | `EntitySelector(domain="infrared")` for the emitter, an **optional** one for the receiver, and a °C/°F display-unit choice |
| `__init__.py` | config-entry setup, forward to `climate` and `number` |
| `climate.py` | the climate entity |
| `number.py` | timer-hours entity |
| `acp35.py` | Phase 3 encoder |
| `strings.json`, `translations/en.json` | config-flow text |

> **Superseded.** `RestoreEntity` was the wrong mechanism and was replaced on
> 2026-08-16; see "The shadow state is stored per config entry" under Settled
> design decisions. The file layout below was also superseded when model code
> moved under `devices/`. Kept as the record of what was planned.

**`climate.py`** — `Acp35Climate(InfraredEmitterConsumerEntity, ClimateEntity, RestoreEntity)`.
`InfraredEmitterConsumerEntity` (from `homeassistant.components.infrared.helpers`) already
provides `_send_command()` and emitter-availability tracking; set
`self._infrared_emitter_entity_id` from the config entry.

- `_attr_assumed_state = True`; IR is one-way, so the entity owns the full shadow state
  (power, mode, fan, °C, timer hours, display unit) and restores it via `RestoreEntity`.
- Every setter mutates the shadow state, builds one `Acp35Command`, and calls
  `_send_command()` — the protocol has no incremental commands.
- `hvac_modes`: `OFF`, `AUTO`, `COOL`, `DRY`, `FAN_ONLY`. `OFF` clears `b1` bit 1 and keeps
  the last mode in `b6`, exactly as the remote does. No heat — the ACP 35 is cooling-only.
- `fan_modes`: `LOW`, `MEDIUM`, `HIGH`; `AUTO` added only if testing confirms `b6` high
  nibble `0`.
- `min_temp` 17, `max_temp` 30, `target_temperature_step` 1.
- Supported features: `TARGET_TEMPERATURE | FAN_MODE | TURN_ON | TURN_OFF`.
- `b7` policy lives in one helper: `Acp35Flag.CELSIUS` (when °C) OR'd with the event bit
  for the field being changed, matching the remote exactly. One-line change to a constant
  `b7` if testing shows the event bits are ignored.
- **Round the requested temperature at this boundary** (raised in Phase 3, *narrowed in
  Phase 4*). `Acp35Command` deliberately raises `ValueError` outside 17–30 °C rather than
  clamping, because a caller asking for 35 °C has a bug and should hear about it. The
  Phase 3 worry was that a `climate.set_temperature` service call would carry that
  straight through to the encoder — **it does not**: Home Assistant's climate component
  validates against `min_temp`/`max_temp` itself and raises `ServiceValidationError`
  before the entity is called (`homeassistant/components/climate/__init__.py`). What it
  does *not* do is round, so a half-degree passes through and must be rounded here.
  Rounding is half-up, since `round()` would send 20.5 down to 20 but 21.5 up to 22.
  A clamp stays as belt and braces for the restore path.
- The same turned out to be true for `set_fan_mode` and `set_hvac_mode`: Home Assistant
  rejects values outside the advertised `fan_modes` / `hvac_modes` before the entity sees
  them, so no defensive mapping is needed.

**`number.py`** — one `NumberEntity`, 0–24 h, step 1. `0` means timer off (`b1` bit 3
clear, `b2` = 0); non-zero arms it. Folds both timer fields into a single control rather
than a switch + number pair.

## Phase 5 — Receiver sync (optional feature)

**This must degrade gracefully.** The receiver entity is optional in the config flow. If
the ESPHome device exposes no `InfraredReceiverEntity`, or the user leaves the field
blank, the integration loads and works exactly as in Phase 4 — climate + timer control,
just without state feedback. No error, no unavailable entity; at most a debug log line.
Nothing in Phases 3–4 may depend on a receiver existing.

When a receiver *is* configured, `Acp35Climate.async_added_to_hass()` calls
`infrared.helpers.async_subscribe_receiver(hass, receiver_id, self._handle_signal)` and
registers the unsubscribe via `self.async_on_remove()`. `_handle_signal` runs
`Acp35Command.from_raw_timings(signal.timings)`; on a match it updates the shadow state and
writes it, so using the original remote no longer desyncs HA; on a non-match it returns
silently. Subscribing directly, rather than also inheriting `InfraredReceiverConsumerEntity`,
avoids a diamond over `InfraredConsumerEntity`.

## Adding another device

The integration is named for the manufacturer, not a product. Stiebel Eltron make
more than one infrared appliance and the next is expected to be another air
conditioner, though it could be a heater. The layout below was settled on
2026-08-16 so that adding one is a new directory plus one registry entry rather
than a search through the code for the literals that assumed there was only one.

### Layout

```text
custom_components/stiebel_eltron_ir/
  __init__.py      setup and unload                          integration-wide
  data.py          StiebelEltronIrData, the entry alias       integration-wide
  storage.py       StiebelEltronIrStore, the save delay       integration-wide
  const.py         DOMAIN, CONF_*, MODEL_*                    integration-wide
  models.py        ModelInfo, MODELS                          integration-wide
  entity.py        StiebelEltronIrEntity base                 integration-wide
  receiver.py      StiebelEltronIrReceiverSync lifecycle      integration-wide
  config_flow.py   StiebelEltronIrConfigFlow                  integration-wide
  climate.py   \
  select.py     >  look up the model, add its entities        integration-wide
  sensor.py    /
  devices/<model>/
    protocol.py    the codec                       (no homeassistant import)
    state.py       the shadow state, its stored payload, and new_state
    entity.py      supplies _build_command and _apply_transmission
    climate.py     the climate entity and its HVAC/fan maps
    select.py, sensor.py, receiver.py
```

### Rules

- **One codec per model at `devices/<model>/protocol.py`**, importing nothing from
  `homeassistant` and nothing from a sibling model. That is what keeps it
  contributable to `infrared-protocols`, and what lets the host test suite import
  it with no Home Assistant present. Both `devices/__init__.py` files are empty
  for the same reason: anything re-exported there would be imported on that path.
- **One protocol document per model under `docs/`**, carrying its own captures.
  The document is the only source of truth for its corpus; register it in
  `CORPORA` in `tests/conftest.py` with its expected count.
- **`models.py` must not be imported by the modules it registers.** It imports
  them, so anything they import must not reach back. This is why an entity reads
  `self._data.model` rather than looking the record up: `async_setup_entry`
  resolves the record once and puts what is needed on the runtime data.
- **Entity translation keys in `strings.json` are shared** across models and stay
  model-neutral. `appliance_temperature_unit` and `last_known_timer` already are. A
  key whose meaning differs by model gets its own key rather than a reused one.
- **A frame can change the appliance in ways no entity asked for, and the model
  says so in `_apply_transmission`.** The base owns the transmit sequence and calls
  it with the frame just sent. Most fields were read out of the shadow state, so
  the default does nothing; the ACP 35 uses it for the timer, which its frames
  always cancel.
- **The shadow state is persisted per config entry, never per entity.** One
  `Store` file keyed by `entry_id`, loaded in `async_setup_entry` before the
  platforms are forwarded. No entity may inherit `RestoreEntity`: that is keyed by
  entity id, so a state belonging to the entry ends up stored once per entity, the
  copies drift, and a rename orphans one of them. Loading before any entity exists
  is also what makes entity add order irrelevant.
- **The stored payload names the model that wrote it**, and `from_dict` refuses
  anything else. Every stored field means something only within one protocol —
  `mode` 2 is dry for the ACP 35 and could be anything elsewhere — so a foreign
  payload is refused whole rather than read field by field. The same applies to
  any value this build could not have written: absent, out of range, or an enum it
  does not have. Without a migration, each says the writer was not this build.
- **Storage is versioned per model**, through `ModelInfo.storage_version`. A file
  holds one model's payload, so one model changing its shape must not force a
  conversion on another's files. A version this build cannot read or convert
  **fails the entry** rather than falling back to defaults: `Store` writes back
  after migrating, so defaulting would overwrite a newer file and destroy state a
  re-upgrade could have read.
- **A config entry is unique on emitter *and* model.** Two appliances of the same
  model cannot share an emitter, because the frame carries no device address; two
  different models can, because each codec rejects the other's frames.

### What a heater will need

Not built, but the split above is what makes each of these a local change.

- `hvac_modes` is model data. `HVAC_TO_MODE` and the cooling-only comment live in
  `devices/acp35/climate.py`; a heater needs `HVACMode.HEAT` and has no dry, and
  could not share a table with the ACP 35.
- The `supported_features` rules — temperature hidden outside cool, fan reduced to
  low in dry, both hidden when powered off — are ACP 35 remote behaviour rather
  than integration policy, and stay in the model's climate module.
- The temperature bounds and the two non-inverse conversion tables are ACP 35
  firmware facts and stay in its `protocol.py`. Another model will have its own
  range and may have its own tables.
- The rule that the entity's scale follows the Home Assistant profile rather than
  the appliance is about how Home Assistant converts, not about any appliance, so
  it belongs with the shared climate behaviour.
- A model with no display-unit switch or no timer read-out omits `Platform.SELECT`
  or `Platform.SENSOR` from its `platforms`, and the root dispatcher adds nothing.

### Still coupled to the ACP 35

Recorded rather than fixed, because a second protocol is what decides the shape:

- The `ShadowState` and `StoredState` aliases in `data.py` still resolve to
  `Acp35State` and `Acp35RestoreData`. Everything annotates against the aliases
  rather than the classes, so those two lines are the whole seam: grepping either
  name finds exactly what a second protocol has to revisit, and whether they
  become type variables or shared bases depends on what the second pair look
  like.

## Settled design decisions

### The timer is read-only and never replayed. Settled 2026-08-17

Home Assistant cannot set a timer, and it does not carry one either. Every frame
we send holds `timer_hours = 0`, so the first change made in Home Assistant after
the remote armed a timer cancels it. The `Last known timer` sensor is a read-out
of the last frame we know about and nothing more.

This replaces a defect. The timer had stopped being settable but `b2` and `b1`
bit 3 still travelled in every frame, so whatever the receiver last heard kept
being retransmitted:

```text
t=0     remote sets 3 h          shadow stores 3
t=1h    any change made in HA    we transmit timer=3, armed
                                 -> the appliance now switches off at t=4h
```

**Nothing could clear it.** The appliance acting on its own timer is not a button
press and emits no infrared, so expiry is invisible to us. Only a cancel heard
from the remote set it back to zero. `b2` counts down (answered 2026-08-13), so
the value we replayed was also wrong by however long we had held it.

Replaying was originally kept because it is what the remote does: both fields
survive a press that is not a timer press (question 5). That reasoning does not
hold. The remote can replay correctly because it counts down internally. We
cannot see expiry at all, so replaying the byte without the clock produces
something the remote never produces — an expired timer re-armed, switching the
appliance off at a time nobody asked for, for as long as nobody notices.

Both directions lose something, and the choice is which failure to take:

| | what is lost | how it fails |
| - | ------------ | ------------ |
| replay | nothing, until a timer expires unseen | the appliance switches off unbidden, unbounded in time |
| send zero | a timer set on the handset, the moment Home Assistant is touched | the timer does not happen |

Sending zero fails toward *nothing happening*, is explainable in one sentence,
and needs no unanswered protocol knowledge. The countdown alternative — hold a
deadline, derive the hours — needs to know whether `b2` is `ceil(remaining)` or a
decrement on each whole hour, which the captures do not settle, and it stays
approximate regardless: whole hours locate an expiry only within an hour.

Consequences worth stating:

- **The handset disagrees with the appliance after we cancel.** It does not hear
  our frame and goes on displaying the timer it set, until it is used again.
- **`timer_hours = 0` with no `TIMER_UI` flag clears `b1` bit 3**, which is the
  unambiguous cancel the remote sends for TIMER twice, not the armed-at-zero
  shape that winding the hours down leaves behind. The derivation in
  `Acp35Command.__init__` gives this for free.
- **The read-out follows our own frames.** We know a frame we sent carried no
  timer, so the sensor reads zero afterwards rather than continuing to report a
  timer this integration had just cancelled.
- **It is not persisted.** A value written before a restart says nothing about
  the appliance afterwards, because the appliance kept counting down. The
  entity's own `last_reported` is what tells the user how fresh a reading is.

**Measured 2026-08-19, and the inference was right: our frames clear the timer.**
A timer armed at 3 h on the remote (`55 7A 03 0E 00 00 21 82 83`) was cleared by
one ordinary setpoint change of ours (`55 82 00 10 00 00 21 C0 C8`), which the
appliance demonstrably received — the panel moved from 23 to 24 °C at the same
time. So **any command sent from Home Assistant clears a timer the user set on
the remote.**

That is the price of this decision, not an argument against it. The remote is the
specification for what the bytes mean and what the appliance does with them; it
does not oblige us to implement every feature it has. The frames we send are ones
the remote itself emits whenever no timer is set, and the appliance treats them
exactly as it treats the remote's. Choosing not to transmit timers is a scope
decision, and this is its consequence — now measured rather than assumed, and
pinned by `test_timer.py` so a change in the appliance's behaviour would show up.

**Users are told in the README**, since a timer set on the remote disappearing
after an unrelated adjustment in Home Assistant is surprising, along with the two
consequences that follow: the handset does not notice and goes on displaying the
timer it set, and a Home Assistant automation does the job better anyway because
every frame carries the whole state.

Not in the entity's description, because Home Assistant has none. Entity
translations accept `name`, `state`, `state_attributes` and `unit_of_measurement`
and nothing else — see `script/hassfest/translations.py` in ha-core. The entity
name carries what it can: *Last known timer*.

### The shadow state is stored per config entry. Settled 2026-08-16

`RestoreEntity` was the original mechanism and was the wrong one. It is keyed by
*entity* id, while the shadow state belongs to the config entry, so every entity
inherited `extra_restore_state_data` and stored the whole state: the climate
entity persisted the display unit, which is the select's, and the select
persisted the temperature, fan and timer, which are the climate's. Two complete
copies, three with the timer read-out enabled.

Duplication was not the whole of it. The copies had independent lifetimes and
could disagree — renaming an entity changed its restore key and orphaned its
copy, disabling one stopped it updating, and each expired on its own after seven
days. And because every entity restored separately over the shared object, one
copy being refused while another was accepted left the accepted values in place,
so "a refused payload means defaults" was not a guarantee the code could make.

The justification for the duplication had been that entity add order is not
guaranteed. That problem only existed because the data was in per-entity storage
to begin with.

Now: one `Store` file per entry, keyed on `entry_id`, loaded in
`async_setup_entry` before the platforms are forwarded. Entities persist nothing
and no longer inherit `RestoreEntity`. Divergence is unrepresentable rather than
merely unlikely, there is one accept-or-refuse decision, and renaming or
disabling an entity no longer loses anything. Verified against the hardware: an
entity id renamed between restarts keeps its state.

### Mode-dependent controls are hidden, not shown inert. Settled 2026-08-13

The remote adjusts the temperature only in cool, forces low fan in dry, and
answers nothing but power and timer while off. The climate entity mirrors that by
dropping `TARGET_TEMPERATURE` and `FAN_MODE` from `supported_features`, and by
narrowing `fan_modes` to low in dry.

The alternative considered was keeping every control present and reporting the
pinned value. Rejected:

- `supported_features` is a bitmask with no read-only state, so a control that is
  present but does nothing looks identical to one that works. Home Assistant's
  climate platform cannot render a disabled control with an explanation.
- Home Assistant validates `set_temperature` against `supported_features` before
  the entity is called, so hiding the feature makes a wrong automation fail with
  a `ServiceValidationError` instead of silently doing nothing.
- The cost predicted for hiding -- the card resizing as controls appear and
  disappear on a mode change -- does not occur in practice. Confirmed against the
  live instance.

### Appliance temperature unit is an entity, not a setup question. Settled 2026-08-13

`CONF_DISPLAY_CELSIUS` was a config-flow checkbox, "Air conditioner displays
Celsius". Three problems, in increasing order of importance:

- **The label described state but the field was an instruction.** It sets `b7`
  bit 7 on everything we transmit, so it does not report what the unit is
  showing, it decides what the unit *will* show.
- **It could not be changed afterwards.** Config-entry data with no options flow,
  so a user who picked wrong had to delete and re-add the integration.
- **The hardware already answers it.** `receiver.py` overwrites `display_celsius`
  from any frame the receiver hears, so with a receiver configured the setup
  answer is replaced the first time anyone touches the remote.

The third rules out configuration entirely rather than arguing for an options
flow, which is what the intended fix had been. A value the hardware overwrites at
runtime is state, and state belongs in an entity. It became a `select`, named
"Appliance temperature unit", seeded from the Home Assistant install's own unit,
persisted with the rest of the shadow state, and followed from the receiver. One
value, so the select, the receiver and the transmitted frame cannot disagree.

The appliance acts on the bit -- pressing C/F moves its own display panel -- so
this is not cosmetic. It does not decide the scale Home Assistant controls in;
that follows the profile's unit, for the reasons in the protocol document's
"Two scales, not one value shown twice".

## Verification

**Everything is a pytest test**, including the hardware steps. Nothing is a prose checklist
someone has to remember to perform.

The `hardware` and `manual` markers declared in `pyproject.toml` (Phase 1) are excluded by
`addopts` default, so a bare `uv run pytest` is always safe and never reaches for a device.
Every `manual` test also carries `hardware`, so that single filter covers both. Run them
deliberately: `uv run pytest -m hardware`, or `-m "hardware and not manual"` for the subset
that needs no human, `-s` for the ones that prompt.

`tests/hardware/conftest.py` supplies the fixtures that make hardware tests skip cleanly
rather than fail: `ha` (a REST session from `HA_URL`/`HA_TOKEN`, `pytest.skip` if unset or
Home Assistant is unreachable), `journal` (the frames `acp35_bench` recorded, with a helper
that waits for the next one and `pytest.skip`s if the receiver never became available), and
`ask(question)` (resolves against `answers.toml`, prompts if a tty is attached, otherwise
skips printing the question).

No direct connection to the ESPHome device is needed from the test process: the bench is
already inside Home Assistant, on the other end of the same API. So there is no
`aioesphomeapi`, and **no dependency group for the hardware tests at all** — REST over
`urllib` is the whole requirement, and `uv run pytest -m hardware` needs nothing installed
beyond the ordinary development environment.

That is a constraint rather than a preference. `tests/hardware/conftest.py` is imported when
pytest *enters the directory*, which happens on every run, before `-m` deselection and even
when the directory holds no tests at all. A third-party client imported there breaks
`uv run pytest` outright for anyone who has not installed it — demonstrated, not assumed: an
`import httpx` in that file turned a passing 885-test run into `1 error during collection`.
A test module may import whatever it likes, because it is only imported when it is about to
be collected. A conftest has no such isolation.

### Always runs — pure Python, no HA, no hardware

| module | asserts |
| ------ | ------- |
| `tests/test_protocol.py` | field packing, both °C↔°F lookup tables and the rules that generated them, checksum, `from_raw_timings(get_raw_timings())` round-trip, 17 °C → 62 °F and 30 °C → 86 °F boundaries, out-of-range raises `ValueError` |
| `tests/test_captures.py` | all 39 captures, **read from the protocol document rather than a generated `captures.jsonl`**, decode to the expected 9 bytes, checksum-validate, re-encode bit-identically, and match expectations written from the document's prose |
| `tests/test_cli.py` | `tools/acp35_cli.py` decodes an ESPHome log line, a bare Pronto code and raw timings, and `--document` decodes every capture in text, table and bytes form. There is no `--extract`: dropping `captures.jsonl` removed the need |

### Devcontainer — needs HA, no hardware

Guarded by a module-level `pytest.importorskip("homeassistant")` so they simply skip on the
host instead of erroring.

These live in `tests/integration/` and run from ha-core's test tree; see
[devcontainer.md](devcontainer.md).

| module | asserts |
| ------ | ------- |
| `test_config_flow.py` | flow completes with and without a receiver selected; a config entry with no receiver loads and works; the model is recorded, and an unsupported one fails the entry |
| `test_climate.py`, `test_select.py`, `test_sensor.py` | every service call produces the expected `Acp35Command`; `OFF` keeps the last mode in `b6`; the entities share one state without clobbering each other; every frame carries no timer, and the read-out follows it to zero |
| `test_storage.py` | the state is stored once per config entry and no entity persists anything; it survives a reload and an entity-id rename; the timer read-out deliberately does not; storage this build cannot read or convert fails the entry and leaves the file untouched |
| `test_scales.py` | the entity drives on the Home Assistant profile's scale while the appliance's own display unit follows the select |
| `test_emit_live.py` | drives live HA against the `fake_ir` stub emitter and asserts the captured µs list against timings decoded from the corresponding `.md` capture. **This is the real encoder proof** — HA → `infrared` → emitter, end to end. It cannot cover `HEADER_MARK`, since no capture contains it |
| `test_receiver.py` | feeds recorded remote timings straight into the model's `handle_signal()` to exercise the Phase 5 decode path, plus garbage timings that must be ignored silently |

### `-m hardware` — the harness for the device

**What** these tests ask is not written here. Every inquiry lives exactly once, in
*Open questions the hardware must settle* below; this section is only the harness that
puts each one in front of the device. A row that adds a question rather than citing one
is a bug in this document.

**Decided: bring the device to the code.** The KC868-AG is added to the *devcontainer's*
Home Assistant by IP address, since mDNS discovery does not cross Docker Desktop's NAT.
The alternative — copying the integration into production HA — was rejected: it would
test the code in an instance we cannot restart freely, and every question below needs
restarts. Wiring is in [devcontainer.md](devcontainer.md).

The device exposes the new infrared platform entities, an emitter and a receiver, which
is what makes the rest of this section possible. Our integration is exercised exactly as
it will ship rather than through a substitute transport.

#### What the developer's IR device's own config settles

ESPHome 2026.7.4, `infrared:` with two `ir_rf_proxy` instances over
`remote_transmitter` `ir_tx` (GPIO2) and `remote_receiver` `ir_rx` (GPIO23, inverted).
Entity ids follow from the names: `infrared.kc868_ag_ir_proxy_transmitter` and
`infrared.kc868_ag_ir_proxy_receiver`.

Four things worth having on the record before a session:

- **`idle` is not overridden, so it is ESPHome's default 10 ms.** That confirms rather
  than infers the 9990 µs tail on all 39 captures. It also bounds question 9: two frames
  closer together than 10 ms arrive as *one* merged buffer, so a loopback capture cannot
  measure a gap shorter than that. The unit's own tolerance can still be probed; only our
  ability to observe it over the receiver stops at 10 ms.
- **No `dump:` is configured.** The Pronto log lines that produced the original corpus no
  longer appear. Nothing needs them — the bench takes timings over the native API — but
  do not go looking for `remote.pronto` in the logs and conclude the receiver is broken.
- **`filter` and `buffer_size` are defaults**, 50 µs and 10 kB. The shortest duration in
  the corpus is 474 µs and a frame is 74 RMT symbols, so neither is close to binding.
- **BLE is active**: `esp32_ble_tracker` plus `bluetooth_proxy` with active connections.
  Scanning competes for interrupt latency on the same chip that is timing IR edges. If
  captured durations come back noisier than the corpus's tight spreads, disabling BLE and
  recapturing is the first diagnostic, not a protocol mystery.

#### Getting at the platform from outside Home Assistant

Neither half of the infrared platform is reachable over REST. `async_subscribe_receiver`
delivers signals to an in-process callback and fires no event, and `async_send_command`
takes a `Command` object, which no service call can carry. So a dev-only component,
`tests/custom_components/acp35_bench`, is loaded inside Home Assistant to bridge both
directions. It writes every received frame to a JSONL journal in this repo — reachable
from the host through the bind mount while Home Assistant is still writing it.

`tools/hw.py` drives a session from the host: `mark` labels what is about to be pressed,
`journal` decodes what was heard, `pronto` renders new frames as document-ready blocks.
The bench never decodes anything itself; that is `hw.py`'s job, using the shipping
`devices/acp35/protocol.py`, so the code validating a capture is the same code that will encode our
transmissions rather than a second implementation that could agree with itself.

#### Human answers are recorded, not typed

A hardware run cannot depend on somebody watching a terminal, and an assistant driving
the session cannot hold one open at all. So `ask()` resolves against
`tests/hardware/answers.toml` first: an answer already recorded means the test asserts
against it non-interactively; missing with a tty attached means it prompts and records
what it is told; missing with no tty means the test **skips and prints the question**.

`uv run pytest -m hardware` is therefore always safe to run, and its output is a precise
list of what remains unanswered.

**The file is gitignored and is not evidence.** It records one session, not a fact
about the appliance: question 8 was run twice on 2026-08-19 and answered its own
questions contradictorily, the only difference being where the emitter sat. Two
consequences follow, and both matter more than the convenience above:

- **A recorded answer is asserted instead of the appliance.** Re-running a sweep
  transmits every frame again and then compares strings to themselves; only the
  loopback assertions genuinely re-verify anything. A green re-run is not a fresh
  measurement, and nothing about it should be read as one.
- **Conclusions belong here, not there.** This document carries them with their
  dates and their conditions; the answers file carries neither. Delete a line to be
  asked again, or the file to run a session from scratch.

Where a question needs the operator's eyes, prefer encoding the answer in the unit's own
display over asking once per case. The header-mark bisect sends each candidate as a
different target temperature, so one glance at the display — "what does it read?" —
identifies the winner instead of five separate confirmations.

| module | marks | settles | how it drives the device |
| ------ | ----- | ------- | ------------------------ |
| `tests/hardware/test_header_mark.py` | `hardware, manual, disruptive` | 7 | **Written, and answered.** Sends each candidate as the appliance's own state with one field changed — the setpoint — so an accepted frame moves the temperature and nothing else. No power cycling. Two passes in opposite orders; the panel identifies the winner. If all fail, vary `CARRIER_HZ` too |
| `tests/hardware/test_behaviour.py` | `hardware, manual` | 8, 11, 12, 13, 14, 15 | One parametrised case per question, each sending a frame and `confirm`ing what the unit did. Answers get folded back into the doc and the encoder |
| `tests/hardware/test_frame_timing.py` | `hardware, manual` | 9 | Repeats one command at decreasing separations and `confirm`s how many the unit acted on |
| `tests/hardware/test_loopback.py` | `hardware` | 10 | **Written, and passing.** Fully automatic. A session fixture transmits once and skips everything if nothing comes back. Otherwise each of the 76 distinct corpus frames is re-encoded, sent, and the journalled capture decoded and compared with what the remote produced for that state |
| `tests/hardware/test_receiver_sync.py` | `hardware, manual` | — | Not a question: end-to-end proof of Phase 5. Skips unless the device exposes a receiver entity, prompts for a press on the physical remote, asserts the climate entity followed. A companion case clears the receiver and asserts climate + timer still work |

Questions 1–6 needed no module here: they were capture exercises, run through
`acp35_bench` and `tools/hw.py`, and are answered.

On the loopback test specifically: the receiver sits centimetres from the emitting LED and
will likely saturate. A *decode failure* is therefore inconclusive, not a bug — the fixture
skips rather than fails. Damping the LED with paper or aiming the pair at a wall may help.

#### The header mark: still a bisect, but confirm it in one press

ESPHome's infrared platform delivers `event.timings` over the native API with no Pronto
conversion, which briefly looked like it might recover the leading mark the dumper lost.
It almost certainly does not. The ESPHome device yaml points the `ir_rf_proxy` receiver at
`remote_receiver_id: ir_rx` — the *same* component the Pronto dumper read. Both consume
one buffer, and the mark is missing from the buffer itself, not from the conversion. The
bisect stands.

Confirm it anyway on the first frame of the first session, because it costs nothing:
`tools/hw.py journal` prints the duration count immediately. **147 durations starting
negative** means the corpus convention holds and question 7 needs the bisect. **148
starting positive** means the native path does deliver the mark, and question 7 is
answered by reading it off. Anything else means something about the receiver changed
since the original captures and the timing statistics need re-checking before the corpus
is extended.

### Power the device independently before any transmit test

The KC868-AG was plugged into the development PC's USB port. Transmitting drops
the rail far enough to re-enumerate the board's CH340 USB-serial bridge — Windows
plays its device-arrival chime and Device Manager flickers — while the ESP32
keeps running, so Home Assistant sees no disconnect and the device's own console
log stays silent. It is intermittent: two chimes in three transmits.

The chime is cosmetic. The corruption is not. Of 24 of our own frames heard back
through the receiver, **one arrived truncated to 144 durations instead of 148**,
a 4% failure rate. All 132 frames from the battery-powered remote were intact.

Every question from 7 onward asks "did the unit respond?", and a supply that
mangles one frame in twenty-five makes a "no" ambiguous between *wrong header
mark* and *the LED browned out mid-frame*. Question 9 would be measuring the
power supply rather than the air conditioner.

**Resolved 2026-08-11: the device now runs from its own supply.** 72 further
transmits produced no truncation at all, against 1 in 24 before. One frame of the
72 came back at 150 durations, but that is a different fault: elements 138-140
were 189, -233, 189 us, and they sum to 611 -- a single bit mark split in three by
a spurious edge. That is the receiver mis-sampling a good transmission, not a
damaged one, and it is the same ambient infrared that fills the journal with
two-element noise bursts.

Keep the distinction when reading results. A truncated *transmission* means the
unit got a broken frame and its silence says nothing. A *receive* glitch means the
unit got a good frame and only our recording of it is damaged, so the test can
simply be repeated.

### The loopback works, and it detects bad transmissions

The plan assumed `test_loopback.py` would usually skip: receiver centimetres from
the emitting LED, saturating, nothing decodable. It does not saturate. Our own
transmissions come back cleanly at 148 durations, which makes question 10
answerable and gives every other transmit test a free integrity check — a frame
that does not return as exactly 148 durations was not sent properly and must be
discarded rather than counted as an answer.

Two consequences for the encoder, both open:

- **Our frames are 148 durations where the remote's are 147**, and the extra one
  is our leading `HEADER_MARK`. The receiver captures a leading mark when there
  is one to capture, so the remote does not appear to send one at all. This is
  the strongest evidence yet on question 7 and may mean `HEADER_MARK = 0`.
- ~~**`from_raw_timings()` cannot decode our own transmissions.**~~ **Fixed.** It
  now drops a leading mark longer than a bit mark along with its space, so a
  frame with our `HEADER_MARK` decodes the same as one without. `test_protocol.py`
  round-trips `from_raw_timings(get_raw_timings())` and both truncated shapes,
  so question 10 is no longer blocked on it.

### Known issue: the development host's clock is unusable for measurement

**Diagnosed 2026-08-17, mitigated, not fixed.** The corrected clocks run fast and
the wall clock steps backwards several times a minute, so a timestamp or an
interval taken on the host does not mean what it says. One clock is exempt, and
that exemption is the workaround.

**The hardware is fine; the correction on top of it is not.** `CLOCK_MONOTONIC_RAW`
reads the clocksource with no NTP or tick adjustment applied, and across three
90 s runs against a raw SNTP query to ntp.ubuntu.com it measured 0.9986, 1.0004
and 1.0028 — accurate to about 0.3%, which is the precision of the SNTP
round-trip estimate rather than a bound on the clock. Everything derived from it
after correction is worse, and unstable between runs:

| clock | vs NTP | |
| ----- | ------ | - |
| `CLOCK_MONOTONIC_RAW` | 1.0004 | the clocksource, uncorrected — **sound** |
| `CLOCK_MONOTONIC` | 1.0084 – 1.0946 | fast, and never steps back to compensate |
| `CLOCK_REALTIME` | 0.9996 – 1.0039 | right on average only because it is stepped backwards |

The lever being misused is `tick`, the microseconds of time the kernel credits
per timer tick. Its default is 10000 and its range is ±10%. Sampling it finds it
rewritten every few seconds and never left at the default: one 90 s window held
23 changes oscillating between 9389 and 10243. **Setting it back by hand does
nothing** — an `adjtimex` write of 10000 was overwritten within about two
seconds. Because `tick` is applied above the clocksource, swapping between
`hyperv_clocksource_tsc_page`, `hyperv_clocksource_msr` and `acpi_pm` changes
nothing; all three measured 7–9.5% fast. **Keep the default clocksource.**

**Two disciplines, two references, one clock.** systemd-timesyncd disciplined the
guest against ntp.ubuntu.com over the network, while Hyper-V's TimeSync feeds it
the Windows host clock, which Windows keeps against time.windows.com. Independent
controllers correcting the same clock from different sources interfere by
construction, and they had something to disagree about: WSL's `CLOCK_REALTIME` sat
between 0.55 s and 1.19 s away from the Windows clock across an evening of
measurements. Removing one of them is what the fourfold improvement below came
from.

**What the conflict does not explain is the rate**, and that is the part still
open. `tick_usec` has exactly one writer in the kernel —
`if (txc->modes & ADJ_TICK) ntpdata->tick_usec = txc->tick;` in
`kernel/time/ntp.c`. No kernel path reaches it, so a privileged userspace process
is calling `adjtimex` with `ADJ_TICK` several times a minute, and neither
discipline is known to be the one doing it:

- **Not systemd-timesyncd alone.** Stopped, the tick kept being rewritten, twelve
  times in ninety seconds — so it is not the only writer, whatever it does while
  running.
- **Not the Hyper-V guest driver.** `drivers/hv/hv_util.c` in the WSL 6.18 kernel
  touches no frequency or tick at all; `hv_set_host_time` calls
  `do_settimeofday64`, a step, and `timesync_implicit` only promotes a SAMPLE to a
  SYNC when the guest is *behind* — "If set treat SAMPLE as SYNC when clock is
  behind".
- **Not [the wrong-MSR clocksource bug](https://github.com/microsoft/WSL2-Linux-Kernel/commit/73049104541866f41d5497d7a4cb23541812dc39)**,
  which used an ARM64 register on x86 and "entirely breaks timekeeping on guests
  without a TSC". It is fixed in this branch, dated one day before this kernel was
  built, it is on the MSR path where ours is the TSC page, and `CLOCK_MONOTONIC_RAW`
  reading correctly is direct evidence that the clocksource read it repairs is
  already sound here.

That driver does explain the **backward steps**: the guest runs fast, so stepping
it to host time on a SYNC moves it back. The rate error is a separate fault on
top.

**A third distro can be the writer.** WSL2 runs every distro in one virtual
machine sharing one kernel, and `docker-desktop` is running here, so a process
outside Ubuntu sets the tick for Ubuntu and never appears in its `ps`. Finding it
needs `strace` or `bpftrace` — neither installed — plus root.

**Mitigation applied: systemd-timesyncd is disabled on this machine**
(`timedatectl set-ntp false`), which is what [Ubuntu's own WSL
guidance](https://documentation.ubuntu.com/wsl/latest/explanation/time-sync/)
recommends — the guest takes its time from the host, so a second NTP client only
conflicts. Measured effect: `CLOCK_MONOTONIC`'s error fell from about 9% to 2.4%
and `CLOCK_REALTIME`'s average rate went from 1.0039 to 0.9996. The backward
steps continue, roughly three per 90 s, and the tick is still driven, so this
reduces the problem rather than removing it. The guest now depends entirely on
the Windows clock, which measured within 0.2% of ntp.ubuntu.com.

Not worth re-reading: [the kernel's Hyper-V clocks
page](https://docs.kernel.org/virt/hyperv/clocks.html) describes the reference
TSC page and the synthetic 10 MHz counter, which is the layer measured as
correct, and covers neither TimeSync nor `/dev/ptp_hyperv`. It does explain why
swapping clocksources changed nothing: `tsc_page` and `msr` are two ways of
reading the same counter.

Environment, for a bug report: WSL 2.7.11.0, kernel 6.18.33.2-2, Windows
10.0.26200.9168. `wsl --shutdown` does not clear it.

#### What is unaffected

**Every infrared measurement.** Durations come from the ESP32's RMT peripheral
over the native API and are timed by the device, never by the host. The capture
corpus, the protocol document and everything `tests/test_captures.py` asserts
stand. Any conclusion drawn from frame *contents* rather than from arrival times
is likewise unaffected, which is all of Phases 1–5.

**The test suites.** Nothing under `tests/` measures elapsed time — no
`monotonic`, no `freezer`, no `async_fire_time_changed`, no sleeps.

**The integration in normal use.** `ECHO_WINDOW_SECONDS = 1.0` is really about
0.98 s of real time and `SAVE_DELAY_SECONDS = 10` fires after about 9.8 s. Both
are deliberately generous and neither is near a threshold that matters.

#### Rules while this stands

- **Measure durations with `CLOCK_MONOTONIC_RAW`.** In Python that is
  `time.clock_gettime(time.CLOCK_MONOTONIC_RAW)`, not `time.monotonic()`, which
  is the corrected clock and runs 2.4% fast. This is the workaround that makes
  host-side timing possible at all here, and it costs nothing anywhere else: on a
  healthy machine the two agree.
- **Order the journal by `index`, never by `at`.** The index is assigned in the
  same callback that reads the clock, so it always reflects delivery order; `at`
  does not. The corpus holds three such inversions, up to 0.98 s, all from
  2026-08-17.
- **Never derive a duration from a wall-clock timestamp.** That means journal
  `at` values, Home Assistant's `last_changed` and `last_reported`, and the
  infrared receiver entity's state, which is itself a timestamp. `CLOCK_REALTIME`
  still steps backwards, so subtracting two of them can yield a negative
  interval.
- **Question 9 is answerable, on `CLOCK_MONOTONIC_RAW` only.** Its measurement is
  a host-scheduled separation between frames; on `time.monotonic()` every
  interval would be 2.4% shorter than the figure written down, and on wall-clock
  timestamps it would be meaningless. Record in the protocol document which clock
  produced the numbers.
- **Prefer evidence that lives in the frame.** Anything the ESP32 timed is sound.
  Where a question can be answered from frame contents instead of from timing,
  answer it that way.

### Open questions the hardware must settle

Accumulated across Phases 3–5. Each one is a place where the code had to assume
something the 39 captures do not answer. **This table is the single list** — the
`-m hardware` section above is only the harness that runs them, and the subsections
below expand the ones that need a procedure rather than a single press.

There are 15, in three groups. **The number is the sequence: work them in order.**
Within a group they are ordered by what unblocks what, and by what is cheapest to
learn first. The subsections below appear in the same order and expand the ones that
need a procedure rather than a single press.

Group one is answered. Groups two and three all need the ACP 35 itself.

**Questions 1–6: ANSWERED 2026-08-11.** One session with the TZ20160122 and the
KC868-AG, no transmitting and no air conditioner. 128 frames, every checksum valid;
31 of them are now Pronto blocks in the protocol document, where
`tests/test_captures.py` reads them.

| # | Question | Answer |
| - | -------- | ------ |
| 1 | The 19 unverified °F → °C pairings | **All 25 confirmed**, plus all 14 °C → °F. Both tables are now fully evidenced and the `(v)` markers are gone. |
| 2 | Does any remote button set `b4`, `b5`, or `b7` bits 5/4/2? | **No.** The remote has exactly seven buttons and all seven were captured, so "reserved" is now evidenced rather than assumed. |
| 3 | Does the remote ever emit fan `0`? | **No.** The fan button cycles high → medium → low over a full lap and a repeat. `Acp35Fan.AUTO` stays out of `fan_modes`. |
| 4 | Is `b7` bit 1 set on an *ordinary* press while a timer is already running? | **No** — the bit reports the entry display, not a pending timer. `_build_command` was wrong and is fixed. |
| 5 | Do `b1` bit 3 and `b2` survive a non-timer press? | **Yes.** Both survive; the shadow state may hold the timer across other changes. |
| 6 | What `b7` bit 0 means | **A TIMER press that reopened the display on a timer already set.** Identical at 5 h and 7 h, so it does not encode the hours. |

A later session (2026-08-13) captured the one timer case the corpus had never covered —
the unit **off** — and it corrected the frame format:

- **`b1` bit 3 is not "a timer is set", it is "the pending timer will switch the unit
  off".** With the unit off and three hours pending the bit is clear while `b2` carries the
  hours; with the unit on the same state sets it. Off-delay while running, on-delay while
  stopped, exactly as the manual describes. Tested over all 76 decodable frames,
  `bit 3 == power AND (hours > 0 OR entry display open)` fits every one; the rule it
  replaces, `bit 3 == hours > 0`, mismatches eight. The field is now `timer_off_delay`.
- **Nothing else about the timer changes when the unit is off.** The entry display opens at
  zero hours, up counts the same, `b7` bits 1 and 0 behave identically, and TIMER-then-TIMER
  cancels by clearing both fields.

Three things turned up that were not asked about:

- **There is one temperature setpoint and cool owns it.** Fan mode mirrors it; dry and
  auto transmit a fixed 22 °C. Fan *speed* is genuinely per-mode. Pulling the remote's
  batteries showed 22 °C is the firmware default rather than stored state, so
  `effective_temperature()` pins dry and auto to it, exactly as `effective_fan()` pins
  dry to low.
- **Dry forces low fan, and it is not a stored preference.** After a battery reset every
  mode came up on high except dry, which came up low. Operating the remote confirmed the
  other three modes each accept low, medium and high, so dry is the only restriction.
- **Fan speed is stored per mode, and the integration now stores it the same way.** A mode
  press transmits the speed stored for the mode being entered. Keeping one shared speed —
  which is what the integration did — made cool's medium follow into fan-only, where the
  remote would have sent whatever fan-only was left on. `Acp35State.fan_by_mode` holds all
  four slots and every one is persisted, so a restart does not collapse them.
- **Off, the remote answers only power and timer.** Every other button is ignored. The
  climate entity now drops both `TARGET_TEMPERATURE` and `FAN_MODE` while off, which fixes
  a reported bug: a fan speed set while off used to survive into the next power-on and
  replace the speed chosen before.
- **`b7` bit 6 is narrower than documented.** It means an up/down press that moved the
  setpoint — not the button alone, and not the value alone. A mode press changes the
  transmitted temperature without setting it.
- **The two timer cancel routes disagree.** TIMER twice disarms `b1` bit 3; winding the
  hours to zero leaves it armed at zero hours. Only the first is unambiguous, and it is
  what `Acp35TimerNumber` now sends for zero.

**Questions 7–9: blocking.** A wrong answer here is not an edge case — the
integration is unusable or silently does the wrong thing, and no fallback exists in
the code. There are three, not the two previously claimed: question 8 belongs with
them because the captures evidence the frame *format* only. That every field means
to the unit what the document says it means is inference, and a swapped mode or fan
value would ship an integration whose controls are confidently mislabelled.

| # | Question | Run by | What currently assumes an answer |
| - | -------- | ------ | -------------------------------- |
| ~~7~~ | ~~Does the unit accept our frame, and with which header mark?~~ **Answered 2026-08-18: `HEADER_MARK = 5100`, the shipped value.** Each candidate went out carrying its own setpoint; the panel showed 22 °C, which belongs to 5100 — sent **first**, so nothing sent after it was accepted as a command. 4400, 3000, 9000 and no header at all did not set the setpoint. Every transmission is confirmed in the journal, so the unit's silence is established rather than assumed — but it was run with the emitter across the room, where a third of correct frames were later found to be going astray, so read it with *Emitter placement is a measurement variable* below | — | — |
| ~~8~~ | ~~Does the unit act correctly on every command we can produce — each mode, each fan speed, 17 °C and 30 °C?~~ **Answered 2026-08-19: yes, all sixteen.** Every mode against every fan speed the hardware has, plus 17 °C and 30 °C, was transmitted and read blind off the panel; every reading matched what was sent. The enum mapping in `protocol.py` is now measured rather than inferred. **The timer half is answered too, 2026-08-19: our frames clear it.** A timer armed at 3 h on the remote was cleared by one ordinary setpoint change of ours, which the appliance demonstrably acted on — the panel moved 23 → 24 °C at the same time. So any command from Home Assistant clears a timer set on the remote, which is the settled consequence of not transmitting timers rather than a fault; see *The timer read-out is read-only* | `test_behaviour.py`, `test_timer.py` | — |
| 9 | Minimum gap between frames, and whether one frame is reliably enough | `test_frame_timing.py` | `repeat_count = 0` and no rate limiting between rapid service calls |

Why this order: until the unit responds to anything, neither of the others can be
attempted, so 7 led. 8 then establishes that a given command is acted on, which 9
depends on — "did it act on the frame" is the measurement 9 repeats at shrinking gaps.
That dependency now has a condition attached: 9 must be run with the emitter close
to the appliance, or it will measure the room rather than the protocol.

### Emitter placement is a measurement variable, not a detail

Question 8 was run twice on 2026-08-19 and the two runs disagree completely. With
the emitter across the room from the appliance, **6 of 16 commands were not obeyed**;
with it moved close to the appliance's infrared window with clear line of sight,
**all 16 were**. Nothing else changed — same frames, same code, same answers file
cleared between runs.

The first run is kept as `journal.2.jsonl`, and it is worth reading, because the
failures were not simple silence:

- Sent cool **high** while the unit sat at cool medium; it went to cool **low**.
- Sent **fan** medium while the unit sat in fan; it went to **cool** low.
- Sent a frame **byte-for-byte identical to one the remote itself produced** —
  `55 62 00 0D 00 00 30 80 74`, auto/high/22 °C, matching a corpus capture — and
  the unit did not obey it.

So marginal reception does not merely drop commands, it produces **wrong ones**: a
mangled frame can still parse into a valid command that was never sent. The ACP 35
therefore does not appear to verify the checksum, since a corrupted frame should
have been rejected outright.

**The loopback cannot detect any of this, and never could.** The receiver sits
beside the emitter, not beside the appliance, so a clean 148-duration echo proves
the LED did its job and says nothing about what arrived across the room. In both
runs every frame was confirmed on the air; only the appliance's behaviour differed.
Any hardware result gathered from a distance is suspect on this evidence, and any
session should place the emitter close before drawing conclusions.

**A wrong header mark may not be simply ignored.** During question 7's descending
pass the appliance went to auto with a low fan although every frame sent carried
cool and medium, and that was attributed to a wrong header leaving the unit's bit
sampling shifted. That attribution is now doubtful: the emitter was in the far
position, and the far-position run of question 8 produced exactly the same class of
behaviour with a *correct* header mark. Reception is the simpler explanation, and it
covers both. Two consequences survive either way:

- **Never ship an unverified header mark.** Whatever the mechanism, the appliance
  has been observed acting on commands that were never sent.
- **The setpoint only works as a carrier while the unit stays in cool**, since auto
  and dry pin it. `test_header_mark.py` takes `changed` as an answer and fails that
  pass instead of recording a number that identifies nothing.

**Question 7's conclusion stands but its margin is thinner than recorded.** 5100 was
accepted and the four candidates sent after it set nothing, across three repeats
each — at a distance where roughly a third of correct frames were being lost. That
still makes four candidates failing twelve times unlikely to be chance, but it is
weaker evidence than it appeared. Re-running it close to the appliance would settle
it properly, and would also answer the descending pass, which remains open.

**Questions 10–15: everything else.** Each would refine behaviour at the edges or let
a restriction be lifted, and each has a defensible answer already committed to code,
so none of them gates a release.

| # | Question | Run by | What currently assumes an answer |
| - | -------- | ------ | -------------------------------- |
| ~~10~~ | ~~Do our frames reach the air as the bytes we built?~~ **Answered 2026-08-18: yes, for every state the remote was recorded producing.** All 76 distinct corpus frames were re-encoded, transmitted and heard back, each decoding to the bytes the remote itself produced. Nothing between `get_raw_timings()` and the LED reshapes the waveform, and a non-default carrier reaches it too, which question 7's fallback needs | — | — |
| ~~11~~ | ~~Does the unit require the `b7` event bits, or is a constant `CELSIUS` enough?~~ **Answered 2026-08-19: they are not required.** The same setpoint change was sent twice, differing only in `TEMP_CHANGED`; the unit acted on both. `b7` could become a constant and `_build_command` could stop tracking which button a change came from. Mirroring the remote is still what ships, because it costs nothing and the remote is the specification — this only records that the simplification is available | — | — |
| ~~12~~ | ~~Does the unit accept fan `0` (`b6` = `0x0x`) as an auto speed?~~ **Closed 2026-08-19 by observation, not by test.** The appliance and the remote both have exactly three fan speeds. `Acp35Fan.AUTO` is a value the nibble can hold and the hardware cannot select, so it is never transmitted and `fan_modes` never gains it | — | — |
| ~~13~~ | ~~Does the unit accept a non-low fan in dry mode?~~ **Closed 2026-08-19 by decision.** The remote forces low in dry and will not let the fan button move it. The remote is the specification, so we never send anything else there and never ask the unit what it would do — which is why the mode/fan sweep has ten cells and not twelve | — | — |
| ~~14~~ | ~~Does the unit act on the unit flag?~~ **Answered 2026-08-13: it acts on it.** Pressing C/F on the remote changes the appliance's own display panel to the chosen unit. That makes the scale the appliance shows the scale the user reads, so the climate entity now reports whichever one `b7` bit 7 selects rather than always Celsius. | — | — |
| ~~15~~ | ~~Does a power-off frame really leave the mode running in `b6`?~~ **Answered 2026-08-19: the unit acts on the nibble.** With the appliance confirmed in cool, a power-off frame carrying `fan` in `b6` switched it off; started again from the button on the appliance itself — which carries no mode — it came up in **fan**, the mode the frame carried, not the cool it had been running. `async_set_hvac_mode(OFF)` keeping the last mode is therefore load-bearing rather than incidental: what we put in `b6` of an off frame is what the unit resumes | — | — |

Why 10 led: it is the only fully automatic test in the hardware set, and answering it
first turns a failure elsewhere into a diagnosis rather than a guess. The saturation
worry that kept it out of the blocking group did not materialise.

**It proves less than was claimed for it, though.** "The frame provably went out
correctly" is exactly what it establishes, and question 8's two runs showed that is
not the same as the frame arriving correctly — the receiver sits beside the emitter.
So a failure elsewhere narrows to *the appliance did not act on a frame that left
correctly*, which still includes everything that happens in the air between them.
Only emitter placement rules that half out, and no test can.

### Procedures for questions 1–6

Removed. They described how to run capture exercises that are now complete; the
answers are in the table above and the frames are in the protocol document. What
the sessions produced, for anyone repeating them: `tools/hw.py mark` before each
step, one step per physical action, and `tools/hw.py journal --since-mark` to
read it back. Sweeping a whole temperature range in one marked step worked far
better than one step per value.

### Frame timing and repeats — question 9

Not answerable from captures: the 10.1 ms tail on every one is ESPHome's receive
idle timeout, not something the remote emitted, so the true inter-frame gap has
never been observed.

**The separation must be measured, not requested. Measured 2026-08-18:** four
frames sent through `acp35_bench.send` with a requested 150 ms gap produced three
receive buffers, one of which held two frames **1415 µs apart**. Something
between Home Assistant and the emitting LED — the ESPHome API, the device's
transmit queue — does not preserve the spacing between service calls. A
procedure that assumes the requested gap reached the air would answer a question
nobody asked.

The loopback measures it exactly, and needs no clock at all: two frames close
enough together arrive in **one** buffer, and the gap between them is a single
duration timed by the ESP32. Read it as the last space before the second frame's
header mark. Where the frames land in separate buffers the gap was at least the
receiver's 10 ms idle timeout, and each transmission's `CLOCK_MONOTONIC_RAW`
reading in the journal bounds it from above.

This supersedes timing the procedure from the host. `time.monotonic()` runs 2.4%
fast here and the wall clock steps backwards — see *Known issue: the development
host's clock is unusable for measurement* — but none of that matters when the
number comes off the device. Say in the protocol document which method produced
each figure.

One consequence for the procedure below: **a buffer is not a frame.** A 296-
duration capture is two frames, not a corrupt one, and counting buffers would
under-count what the unit received.

Two things to try, once 8 has established that the unit reliably acts on a command
sent in isolation — without that baseline a missed frame here is unattributable:

1. **Reliability of a single frame.** Send the same command 20 times with a long
   pause between, and count how many the unit acts on. If any are missed,
   `repeat_count` needs raising and the frame is not as unrepeated as the remote
   made it look.
2. **Back-to-back frames.** Home Assistant can easily issue two service calls in
   quick succession — a mode change and a temperature change from one script — and
   the entity sends a full frame for each with no delay between. Send two frames
   at decreasing separations to find where the unit starts dropping them, and add
   a minimum spacing in `_async_transmit` if one is needed.
