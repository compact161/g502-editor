"""Top view of the G502 with a callout per button; shared by cheat sheet and editor."""

import math

import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Pango, PangoCairo  # noqa: E402

from model import describe  # noqa: E402

ACCENT = (0.24, 0.68, 0.91)

# Solaar button index → physical slot, from each model's factory profile (ROM 0x101).
SLOTS = {
    "hero": [
        "lmb",
        "rmb",
        "wheel",
        "thumb_back",
        "thumb_fwd",
        "sniper",
        "dpi_dn",
        "dpi_up",
        "top",
        "tilt_r",
        "tilt_l",
    ],
    "x plus": [
        "lmb",
        "rmb",
        "wheel",
        "thumb_back",
        "sniper",
        "thumb_fwd",
        "tilt_l",
        "tilt_r",
        "top",
        "dpi_up",
        "dpi_dn",
    ],
}
TITLES = {
    "lmb": "Left button",
    "rmb": "Right button",
    "wheel": "Wheel click",
    "thumb_back": "Thumb, rear",
    "thumb_fwd": "Thumb, front",
    "sniper": "Sniper (thumb)",
    "tilt_l": "Wheel tilt left",
    "tilt_r": "Wheel tilt right",
    "top": "Behind the wheel",
    "dpi_up": "Next to left click · factory DPI +",
    "dpi_dn": "Next to left click · factory DPI −",
}
# Drawing: 980 x 600 units, mouse seen from above. Anchor = point on the mouse the callout points at.
ANCHORS = {
    "dpi_up": (421, 150),
    "dpi_dn": (419, 196),
    "lmb": (452, 120),
    "tilt_l": (478, 158),
    "sniper": (393, 262),
    "thumb_fwd": (397, 312),
    "thumb_back": (399, 366),
    "rmb": (530, 120),
    "wheel": (490, 140),
    "tilt_r": (502, 176),
    "top": (490, 224),
}
LEFT = ["lmb", "dpi_up", "dpi_dn", "tilt_l", "sniper", "thumb_fwd", "thumb_back"]
RIGHT = ["rmb", "wheel", "tilt_r", "top"]
ROW, BOX_W = 80, 290


def slots(name):
    return next((table for key, table in SLOTS.items() if key in name.lower()), [])


def sheet(profiles, sectors, profile_id):
    """{slot: (normal, gshift)} for one profile."""
    p = profiles.profiles[profile_id]
    result = {}

    def short(button):
        return describe(button, sectors).replace(" (macro, held)", " (held)").replace("Macro: ", "")

    for i, slot in enumerate(slots(profiles.name)):
        if i < len(p.buttons):
            result[slot] = (
                short(p.buttons[i]),
                short(p.gbuttons[i]) if i < len(p.gbuttons) else "–",
            )
    return result


def boxes():
    """(slot, x, y) of every callout box in drawing units."""
    for column, x in ((LEFT, 12), (RIGHT, 980 - 12 - BOX_W)):
        for row, slot in enumerate(column):
            yield slot, x, 16 + row * ROW


def transform(width, height):
    scale = min(width / 980, height / 600)
    return scale, (width - 980 * scale) / 2, (height - 600 * scale) / 2


