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

## Running

```bash
# Home Assistant, reachable at http://localhost:8123 (devcontainer.json forwards 8123)
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  bash -lc 'cd /workspaces/ha-core && exec python -m homeassistant -c config'

# Core's infrared suite, as an environment sanity check
npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
  bash -lc 'cd /workspaces/ha-core && python -m pytest tests/components/infrared --no-cov -q'
```

The container's interpreter is `/home/vscode/.local/ha-venv/bin/python`; point the
VS Code Python extension there.

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

It is dev-only and is never loaded by a real installation.
