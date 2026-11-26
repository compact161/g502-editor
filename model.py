"""Profile editing with byte-level invariants; no device access on import."""

import copy
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import yaml
from logitech_receiver import common
from logitech_receiver.hidpp20 import (
    Button,
    LEDEffectSetting,
    OnboardProfile,
    OnboardProfiles,
    OnboardProfilesVersion,
)
from logitech_receiver.hidpp20_constants import SupportedFeature


def load(text):
    profiles = yaml.safe_load(text)
    if not isinstance(profiles, OnboardProfiles) or profiles.version != OnboardProfilesVersion:
        raise ValueError("Not a supported Solaar profile file.")
    if not profiles.profiles or not 1 <= profiles.buttons <= 16 or not 0 <= profiles.gbuttons <= 16:
        raise ValueError("Invalid button/profile structure.")
    for p in profiles.profiles.values():
        if len(p.buttons) != profiles.buttons or len(p.gbuttons) != profiles.gbuttons:
            raise ValueError("Incomplete button mapping.")
    encoded(profiles)  # Validate that all fields can be serialized before editing.
    return profiles


def dump(profiles):
    return yaml.dump(profiles, allow_unicode=True)


def encoded(profiles):
    return {
        0: profiles.to_bytes(),
        **{p.sector: p.to_bytes(profiles.size) for p in profiles.profiles.values()},
    }


def fingerprint(profiles):
    return hashlib.sha256(
        profiles.name.encode()
        + b"".join(n.to_bytes(2, "big") + data for n, data in sorted(encoded(profiles).items()))
    ).hexdigest()


def is_shift(button):
    return button.behavior == 9 and button.value == 11


def replace(profiles, profile_id, layer, index, button):
    if layer not in ("buttons", "gbuttons"):
        raise ValueError("Unknown layer.")
    old = getattr(profiles.profiles[profile_id], layer)[index]
    if is_shift(old) and not is_shift(button):
        raise ValueError("Existing G-Shift buttons stay protected.")
    if len(button.to_bytes()) != 4:
        raise ValueError("Invalid button action.")
    getattr(profiles.profiles[profile_id], layer)[index] = copy.deepcopy(button)


DPI_FIELDS = ("resolutions", "resolution_default_index", "resolution_shift_index")
DPI_MIN, DPI_MAX, DPI_STEP = 100, 25600, 50


def set_dpi(profiles, profile_id, resolutions, default, shift):
    """Up to five DPI steps; unused steps are 0 and only allowed at the end."""
    resolutions = [int(r) for r in resolutions]
    active = [r for r in resolutions if r]
    if len(resolutions) != 5 or not active or resolutions[: len(active)] != active:
        raise ValueError("DPI steps must be filled without gaps from step 1 (1 to 5 steps).")
    if any(not DPI_MIN <= r <= DPI_MAX or r % DPI_STEP for r in active):
        raise ValueError(f"DPI only from {DPI_MIN} to {DPI_MAX} in steps of {DPI_STEP}.")
    if not 0 <= default < len(active) or not 0 <= shift < len(active):
        raise ValueError("Default and DPI shift step must point to a used step.")
    p = profiles.profiles[profile_id]
    p.resolutions, p.resolution_default_index, p.resolution_shift_index = (
        resolutions,
        int(default),
        int(shift),
    )


def set_enabled(profiles, profile_id, enabled):
    profiles.profiles[profile_id].enabled = 1 if enabled else 0
    if not any(p.enabled for p in profiles.profiles.values()):
        profiles.profiles[profile_id].enabled = 1
        raise ValueError("At least one profile must stay enabled.")


def describe_dpi(p):
    steps = [r for r in p.resolutions if r]

    def step(i):
        return f"step {i + 1}" + (f" ({p.resolutions[i]})" if i < 5 and p.resolutions[i] else "")

    return f"{'/'.join(map(str, steps))} · default {step(p.resolution_default_index)} · DPI shift {step(p.resolution_shift_index)}"


