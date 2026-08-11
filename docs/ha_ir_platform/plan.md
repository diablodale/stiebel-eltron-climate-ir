# Stiebel Eltron ACP 35 → Home Assistant infrared platform

## Step 0 — Save this plan

First action: save this plan **unchanged** to `docs/ha_ir_platform/plan.md`.

## Context

The ACP 35 is IR-only. Last year the IR protocol was reverse-engineered from 39 Pronto
captures and documented in [Stiebel Eltron air conditioner ACP 35.md](Stiebel Eltron air conditioner ACP 35.md).
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
| `b1` | bits 7-4 = °C − 16 (1..14 → 17..30 °C); bit 3 = timer armed; bit 1 = power on; bits 2,0 = 0 |
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

## Phase 1 — Rewrite the protocol doc and tooling

**[Stiebel Eltron air conditioner ACP 35.md](Stiebel Eltron air conditioner ACP 35.md)**

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
dev = ["pytest"]
hardware = ["httpx"]                # only for `pytest -m hardware`

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
uv sync                 # or: uv sync --group hardware
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

## Deferred work

Decided but not scheduled. Each is independent of the open hardware questions.

### The display-unit checkbox should not be a setup question

`CONF_DISPLAY_CELSIUS` appears in the config flow as "Air conditioner displays
Celsius". Three problems, in increasing order of importance:

- **The label describes state but the field is an instruction.** It sets `b7`
  bit 7 on everything we transmit, so it does not report what the unit is
  showing, it decides what the unit *will* show. "Set the air conditioner's
  display to Celsius" is what it does.
- **The hardware already answers it.** `receiver.py` overwrites
  `display_celsius` from any frame the receiver hears, so with a receiver
  configured the setup answer is replaced the first time anyone touches the
  remote. Asking at setup time for something discoverable seconds later is a
  question worth not asking.
- **It cannot be changed afterwards.** It is config-entry data with no options
  flow, so a user without a receiver who picks wrong has to delete and re-add the
  integration.

Intended shape: default `True`, drop it from the initial step, and expose it
through an options flow for the no-receiver case.

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
already inside Home Assistant, on the other end of the same API. That is why the `hardware`
dependency group carries only an HTTP client and not `aioesphomeapi`.

### Always runs — pure Python, no HA, no hardware

| module | asserts |
| ------ | ------- |
| `tests/test_protocol.py` | field packing, both °C↔°F lookup tables and the rules that generated them, checksum, `from_raw_timings(get_raw_timings())` round-trip, 17 °C → 62 °F and 30 °C → 86 °F boundaries, out-of-range raises `ValueError` |
| `tests/test_captures.py` | all 39 captures, **read from the protocol document rather than a generated `captures.jsonl`**, decode to the expected 9 bytes, checksum-validate, re-encode bit-identically, and match expectations written from the document's prose |
| `tests/test_cli.py` | `tools/acp35_cli.py` decodes an ESPHome log line, a bare Pronto code and raw timings, and `--document` decodes every capture in text, table and bytes form. There is no `--extract`: dropping `captures.jsonl` removed the need |

### Devcontainer — needs HA, no hardware

Guarded by a module-level `pytest.importorskip("homeassistant")` so they simply skip on the
host instead of erroring.

| module | asserts |
| ------ | ------- |
| `tests/test_config_flow.py` | flow completes with and without a receiver selected; a config entry with no receiver loads and works |
| `tests/test_climate.py`, `tests/test_number.py` | every service call produces the expected `Acp35Command`; shadow state survives a restart; `OFF` keeps the last mode in `b6`; the two entities share one state without clobbering each other |
| `tests/test_emit_live.py` | drives live HA against the `fake_ir` stub emitter and asserts the captured µs list against timings decoded from the corresponding `.md` capture. **This is the real encoder proof** — HA → `infrared` → emitter, end to end. It cannot cover `HEADER_MARK`, since no capture contains it |
| `tests/test_receiver.py` | feeds recorded remote timings straight into `_handle_signal()` to exercise the Phase 5 decode path, plus garbage timings that must be ignored silently |

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
`acp35.py`, so the code validating a capture is the same code that will encode our
transmissions rather than a second implementation that could agree with itself.

#### Human answers are recorded, not typed

A hardware run cannot depend on somebody watching a terminal, and an assistant driving
the session cannot hold one open at all. So `ask()` resolves against
`tests/hardware/answers.toml` first: an answer already recorded means the test asserts
against it non-interactively; missing with a tty attached means it prompts and records
what it is told; missing with no tty means the test **skips and prints the question**.

