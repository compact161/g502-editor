#!/usr/bin/python
"""Tray cheat sheet: shows which action sits on which G502 button. Read-only."""

import json
import os
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

import mouseview  # noqa: E402
from model import SNAPSHOT, describe_dpi, load, save_snapshot  # noqa: E402

HERE = Path(__file__).resolve().parent


def sheet(snapshot, profile_id):
    return mouseview.sheet(
        load(snapshot["profiles"]),
        {int(k): bytes.fromhex(v) for k, v in snapshot["sectors"].items()},
        profile_id,
    )


SNI_XML = """<node><interface name="org.kde.StatusNotifierItem">
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/><property name="IconName" type="s" access="read"/>
<property name="IconThemePath" type="s" access="read"/><property name="ItemIsMenu" type="b" access="read"/>
<property name="Menu" type="o" access="read"/><property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<method name="ProvideXdgActivationToken"><arg type="s" direction="in"/></method>
<signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"""


class Tray:
    """Minimal StatusNotifierItem without menu: every click toggles the cheat sheet."""

    def __init__(self, on_click):
        self.on_click, self.token = on_click, None
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.props = {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", "g502-cheatsheet"),
            "Title": GLib.Variant("s", "G502 Button Map"),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", "input-mouse"),
            "IconThemePath": GLib.Variant("s", ""),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/NO_DBUSMENU"),
            "ToolTip": GLib.Variant(
                "(sa(iiay)ss)", ("input-mouse", [], "G502 Button Map", "Click: show button mapping")
            ),
        }
        info = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self.bus.register_object(
            "/StatusNotifierItem", info, self.call, lambda *a: self.props.get(a[4]), None
        )
        Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        # Register again whenever the tray host (re)appears, e.g. after a plasmashell restart.
        Gio.bus_watch_name_on_connection(
            self.bus,
            "org.kde.StatusNotifierWatcher",
            Gio.BusNameWatcherFlags.NONE,
            lambda *a: self.register(),
            None,
        )

    def register(self):
        self.bus.call(
            "org.kde.StatusNotifierWatcher",
            "/StatusNotifierWatcher",
            "org.kde.StatusNotifierWatcher",
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self.name,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            None,
        )

    def call(self, bus, sender, path, interface, method, params, invocation):
        if method == "ProvideXdgActivationToken":
            self.token = params.unpack()[0]
        elif method in ("Activate", "SecondaryActivate", "ContextMenu"):
            self.on_click(self.token)
            self.token = None
        invocation.return_value(None)