# Onboard lighting: four 11-byte slots from byte 208 (two zones, then the same two for
# power saving). Layout per effect from libratbag hidpp20.h (struct hidpp20_internal_led).
# Byte 252 on the X PLUS selects a stored custom animation that overrides the slots;
# 0 = use the slots (libratbag clears it the same way).
LED_OFFSET, LED_SIZE, ANIMATION = 208, 11, 252
# Factory lighting of the X PLUS as read from all five profiles (2026-10).
X_PLUS_FACTORY = {
    1: bytes.fromhex("0f0000000000000064") + bytes(2),
    3: bytes.fromhex("100000000000000064") + bytes(2),
    "animation": 3,
}
LED_EFFECTS = {0x00: "Off", 0x01: "Static", 0x0A: "Breathe", 0x03: "Cycle"}
LED_PERIOD_MIN, LED_PERIOD_MAX, LED_PERIOD_STEP = 500, 20000, 100


def led_slot(name):
    """The X PLUS uses the second slot (libratbag quirk G502X_PLUS); others the first."""
    return 1 if "x plus" in name.lower() else 0


def led_bytes(effect, color=0, period=5000, intensity=100):
    if effect not in LED_EFFECTS:
        raise ValueError("Unknown lighting effect.")
    color, period, intensity = int(color), int(period), int(intensity)
    if not 0 <= color <= 0xFFFFFF:
        raise ValueError("Invalid colour.")
    if effect in (0x0A, 0x03):
        if not LED_PERIOD_MIN <= period <= LED_PERIOD_MAX or period % LED_PERIOD_STEP:
            raise ValueError(
                f"Period only from {LED_PERIOD_MIN} to {LED_PERIOD_MAX} ms in steps of {LED_PERIOD_STEP}."
            )
        if not 1 <= intensity <= 100:
            raise ValueError("Brightness only from 1 to 100 %.")
    raw = bytearray(LED_SIZE)
    raw[0] = effect
    rgb = color.to_bytes(3, "big")
    if effect == 0x01:  # color, ramp (0 = default)
        raw[1:4] = rgb
    elif effect == 0x0A:  # color, period, waveform (0 = default), intensity
        raw[1:4], raw[4:6], raw[7] = rgb, period.to_bytes(2, "big"), intensity
    elif effect == 0x03:  # 5 unused bytes, period, intensity
        raw[6:8], raw[8] = period.to_bytes(2, "big"), intensity
    return bytes(raw)


def led_params(raw):
    """{'effect', 'color', 'period', 'intensity'} for an effect this editor writes, else None."""
    raw = bytes(raw)
    effect = raw[0]
    if effect not in LED_EFFECTS:
        return None
    timed = effect in (0x0A, 0x03)
    params = {
        "effect": effect,
        "color": int.from_bytes(raw[1:4], "big") if effect in (0x01, 0x0A) else 0,
        "period": int.from_bytes(raw[4:6] if effect == 0x0A else raw[6:8], "big")
        if timed
        else 5000,
        "intensity": (raw[7] if effect == 0x0A else raw[8]) if timed else 100,
    }
    try:
        if led_bytes(**params) != raw:
            return None  # other bytes set (e.g. ramp or waveform): shown as kept
    except ValueError:
        return None
    return params


def x_plus(profiles):
    return "x plus" in profiles.name.lower()


def attach_animation(profiles, sectors):
    """Store byte 252 of each X PLUS profile sector as p.animation (Solaar does not model it)."""
    if sectors is not None and x_plus(profiles):
        for p in profiles.profiles.values():
            p.animation = bytes(sectors[p.sector])[ANIMATION]
    return profiles


def _set_slots(profiles, profile_id, data, alt, animation):
    p, slot = profiles.profiles[profile_id], led_slot(profiles.name)
    for i, raw in ((slot, data), (slot + 2, alt)):
        p.lighting[i] = LEDEffectSetting.from_bytes(raw)
        if p.lighting[i].to_bytes() != raw:
            raise ValueError("Lighting effect cannot be represented.")
    if x_plus(profiles):
        p.animation = animation


