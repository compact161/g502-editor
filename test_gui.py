"""Run with xvfb-run -a /usr/bin/python test_gui.py. No device IO."""

import argparse
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from gi.repository import Gdk

import model
from app import Editor, GLib, Gtk
from model import changes, dump, encoded, load
from test_model import fixture

errors = []
app = Editor(argparse.Namespace(file=None, read_only=False))


def iter_children(widget):
    child = widget.get_first_child()
    while child:
        yield child
        child = child.get_next_sibling()


def check():
    try:
        app.set_profiles(fixture(), None, "Automatischer Offline-Test")
        assert not app.write_button.get_sensitive()
        app.edit_mapping(1, "gbuttons", 0)
        dialogs = [w for w in Gtk.Window.list_toplevels() if w != app.window]
        dialog = dialogs[-1]
        content = dialog.get_child()
        action = content.get_first_child().get_next_sibling()
        offline_keys = (
            [c for c in iter_children(content) if isinstance(c, Gtk.Box)][0]  # noqa: RUF015
            .get_first_child()
            .get_model()
        )
        assert "(mouse)" not in offline_keys.get_string(offline_keys.get_n_items() - 1)
        action.set_selected(2)  # Replace macro explicitly with left click.
        child = content.get_first_child()
        while child:
            if isinstance(child, Gtk.Button) and child.get_label() == "Apply to draft":
                child.emit("clicked")
                break
            child = child.get_next_sibling()
        assert len(changes(app.before, app.edited)) == 1
        assert app.edited.profiles[1].gbuttons[0].value == 1
        assert not app.write_button.get_sensitive()
        assert app.before.profiles[1].gbuttons[0].behavior == 0
        assert [s.get_value_as_int() for s in app.dpi_spins] == [800, 1600, 0, 0, 0]
        app.dpi_spins[2].set_value(3200)
        app.dpi_default.set_selected(2)
        app.apply_dpi()
        assert app.edited.profiles[1].resolutions == [800, 1600, 3200, 0, 0]
        assert app.edited.profiles[1].resolution_default_index == 2
        assert len(changes(app.before, app.edited)) == 2
        app.dpi_spins[0].set_value(0)  # gap: rejected, draft unchanged, fields reset
        app.apply_dpi()
        assert "not applied" in app.status.get_text()
        assert app.dpi_spins[0].get_value_as_int() == 800
        # Lighting: X PLUS edits slot 1; "As read" restores; bad period rejected.
        assert app.led_effect.get_selected() == 0 and not app.led_color.get_sensitive()
        app.led_effect.set_selected(list(model.LED_EFFECTS).index(0x0A) + 1)
        assert app.led_color.get_sensitive() and app.led_period.get_sensitive()
        rgba = Gdk.RGBA()
        rgba.parse("#ff8000")
        app.led_color.set_rgba(rgba)
        app.led_period.set_value(3000)
        app.led_intensity.set_value(60)
        app.apply_lighting()
        assert app.edited.profiles[1].lighting[1].to_bytes() == model.led_bytes(
            0x0A, 0xFF8000, 3000, 60
        )
        assert (
            app.edited.profiles[1].lighting[3].to_bytes()
            == app.edited.profiles[1].lighting[1].to_bytes()
        )
        assert (
            app.edited.profiles[1].lighting[0].to_bytes()
            == app.before.profiles[1].lighting[0].to_bytes()
        )
        assert app.edited.profiles[1].animation == 0
        assert any("lighting" in c for c in changes(app.before, app.edited))
        app.led_effect.set_selected(0)
        app.apply_lighting()
        assert not any("lighting" in c for c in changes(app.before, app.edited))
        app.led_effect.set_selected(len(model.LED_EFFECTS) + 1)
        app.apply_lighting()
        assert app.led_effect.get_selected() == len(model.LED_EFFECTS) + 1
        assert any("Factory animation" in c for c in changes(app.before, app.edited))
        app.led_effect.set_selected(0)
        app.apply_lighting()
        # Click macro: only offered with live sector data.
        assert app.view() is None
        live = {str(i): ("ff" * 255) for i in range(16)}
        app.set_profiles(fixture(), None, "Test mit Sektordaten", live)
        app.edit_mapping(1, "buttons", 0)
        dialog = [w for w in Gtk.Window.list_toplevels() if w != app.window and w.get_visible()][-1]
        content = dialog.get_child()
        action = content.get_first_child().get_next_sibling()
        action.set_selected(action.get_model().get_n_items() - 1)
        custom = [c for c in iter_children(content) if isinstance(c, Gtk.Box)][0]  # noqa: RUF015
        key_combo = list(iter_children(custom))[0]  # noqa: RUF015
        names = [
            key_combo.get_model().get_string(i) for i in range(key_combo.get_model().get_n_items())
        ]
        assert names[-3:] == [
            "Left click (mouse)",
            "Right click (mouse)",
            "Middle click (mouse)",
        ], names[-3:]
        key_combo.set_selected(names.index("Left click (mouse)"))
        shift = [  # noqa: RUF015
            c for c in iter_children(list(iter_children(custom))[1]) if c.get_label() == "Shift"
        ][0]
        shift.set_active(True)
        [  # noqa: RUF015
            c
            for c in iter_children(content)
            if isinstance(c, Gtk.Button) and c.get_label() == "Apply to draft"
        ][0].emit("clicked")
        assert app.macros == [{"modifiers": 2, "button": 1, "sector": 2, "address": 0}], app.macros
        assert changes(app.before, app.edited, app.view()) == [
            "Profile 1, Normal, button 0: Left click → Shift+Left click (macro, held)"
        ]
        area = app.rows.get_first_child()
        assert isinstance(area, Gtk.DrawingArea)
        before_dialogs = len(Gtk.Window.list_toplevels())
        app.sheet_clicked(1, ("rmb", "buttons"))  # G-Shift trigger: protected, no dialog
        assert (
            "protected" in app.status.get_text()
            and len(Gtk.Window.list_toplevels()) == before_dialogs
        )
        app.sheet_clicked(1, ("lmb", "gbuttons"))
        assert len(Gtk.Window.list_toplevels()) == before_dialogs + 1
        [
            w
            for w in Gtk.Window.list_toplevels()
            if w.get_title() == "Button 0 · G-Shift" and w.get_visible()
        ][-1].destroy()
        checks = [c for c in iter_children(app.enabled_row) if isinstance(c, Gtk.CheckButton)]
        assert [c.get_active() for c in checks] == [True]
        checks[0].set_active(False)  # last enabled profile: rejected
        assert (
            app.edited.profiles[1].enabled == 1 and "At least one profile" in app.status.get_text()
        )
        app.identity = {"name": "G502 SE Hero Gaming Mouse", "serial": "x"}
        app.update_summary()
        assert not app.write_button.get_sensitive()
        app.identity = {"name": "G502 X PLUS", "serial": "x"}
        app.update_summary()
        assert app.write_button.get_sensitive()
        app.identity = None
        app.update_summary()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.yaml"
            path.write_text(dump(app.edited))
            assert encoded(load(path.read_text())) == encoded(app.edited)
        # Exercise the actual user's file optionally, without including it in Git.
        if os.environ.get("G502_TEST_FILE"):
            app.open_file(Path(os.environ["G502_TEST_FILE"]))
            assert len(app.before.profiles) == 5
        GLib.timeout_add(500, finish)
    except Exception as exc:
        errors.append(exc)
        app.quit()
    return False


def finish():
    try:
        if os.environ.get("G502_TEST_SCREENSHOT"):
            from PIL import ImageGrab

            ImageGrab.grab().save(os.environ["G502_TEST_SCREENSHOT"])
    except Exception as exc:
        errors.append(exc)
    app.quit()
    return False


with patch("app.subprocess.run", side_effect=AssertionError("Unexpected device access")):
    GLib.timeout_add(250, check)
    app.run([])
if errors:
    raise errors[0]
print(
    "GTK smoke test passed: edit, DPI, lighting, diff, write gating, YAML roundtrip; no device access."
)
