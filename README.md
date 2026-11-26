# G502 Onboard Editor

Buttons, G-Shift, DPI and lighting in the onboard memory of the mouse.

Local GTK 4 editor for the onboard profiles of the Logitech G502 X PLUS (through
Solaar's library). The G502 SE HERO can be read but is never written.
As of 2026-10-06 the write path was tested on real hardware with the X PLUS over wireless.

## Start

Requirements on Arch: `solaar`, `python-yaml`, `python-gobject`, `gtk4`.
No Piper/libratbag, no administrator access. Don't let Solaar or other
configuration tools write to the mouse at the same time.

`./install.sh` copies the editor and the window cheat sheet to
`~/.local/share/g502-onboard-editor/app` and writes the menu entries pointing there
(the `.desktop` files here carry the placeholder `@APP@`). The menu entries and
the tray applet's "Open editor" therefore do not depend on which branch this
checkout is on; run `./install.sh` again after changing the code.

```sh
./install.sh                 # install/update the menu entries
./start.sh                   # run from the checkout; menu entry "G502 Onboard Editor"
./start.sh --read-only       # writing locked
./start.sh --file draft.yaml # view/edit a Solaar YAML offline
```

## Usage

1. "Find mice (read)", pick a device, "Read selected mouse". There is no
   automatic device access.
2. Pick a profile. Buttons: click the button's box in the mouse drawing (top
   part = normal, blue line = G-Shift) and choose an action (the lists are
   searchable; key combination = one key plus Ctrl/Shift/Alt/Super(Meta)).
   The key list also contains "Left/Right/Middle click (mouse)": with a
   modifier this becomes a held macro, e.g. Super+left click on G-Shift;
   without a modifier it is a plain mouse click. The mouse entries only exist
   after reading a mouse (see below).
3. DPI: up to five steps (100–25600 in steps of 50, `0` = off, no gaps from
   step 1), plus default step and DPI shift step (sniper button).
   "Apply DPI to draft".
4. Lighting (per profile): effect Off, Static (colour), Breathe (colour,
   period, brightness) or Cycle (period, brightness), then "Apply lighting to
   draft". "Factory animation (X PLUS)" restores the factory lighting; "As read
   from mouse" puts back whatever was read.
5. Enable/disable profiles: check boxes in the "Enabled profiles" row. Only
   enabled profiles can be reached with the profile button on the mouse. At
   least one stays enabled; the profile that is running cannot be disabled.
6. Check the change list, "Write changes to X Plus …" and confirm.

"Export draft" writes a new YAML file (never overwrites).
"Discard changes" only resets the draft, not the mouse.
G-Shift triggers are protected against replacement. Macro references are kept
until the button is explicitly mapped to something else. The editor only
creates new macros for "mouse click with modifier". Report rate,
power saving and profile names cannot be changed. Which profile is running is
switched on the mouse, not in the editor.

The position names per button index come from the factory profiles (ROM
`0x101`) of both models; the indices differ between HERO and X PLUS. Which of
the two buttons next to the left click is front/rear is only named by its
factory function (DPI +/−).

## Cheat sheet in the system tray (Plasma applet)

Since 2026-10-13 the cheat sheet is a real tray applet `io.github.compact161.g502onboardeditor.buttonmap`
(`plasmoid/`, own monochrome icon `contents/icons/g502.svg`, tinted with the panel
text colour): clicking the icon opens a popup right at the icon, with no
window and no taskbar entry. Left: the numbered mouse drawing with DPI; right:
normal and G-Shift mapping per button. Hovering a row or a number highlights
the other one. Header: profile (if several are enabled), "Refresh from mouse"
(read-only), "Open editor".

The cheat sheet never writes to the mouse. It shows the last state read, from
`~/.local/share/g502-onboard-editor/cheatsheet.json` (no device identifiers); the editor
updates this file whenever it reads or writes, and "Refresh from mouse" reads
the mouse directly (X PLUS preferred). A profile switch on the mouse only shows
up after a refresh. Enabled profiles are shown; the running one is marked.

Install/update: `plasmoid/install.sh`. It backs up the panel configuration to
`~/.local/state/g502-onboard-editor/plasmoid/`, copies `sheet_json.py`, `mouseview.py`,
`model.py` and `device_io.py` into the package as `contents/code/` (so the
applet does not depend on this checkout; reinstall after changing these files),
adds the applet to `extraItems` of every system tray, moves the autostart of
`cheatsheet.py` into the backup and stops that program. After an update Plasma
shows the new UI only after restarting `plasmashell`.