def set_lighting(profiles, profile_id, effect, color=0, period=5000, intensity=100):
    """Main slot and its power-saving twin get the same effect (as libratbag does);
    on the X PLUS the custom animation is switched off so the slot takes effect."""
    data = led_bytes(effect, color, period, intensity)
    _set_slots(copy.deepcopy(profiles), profile_id, data, data, 0)  # validate first
    _set_slots(profiles, profile_id, data, data, 0)


def set_factory_lighting(profiles, profile_id):
    if not x_plus(profiles):
        raise ValueError("Factory lighting is only known for the X PLUS.")
    _set_slots(
        profiles, profile_id, X_PLUS_FACTORY[1], X_PLUS_FACTORY[3], X_PLUS_FACTORY["animation"]
    )


def is_factory_lighting(profiles, p):
    return (
        x_plus(profiles)
        and getattr(p, "animation", None) == X_PLUS_FACTORY["animation"]
        and p.lighting[1].to_bytes() == X_PLUS_FACTORY[1]
        and p.lighting[3].to_bytes() == X_PLUS_FACTORY[3]
    )


def lighting_state(profiles, p):
    """Comparable lighting state: the four slots plus the animation byte."""
    return [l.to_bytes() for l in p.lighting], getattr(p, "animation", None)  # noqa: E741


def describe_lighting(profiles, p):
    if is_factory_lighting(profiles, p):
        return "Factory animation"
    text = describe_led(p.lighting[led_slot(profiles.name)].to_bytes())
    animation = getattr(p, "animation", None)
    if animation:
        text += f" (overridden by custom animation {animation})"
    return text


def describe_led(raw):
    params = led_params(raw)
    if params is None:
        return f"Other effect 0x{bytes(raw)[0]:02x} (kept)"
    text = LED_EFFECTS[params["effect"]]
    if params["effect"] in (0x01, 0x0A):
        text += f" #{params['color']:06x}"
    if params["effect"] in (0x0A, 0x03):
        text += f" · {params['period']} ms · {params['intensity']} %"
    return text


def changes(before, after, sectors=None):
    """Only button replacement, DPI steps, lighting and profile enabling are allowed."""
    if before.name != after.name or before.profiles.keys() != after.profiles.keys():
        raise ValueError("Device or profile structure does not match.")
    expected = copy.deepcopy(before)
    result = []
    for pid, old_p in before.profiles.items():
        new_p = after.profiles[pid]
        if old_p.enabled != new_p.enabled:
            if old_p.enabled not in (0, 1) or new_p.enabled not in (0, 1):
                raise ValueError("Unknown enable value in the profile directory.")
            set_enabled(expected, pid, new_p.enabled)
            result.append(f"Profile {pid}: {'enable' if new_p.enabled else 'disable'}")
        if any(getattr(old_p, f) != getattr(new_p, f) for f in DPI_FIELDS):
            set_dpi(
                expected,
                pid,
                new_p.resolutions,
                new_p.resolution_default_index,
                new_p.resolution_shift_index,
            )
            result.append(f"Profile {pid}, DPI: {describe_dpi(old_p)} → {describe_dpi(new_p)}")
        if lighting_state(before, old_p) != lighting_state(after, new_p):
            params = led_params(new_p.lighting[led_slot(before.name)].to_bytes())
            if is_factory_lighting(after, new_p):
                set_factory_lighting(expected, pid)
            elif params is not None:
                set_lighting(expected, pid, **params)
            else:
                raise ValueError("Unsupported lighting change.")
            result.append(
                f"Profile {pid}, lighting: {describe_lighting(before, old_p)} → {describe_lighting(after, new_p)}"
            )
        for layer in ("buttons", "gbuttons"):
            old, new = getattr(old_p, layer), getattr(new_p, layer)
            if len(old) != len(new):
                raise ValueError("The number of buttons must not change.")
            for i, (a, b) in enumerate(zip(old, new)):  # noqa: B905
                if a.to_bytes() != b.to_bytes():
                    replace(expected, pid, layer, i, b)
                    result.append(
                        f"Profile {pid}, {'G-Shift' if layer == 'gbuttons' else 'Normal'}, button {i}: {describe(a, sectors)} → {describe(b, sectors)}"
                    )
    if dump(expected) != dump(after):
        raise ValueError(
            "Changes outside button mapping, DPI, lighting and profile enabling are not allowed."
        )
    return result


