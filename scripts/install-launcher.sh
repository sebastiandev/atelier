#!/usr/bin/env bash
# Install a thin desktop launcher that runs scripts/dev.sh.
# macOS  -> ~/Applications/Atelier.app
# Linux  -> ~/.local/share/applications/atelier.desktop

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATES="$SCRIPT_DIR/launchers"

os="$(uname -s)"

# Escape '&', '\', '|' for use in a sed replacement. The substituted path goes
# into shell strings (macOS) or `Exec=`/`Path=` keys (Linux), so do not shell-quote here.
escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

REPO_ROOT_SED="$(escape_sed_replacement "$REPO_ROOT")"

case "$os" in
    Darwin)
        DEST="$HOME/Applications/Atelier.app"
        mkdir -p "$HOME/Applications"

        if [ -e "$DEST" ]; then
            echo "Removing existing $DEST"
            rm -rf "$DEST"
        fi

        cp -R "$TEMPLATES/Atelier.app.template" "$DEST"
        mkdir -p "$DEST/Contents/Resources"
        cp "$TEMPLATES/icons/Atelier.icns" "$DEST/Contents/Resources/Atelier.icns"
        sed -i '' "s|__REPO_ROOT__|$REPO_ROOT_SED|g" "$DEST/Contents/MacOS/launcher"
        chmod +x "$DEST/Contents/MacOS/launcher"

        # Touch the bundle so Launch Services re-registers it (picks up new icon).
        touch "$DEST"

        echo "Installed: $DEST"
        echo "Open it from ~/Applications or drag it to the Dock."
        ;;
    Linux)
        DEST_DIR="$HOME/.local/share/applications"
        DEST="$DEST_DIR/atelier.desktop"
        ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
        ICON_DEST="$ICON_DIR/atelier.png"
        mkdir -p "$DEST_DIR" "$ICON_DIR"

        cp "$TEMPLATES/icons/atelier.png" "$ICON_DEST"

        sed "s|__REPO_ROOT__|$REPO_ROOT_SED|g" \
            "$TEMPLATES/atelier.desktop.tmpl" > "$DEST"
        chmod +x "$DEST"

        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database "$DEST_DIR" >/dev/null 2>&1 || true
        fi
        if command -v gtk-update-icon-cache >/dev/null 2>&1; then
            gtk-update-icon-cache -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
        fi

        echo "Installed: $DEST"
        echo "Installed icon: $ICON_DEST"
        echo "Search for 'Atelier' in your application launcher."
        ;;
    *)
        echo "Unsupported OS: $os" >&2
        echo "For Windows, run scripts/install-launcher.ps1 from PowerShell." >&2
        exit 1
        ;;
esac