The system tray popup has one size shared by all tray applets (resizable by
dragging its edge); hence a list instead of the wide drawing with callout
boxes. `sheet_json.py` delivers the data as JSON (`test_sheet_json.py`).

### Window variant

`cheatsheet.py` is the older variant: a StatusNotifierItem directly over GDBus
(no extra packages) whose click shows a window with the full drawing (callout
boxes, normal and blue G-Shift mapping, DPI). Esc closes the window; the
program keeps running. Menu entry "G502 – Button Map (Window)". `mouseview.py`
holds the drawing and is also used by the editor. The mouse is drawn
schematically, not to scale.

## Mouse click with modifier

The profile format has no "modifier + mouse button" mapping. The editor creates
a macro for it: press modifier (`43 mod 00`), press mouse button (`41 00 btn`),
wait for the physical button to be released (`01`), release mouse button
(`42 00 btn`) and modifier (`44 mod 00`), end (`ff`). Opcodes from libratbag
`hidpp20.h`. The macro is written behind the existing macros into the first
free non-profile sector (sector 6 on the X PLUS); identical macros are reused.
Sectors with unknown macro commands are never touched. Macros no longer used
stay in memory (no cleanup).

Written to the X PLUS on 2026-10-06: profile 1, G-Shift + left click →
Shift+left click (sector 6, offset 68; previously Shift+left arrow). Data read
back; the user confirmed the function on 2026-10-08.

## Lighting

Onboard profiles hold four 11-byte LED slots from byte 208: two zones, then the
same two for power saving. The X PLUS has a single RGB zone and uses the
**second** slot (libratbag quirk `G502X_PLUS`); the HERO the first. The editor
writes the same effect to that slot and its power-saving twin (slot + 2, as
libratbag does), always all 11 bytes; the other slots stay byte-identical.

On the X PLUS, byte 252 of the profile sector selects a stored custom animation
(factory: `3`) that **overrides** the slots; libratbag calls it
`custom_animation_index` and clears it on write. Without clearing it a slot
change has no visible effect (tested). Setting an effect therefore sets byte
252 to `0`; "Factory animation" writes back the factory state observed on all
five profiles: slot 1 `0f…64`, slot 3 `10…64`, byte 252 `3`. The editor keeps
the byte as `animation` on the draft profile (Solaar does not model it) and
refuses to write if it changed on the mouse since reading. Byte layout per effect
from libratbag `hidpp20.h` (`struct hidpp20_internal_led`), big-endian period:

| Effect | ID | Bytes after the ID |
|---|---|---|
| Off | `00` | all 0 |
| Static | `01` | RGB, ramp 0 |
| Breathe | `0a` | RGB, period ms (2), waveform 0, brightness % |
| Cycle | `03` | 5 × 0, period ms (2), brightness % |

The X PLUS reports exactly these four effects for zone 0 (`RGB_EFFECTS`
0x8071, `solaar show`, 2026-11-13). Factory state on all five profiles:
`0f`/`0f`/`10`/`10` (Logitech signature effects). The lighting only applies
while the mouse is in onboard mode (Solaar "LED Control" off, the default).

Hardware test 2026-11-13 (X PLUS, wireless, active profile 1): Static red only
showed after clearing byte 252; green Breathe 3 s through the editor's write
path showed as expected; "Factory animation" brought the factory effect back and
all 16 sectors were byte-identical to the backup taken before the test. Not
tested: Off, Cycle, power-saving behaviour, the HERO (stays read-only).

## Write path and backup

The editor does not write Solaar's re-serialization (it changes padding bytes
and fields Solaar does not model). Instead it patches only bytes 1–12 (DPI), the button fields and
the changed LED slots and byte 252 in the raw profile sector and recomputes the CRC16. For
enabling, only the entry's enable byte in the profile directory (sector 0) is
changed, last, after the profile sectors. Sequence:

- The device must be an X PLUS whose identity and profile state are unchanged
  since it was read.
- All 16 sectors are read and backed up to
  `~/.local/share/g502-onboard-editor/backups/<UTC time>/`
  (`profiles.yaml`, `device-and-sectors.json`). Contains device identifiers;
  keep out of Git.