MODIFIERS = ((1, "Ctrl"), (2, "Shift"), (4, "Alt"), (8, "Super"))
CLICKS = {1: "Left click", 2: "Right click", 4: "Middle click"}
# Macro opcodes (libratbag hidpp20.h): wait for release, delay, button down/up, key press/release.
MACRO_LEN = {0x01: 1, 0x40: 3, 0x41: 3, 0x42: 3, 0x43: 3, 0x44: 3}


def click_macro(modifiers, button):
    """Hold modifiers and a mouse button for as long as the physical button is held."""
    if not 0 < modifiers < 16 or button not in CLICKS:
        raise ValueError("At least one modifier and a left, right or middle click are required.")
    return bytes(
        [0x43, modifiers, 0, 0x41, 0, button, 0x01, 0x42, 0, button, 0x44, modifiers, 0, 0xFF]
    )


def macro_layout(raw):
    """Start offsets of the macros in a sector and the first free offset behind them."""
    starts, pos, end = [], 0, len(raw) - 2
    while pos < end and any(x != 0xFF for x in raw[pos:end]):
        starts.append(pos)
        while raw[pos] != 0xFF:
            step = MACRO_LEN.get(raw[pos])
            if step is None or pos + step >= end:
                raise ValueError("Macro sector contains unknown commands.")
            pos += step
        pos += 1
    return starts, pos


def apply_macros(sectors, macros, reserved=()):
    """Sectors changed by appending the pending click macros, each with a fresh CRC."""
    out = {}
    for m in macros:
        data = click_macro(m["modifiers"], m["button"])
        sector = m["sector"]
        if sector in reserved or not 0 < sector < len(sectors):
            raise ValueError("Invalid macro sector.")
        raw = bytearray(out.get(sector, sectors[sector]))
        _, free = macro_layout(raw)
        if m["address"] != free or free + len(data) > len(raw) - 2:
            raise ValueError("Macro does not fit at the planned position. Read the mouse again.")
        raw[free : free + len(data)] = data
        out[sector] = with_crc(raw)
    return out


def place_macro(sectors, reserved, data, pending):
    """(sector, address, is_new): reuse an identical macro or take free space."""
    current = {**sectors, **apply_macros(sectors, pending, reserved)}
    for sector in sorted(set(range(1, len(sectors))) - set(reserved)):
        try:
            starts, free = macro_layout(current[sector])
        except ValueError:
            continue
        for start in starts:
            if current[sector][start : start + len(data)] == data:
                return sector, start, False
        if free + len(data) <= len(current[sector]) - 2:
            return sector, free, True
    raise ValueError("No free macro memory on the mouse.")


def key_name(modifiers, key):
    return describe(Button(behavior=8, type=2, modifiers=modifiers, value=key))


def macro_keys(raw, address):
    """Key combinations a macro presses, in order; None if it cannot be decoded."""
    keys, held, pos, end = [], 0, address, len(raw) - 2
    try:
        while raw[pos] != 0xFF:
            step = MACRO_LEN.get(raw[pos])
            if step is None or pos + step >= end or raw[pos] in (0x41, 0x42):
                return None
            modifiers, key = raw[pos + 1], raw[pos + 2]
            if raw[pos] in (0x43, 0x44) and (
                key == 0 or 0xE0 <= key <= 0xE7
            ):  # modifier key itself
                held = held | modifiers if raw[pos] == 0x43 else held & ~modifiers
            elif raw[pos] == 0x43:
                name = key_name(held | modifiers, key)
                if name not in keys:
                    keys.append(name)
            pos += step
    except (IndexError, KeyError, ValueError):
        return None
    return keys or None


