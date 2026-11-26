"""Copy active HERO profile 1 by factory button identity, preserving X PLUS fields.

Default: dry run. --apply writes only the planned X PLUS sectors.
--restore restores those sectors byte for byte from the target backup.
Backup directories contain private device IDs and must never be committed.
"""

import argparse
import json
from pathlib import Path

from logitech_receiver import common
from logitech_receiver.hidpp20 import Button, OnboardProfiles
from logitech_receiver.hidpp20_constants import SupportedFeature

from model import backup, load, write_sector


def read_backup(path):
    path = Path(path).expanduser()
    data = json.loads((path / "device-and-sectors.json").read_text())
    profiles = load((path / "profiles.yaml").read_text())
    return (
        profiles,
        data["identity"],
        {int(k): bytes.fromhex(v) for k, v in data["sectors"].items()},
    )


def crc(data):
    return bytes(data[:-2]) + common.crc16(bytes(data[:-2])).to_bytes(2, "big")


def button_map(source_rom, target_rom):
    def keys(raw):
        result = []
        for i in range(11):
            b = Button.from_bytes(raw[32 + i * 4 : 36 + i * 4])
            # Factory function padding/data is ff on HERO and 00 on X PLUS.
            if b.behavior == 9 and b.value in (1, 2, 3, 4, 7, 10):
                result.append(("function", int(b.value)))
            else:
                result.append(("binding", b.to_bytes()))
        return result

    source, target = keys(source_rom), keys(target_rom)
    if len(set(source)) != 11 or set(source) != set(target):
        raise ValueError("Factory buttons cannot be matched unambiguously.")
    return [source.index(b) for b in target]


def plan(source, target, mapping):
    if set(source) != set(range(16)) or set(target) != set(range(16)):
        raise ValueError("A complete backup of 16 sectors is required.")
    if any(len(b) != 255 for b in [*source.values(), *target.values()]):
        raise ValueError("Unexpected sector size.")
    if sorted(mapping) != list(range(11)):
        raise ValueError("Invalid button mapping.")
    if source[1] != crc(source[1]) or target[1] != crc(target[1]):
        raise ValueError("Profile CRC invalid.")
    changed = bytearray(target[1])
    changed[:13] = source[1][:13]  # report rate, default/shift DPI indices, 5 DPI steps
    macros = set()
    for offset in (32, 96):
        for dst, src in enumerate(mapping):
            value = source[1][offset + src * 4 : offset + src * 4 + 4]
            b = Button.from_bytes(value)
            if b.behavior in (0, 1):
                if b.behavior != 0 or not 6 <= b.sector < 16:
                    raise ValueError("Unsupported macro reference.")
                if b.address >= 253 or source[b.sector][b.address] == 0xFF:
                    raise ValueError("Empty/invalid macro reference in profile 1.")
                # Conservative parser: only known keyboard events and delays, no jumps.
                pos = b.address
                while pos < 253 and source[b.sector][pos] != 0xFF:
                    if source[b.sector][pos] not in (0x40, 0x43, 0x44) or pos + 3 > 253:
                        raise ValueError("Macro contains unknown commands or a sector change.")
                    pos += 3
                if pos >= 253:
                    raise ValueError("Macro without end.")
                if target[b.sector] != b"\xff" * 255:
                    raise ValueError("Macro sector of the X PLUS is not empty.")
                if source[b.sector][253:] != b"\xff\xff":
                    raise ValueError("Macro sector overlaps the CRC area.")
                macros.add(b.sector)
            changed[offset + dst * 4 : offset + dst * 4 + 4] = value
    # Require valid left/right click and an accessible G-Shift in both layers.
    if changed[32:40] != bytes.fromhex("8001000180010002"):
        raise ValueError("Normal main clicks must stay unchanged.")
    if not any(
        Button.from_bytes(changed[32 + i * 4 : 36 + i * 4]).behavior == 9
        and Button.from_bytes(changed[32 + i * 4 : 36 + i * 4]).value == 11
        for i in range(11)
    ):
        raise ValueError("No G-Shift trigger in the source profile.")
    # Macro sectors first, then profile references. No directory or other profile writes.
    # Every X PLUS sector written so far carries a CRC16 (HERO tolerates ffff, X PLUS
    # answered HARDWARE_ERROR on end-write), so macro sectors get one as well.
    return {**{s: crc(source[s]) for s in sorted(macros)}, 1: crc(changed)}


def interim(source, desired):
    """Device content left by the first attempt: macro sectors as raw HERO copy without CRC."""
    return {s: source[s] for s in desired if s != 1}