def hit(width, height, px, py):
    """(slot, layer) under a pointer position, layer being 'buttons' or 'gbuttons'."""
    scale, ox, oy = transform(width, height)
    ux, uy = (px - ox) / scale, (py - oy) / scale
    for slot, x, y in boxes():
        if x <= ux <= x + BOX_W and y <= uy <= y + ROW - 10:
            return slot, "gbuttons" if uy > y + 42 else "buttons"
    return None


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def draw_mouse(cr, fg):
    def stroke(alpha=0.75, width=2):
        cr.set_source_rgba(*fg, alpha)
        cr.set_line_width(width)
        cr.stroke()

    def fill_stroke(alpha=0.10):
        cr.set_source_rgba(*fg, alpha)
        cr.fill_preserve()
        stroke()

    # Body
    cr.move_to(490, 62)
    cr.curve_to(545, 62, 580, 85, 584, 150)
    cr.curve_to(590, 250, 600, 330, 588, 420)
    cr.curve_to(578, 500, 540, 540, 490, 540)
    cr.curve_to(440, 540, 404, 500, 396, 420)
    cr.curve_to(388, 350, 402, 300, 408, 240)
    cr.curve_to(410, 200, 404, 160, 408, 130)
    cr.curve_to(414, 85, 440, 62, 490, 62)
    cr.close_path()
    fill_stroke(0.06)
    # Split between the main buttons and their rear edge
    cr.move_to(490, 62)
    cr.line_to(490, 250)
    stroke(0.45, 1.5)
    cr.move_to(410, 250)
    cr.curve_to(450, 268, 530, 268, 589, 250)
    stroke(0.45, 1.5)
    # Wheel, button behind it
    rounded(cr, 480, 110, 20, 78, 9)
    fill_stroke(0.22)
    for y in range(122, 182, 10):
        cr.move_to(483, y)
        cr.line_to(497, y)
        stroke(0.4, 1)
    rounded(cr, 480, 210, 20, 28, 5)
    fill_stroke(0.22)
    # Two buttons at the edge of the left main button
    rounded(cr, 412, 128, 16, 42, 5)
    fill_stroke(0.22)
    rounded(cr, 410, 176, 16, 42, 5)
    fill_stroke(0.22)
    # Thumb side: sniper, forward, back
    rounded(cr, 380, 244, 22, 36, 7)
    fill_stroke(0.22)
    rounded(cr, 386, 290, 18, 46, 6)
    fill_stroke(0.22)
    rounded(cr, 388, 342, 18, 50, 6)
    fill_stroke(0.22)
    # Tilt arrows
    for x, d in ((474, -1), (506, 1)):
        y = 158 if d < 0 else 176
        cr.move_to(x, y - 6)
        cr.line_to(x + 8 * d, y)
        cr.line_to(x, y + 6)
        stroke(0.7, 2)


def draw(cr, width, height, fg, data, footer):
    scale, ox, oy = transform(width, height)
    cr.translate(ox, oy)
    cr.scale(scale, scale)
    draw_mouse(cr, fg)

    def text(markup, x, y, w, wrap=False):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription("Sans 10"))
        layout.set_width(int(w * Pango.SCALE))
        if not wrap:
            layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_markup(markup)
        cr.move_to(x, y)
        cr.set_source_rgba(*fg, 1)
        PangoCairo.show_layout(cr, layout)

    esc = GLib.markup_escape_text
    accent = "#%02x%02x%02x" % tuple(int(c * 255) for c in ACCENT)  # noqa: UP031
    for slot, x, y in boxes():
        if slot not in data:
            continue
        normal, shift = data[slot]
        rounded(cr, x, y, BOX_W, ROW - 10, 8)
        cr.set_source_rgba(*fg, 0.07)
        cr.fill()
        text(
            f'<span size="small" alpha="65%">{esc(TITLES[slot])}</span>\n<b>{esc(normal)}</b>\n'
            f'<span foreground="{accent}">G-Shift: <b>{esc(shift)}</b></span>',
            x + 10,
            y + 6,
            BOX_W - 20,
        )
        ax, ay = ANCHORS[slot]
        edge = x + BOX_W if x < 490 else x
        cr.move_to(edge, y + (ROW - 10) / 2)
        cr.line_to(edge + (22 if x < 490 else -22), y + (ROW - 10) / 2)
        cr.line_to(ax, ay)
        cr.set_source_rgba(*ACCENT, 0.8)
        cr.set_line_width(1.3)
        cr.stroke()
        cr.arc(ax, ay, 3.5, 0, 2 * math.pi)
        cr.set_source_rgba(*ACCENT, 1)
        cr.fill()
    text(footer, 980 - 12 - BOX_W, 16 + len(RIGHT) * ROW + 12, BOX_W, wrap=True)