def click_macro_of(b, sectors=None):
    """{'modifiers', 'button'} if the button runs one of our held click macros, else None."""
    if b.behavior != 0:
        return None
    data = bytes((sectors or {}).get(b.sector, b""))[b.address : b.address + 14]
    if (
        len(data) == 14
        and data[1]
        and data[5] in CLICKS
        and data == click_macro(data[1] & 15, data[5])
    ):
        return {"modifiers": data[1] & 15, "button": data[5]}
    return None


def describe(b, sectors=None):
    if b.behavior in (0, 1):
        raw = bytes((sectors or {}).get(b.sector, b""))
        click = click_macro_of(b, sectors)
        if click:
            return (
                "+".join(
                    [name for bit, name in MODIFIERS if click["modifiers"] & bit]
                    + [CLICKS[click["button"]]]
                )
                + " (macro, held)"
            )
        keys = macro_keys(raw, b.address) if b.behavior == 0 and raw else None
        if keys:
            return "Macro: " + ", ".join(keys)
        return f"Macro · sector {b.sector}, offset {b.address} (kept)"
    if b.behavior == 9:
        return {
            1: "Wheel tilt left",
            2: "Wheel tilt right",
            3: "DPI +",
            4: "DPI −",
            5: "Cycle DPI",
            6: "Default DPI",
            7: "DPI shift",
            10: "Cycle profile",
            11: "G-Shift",
        }.get(b.value, f"Mouse function {b.value}")
    if b.behavior == 8:
        if b.type == 0:
            return "No action"
        if b.type == 1:
            return {
                1: "Left click",
                2: "Right click",
                4: "Middle click",
                8: "Back",
                16: "Forward",
            }.get(b.value, f"Mouse button {b.value}")
        if b.type == 3:
            return {233: "Volume up", 234: "Volume down", 226: "Mute", 205: "Play / Pause"}.get(
                b.value, f"Media key {b.value}"
            )
        if b.type == 2:
            from logitech_receiver.hidpp20 import ButtonKeys

            modifiers = [
                name
                for bit, name in (
                    (1, "Ctrl"),
                    (2, "Shift"),
                    (4, "Alt"),
                    (8, "Super"),
                    (16, "R-Ctrl"),
                    (32, "R-Shift"),
                    (64, "R-Alt"),
                    (128, "R-Super"),
                )
                if b.modifiers & bit
            ]
            key = {
                0: "",
                40: "Enter",
                41: "Esc",
                42: "Backspace",
                43: "Tab",
                44: "Space",
                79: "Right arrow",
                80: "Left arrow",
                81: "Down arrow",
                82: "Up arrow",
            }.get(int(b.value), str(ButtonKeys[b.value]))
            return "+".join(modifiers + ([key] if key else [])) or "No key"
    return f"Unknown action · {b.to_bytes().hex()} (kept)"


def backup(directory, profiles, identity, raw):
    root = Path(directory).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = root / stamp
    target.mkdir(mode=0o700)
    for name, content in {
        "profiles.yaml": dump(profiles),
        "device-and-sectors.json": json.dumps(
            {"identity": identity, "sectors": {str(k): v.hex() for k, v in raw.items()}}, indent=2
        ),
    }.items():
        with (target / name).open("x") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return str(target)


def with_crc(data):
    return bytes(data[:-2]) + common.crc16(bytes(data[:-2])).to_bytes(2, "big")