`uv run pytest -m hardware` is therefore always safe to run, and its output is a precise
list of what remains unanswered. Nothing is asked twice, and every answer keeps its
evidence beside it.

Where a question needs the operator's eyes, prefer encoding the answer in the unit's own
display over asking once per case. The header-mark bisect sends each candidate as a
different target temperature, so one glance at the display — "what does it read?" —
identifies the winner instead of five separate confirmations.

| module | marks | settles | how it drives the device |
| ------ | ----- | ------- | ------------------------ |
| `tests/hardware/test_header_mark.py` | `hardware, manual` | 7 | Parametrised over the candidate list; sends power-on with each and `confirm`s. Exactly one is expected to pass; the winner is written into the doc and `acp35.py`. If all fail, a second parametrisation varies `CARRIER_HZ` too |
| `tests/hardware/test_behaviour.py` | `hardware, manual` | 8, 11, 12, 13, 14, 15 | One parametrised case per question, each sending a frame and `confirm`ing what the unit did. Answers get folded back into the doc and the encoder |
| `tests/hardware/test_frame_timing.py` | `hardware, manual` | 9 | Repeats one command at decreasing separations and `confirm`s how many the unit acted on |
| `tests/hardware/test_loopback.py` | `hardware` | 10 | Fully automatic. A session fixture transmits once and looks in the bench journal for any received frame; `pytest.skip("receiver does not hear its own emitter")` if none. Otherwise each state is sent, the journalled frame decoded to 9 bytes, and compared with both what we intended and what the remote produced for that state |
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
- **`from_raw_timings()` cannot decode our own transmissions.** It tolerates a
  missing leading mark but not an extra one, so every loopback frame decodes as
  `None`. Fix before question 10 can assert anything.

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
| 7 | Does the unit accept our frame at all, and with which header mark — `5100`, `4400`, `3000`, `9000` or none? | `test_header_mark.py` | `HEADER_MARK = 5100`, the one unmeasured constant |
| 8 | Does the unit act correctly on every command we can produce — each mode, each fan speed, 17 °C and 30 °C, timer 1 h / 24 h / cancel? | `test_behaviour.py` | the whole of `const.py`'s enum mapping |
| 9 | Minimum gap between frames, and whether one frame is reliably enough | `test_frame_timing.py` | `repeat_count = 0` and no rate limiting between rapid service calls |

Why this order: until the unit responds to anything, neither of the others can be
attempted, so 7 leads. 8 then establishes that a given command is acted on, which 9
depends on — "did it act on the frame" is the measurement 9 repeats at shrinking gaps.

**Questions 10–15: everything else.** Each would refine behaviour at the edges or let
a restriction be lifted, and each has a defensible answer already committed to code,
so none of them gates a release.

| # | Question | Run by | What currently assumes an answer |
| - | -------- | ------ | -------------------------------- |
| 10 | Do our frames reach the air as the bytes we built, and match the remote's for the same state? | `test_loopback.py` | that nothing between `get_raw_timings()` and the LED reshapes the waveform |
| 11 | Does the unit require the `b7` event bits, or is a constant `CELSIUS` enough? | `test_behaviour.py` | `_build_command` mirrors the remote's event bits — safe either way, so this only buys simplification |
| 12 | Does the unit accept fan `0` (`b6` = `0x0x`) as an auto speed? | `test_behaviour.py` | whether `fan_modes` can gain `auto` |
| 13 | Does the unit accept a non-low fan in dry mode? | `test_behaviour.py` | nothing any more. The remote forces low in dry and will not let the fan button move it, so `effective_fan()` mirrors that and we never send one. Answering this could relax the restriction, not fix a bug. |
| 14 | Does the unit act on the unit flag, or is it display-only? | `test_behaviour.py` | that `display_celsius` is cosmetic and safe to follow from the remote |
| 15 | Does a power-off frame really leave the mode running in `b6`? | `test_behaviour.py` | `async_set_hvac_mode(OFF)` keeps the last mode |

Why 10 leads: it is the only fully automatic test in the hardware set, and if the
receiver can hear the emitter it turns a failure of 7 into a diagnosis rather than a
guess — worth running alongside 7 even though it sits in this group. It is not itself
blocking precisely because it may be unanswerable, for the saturation reason noted
with the harness above; something that might never return an answer cannot gate a
release. 12 is worth pairing with 3, which asks the same thing of the remote.

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
