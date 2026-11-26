"""Run with GSK_RENDERER=cairo GDK_BACKEND=x11 xvfb-run -a /usr/bin/python test_cheatsheet.py.
No device IO and no tray registration: the D-Bus side of the tray is replaced."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import cairo

import cheatsheet
from model import dump, save_snapshot, with_crc
from test_model import fixture

errors = []
tmp = tempfile.TemporaryDirectory()
path = Path(tmp.name) / "cheatsheet.json"
macro = (
    bytes.fromhex("4302e1 400032 4308e3 400032 43004f 400032 44004f 400032 4408e3 400032 4402e1")
    + b"\xff"
)
sectors = {str(i): ("ff" * 255) for i in range(16)}
sectors["6"] = with_crc(b"\xff" * 34 + macro + b"\xff" * (255 - 34 - len(macro))).hex()
save_snapshot("G502 X PLUS", dump(fixture()), sectors, 1, path)
snapshot = json.loads(path.read_text())
assert set(snapshot) == {"name", "profiles", "sectors", "active", "time"}
save_snapshot(
    "G502 X PLUS", dump(fixture()), sectors, None, path
)  # editor write keeps the active profile
assert json.loads(path.read_text())["active"] == 1
assert cheatsheet.sheet(snapshot, 1) == {
    "lmb": ("Left click", "Shift+Super+Right arrow"),
    "rmb": ("G-Shift", "G-Shift"),
}
surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 980, 600)
cheatsheet.mouseview.draw(
    cairo.Context(surface), 980, 600, (1, 1, 1), cheatsheet.sheet(snapshot, 1), "DPI"
)
assert any(surface.get_data())
assert cheatsheet.mouseview.hit(980, 600, 30, 30) == (
    "lmb",
    "buttons",
) and cheatsheet.mouseview.hit(1960, 1200, 60, 150) == ("lmb", "gbuttons")
assert cheatsheet.mouseview.hit(980, 600, 490, 300) is None

with (
    patch.object(cheatsheet, "SNAPSHOT", path),
    patch("cheatsheet.subprocess.run", side_effect=AssertionError("Unexpected device access")),
):

    class FakeBus:
        def register_object(self, *a):
            pass

        def call(self, *a):
            raise AssertionError("Unexpected tray registration")

    with (
        patch.object(cheatsheet.Gio, "bus_get_sync", return_value=FakeBus()),
        patch.object(cheatsheet.Gio, "bus_own_name_on_connection"),
        patch.object(cheatsheet.Gio, "bus_watch_name_on_connection"),
    ):
        app = cheatsheet.CheatSheet(cheatsheet.Gio.ApplicationFlags.NON_UNIQUE)
        app.register()

    def check():
        try:
            assert not app.window.get_visible()
            assert "G502 X PLUS" in app.status.get_text()
            assert app.profile.get_model().get_string(0) == "Profile 1 · running"
            app.tray.call(
                None,
                None,
                None,
                None,
                "Activate",
                None,
                type("I", (), {"return_value": lambda s, v: None})(),
            )
            assert app.window.get_visible()
            assert app.tray.props["Menu"].unpack() == "/NO_DBUSMENU"
        except Exception as exc:
            errors.append(exc)
        app.quit()
        return False

    cheatsheet.GLib.timeout_add(400, check)
    app.run([])
if errors:
    raise errors[0]
print("Cheat sheet test passed: snapshot, decoding, drawing, tray click; no device access.")
