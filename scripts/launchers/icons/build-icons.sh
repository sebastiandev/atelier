#!/usr/bin/env bash
# Render the master SVG into platform-specific launcher icons.
#
#   Atelier.icns  - macOS (multi-resolution, packed by iconutil)
#   atelier.png   - Linux .desktop (256x256)
#   Atelier.ico   - Windows .lnk shortcut (multi-resolution)
#
# Re-run this whenever atelier-app-icon.svg changes. Outputs are committed
# so installs don't need any rendering tools.
#
# Requires: macOS host (qlmanage + iconutil + sips), ImageMagick (`magick`).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SVG="$SCRIPT_DIR/atelier-app-icon.svg"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "build-icons.sh must run on macOS (uses qlmanage + iconutil)." >&2
    exit 1
fi

for tool in qlmanage iconutil sips magick; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Missing required tool: $tool" >&2
        exit 1
    fi
done

if [ ! -f "$SVG" ]; then
    echo "Source SVG not found: $SVG" >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

render() {
    local size="$1" out="$2"
    qlmanage -t -s "$size" -o "$WORK" "$SVG" >/dev/null 2>&1
    mv "$WORK/$(basename "$SVG").png" "$out"
}

# -- macOS .icns -----------------------------------------------------------
ICONSET="$WORK/Atelier.iconset"
mkdir -p "$ICONSET"

declare -a SLOTS=(
    "16:icon_16x16.png"
    "32:icon_16x16@2x.png"
    "32:icon_32x32.png"
    "64:icon_32x32@2x.png"
    "128:icon_128x128.png"
    "256:icon_128x128@2x.png"
    "256:icon_256x256.png"
    "512:icon_256x256@2x.png"
    "512:icon_512x512.png"
    "1024:icon_512x512@2x.png"
)

# Render each unique pixel size once (cache by filename), then copy into slots.
render_cached() {
    local size="$1"
    local out="$WORK/render-$size.png"
    if [ ! -f "$out" ]; then
        render "$size" "$out"
    fi
    printf '%s' "$out"
}

for slot in "${SLOTS[@]}"; do
    size="${slot%%:*}"
    name="${slot##*:}"
    src="$(render_cached "$size")"
    cp "$src" "$ICONSET/$name"
done

iconutil -c icns "$ICONSET" -o "$SCRIPT_DIR/Atelier.icns"
echo "Wrote $SCRIPT_DIR/Atelier.icns"

# -- Linux PNG -------------------------------------------------------------
cp "$(render_cached 256)" "$SCRIPT_DIR/atelier.png"
echo "Wrote $SCRIPT_DIR/atelier.png"

# -- Windows .ico (16/32/48/64/128/256) ------------------------------------
magick \
    "$(render_cached 16)" \
    "$(render_cached 32)" \
    "$(render_cached 48)" \
    "$(render_cached 64)" \
    "$(render_cached 128)" \
    "$(render_cached 256)" \
    "$SCRIPT_DIR/Atelier.ico"
echo "Wrote $SCRIPT_DIR/Atelier.ico"