def patch_sector(raw, before, after, buttons, gbuttons, x_plus=False):
    """Apply the edit to the raw device bytes. Solaar's serialization only locates the
    changed bytes; everything it does not model or normalizes stays as on the device."""
    old, new = before.to_bytes(len(raw)), after.to_bytes(len(raw))
    allowed = (
        set(range(1, 13)) | set(range(32, 32 + 4 * buttons)) | set(range(96, 96 + 4 * gbuttons))
    )
    patched = bytearray(raw)
    for i in range(len(raw) - 2):
        if (
            old[i] != new[i]
            and i not in allowed
            and not LED_OFFSET <= i < LED_OFFSET + 4 * LED_SIZE
        ):
            raise ValueError("Change outside button, DPI and lighting bytes. Nothing written.")
        if old[i] != new[i]:
            patched[i] = new[i]
    for slot, (a, b) in enumerate(zip(before.lighting, after.lighting)):  # noqa: B905
        if a.to_bytes() != b.to_bytes():
            # A changed lighting slot is written completely, so no stale effect bytes remain.
            start = LED_OFFSET + slot * LED_SIZE
            patched[start : start + LED_SIZE] = b.to_bytes()
    animation = getattr(after, "animation", None)
    if x_plus and animation is not None and animation != getattr(before, "animation", None):
        if getattr(before, "animation", raw[ANIMATION]) != raw[ANIMATION]:
            raise ValueError(
                "Custom animation byte does not match the state that was read. Nothing written."
            )
        patched[ANIMATION] = animation
    patched = with_crc(patched)
    # The device bytes must now decode to exactly the edited profile.
    if (
        OnboardProfile.from_bytes(after.sector, after.enabled, buttons, gbuttons, patched).to_bytes(
            len(raw)
        )
        != new
    ):
        raise ValueError("Patched sector does not match the draft. Nothing written.")
    return patched


def patch_directory(raw, before, after):
    """Flip only the enabled byte of each changed entry; entry padding and end marker
    differ between models and stay as on the device."""
    patched = bytearray(raw)
    for i, pid in enumerate(sorted(before.profiles)):
        old, new = before.profiles[pid], after.profiles[pid]
        if (
            bytes(raw[i * 4 : i * 4 + 3]) != old.sector.to_bytes(2, "big") + bytes([old.enabled])
            or old.sector != new.sector
        ):
            raise ValueError(
                "Profile directory does not match the state that was read. Nothing written."
            )
        patched[i * 4 + 2] = new.enabled
    return with_crc(patched)


def write_sector(dev, sector, raw):
    # Same sequence as Solaar, but without its skip when only the CRC bytes differ.
    dev.feature_request(
        SupportedFeature.ONBOARD_PROFILES,
        0x60,
        sector >> 8,
        sector & 0xFF,
        0,
        0,
        len(raw) >> 8,
        len(raw) & 0xFF,
    )
    for o in range(0, len(raw), 16):
        dev.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x70, raw[o : o + 16])
    dev.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x80)


SNAPSHOT = Path.home() / ".local/share/g502-onboard-editor/cheatsheet.json"


def save_snapshot(name, profiles_yaml, sectors, active=None, path=SNAPSHOT):
    """Last known mouse state for the tray cheat sheet. No device identifiers."""
    path = Path(path)
    if active is None:
        try:  # noqa: SIM105
            active = json.loads(path.read_text()).get("active")
        except (OSError, ValueError):
            pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "name": name,
                "profiles": profiles_yaml,
                "sectors": sectors,
                "active": active,
                "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
    )
    os.replace(tmp, path)


