#!/usr/bin/python
"""Local GTK4 editor for buttons, G-Shift and DPI; writes only after reading a mouse and confirming."""

import argparse
import copy
import json
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from logitech_receiver.hidpp20 import Button, ButtonKeys  # noqa: E402

import mouseview  # noqa: E402
from model import (  # noqa: E402
    CLICKS,
    DPI_MAX,
    DPI_STEP,
    LED_EFFECTS,
    LED_PERIOD_MAX,
    LED_PERIOD_MIN,
    LED_PERIOD_STEP,
    apply_macros,
    attach_animation,
    changes,
    click_macro,
    click_macro_of,
    describe,
    describe_lighting,
    dump,
    is_factory_lighting,
    is_shift,
    led_params,
    led_slot,
    load,
    place_macro,
    replace,
    save_snapshot,
    set_dpi,
    set_enabled,
    set_factory_lighting,
    set_lighting,
)

HERE = Path(__file__).resolve().parent
BACKUPS = Path.home() / ".local/share/g502-onboard-editor/backups"
# Solaar button index → physical button, taken from each model's factory profile (ROM 0x101).
POSITIONS = {
    "hero": [
        "Left click",
        "Right click",
        "Wheel click",
        "Thumb, rear",
        "Thumb, front",
        "Sniper (thumb)",
        "Next to left click · factory: DPI −",
        "Next to left click · factory: DPI +",
        "Behind the wheel · factory: profile",
        "Wheel tilt right",
        "Wheel tilt left",
    ],
    "x plus": [
        "Left click",
        "Right click",
        "Wheel click",
        "Thumb, rear",
        "Sniper (thumb)",
        "Thumb, front",
        "Wheel tilt left",
        "Wheel tilt right",
        "Behind the wheel · factory: profile",
        "Next to left click · factory: DPI +",
        "Next to left click · factory: DPI −",
    ],
}


def positions(name):
    return next((table for key, table in POSITIONS.items() if key in name.lower()), [])


def label(text, **kwargs):
    return Gtk.Label(label=text, xalign=0, **kwargs)


def button(text, callback):
    obj = Gtk.Button(label=text)
    obj.connect("clicked", lambda _: callback())
    return obj


def searchable_dropdown(items):
    dropdown = Gtk.DropDown.new_from_strings(items)
    dropdown.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
    dropdown.set_enable_search(True)
    dropdown.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
    return dropdown


def box(vertical=False, spacing=8):
    return Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
        spacing=spacing,
    )


