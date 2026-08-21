# Running Home Assistant against this integration

A Home Assistant core devcontainer, for the two things a test cannot do. Setup
follows
[setup_devcontainer_environment](https://developers.home-assistant.io/docs/setup_devcontainer_environment/).

1. **Run a real Home Assistant.** Walk the config flow as a user does, see the
   entities appear, watch the device page. The suite proves the integration behaves;
   only a running instance shows what the README describes.
2. **Hardware sessions.** `acp35_bench` records what the KC868-AG's receiver hears
   and transmits raw frames through its emitter. Every open question the protocol
   had was settled here.

**Development and testing no longer need it.** All 1110 tests, and their coverage,
run on the host with `uv run pytest` — Home Assistant and its fixtures come from
`pytest-homeassistant-custom-component`.

A real Home Assistant could in principle run on the host too, since `homeassistant`
is a dev dependency — but `home-assistant-frontend` is not in `uv.lock`, so there
would be no interface, and Home Assistant installs further requirements into
`config/deps` at runtime. The container already provides all of it.

## Only on WSL2, with the checkout on a Windows volume

None of this applies on native Linux or macOS, where the setup below is the same
minus these three snags.

- **Home Assistant does not notice edits.** inotify does not fire for files under
  `/mnt/c`, so restart Home Assistant after changing the integration rather than
  waiting for a reload that never comes.
- **Bulk I/O across the mount is slow.** Clone ha-core to a Linux filesystem, not
  beside this repo: its dependency install writes thousands of files.
- **`npx` must resolve to nvm's Node**, not to a Windows install reachable through
  `/mnt/c`, which would hand Windows paths to a Linux workspace. Every `npx` command
  in this document depends on it. A shell you typed into has already sourced nvm from
  `~/.bashrc` and is fine; a script, a cron job or any other non-interactive shell has
  not, and must do it first, for example:

  ```bash
  export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"   # only where nvm is not loaded
  command -v npx                                       # expect a path outside /mnt/c
  ```

## One-time setup

```bash
# 1. Clone the ha-core fork the container is built from
git clone --filter=blob:none https://github.com/diablodale/ha-core.git ~/src/ha-core
cd ~/src/ha-core && git remote add upstream https://github.com/home-assistant/core.git

# 2. Build and start, bind-mounting this repo at /workspaces/acp35. `source` is
#    wherever you cloned it; every later command uses the target path, not this one.
#    `up` runs postCreateCommand (script/setup) and postStartCommand
#    (script/bootstrap) itself, so there is nothing else to invoke.
npx --yes @devcontainers/cli up --workspace-folder ~/src/ha-core \
  --mount 'type=bind,source=/mnt/c/njs/stiebel-eltron-climate-ir,target=/workspaces/acp35'

# 3. Verify the bind mount survived Docker Desktop's Windows path translation
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  bash -lc 'ls /workspaces/acp35 && touch /workspaces/acp35/.mnt-probe'

# 4. Create the three symlinks ha-core needs to load this repo's sources.
#    ha-core gitignores config/, so the components Home Assistant loads at runtime
#    are referenced from here. Runs inside the container, where the target paths
#    resolve. Re-running is safe.
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  /workspaces/acp35/tools/link_devcontainer.sh
```

`tools/link_devcontainer.sh` is the only definition of that wiring; it prints each
link it creates and fails if one does not resolve. It takes `ACP35_DIR` and
`HA_CORE_DIR` from the environment if the container ever uses different paths.

Then append to `~/src/ha-core/config/configuration.yaml`:

```yaml
infrared:
  - platform: fake_ir
```

## Integration tests do not need this container

They did once. `tests/integration/` now runs on the host, in the same `uv run
pytest` as everything else, because `pytest-homeassistant-custom-component`
packages ha-core's own `hass` fixture and `MockConfigEntry`. Nothing is symlinked
into ha-core's test tree, and no `sys.path` manipulation is needed: `pythonpath` in
`pyproject.toml` puts the repository root where `custom_components.stiebel_eltron_ir`
resolves, which is the name Home Assistant's loader uses — import it under any other
name and a second copy of the enums appears, after which `mode is Acp35Mode.COOL`
fails for no visible reason.

That package pins an exact Home Assistant version (0.13.356 is 2026.8.2), so bumping
it is how this repo moves to a new Home Assistant, and the tests then run against
precisely what users will run.

One fixture had to be written locally. `entity_registry_enabled_by_default` lives in
ha-core's `tests/components/conftest.py`, which the package does not carry — it
brings the root test fixtures, not the per-domain ones. It is four lines in
`tests/integration/conftest.py`.

## Running

```bash
# Home Assistant, reachable at http://localhost:8123 (devcontainer.json forwards 8123)
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  bash -lc 'cd /workspaces/ha-core && exec python -m homeassistant -c config'

# Core's infrared suite, as an environment sanity check
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  bash -lc 'cd /workspaces/ha-core && python -m pytest tests/components/infrared --no-cov -q'
```

This repo's own tests do not run in the container. Run them on the host with
`uv run pytest`; see [Development](../../README.md#development).

## Hardware sessions — the KC868-AG and the real remote

For answering the protocol's open questions the devcontainer talks to the real
device. The KC868-AG must be added **by IP address**: mDNS discovery does not cross
Docker Desktop's NAT, though outbound API connections to the LAN are fine.

Settings → Devices → Add integration → ESPHome → enter the IP and the encryption
key. The device's two `ir_rf_proxy` instances arrive as two HA entities.

`acp35_bench` records what the receiver delivers and transmits through the
emitter, so it needs both entity ids. Read them from Home Assistant rather than
deriving them from the ESPHome config, then write the configuration entry with
the same script as step 4:

```bash
# Two emitters and two receivers exist once fake_ir is loaded; `real` marks the
# device, and resolve_entity() refuses to select a simulated one.
uv run python tools/hw.py entities

npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  /workspaces/acp35/tools/link_devcontainer.sh \
    --receiver infrared.<receiver id> --emitter infrared.<emitter id>
```

That appends `acp35_bench:` to `configuration.yaml` if it is not already present,
naming the receiver, the emitter and the journal path. Restart Home Assistant
afterwards. The emitter is optional — a session that only listens to the remote
does not need one — but without it `acp35_bench.send` refuses unless every call
names an emitter itself, and the transmit tests need it.

The journal path is a *container* path that lands in this repo through the bind
mount, so the file is readable from Windows and WSL2 while Home Assistant is still
writing it. It is gitignored: frames worth keeping are promoted into the protocol
document, and the rest is session noise.

**One file is one Home Assistant run.** The bench rotates the journal at startup,
leaving the previous five beside it as `journal.1.jsonl` onwards. Starting a
session on a clean journal therefore means restarting Home Assistant — which is
worth doing before a hardware session anyway, on an instance that has been up for
hours with a few thousand records of ambient infrared in front of it.

A host-side `clear` command was tried and removed. `receiver_ready` is written
when the bench subscribes, so moving the file leaves the new one without it and
the hardware fixtures skip on "the bench never subscribed to a receiver". Making
it usable meant restarting Home Assistant afterwards, and restarting already
rotates.

That is not housekeeping. The two stamps that order the records both restart:
`seq` with the Home Assistant process that assigns it, `raw` with the machine. So
records from two runs in one file cannot be reliably ordered against each other,
and a file that accumulates runs eventually misleads — a stale `receiver_lost`
from an earlier run once compared as newer than the current run's
`receiver_ready`, and skipped a whole hardware session with the device sitting
there working. Rotating removes that by construction. Reading back further than
the current run means naming an archived file with `--journal`.

`tools/hw.py` then drives the session from the host. Copy `.env.example` to `.env`,
which is gitignored, and fill in `HA_TOKEN` with a long-lived access token from the
devcontainer's Home Assistant rather than the production one:

```bash
uv run python tools/hw.py status                       # is the receiver subscribed
uv run python tools/hw.py mark "fan button, 3rd press" # label what happens next
uv run python tools/hw.py journal --since-mark         # decode what it heard
uv run python tools/hw.py pronto --since-mark          # blocks for the document
```

Only `mark` needs Home Assistant running; the others read the journal file and work
with the container stopped.

### Transmitting

`acp35_bench.send` puts a frame on the air. It takes raw durations and never
encodes, exactly as it records raw durations and never decodes — whoever calls it
builds the frame with the shipping codec, or by hand when the point is to send
something the codec cannot produce. That is what question 7 needs: `HEADER_MARK`
is a module constant, so trying another value means assembling the durations
outside the encoder.

| field | |
| ----- | - |
| `timings` | required; microseconds, marks positive and spaces negative |
| `emitter` | defaults to the configured one |
| `modulation` | carrier in Hz, default 38000 |
| `repeat_count` | passed to the emitter, default 0 |
| `count`, `gap` | send the frame `count` times, `gap` seconds apart |

`count` and `gap` exist because question 9 measures the smallest separation the
appliance still acts on, and two REST calls arrive tens of milliseconds apart —
the same order as the gaps being measured.

**`gap` spaces the service calls, not the emissions.** Four frames requested
150 ms apart produced three receive buffers, one holding two frames 1415 µs
apart: something between Home Assistant and the LED does not preserve the
spacing. So the separation is always measured, never assumed. The loopback is
what measures it — two frames close enough together arrive in one buffer with the
gap between them as a single duration, timed by the ESP32 and free of every clock
problem on this host. Each transmission also carries a `CLOCK_MONOTONIC_RAW`
reading, which bounds the separation when the frames land in separate buffers.
See the known clock issue in [plan.md](plan.md).

Journal records now carry `seq`, assigned as each record is created, and
`tools/hw.py` orders by it. That is the only ordering that can be trusted here:
the writes go through an executor, and the wall clock steps backwards.

## `fake_ir`

Lives at `tests/custom_components/fake_ir/` — it is test support, not a shipped
component, and is never loaded by a real installation.

A stub emitter that records the commands it is asked to send instead of
transmitting them, so this whole chain runs with no hardware:

```text
climate service call -> Acp35Command -> infrared.async_send_command() -> emitter
```

Each send is appended to `hass.data["fake_ir_sent"]` and mirrored onto the
entity's attributes (`last_timings`, `last_modulation`, `last_repeat_count`,
`sent_count`) so REST-driven tests can assert on the exact µs list. The base
`InfraredEmitterEntity` owns `state` — it is the last-sent timestamp and is
`@final` — so the payload has to travel as an attribute.

The platform also provides `FakeIrReceiver`, whose `inject(timings)` fans a
signal out to whoever subscribed via `async_subscribe_receiver`, exactly as real
hardware would. That is what lets the receiver-sync tests exercise Home
Assistant's own subscription machinery rather than calling the handler directly.

Both are dev-only and are never loaded by a real installation.