def write_checked(
    dev, before, after, identity, expected_identity, directory, *, allow=False, macros=()
):
    # The HERO stays read-only; only the hardware-tested X PLUS write path is enabled.
    if not allow or "g502 x plus" not in dev.name.lower():
        raise ValueError("Writing is only possible for a G502 X Plus; the HERO stays locked.")
    if identity != expected_identity or not identity.get("serial"):
        raise ValueError("Device identity is missing or has changed.")
    diff = changes(before, after)
    if not diff:
        raise ValueError("No changes to write.")
    current = OnboardProfiles.from_device(dev)
    if current is None or fingerprint(current) != fingerprint(before):
        raise ValueError("The mouse has changed in the meantime. Read it again.")
    profile_sectors = [p.sector for p in current.profiles.values()]
    if len(set(profile_sectors)) != len(profile_sectors) or any(
        not 0 < s < current.sectors for s in profile_sectors
    ):
        raise ValueError("Profile sectors are not unambiguously writable. Nothing written.")
    raw = {s: OnboardProfiles.read_sector(dev, s, current.size) for s in range(current.sectors)}
    if any(len(data) != current.size for data in raw.values()):
        raise ValueError("Incomplete sector backup. Nothing written.")
    # New macros first, so that no profile ever points at missing macro data.
    planned = apply_macros(raw, macros, reserved=[0, *profile_sectors])
    known = {(m["sector"], m["address"]) for m in macros}
    for pid, p in before.profiles.items():
        for layer in ("buttons", "gbuttons"):
            for old, new in zip(getattr(p, layer), getattr(after.profiles[pid], layer)):  # noqa: B905
                if (  # noqa: SIM102
                    new.behavior in (0, 1)
                    and new.to_bytes() != old.to_bytes()
                    and (new.sector, new.address) not in known
                ):
                    # Otherwise only a reference to a click macro that is already on the mouse.
                    if (
                        new.behavior != 0
                        or not 0 < new.sector < current.sectors
                        or new.sector in profile_sectors
                        or new.address not in macro_layout(raw[new.sector])[0]
                        or "(macro, held)" not in describe(new, raw)
                    ):
                        raise ValueError("New macro reference without its macro. Nothing written.")
    for pid, p in before.profiles.items():
        if getattr(p, "animation", raw[p.sector][ANIMATION]) != raw[p.sector][ANIMATION]:
            raise ValueError("The mouse has changed in the meantime. Read it again.")
        if p.to_bytes(current.size) != after.profiles[pid].to_bytes(current.size) or lighting_state(
            before, p
        ) != lighting_state(after, after.profiles[pid]):
            if raw[p.sector] != with_crc(raw[p.sector]):
                raise ValueError("Profile CRC on the mouse is invalid. Nothing written.")
            planned[p.sector] = patch_sector(
                raw[p.sector],
                p,
                after.profiles[pid],
                current.buttons,
                current.gbuttons,
                x_plus(current),
            )
    active = int.from_bytes(dev.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x40)[:2], "big")
    if any(p.sector == active and not p.enabled for p in after.profiles.values()):
        raise ValueError(
            "The currently active profile cannot be disabled. Switch profiles on the mouse first."
        )
    if any(p.enabled != after.profiles[pid].enabled for pid, p in before.profiles.items()):
        if raw[0] != with_crc(raw[0]):
            raise ValueError("Profile directory CRC is invalid. Nothing written.")
        planned[0] = patch_directory(raw[0], before, after)  # after the profile sectors
    saved = backup(directory, current, identity, raw)
    if any(OnboardProfiles.read_sector(dev, s, current.size) != data for s, data in raw.items()):
        raise ValueError(f"Mouse changed during the backup. Write aborted. Backup: {saved}")
    try:
        for sector, data in planned.items():
            write_sector(dev, sector, data)
            if OnboardProfiles.read_sector(dev, sector, current.size) != data:
                raise RuntimeError(f"Sector {sector}: readback differs")
        # Reload the active profile if it was rewritten; no mode or directory changes.
        active = bytes(dev.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x40)[:2])
        if int.from_bytes(active, "big") in planned:
            dev.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x30, active)
        expected_raw = {**raw, **planned}
        if any(
            OnboardProfiles.read_sector(dev, s, current.size) != data
            for s, data in expected_raw.items()
        ):
            raise RuntimeError("Sector readback differs")
        readback = OnboardProfiles.from_device(dev)
        if readback is None or fingerprint(readback) != fingerprint(after):
            raise RuntimeError("Profile readback differs")
    except Exception as exc:
        raise RuntimeError(
            f"Write not fully confirmed: {exc}. Partial changes possible. Backup: {saved}; no automatic restore."
        ) from exc
    return {
        "backup": saved,
        "sectors_written": len(planned),
        "profiles": dump(readback),
        "sectors": {str(s): data.hex() for s, data in expected_raw.items()},
    }
