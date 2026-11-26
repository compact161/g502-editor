#!/usr/bin/env bash
# Installs or updates the editor and the window cheat sheet for the current user as a copy
# in ~/.local/share/g502-onboard-editor/app, so the menu entries do not depend on which branch
# this checkout is on. Run again after changing the code. Never touches the mouse.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
data="$HOME/.local/share/g502-onboard-editor"
app="$data/app"
menu="$HOME/.local/share/applications"

# Until 2026-11 the project was called G502 G-Shift: move its data folder (backups,
# cheat sheet state) once.
old_data="$HOME/.local/share/g502-gshift"
if [[ -d "$old_data" && ! -e "$data" ]]; then
    mv -- "$old_data" "$data"
    echo "Moved $old_data to $data."
fi

mkdir -p "$app" "$menu"
install -m 644 app.py model.py device_io.py mouseview.py cheatsheet.py sheet_json.py "$app/"
install -m 755 start.sh "$app/"
git rev-parse --short HEAD >"$app/VERSION" 2>/dev/null || true
for entry in io.github.compact161.G502OnboardEditor.desktop \
    io.github.compact161.G502OnboardEditor.CheatSheet.desktop; do
    sed "s|@APP@|$app|g" "$entry" >"$menu/$entry"
done
update-desktop-database "$menu" 2>/dev/null || true
echo "Installed to $app ($(cat "$app/VERSION" 2>/dev/null || echo unknown)). Restart a running editor to see changes."