class CheatSheet(Gtk.Application):
    def __init__(self, flags=Gio.ApplicationFlags.DEFAULT_FLAGS):
        super().__init__(
            application_id="io.github.compact161.G502OnboardEditor.CheatSheet", flags=flags
        )
        self.snapshot = self.window = None
        self.busy = self.started = False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()  # keep running in the tray without a window
        self.build()
        self.tray = Tray(self.toggle)
        self.load_snapshot()
        self.monitor = Gio.File.new_for_path(str(SNAPSHOT)).monitor_file(
            Gio.FileMonitorFlags.NONE, None
        )
        self.monitor.connect("changed", lambda *a: self.load_snapshot())

    def do_activate(self):
        # The first activation is the (auto)start: stay in the tray. A second start shows the sheet.
        if self.started:
            self.toggle(None, show=True)
        self.started = True

    def build(self):
        self.window = Gtk.ApplicationWindow(
            application=self, title="G502 · Button Map", hide_on_close=True
        )
        self.window.set_default_size(1040, 720)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for edge in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{edge}")(12)
        self.window.set_child(outer)
        bar = Gtk.Box(spacing=8)
        outer.append(bar)
        self.profile = Gtk.DropDown.new_from_strings([])
        self.profile.connect("notify::selected", lambda *a: self.area.queue_draw())
        bar.append(self.profile)
        self.status = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        bar.append(self.status)
        for text, action in (
            ("Refresh from mouse", self.refresh),
            ("Open editor", self.editor),
            ("Quit", self.quit),
        ):
            b = Gtk.Button(label=text)
            b.connect("clicked", lambda _, action=action: action())
            bar.append(b)
            if action == self.refresh:
                self.refresh_button = b
        self.area = Gtk.DrawingArea(vexpand=True, hexpand=True)
        self.area.set_draw_func(self.on_draw)
        outer.append(self.area)
        key = Gtk.EventControllerKey()
        key.connect(
            "key-pressed",
            lambda c, keyval, *a: (
                keyval == Gdk.KEY_Escape and (self.window.set_visible(False) or True)
            ),
        )
        self.window.add_controller(key)

    def toggle(self, token, show=None):
        if show is None:
            show = not (self.window.get_visible() and self.window.is_active())
        if not show:
            self.window.set_visible(False)
            return
        if token:
            self.window.set_startup_id(token)  # Wayland: lets the compositor give us focus
        self.window.present()
        if self.snapshot is None:
            self.refresh()

    def profile_ids(self):
        profiles = load(self.snapshot["profiles"]).profiles
        return [
            pid
            for pid, p in profiles.items()
            if p.enabled or p.sector == self.snapshot.get("active")
        ]

    def load_snapshot(self):
        try:
            snapshot = json.loads(SNAPSHOT.read_text())
            profiles = load(snapshot["profiles"])
        except (OSError, ValueError, KeyError):
            if self.snapshot is None:
                self.status.set_text(
                    "No state yet. “Refresh from mouse” reads the mouse (read-only)."
                )
            return
        self.snapshot = snapshot
        ids = self.profile_ids()
        active = next(
            (pid for pid in ids if profiles.profiles[pid].sector == snapshot.get("active")), ids[0]
        )
        self.profile.set_model(
            Gtk.StringList.new(
                [f"Profile {pid}" + (" · running" if pid == active else "") for pid in ids]
            )
        )
        self.profile.set_selected(ids.index(active))
        self.status.set_text(
            f"{snapshot['name']} · as of {snapshot['time'].replace('T', ' ')[:16]}"
        )
        self.area.queue_draw()

    def on_draw(self, area, cr, width, height):
        if self.snapshot is None:
            return
        color = area.get_color()
        ids = self.profile_ids()
        pid = ids[min(self.profile.get_selected(), len(ids) - 1)]
        p = load(self.snapshot["profiles"]).profiles[pid]
        footer = (
            f'<span size="small" alpha="65%">DPI</span>\n<b>{GLib.markup_escape_text(describe_dpi(p))}</b>\n\n'
            '<span size="small" alpha="65%">Hold the G-Shift button to use the blue layer.</span>'
        )
        mouseview.draw(
            cr,
            width,
            height,
            (color.red, color.green, color.blue),
            sheet(self.snapshot, pid),
            footer,
        )

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_button.set_sensitive(False)
        self.status.set_text("Reading mouse …")

        def work():
            try:
                proc = subprocess.run(  # noqa: PLW1510
                    ["/usr/bin/python", str(HERE / "device_io.py")],
                    input=json.dumps({"action": "read_any"}),
                    text=True,
                    capture_output=True,
                    timeout=90,
                )
                result = json.loads(proc.stdout)
                if not result["ok"]:
                    raise RuntimeError(result["error"])
                r = result["result"]
                save_snapshot(r["identity"]["name"], r["profiles"], r["sectors"], r["active"])
                GLib.idle_add(done, None)
            except Exception as exc:
                GLib.idle_add(done, str(exc))

        def done(error):
            self.busy = False
            self.refresh_button.set_sensitive(True)
            if error:
                self.status.set_text(f"Reading failed: {error}")
            else:
                self.load_snapshot()
            return False

        threading.Thread(target=work, daemon=True).start()

    def editor(self):
        subprocess.Popen([str(HERE / "start.sh")], start_new_session=True)


if __name__ == "__main__":
    CheatSheet().run([])
