"""Separate process: Solaar CLI imports GTK3; the editor uses GTK4."""

import json
import sys

from model import dump, load, write_checked

SNAPSHOT_FEATURE = 0x40  # ONBOARD_PROFILES: active profile sector


def identity(dev):
    return {
        "name": dev.name,
        "serial": str(dev.unitId or dev.serial or ""),
        "path": str(dev.path),
        "number": dev.number,
    }


def run(request):
    from logitech_receiver.hidpp20 import OnboardProfiles
    from solaar.cli import _receivers_and_devices

    opened = []
    try:
        devices = []
        for receiver in _receivers_and_devices():
            opened.append(receiver)
            for dev in [receiver] if receiver.isDevice else receiver:
                if "g502" in dev.name.lower() and dev.ping():
                    devices.append(dev)
        if request["action"] == "list":
            return [identity(d) for d in devices]
        if request["action"] == "read_any":  # cheat sheet: X PLUS preferred, read only
            devices.sort(key=lambda d: "x plus" not in d.name.lower())
            if not devices:
                raise ValueError("No G502 reachable. Move or switch on the mouse.")
            request = {"action": "read", "identity": identity(devices[0])}
        matches = [d for d in devices if identity(d) == request["identity"]]
        if len(matches) != 1:
            raise ValueError("The selected device is not unambiguously reachable.")
        dev = matches[0]
        if request["action"] == "read":
            profiles = OnboardProfiles.from_device(dev)
            if profiles is None:
                raise ValueError("Solaar does not support this device/profile format.")
            from logitech_receiver.hidpp20_constants import SupportedFeature

            active = int.from_bytes(
                dev.feature_request(SupportedFeature.ONBOARD_PROFILES, SNAPSHOT_FEATURE)[:2], "big"
            )
            return {
                "identity": identity(dev),
                "profiles": dump(profiles),
                "active": active,
                "sectors": {
                    str(s): OnboardProfiles.read_sector(dev, s, profiles.size).hex()
                    for s in range(profiles.sectors)
                },
            }
        if request["action"] == "write":
            return write_checked(
                dev,
                load(request["before"]),
                load(request["after"]),
                identity(dev),
                request["identity"],
                request["backup_dir"],
                allow=request.get("allow") is True,
                macros=request.get("macros", ()),
            )
        raise ValueError("Unknown action.")
    finally:
        for obj in opened:
            obj.close()


if __name__ == "__main__":
    try:
        result = {"ok": True, "result": run(json.load(sys.stdin))}
    except (Exception, SystemExit) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result))