class Editor(Gtk.Application):
    def __init__(self, args):
        super().__init__(application_id="io.github.compact161.G502OnboardEditor", flags=0)
        self.args = args
        self.before = self.edited = self.identity = self.sectors = None
        self.macros = []  # pending click macros, written together with the profile
        self.devices = []
        self.busy = False

    def do_activate(self):
        if hasattr(self, "window"):
            self.window.present()
            return
        self.window = Gtk.ApplicationWindow(application=self, title="G502 Onboard Editor")
        self.window.set_default_size(1080, 1000)
        self.window.connect("close-request", self.close_requested)
        outer = box(True, 12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{edge}")(18)
        self.window.set_child(outer)
        title = label("G502 Onboard Editor")
        title.add_css_class("title-1")
        outer.append(title)
        outer.append(
            label(
                "Edit the draft, then write it to the X Plus · the HERO stays read-only", wrap=True
            )
        )
        self.controls = box(True)
        outer.append(self.controls)
        files = box()
        self.controls.append(files)
        files.append(button("Open YAML …", lambda: self.guard(lambda: self.file_dialog(False))))
        files.append(button("Export draft …", lambda: self.file_dialog(True)))
        files.append(button("Discard changes", self.reset))
        devices = box()
        self.controls.append(devices)
        devices.append(
            button("Find mice (read)", lambda: self.worker({"action": "list"}, self.listed))
        )
        self.device_combo = Gtk.ComboBoxText(hexpand=True)
        devices.append(self.device_combo)
        devices.append(button("Read selected mouse", lambda: self.guard(self.read_device)))
        self.source = label("No profile loaded yet.", wrap=True, selectable=True)
        self.controls.append(self.source)
        self.enabled_row = box()
        self.controls.append(self.enabled_row)
        self.profile_combo = Gtk.ComboBoxText()
        self.profile_combo.connect("changed", lambda _: self.render())
        self.controls.append(self.profile_combo)
        dpi = box()
        self.controls.append(dpi)
        dpi.append(label("DPI steps (0 = off)"))
        self.dpi_spins = []
        for _ in range(5):
            spin = Gtk.SpinButton.new_with_range(0, DPI_MAX, DPI_STEP)
            spin.set_width_chars(6)
            dpi.append(spin)
            self.dpi_spins.append(spin)
        steps = [f"Step {i}" for i in range(1, 6)]
        dpi = box()
        self.controls.append(dpi)
        dpi.append(label("Default"))
        self.dpi_default = Gtk.DropDown.new_from_strings(steps)
        dpi.append(self.dpi_default)
        dpi.append(label("DPI shift"))
        self.dpi_shift = Gtk.DropDown.new_from_strings(steps)
        dpi.append(self.dpi_shift)
        dpi.append(button("Apply DPI to draft", self.apply_dpi))
        led = box()
        self.controls.append(led)
        led.append(label("Lighting"))
        self.led_choices = [
            "As read from mouse",
            *LED_EFFECTS.values(),
            "Factory animation (X PLUS)",
        ]
        self.led_effect = Gtk.DropDown.new_from_strings(self.led_choices)
        self.led_effect.connect("notify::selected", lambda *_: self.led_sensitivity())
        led.append(self.led_effect)
        self.led_color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
        self.led_color.set_tooltip_text("Colour (Static, Breathe)")
        led.append(self.led_color)
        led.append(label("Period ms"))
        self.led_period = Gtk.SpinButton.new_with_range(
            LED_PERIOD_MIN, LED_PERIOD_MAX, LED_PERIOD_STEP
        )
        led.append(self.led_period)
        led.append(label("Brightness %"))
        self.led_intensity = Gtk.SpinButton.new_with_range(1, 100, 1)
        led.append(self.led_intensity)
        led.append(button("Apply lighting to draft", self.apply_lighting))
        self.led_current = label("", wrap=True)
        led.append(self.led_current)
        scroller = Gtk.ScrolledWindow(vexpand=True, min_content_height=250)
        self.controls.append(scroller)
        self.rows = box(True, 5)
        scroller.set_child(self.rows)
        self.summary = Gtk.TextView(
            editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR
        )
        summary_scroll = Gtk.ScrolledWindow(min_content_height=70)
        summary_scroll.set_child(self.summary)
        self.controls.append(summary_scroll)
        self.write_button = button("Write changes to X Plus …", self.confirm_write)
        self.write_button.add_css_class("suggested-action")
        self.controls.append(self.write_button)
        self.status = label(
            "Open a file or explicitly read a device. No automatic device access.",
            wrap=True,
            selectable=True,
        )
        outer.append(self.status)
        self.update_summary()
        if self.args.file:
            try:
                self.open_file(Path(self.args.file))
            except Exception as exc:
                self.status.set_text(f"Could not open file: {exc}")
        self.window.present()

    def dirty(self):
        return self.before is not None and bool(changes(self.before, self.edited))

    def reserved(self):
        return [0, *(p.sector for p in self.before.profiles.values())]

    def view(self):
        """Macro sectors as they will be after writing; None for offline files."""
        if self.sectors is None:
            return None
        return {**self.sectors, **apply_macros(self.sectors, self.macros, self.reserved())}

    def confirm(self, heading, detail, accept, action):
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            text=heading,
            secondary_text=detail,
            message_type=Gtk.MessageType.WARNING,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button(accept, Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)

        def response(d, value):
            d.destroy()
            if value == Gtk.ResponseType.ACCEPT:
                action()

        dialog.connect("response", response)
        dialog.present()

    def guard(self, action):
        if self.dirty():
            self.confirm(
                "Discard unsaved changes?", "Export the draft first if needed.", "Discard", action
            )
        else:
            action()

    def close_requested(self, _):
        if self.busy:
            self.status.set_text("Device operation running. Please wait for the result.")
            return True
        if self.dirty():
            self.confirm(
                "Close editor?", "Changes that were not exported will be lost.", "Close", self.quit
            )
            return True
        return False

    def file_dialog(self, saving):
        if saving and self.edited is None:
            self.status.set_text("Read a profile file or a mouse first.")
            return
        chooser = Gtk.FileChooserNative.new(
            "Export draft" if saving else "Open Solaar YAML",
            self.window,
            Gtk.FileChooserAction.SAVE if saving else Gtk.FileChooserAction.OPEN,
            "Export" if saving else "Open",
            "Cancel",
        )
        filt = Gtk.FileFilter()
        filt.set_name("Solaar YAML")
        filt.add_pattern("*.yaml")
        filt.add_pattern("*.yml")
        chooser.add_filter(filt)
        if saving:
            chooser.set_current_name("g502-draft.yaml")

        def response(d, value):
            try:
                if value == Gtk.ResponseType.ACCEPT:
                    path = Path(d.get_file().get_path())
                    if saving:
                        # Never overwrite an existing backup, even after a chooser confirmation.
                        with path.open("x") as f:
                            f.write(dump(self.edited))
                        self.status.set_text(
                            f"Draft exported: {path}. Nothing was written to the mouse."
                        )
                    else:
                        self.open_file(path)
            except Exception as exc:
                self.status.set_text(f"File action failed: {exc}")
            finally:
                d.destroy()

        chooser.connect("response", response)
        chooser.show()

    def open_file(self, path):
        self.set_profiles(load(path.read_text()), None, f"File: {path} · offline, no write target")

    def set_profiles(self, profiles, identity, source, sectors=None):
        self.sectors = (
            None if sectors is None else {int(k): bytes.fromhex(v) for k, v in sectors.items()}
        )
        attach_animation(profiles, self.sectors)
        self.macros = []
        self.before = profiles
        self.edited = copy.deepcopy(profiles)
        self.identity = identity
        self.source.set_text(f"{profiles.name}\n{source}")
        self.profile_combo.remove_all()
        for pid, p in profiles.profiles.items():
            self.profile_combo.append(str(pid), f"Profile {pid} · {p.name or 'Unnamed'}")
        self.profile_combo.set_active(0)
        self.render_enabled()
        self.update_summary()

    def render(self):
        while self.rows.get_first_child():
            self.rows.remove(self.rows.get_first_child())
        pid = self.profile_combo.get_active_id()
        if self.edited is None or pid is None:
            return
        pid = int(pid)
        p = self.edited.profiles[pid]
        for spin, value in zip(self.dpi_spins, p.resolutions):  # noqa: B905
            spin.set_value(value)
        self.dpi_default.set_selected(min(p.resolution_default_index, 4))
        self.dpi_shift.set_selected(min(p.resolution_shift_index, 4))
        self.render_lighting(pid)
        known = positions(self.edited.name)
        table = mouseview.slots(self.edited.name)
        if table:
            # Clickable drawing: upper part of a box edits the normal layer, the blue line G-Shift.
            area = Gtk.DrawingArea(
                hexpand=True, vexpand=True, content_width=700, content_height=430
            )
            data = mouseview.sheet(self.edited, self.view(), pid)
            footer = '<span size="small" alpha="65%">Click a box to change it:\ntop part normal, blue line G-Shift.</span>'

            def paint(widget, cr, width, height):
                color = widget.get_color()
                mouseview.draw(
                    cr, width, height, (color.red, color.green, color.blue), data, footer
                )

            area.set_draw_func(paint)
            click = Gtk.GestureClick()
            click.connect(
                "released",
                lambda g, n, x, y: self.sheet_clicked(
                    pid, mouseview.hit(area.get_width(), area.get_height(), x, y)
                ),
            )
            area.add_controller(click)
            area.set_cursor_from_name("pointer")
            self.rows.append(area)
            return
        grid = Gtk.Grid(column_spacing=12, row_spacing=5, column_homogeneous=True)
        self.rows.append(grid)
        for col, text in enumerate(("Button / index", "Normal", "G-Shift")):
            head = label(text)
            head.add_css_class("heading")
            grid.attach(head, col, 0, 1, 1)
        for i in range(max(len(p.buttons), len(p.gbuttons))):
            position = known[i] if i < len(known) else "Position unknown"
            grid.attach(label(f"{i} · {position}", wrap=True), 0, i + 1, 1, 1)
            for col, layer in ((1, "buttons"), (2, "gbuttons")):
                mappings = getattr(p, layer)
                if i >= len(mappings):
                    grid.attach(label("Not present"), col, i + 1, 1, 1)
                    continue
                mapping = mappings[i]
                edit = button(
                    describe(mapping, self.view()),
                    lambda pid=pid, layer=layer, i=i: self.edit_mapping(pid, layer, i),
                )
                edit.set_sensitive(not is_shift(mapping))
                edit.set_tooltip_text(
                    "G-Shift stays protected"
                    if is_shift(mapping)
                    else "Change mapping in the draft"
                )
                grid.attach(edit, col, i + 1, 1, 1)

    def sheet_clicked(self, pid, target):
        if target is None:
            return
        slot, layer = target
        index = mouseview.slots(self.edited.name).index(slot)
        mappings = getattr(self.edited.profiles[pid], layer)
        if index >= len(mappings):
            return
        if is_shift(mappings[index]):
            self.status.set_text("The G-Shift button stays protected and cannot be remapped.")
        else:
            self.edit_mapping(pid, layer, index)

    def edit_mapping(self, pid, layer, index):
        current = getattr(self.edited.profiles[pid], layer)[index]
        dialog = Gtk.Window(
            title=f"Button {index} · {'G-Shift' if layer == 'gbuttons' else 'Normal'}",
            transient_for=self.window,
            modal=True,
        )
        content = box(True, 10)
        for edge in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{edge}")(18)
        dialog.set_child(content)
        content.append(label(f"Current: {describe(current, self.view())}", wrap=True))
        actions = [copy.deepcopy(current), Button(behavior=8, type=0)]
        actions += [Button(behavior=8, type=1, value=v) for v in (1, 2, 4, 8, 16)]
        actions += [Button(behavior=8, type=3, value=v) for v in (233, 234, 226, 205)]
        actions += [Button(behavior=9, value=v, data=0) for v in (5, 7, 11)]
        action = searchable_dropdown(
            ["Keep unchanged"]
            + [describe(item) for item in actions[1:]]
            + ["Key / key combination …"]
        )
        content.append(action)
        content.append(
            label(
                "Open the list and type in the search field, e.g. Enter, arrow, F12 or left click.",
                wrap=True,
            )
        )
        custom = box(True)
        keys = [int(key) for key in ButtonKeys]
        key_names = []
        for key in ButtonKeys:
            translated = describe(Button(behavior=8, type=2, modifiers=0, value=int(key)))
            key_names.append(translated if translated == str(key) else f"{translated} ({key})")
        # Mouse buttons join the key list; with a modifier they become a held macro and need live sector data.
        clicks = list(CLICKS) if self.sectors is not None else []
        key_names += [f"{CLICKS[c]} (mouse)" for c in clicks]
        key_combo = searchable_dropdown(key_names)
        keyboard = current.behavior == 8 and current.type == 2
        current_click = click_macro_of(current, self.view())
        if keyboard:
            key_combo.set_selected(keys.index(int(current.value)))
        elif current_click and current_click["button"] in clicks:
            key_combo.set_selected(len(keys) + clicks.index(current_click["button"]))
        else:
            key_combo.set_selected(keys.index(40))
        custom.append(key_combo)
        checks = []
        mod_row = box()
        custom.append(mod_row)
        preset = (
            current.modifiers if keyboard else current_click["modifiers"] if current_click else 0
        )
        for bit, name in ((1, "Ctrl"), (2, "Shift"), (4, "Alt"), (8, "Super (Meta)")):
            check = Gtk.CheckButton(label=name)
            check.set_active(bool(preset & bit))
            mod_row.append(check)
            checks.append((bit, check))
        content.append(custom)

        def sensitivity(*_):
            custom.set_sensitive(action.get_selected() == len(actions))

        sensitivity()
        action.connect("notify::selected", sensitivity)
        content.append(
            label(
                "Existing macros are kept until you explicitly replace them with another action.",
                wrap=True,
                max_width_chars=55,
            )
        )

        def apply():
            selected = action.get_selected()
            modifiers = sum(bit for bit, check in checks if check.get_active())
            chosen = key_combo.get_selected()
            if selected == len(actions) and chosen >= len(keys):
                click = clicks[chosen - len(keys)]
                if not modifiers:
                    item = Button(behavior=8, type=1, value=click)
                else:
                    try:
                        macro = {"modifiers": modifiers, "button": click}
                        sector, address, new = place_macro(
                            self.sectors, self.reserved(), click_macro(**macro), self.macros
                        )
                    except ValueError as exc:
                        self.status.set_text(str(exc))
                        return
                    if new:
                        self.macros.append({**macro, "sector": sector, "address": address})
                    item = Button(behavior=0, sector=sector, address=address)
            elif selected == len(actions):
                item = Button(behavior=8, type=2, modifiers=modifiers, value=keys[chosen])
            else:
                item = actions[selected]
            replace(self.edited, pid, layer, index, item)
            self.render()
            self.update_summary()
            dialog.destroy()

        content.append(button("Apply to draft", apply))
        content.append(button("Cancel", dialog.destroy))
        dialog.present()

    def render_enabled(self):
        while self.enabled_row.get_first_child():
            self.enabled_row.remove(self.enabled_row.get_first_child())
        if self.edited is None:
            return
        self.enabled_row.append(label("Enabled profiles (reachable with the profile button):"))
        for pid, p in self.edited.profiles.items():
            check = Gtk.CheckButton(label=f"Profile {pid}", active=bool(p.enabled))
            check.connect("toggled", lambda c, pid=pid: self.toggle_enabled(pid, c.get_active()))
            self.enabled_row.append(check)

    def toggle_enabled(self, pid, enabled):
        if bool(self.edited.profiles[pid].enabled) == enabled:
            return
        try:
            set_enabled(self.edited, pid, enabled)
            self.status.set_text(
                "Profile enabling changed in the draft. Nothing was written to the mouse."
            )
        except ValueError as exc:
            self.status.set_text(str(exc))
            GLib.idle_add(self.render_enabled)  # not while the toggle signal is running
        self.update_summary()

    def apply_dpi(self):
        pid = self.profile_combo.get_active_id()
        if self.edited is None or pid is None:
            self.status.set_text("Read a profile file or a mouse first.")
            return
        try:
            set_dpi(
                self.edited,
                int(pid),
                [spin.get_value_as_int() for spin in self.dpi_spins],
                self.dpi_default.get_selected(),
                self.dpi_shift.get_selected(),
            )
            self.status.set_text("DPI applied to the draft. Nothing was written to the mouse.")
        except ValueError as exc:
            self.status.set_text(f"DPI not applied: {exc}")
        self.render()
        self.update_summary()

    def render_lighting(self, pid):
        p = self.edited.profiles[pid]
        params = led_params(p.lighting[led_slot(self.edited.name)].to_bytes())
        effects = list(LED_EFFECTS)
        if is_factory_lighting(self.edited, p):
            self.led_effect.set_selected(len(effects) + 1)
        else:
            self.led_effect.set_selected(
                effects.index(params["effect"]) + 1
                if params and not getattr(p, "animation", 0)
                else 0
            )
        params = params or {"color": 0xFFFFFF, "period": 5000, "intensity": 100}
        rgba = Gdk.RGBA()
        rgba.parse(f"#{params['color']:06x}")
        self.led_color.set_rgba(rgba)
        self.led_period.set_value(params["period"])
        self.led_intensity.set_value(params["intensity"])
        self.led_current.set_text(
            f"Read: {describe_lighting(self.before, self.before.profiles[pid])}"
        )
        self.led_sensitivity()

    def led_sensitivity(self):
        selected = self.led_effect.get_selected()
        effect = list(LED_EFFECTS)[selected - 1] if 0 < selected <= len(LED_EFFECTS) else None
        self.led_color.set_sensitive(effect in (0x01, 0x0A))
        self.led_period.set_sensitive(effect in (0x0A, 0x03))
        self.led_intensity.set_sensitive(effect in (0x0A, 0x03))

    def apply_lighting(self):
        pid = self.profile_combo.get_active_id()
        if self.edited is None or pid is None:
            self.status.set_text("Read a profile file or a mouse first.")
            return
        pid, selected = int(pid), self.led_effect.get_selected()
        try:
            if selected == 0:
                old, new = self.before.profiles[pid], self.edited.profiles[pid]
                new.lighting = copy.deepcopy(old.lighting)
                if hasattr(old, "animation"):
                    new.animation = old.animation
                else:
                    new.__dict__.pop("animation", None)
            elif selected > len(LED_EFFECTS):
                set_factory_lighting(self.edited, pid)
            else:
                c = self.led_color.get_rgba()
                color = (
                    (round(c.red * 255) << 16) | (round(c.green * 255) << 8) | round(c.blue * 255)
                )
                set_lighting(
                    self.edited,
                    pid,
                    list(LED_EFFECTS)[selected - 1],
                    color,
                    self.led_period.get_value_as_int(),
                    self.led_intensity.get_value_as_int(),
                )
            self.status.set_text("Lighting applied to the draft. Nothing was written to the mouse.")
        except ValueError as exc:
            self.status.set_text(f"Lighting not applied: {exc}")
        self.render()
        self.update_summary()

    def reset(self):
        if self.before is not None:

            def restore():
                self.edited = copy.deepcopy(self.before)
                self.macros = []
                self.render_enabled()
                self.render()
                self.update_summary()

            self.guard(restore)

    def update_summary(self):
        diff = changes(self.before, self.edited, self.view()) if self.before is not None else []
        self.summary.get_buffer().set_text("\n".join(diff) if diff else "No changes in the draft.")
        can_write = bool(
            diff
            and self.identity
            and not self.args.read_only
            and "g502 x plus" in self.identity["name"].lower()
        )
        self.write_button.set_sensitive(can_write)
        self.write_button.set_tooltip_text(
            "Only available after reading an X Plus live (not with --read-only)."
        )

    def worker(self, request, callback):
        if self.busy:
            return
        self.busy = True
        self.controls.set_sensitive(False)
        self.status.set_text("Device operation running …")

        def work():
            try:
                # No timeout while writing: killing a write halfway would be unsafe.
                proc = subprocess.run(  # noqa: PLW1510
                    ["/usr/bin/python", str(HERE / "device_io.py")],
                    input=json.dumps(request),
                    text=True,
                    capture_output=True,
                    timeout=None if request["action"] == "write" else 90,
                )
                if proc.returncode:
                    raise RuntimeError(
                        proc.stderr[-1500:] or f"Device process exited: {proc.returncode}"
                    )
                result = json.loads(proc.stdout)
                if not result["ok"]:
                    raise RuntimeError(result["error"])
                GLib.idle_add(done, result["result"], None)
            except Exception as exc:
                GLib.idle_add(done, None, str(exc))

        def done(result, error):
            self.busy = False
            self.controls.set_sensitive(True)
            if error:
                self.status.set_text(f"Error: {error}")
                if request["action"] == "write":
                    self.identity = None  # Require fresh read after any uncertain write.
            else:
                try:
                    callback(result)
                except Exception as exc:
                    self.status.set_text(f"Could not load the result: {exc}")
            self.update_summary()
            return False

        threading.Thread(target=work, daemon=True).start()

    def listed(self, devices):
        self.devices = devices
        self.device_combo.remove_all()
        for i, dev in enumerate(devices):
            self.device_combo.append(
                str(i),
                f"{dev['name']} · {'wireless' if dev['path'] in (None, 'None') else dev['path']} · device {dev['number']}",
            )
        self.device_combo.set_active(0)
        self.status.set_text(f"Found {len(devices)} reachable G502. No profiles read yet.")

    def read_device(self):
        selected = self.device_combo.get_active_id()
        if selected is None:
            self.status.set_text("Find mice and select a device first.")
            return

        def read(result):
            self.set_profiles(
                load(result["profiles"]),
                result["identity"],
                "Read directly from the selected mouse",
                result["sectors"],
            )
            save_snapshot(
                result["identity"]["name"], result["profiles"], result["sectors"], result["active"]
            )
            self.status.set_text("Profiles read. Nothing was written to the mouse.")

        self.worker({"action": "read", "identity": self.devices[int(selected)]}, read)

    def confirm_write(self):
        diff = changes(self.before, self.edited, self.view())

        def write():
            self.worker(
                {
                    "action": "write",
                    "identity": self.identity,
                    "before": dump(self.before),
                    "after": dump(self.edited),
                    "allow": not self.args.read_only,
                    "macros": self.macros,
                    "backup_dir": str(BACKUPS),
                },
                self.written,
            )

        self.confirm(
            f"Write mapping to {self.identity['name']}?",
            "\n".join(diff)
            + f"\n\nAll sectors are backed up to {BACKUPS} first. Only button, DPI and lighting bytes of the affected profiles and the enabling in the profile directory are changed.",
            "Back up and write",
            write,
        )

    def written(self, result):
        self.set_profiles(
            load(result["profiles"]),
            self.identity,
            "Read back successfully after writing",
            result["sectors"],
        )
        save_snapshot(self.identity["name"], result["profiles"], result["sectors"])
        self.status.set_text(
            f"{result['sectors_written']} sectors written and read back. Backup: {result['backup']}. Please check the function on the mouse."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="open a Solaar YAML offline")
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="lock writing to the X Plus (the HERO is always locked)",
    )
    Editor(parser.parse_args()).run([])
