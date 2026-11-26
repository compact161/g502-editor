#!/usr/bin/env bash
# Installs or updates the tray applet io.github.compact161.g502onboardeditor.buttonmap
# for the current user, adds it to every existing system tray and replaces the old tray program (cheatsheet.py): its autostart
# entry is moved to the backup folder and the process is stopped.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
state="$HOME/.local/state/g502-onboard-editor"
old_state="$HOME/.local/state/g502-gshift"
if [[ -d "$old_state" && ! -e "$state" ]]; then
    mv -- "$old_state" "$state"
fi
backup="$state/plasmoid/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
cp "$HOME/.config/plasma-org.kde.plasma.desktop-appletsrc" "$backup/"

# The package carries its own copy of the read-only helper, so it does not depend on this checkout.
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
cp -r package "$build/package"
mkdir -p "$build/package/contents/code"
cp ../sheet_json.py ../mouseview.py ../model.py ../device_io.py "$build/package/contents/code/"

if [[ -d "$HOME/.local/share/plasma/plasmoids/io.github.compact161.g502onboardeditor.buttonmap" ]]; then
    kpackagetool6 --type Plasma/Applet --upgrade "$build/package"
    echo "Updated. The changed UI appears after restarting plasmashell (or logging in again)."
else
    kpackagetool6 --type Plasma/Applet --install "$build/package"
fi
qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "$(cat enable.js)"

autostart="$HOME/.config/autostart/io.github.compact161.G502OnboardEditor.CheatSheet.desktop"
if [[ -e "$autostart" ]]; then
    mv "$autostart" "$backup/"
    echo "Moved the old tray program's autostart to $backup."
fi
pkill -u "$(id -u)" -f 'g502-(gshift|onboard-editor)/(app/)?cheatsheet\.py' && echo "Stopped the old tray program." || true