def execute(dev, old, new, restore=False, tolerated=None):
    expected = dict(old)
    if restore:
        expected.update(new)
    observed = {s: OnboardProfiles.read_sector(dev, s, 255) for s in old}
    if any(observed[s] != expected[s] and observed[s] != (tolerated or {}).get(s) for s in old):
        raise ValueError("Device state differs from the expected backup. Nothing written.")
    writes = {1: old[1], **{s: old[s] for s in new if s != 1}} if restore else new
    attempted = []
    try:
        for sector, raw in writes.items():
            attempted.append(sector)
            write_sector(dev, sector, raw)
            if OnboardProfiles.read_sector(dev, sector, 255) != raw:
                raise RuntimeError(f"Sector {sector}: readback differs")
        # Reload only the already-active profile; no mode or directory changes.
        dev.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x30, b"\x00\x01")
        expected.update(writes)
        actual = {s: OnboardProfiles.read_sector(dev, s, 255) for s in old}
        if actual != expected:
            raise RuntimeError("Full comparison of the 16 sectors differs")
        if dev.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x40)[:2] != b"\x00\x01":
            raise RuntimeError("Profile 1 not active")
    except Exception as exc:
        raise RuntimeError(
            f"Write failed; sectors touched {attempted}. Partial changes possible; backups kept. {exc}"
        ) from exc
    return list(writes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-backup", required=True)
    parser.add_argument("--target-backup", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    src, src_id, source = read_backup(args.source_backup)
    dst, dst_id, target = read_backup(args.target_backup)
    if src.name != "G502 SE Hero Gaming Mouse" or dst.name != "G502 X PLUS":
        raise ValueError("Nur HERO nach X PLUS ist freigegeben.")
    if any((p.size, p.sectors, p.buttons, p.gbuttons) != (255, 16, 11, 11) for p in (src, dst)):
        raise ValueError("Unbekanntes Profilformat.")
    from solaar.cli import _receivers_and_devices

    from device_io import identity

    opened = list(_receivers_and_devices())
    try:
        devices = [
            d
            for r in opened
            for d in ([r] if r.isDevice else r)
            if "g502" in d.name.lower() and d.ping()
        ]

        def find(ident, require_serial=True):
            matches = [d for d in devices if identity(d) == ident]
            if len(matches) != 1 or (require_serial and not ident.get("serial")):
                raise ValueError("Backed-up device not unambiguously reachable.")
            return matches[0]

        hero, plus = find(src_id, require_serial=False), find(dst_id)
        for d, prefix in ((hero, bytes.fromhex("010201")), (plus, bytes.fromhex("010501"))):
            if d.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x00)[:3] != prefix:
                raise ValueError("Device format has changed.")
            if (
                d.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x20)[0] != 1
                or d.feature_request(SupportedFeature.ONBOARD_PROFILES, 0x40)[:2] != b"\x00\x01"
            ):
                raise ValueError("Onboard mode and active profile 1 required.")
        if {s: OnboardProfiles.read_sector(hero, s, 255) for s in source} != source:
            raise ValueError("HERO has changed since the backup.")
        mapping = button_map(
            OnboardProfiles.read_sector(hero, 0x101, 255),
            OnboardProfiles.read_sector(plus, 0x101, 255),
        )
        desired = plan(source, target, mapping)
        print("X-PLUS-Index -> HERO-Index:", mapping, flush=True)
        print(
            "Target sectors:",
            list(desired),
            "Profile 1: buttons, G-Shift, DPI, polling; lighting/power saving/other profiles unchanged.",
            flush=True,
        )
        if args.apply or args.restore:
            expected = dict(target)
            if args.restore:
                expected.update(desired)
            tolerated = {} if args.restore else interim(source, desired)
            current = {s: OnboardProfiles.read_sector(plus, s, 255) for s in target}
            if any(current[s] != expected[s] and current[s] != tolerated.get(s) for s in target):
                raise ValueError("X PLUS has changed since the backup.")
            saved = backup(
                Path.home() / ".local/share/g502-onboard-editor/migration-backups",
                OnboardProfiles.from_device(plus),
                identity(plus),
                current,
            )
            print("Unmittelbare Sicherung:", saved, flush=True)
            result = execute(plus, target, desired, args.restore, tolerated)
            if {s: OnboardProfiles.read_sector(hero, s, 255) for s in source} != source:
                raise RuntimeError("HERO-Vergleich unerwartet abweichend")
            print(
                "RESTORE OK" if args.restore else "TRANSFER OK",
                result,
                "All 16 target sectors checked; HERO unchanged.",
                flush=True,
            )
        else:
            print("DRY RUN: keine Schreibbefehle.", flush=True)
    finally:
        for r in opened:
            r.close()


if __name__ == "__main__":
    main()
