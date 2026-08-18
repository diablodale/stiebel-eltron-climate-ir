#!/usr/bin/env bash
# Create the symlinks the Home Assistant devcontainer needs to load this repo.
#
# ha-core gitignores config/, and its test tree has to contain the integration
# tests for them to reach the `hass` fixture, so both trees reference sources
# that live here. Run inside the container, where these paths resolve:
#
#     npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
#       /workspaces/acp35/tools/link_devcontainer.sh
#
# Re-running is safe. Pass --receiver to also add the acp35_bench configuration
# entry, and --emitter so the bench can transmit without every service call
# naming one; obtain both entity ids with `tools/hw.py entities` rather than
# typing them.
set -euo pipefail

ACP35_DIR="${ACP35_DIR:-/workspaces/acp35}"
HA_CORE_DIR="${HA_CORE_DIR:-/workspaces/ha-core}"
RECEIVER=""
EMITTER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --receiver) RECEIVER="${2:-}"; shift 2 ;;
        --receiver=*) RECEIVER="${1#*=}"; shift ;;
        --emitter) EMITTER="${2:-}"; shift 2 ;;
        --emitter=*) EMITTER="${1#*=}"; shift ;;
        -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

for dir in "$ACP35_DIR" "$HA_CORE_DIR"; do
    if [ ! -d "$dir" ]; then
        echo "not a directory: $dir" >&2
        echo "Run this inside the devcontainer, or set ACP35_DIR/HA_CORE_DIR." >&2
        exit 1
    fi
done

link() {
    local target="$1" name="$2"
    if [ ! -e "$target" ]; then
        echo "missing source: $target" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$name")"
    # -n so an existing correct symlink is replaced rather than followed, which
    # would otherwise nest a link inside the directory it already points at.
    ln -sfn "$target" "$name"
    if [ ! -e "$name" ]; then
        echo "link does not resolve: $name -> $target" >&2
        exit 1
    fi
    echo "  $name -> $target"
}

echo "Custom components Home Assistant loads at runtime:"
link "$ACP35_DIR/custom_components/stiebel_eltron_ir" \
     "$HA_CORE_DIR/config/custom_components/stiebel_eltron_ir"
link "$ACP35_DIR/tests/custom_components/fake_ir" \
     "$HA_CORE_DIR/config/custom_components/fake_ir"
link "$ACP35_DIR/tests/custom_components/acp35_bench" \
     "$HA_CORE_DIR/config/custom_components/acp35_bench"

echo "Integration tests, run from ha-core's test tree:"
link "$ACP35_DIR/tests/integration" \
     "$HA_CORE_DIR/tests/components/stiebel_eltron_ir"

# enable_custom_integrations looks here, and importing the integration under any
# other package name creates a second copy of the enums, so identity checks like
# `mode is Acp35Mode.COOL` fail with no visible cause.
echo "Custom components the test fixtures import:"
link "$ACP35_DIR/custom_components/stiebel_eltron_ir" \
     "$HA_CORE_DIR/tests/testing_config/custom_components/stiebel_eltron_ir"
link "$ACP35_DIR/tests/custom_components/fake_ir" \
     "$HA_CORE_DIR/tests/testing_config/custom_components/fake_ir"

CONFIG="$HA_CORE_DIR/config/configuration.yaml"

if grep -q "^acp35_bench:" "$CONFIG" 2>/dev/null; then
    echo "acp35_bench is already configured in $CONFIG"
    if [ -n "$EMITTER" ] && ! grep -q "^  emitter:" "$CONFIG"; then
        echo "  It names no emitter. Add this line under acp35_bench: by hand,"
        echo "  or the bench can only receive:"
        echo "    emitter: $EMITTER"
    fi
elif [ -n "$RECEIVER" ]; then
    cat >> "$CONFIG" <<YAML

# Dev-only capture bench. Appends every infrared signal the receiver entity
# delivers to a JSONL journal, which tools/hw.py decodes, and transmits raw
# timings through the emitter on acp35_bench.send.
acp35_bench:
  receiver: $RECEIVER
YAML
    if [ -n "$EMITTER" ]; then
        echo "  emitter: $EMITTER" >> "$CONFIG"
    fi
    echo "  journal: $ACP35_DIR/tests/hardware/journal.jsonl" >> "$CONFIG"
    echo "Added acp35_bench to $CONFIG, receiver $RECEIVER${EMITTER:+, emitter $EMITTER}"
    if [ -z "$EMITTER" ]; then
        echo "No emitter named: the bench can record but not transmit."
    fi
    echo "Restart Home Assistant for it to take effect."
else
    echo
    echo "acp35_bench is not configured. Add it with:"
    echo "  $0 \\"
    echo "    --receiver \$(cd $ACP35_DIR && python tools/hw.py entities | \\"
    echo "        awk '\$2 == \"receiver\" && \$NF == \"real\" {print \$1}') \\"
    echo "    --emitter \$(cd $ACP35_DIR && python tools/hw.py entities | \\"
    echo "        awk '\$2 == \"emitter\" && \$NF == \"real\" {print \$1}')"
fi