- Write the changed profile sectors and read each back; reload the active
  profile; then compare all 16 sectors and the profiles again.

Writing is not atomic. On an error there is no automatic rollback; the backup
path is shown and the mouse must be read again. There is no "restore all"
button.

## Code and tests

`model.py`: loading, comparing, DPI checks, raw patch, backup, writing.
`device_io.py`: short-lived device process (Solaar imports GTK 3, the editor
uses GTK 4). `app.py`: UI. `migrate_profile1.py`: one-off HERO → X PLUS
migration (see below).

```sh
/usr/bin/python -m unittest -v test_model.py test_migration.py
/usr/bin/python test_sheet_json.py
GSK_RENDERER=cairo GDK_BACKEND=x11 xvfb-run -a /usr/bin/python test_gui.py
GSK_RENDERER=cairo GDK_BACKEND=x11 xvfb-run -a /usr/bin/python test_cheatsheet.py
```

Checked on 2026-10-06: 23 simulation tests, GTK test (buttons, DPI, write
locks) and a hardware round trip through `device_io.py` on the X PLUS over
wireless: default DPI step 1→2→1, one sector written each time, only byte 1 and
the CRC changed in between, final state byte-identical to the start. Likewise
profile 3 enabled and disabled again (only byte 10 and the CRC in sector 0);
disabling the active profile was correctly refused. Not checked: whether the
profile button really reaches a newly enabled profile, restoring from a backup.

## HERO → X PLUS: targeted migration (2026-10-06)

A plain YAML import is **not** suitable: the HERO reports profile format 2, the
X PLUS format 5, and Solaar normalizes unknown bytes and some function bytes.

`migrate_profile1.py` only handles the active profile 1. It transfers the
1000 Hz/DPI settings, the normal/G-Shift mappings and the referenced keyboard
macros into previously empty macro sectors. Lighting, power saving, the other
profiles and the profile table stay byte-identical. Only the known ranges are
patched and the profile CRC recomputed.

The factory mappings in ROM of both real devices define the mapping: X PLUS
indices 0–10 correspond to HERO indices **0,1,2,3,5,4,10,9,8,7,6**.

```sh
/usr/bin/python migrate_profile1.py --source-backup /path/HERO-backup --target-backup /path/X-PLUS-backup
```

Without further switches this is only a dry run. `--apply` compares the current
state, makes a new backup, writes the macro sectors first, then profile 1, and
reloads the already active profile 1. `--restore` reverts only these changes
from the original raw backup; the full device state must match the expected
migration exactly. Both modes finally compare all 16 target sectors and the
unchanged HERO. No automatic rollback on an unclear partial failure.

Backups contain the device identity and must not go into Git. Cable/port
changes can deliberately block the identity check: re-inventory and back up
first, never just disable the check. Over USB both mice report no `serial` but a
unit ID; the identity uses `dev.unitId` with fallback to `dev.serial`.

History: the first `--apply` over USB wrote sector 6 but the X PLUS answered the
commit (`ONBOARD_PROFILES` function `0x80`) with `HARDWARE_ERROR`; the macro
sector had been copied with `ffff` instead of a CRC. With a CRC16 in the macro
sector (bytes 253–254) and an own write sequence the second attempt succeeded:
sectors 6 and 1 written, all 16 sectors read back, HERO unchanged. The cause is
supported only by this one counter-test. `--restore` is untested on hardware.
Full handover with local backup paths: shared vault,
`10-Infrastruktur/G502-Claude-Uebergabe-2026-10-06.md`.

Source for the different LED handling of the X PLUS:
[libratbag device definition](https://github.com/libratbag/libratbag/blob/master/data/devices/logitech-g502-x-plus.device).

## AI use

Large parts of this project were written with AI coding assistants.
I review and test the results on my own G502 X PLUS, but treat the code like any
third-party code: read it before you rely on it, especially the write path to the
mouse. It comes without warranty (see LICENSE). Commits written with an assistant
carry a `Co-Authored-By` line.

## Licence

MIT (see `LICENSE`). The editor uses Solaar's `logitech_receiver` library,
which is installed separately and keeps its own licence (GPL-2.0-or-later).
Not affiliated with or endorsed by Logitech.
