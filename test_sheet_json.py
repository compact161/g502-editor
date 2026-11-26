"""Run with /usr/bin/python test_sheet_json.py. No device IO."""

import json
import tempfile
from pathlib import Path

import sheet_json
from model import dump, save_snapshot, with_crc
from test_model import fixture

tmp = tempfile.TemporaryDirectory()
path = Path(tmp.name) / "cheatsheet.json"
assert sheet_json.main([], path)["ok"] is False  # no snapshot yet
macro = (
    bytes.fromhex("4302e1 400032 4308e3 400032 43004f 400032 44004f 400032 4408e3 400032 4402e1")
    + b"\xff"
)
sectors = {str(i): ("ff" * 255) for i in range(16)}
sectors["6"] = with_crc(b"\xff" * 34 + macro + b"\xff" * (255 - 34 - len(macro))).hex()
save_snapshot("G502 X PLUS", dump(fixture()), sectors, 1, path)
result = sheet_json.main([], path)
json.dumps(result)
assert result["ok"] and result["name"] == "G502 X PLUS" and len(result["time"]) == 16
running = result["profiles"][result["selected"]]
assert running["id"] == 1 and running["running"] and running["dpi"]
assert running["rows"] == [
    {
        "slot": "lmb",
        "title": "Left button",
        "normal": "Left click",
        "shift": "Shift+Super+Right arrow",
    },
    {"slot": "rmb", "title": "Right button", "normal": "G-Shift", "shift": "G-Shift"},
]
assert all(p["running"] or p["id"] != 1 for p in result["profiles"])
path.write_text("{broken")
assert sheet_json.main([], path)["ok"] is False
print("ok")
