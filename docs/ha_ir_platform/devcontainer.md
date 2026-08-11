# Home Assistant development environment

A Home Assistant core devcontainer used to develop and test
`custom_components/stiebel_eltron_ir` without touching the KC868-AG or the air
conditioner. Setup follows
[setup_devcontainer_environment](https://developers.home-assistant.io/docs/setup_devcontainer_environment/).

## Host constraints

This repo lives on a Windows volume, exposed to WSL2 at `/mnt/c` over **9p**, and
is bind-mounted into the container. Two consequences worth knowing before you
wonder why something is not happening:

- **inotify does not fire for Windows-hosted files under WSL2.** Home Assistant
  will not auto-reload on edit and `pytest-watch` will not react. Restart
  Home Assistant manually after changing the integration.
- Bulk I/O over the mount is slow. Only this repo pays that cost; ha-core itself
  is cloned to ext4, where the dependency install and core test runs happen.

`npx` must be the WSL nvm one, not the Windows install under `/mnt/c`. nvm is
sourced from `~/.bashrc`, so a **non-interactive** shell silently falls back to
Windows Node and would hand Windows paths to a Linux workspace:

```bash
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"
command -v npx        # must NOT be under /mnt/c
```

## One-time setup

```bash
# 1. Clone the fork onto ext4, NOT under /mnt/c
git clone --filter=blob:none https://github.com/diablodale/ha-core.git ~/src/ha-core
cd ~/src/ha-core && git remote add upstream https://github.com/home-assistant/core.git

# 2. Build and start. `up` runs postCreateCommand (script/setup) and
#    postStartCommand (script/bootstrap) itself, so there is nothing else to invoke.
npx --yes @devcontainers/cli up --workspace-folder ~/src/ha-core \
  --mount 'type=bind,source=/mnt/c/njs/stiebel-eltron-climate-ir,target=/workspaces/acp35'

# 3. Verify the bind mount survived Docker Desktop's Windows path translation
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  bash -lc 'ls /workspaces/acp35 && touch /workspaces/acp35/.mnt-probe'

# 4. Link both custom components into the Home Assistant config directory.
#    ha-core gitignores /config, so the sources live in this repo and are
#    symlinked in. The targets are *container* paths and only resolve there.
ln -s /workspaces/acp35/tests/custom_components/fake_ir \
      ~/src/ha-core/config/custom_components/fake_ir
ln -s /workspaces/acp35/custom_components/stiebel_eltron_ir \
      ~/src/ha-core/config/custom_components/stiebel_eltron_ir
```

Then append to `~/src/ha-core/config/configuration.yaml`:

```yaml
infrared:
  - platform: fake_ir
```

## Integration tests

The tests in `tests/integration/` need ha-core's `hass` fixture and
`MockConfigEntry`, so they run from **ha-core's** test tree rather than this
repo's. Three more symlinks wire them in:

```bash
# The tests themselves, as if they were a core component's suite
ln -s /workspaces/acp35/tests/integration \
      ~/src/ha-core/tests/components/stiebel_eltron_ir

# Both custom components, where `enable_custom_integrations` looks for them
ln -s /workspaces/acp35/custom_components/stiebel_eltron_ir \
      ~/src/ha-core/tests/testing_config/custom_components/stiebel_eltron_ir
ln -s /workspaces/acp35/tests/custom_components/fake_ir \
      ~/src/ha-core/tests/testing_config/custom_components/fake_ir
```

`tests/integration/conftest.py` puts `tests/testing_config` on `sys.path` so the
tests import `custom_components.stiebel_eltron_ir` — the *same* module object
Home Assistant's loader uses. Importing it under any other package name creates a
second copy of the enums, and identity checks like `mode is Acp35Mode.COOL` then
fail for no visible reason.

`pyproject.toml` excludes `tests/integration` from this repo's own pytest run,
since it cannot supply those fixtures.

## Running

```bash
# This repo's tests: protocol, captures, CLI. No Home Assistant, no hardware.
uv run pytest

# The integration tests, from inside the container
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  bash -lc 'cd /workspaces/ha-core && python -m pytest tests/components/stiebel_eltron_ir --no-cov -q'

# Home Assistant, reachable at http://localhost:8123 (devcontainer.json forwards 8123)
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  bash -lc 'cd /workspaces/ha-core && exec python -m homeassistant -c config'

# Core's infrared suite, as an environment sanity check
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  bash -lc 'cd /workspaces/ha-core && python -m pytest tests/components/infrared --no-cov -q'
```

The container's interpreter is `/home/vscode/.local/ha-venv/bin/python`; point the
VS Code Python extension there.

## Hardware sessions — the KC868-AG and the real remote

For answering the protocol's open questions the devcontainer talks to the real
device. The KC868-AG must be added **by IP address**: mDNS discovery does not cross
Docker Desktop's NAT, though outbound API connections to the LAN are fine.

Settings → Devices → Add integration → ESPHome → enter the IP and the encryption
key. The device's two `ir_rf_proxy` instances arrive as two HA entities.
Write the receiver's entity id as the `receiver` configuration value of
`acp35_bench` in `configuration.yaml`:

```yaml
acp35_bench:
  receiver: infrared.examplekc868_ag_ir_proxy_receiver
  journal: /workspaces/acp35/tests/hardware/journal.jsonl
```

and symlink it in beside the others, exactly as `fake_ir` is:

```bash
ln -s /workspaces/acp35/tests/custom_components/acp35_bench \
      ~/src/ha-core/config/custom_components/acp35_bench
```

The journal path is a *container* path that lands in this repo through the bind
mount, so the file is readable from Windows and WSL2 while Home Assistant is still
writing it. It is gitignored: frames worth keeping are promoted into the protocol
document, and the rest is session noise.

`tools/hw.py` then drives the session from the host. Copy `.env.example` to `.env`
and fill in a long-lived token from the devcontainer's Home Assistant:

```bash
uv run python tools/hw.py status                       # is the receiver subscribed
uv run python tools/hw.py mark "fan button, 3rd press" # label what happens next
uv run python tools/hw.py journal --since-mark         # decode what it heard
uv run python tools/hw.py pronto --since-mark          # blocks for the document
```

Only `mark` needs Home Assistant running; the others read the journal file and work
with the container stopped.

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
