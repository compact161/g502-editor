#!/usr/bin/python
"""Cheat sheet as JSON for the Plasma tray applet. Never writes to the mouse; --refresh reads it first."""

import json
import subprocess
import sys
from pathlib import Path

import mouseview
from model import SNAPSHOT, describe_dpi, load, save_snapshot

HERE = Path(__file__).resolve().parent
# Top to bottom on the mouse; the applet numbers the buttons in this order.
ORDER = [
    "lmb",
    "rmb",
    "wheel",
    "tilt_l",
    "tilt_r",
    "top",
    "dpi_up",
    "dpi_dn",
    "sniper",
    "thumb_fwd",
    "thumb_back",
]


def view(snapshot):
    profiles = load(snapshot["profiles"])
    sectors = {int(k): bytes.fromhex(v) for k, v in snapshot["sectors"].items()}
    active = snapshot.get("active")
    result = []
    for pid, p in profiles.profiles.items():
        if not (p.enabled or p.sector == active):
            continue
        data = mouseview.sheet(profiles, sectors, pid)
        result.append(
            {
                "id": pid,
                "running": p.sector == active,
                "dpi": describe_dpi(p),
                "rows": [
                    {
                        "slot": slot,
                        "title": mouseview.TITLES[slot],
                        "normal": data[slot][0],
                        "shift": data[slot][1],
                    }
                    for slot in ORDER
                    if slot in data
                ],
            }
        )
    return {
        "ok": True,
        "name": snapshot["name"],
        "time": snapshot["time"].replace("T", " ")[:16],
        "profiles": result,
        "selected": next((i for i, p in enumerate(result) if p["running"]), 0),
    }


def refresh():
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


def main(argv, path=SNAPSHOT):
    try:
        if "--refresh" in argv:
            refresh()
        try:
            snapshot = json.loads(Path(path).read_text())
        except OSError:
            return {
                "ok": False,
                "error": "No state yet. “Refresh from mouse” reads the mouse (read-only).",
            }
        return view(snapshot)
    except Exception as exc:
        return {"ok": False, "error": f"Reading failed: {exc}"}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:])))
