import copy
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from logitech_receiver.hidpp20 import (
    Button,
    LEDEffectSetting,
    OnboardProfile,
    OnboardProfiles,
    OnboardProfilesVersion,
)

import model
from model import (
    apply_macros,
    changes,
    click_macro,
    click_macro_of,
    describe,
    dump,
    encoded,
    load,
    macro_layout,
    patch_directory,
    patch_sector,
    place_macro,
    replace,
    set_dpi,
    set_enabled,
    with_crc,
    write_checked,
)


def fixture():
    profile = OnboardProfile(
        sector=1,
        enabled=1,
        report_rate=1,
        resolution_default_index=0,
        resolution_shift_index=0,
        resolutions=[800, 1600, 0, 0, 0],
        red=0,
        green=0,
        blue=0,
        power_mode=0,
        angle_snap=0,
        write_count=0,
        reserved=b"\xff" * 8,
        ps_timeout=0,
        po_timeout=0,
        name="",
        buttons=[Button(behavior=8, type=1, value=1), Button(behavior=9, value=11, data=0)],
        gbuttons=[Button(behavior=0, sector=6, address=34), Button(behavior=9, value=11, data=0)],
        lighting=[LEDEffectSetting.from_bytes(b"\xff" * 11) for _ in range(4)],
    )
    return OnboardProfiles(
        version=OnboardProfilesVersion,
        name="G502 X Plus",
        count=1,
        buttons=2,
        gbuttons=2,
        sectors=16,
        size=255,
        profiles={1: profile},
    )


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.before = fixture()
        self.after = copy.deepcopy(self.before)
        replace(self.after, 1, "gbuttons", 0, Button(behavior=8, type=2, modifiers=10, value=79))
        self.identity = {"serial": "test-device"}
        self.dev = SimpleNamespace(name="G502 X Plus", feature_request=lambda *a: b"\x00\x01")

    def test_roundtrip_and_opaque_macro(self):
        self.assertEqual(encoded(load(dump(self.before))), encoded(self.before))
        copy_ = copy.deepcopy(self.before)
        replace(copy_, 1, "buttons", 0, Button(behavior=8, type=2, modifiers=0, value=40))
        self.assertEqual(
            copy_.profiles[1].gbuttons[0].to_bytes(), self.before.profiles[1].gbuttons[0].to_bytes()
        )
        self.assertEqual(len(changes(self.before, copy_)), 1)

    def test_shift_and_non_button_changes_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.after, 1, "buttons", 1, Button(behavior=8, type=0))
        self.after.profiles[1].report_rate = 2
        with self.assertRaises(ValueError):
            changes(self.before, self.after)

    def invoke(self, **kwargs):
        return write_checked(
            self.dev,
            self.before,
            self.after,
            self.identity,
            self.identity,
            self.directory,
            **kwargs,
        )

    def device(self):
        """Raw sectors as on a real mouse: opaque bytes Solaar would normalize away."""
        raw = bytearray(self.before.profiles[1].to_bytes(255))
        raw[252] = 3
        raw[40:44] = b"\x12\x34\x56\x78"  # beyond the two modeled buttons
        sectors = {s: b"\xff" * 255 for s in range(16)}
        sectors[0] = self.before.to_bytes()
        sectors[1] = with_crc(raw)
        return sectors

    def test_dpi_validation_and_change_listing(self):
        for bad in (
            [800, 0, 1600, 0, 0],
            [0, 0, 0, 0, 0],
            [825, 0, 0, 0, 0],
            [50, 0, 0, 0, 0],
            [800, 1600, 0, 0],
        ):
            with self.assertRaises(ValueError):
                set_dpi(self.after, 1, bad, 0, 0)
        with self.assertRaises(ValueError):
            set_dpi(self.after, 1, [800, 1600, 0, 0, 0], 2, 0)
        fresh = copy.deepcopy(self.before)
        set_dpi(fresh, 1, [400, 1600, 3200, 0, 0], 1, 0)
        self.assertEqual(len(changes(self.before, fresh)), 1)
        self.assertIn("400/1600/3200 · default step 2 (1600)", changes(self.before, fresh)[0])
        self.assertEqual(encoded(load(dump(fresh))), encoded(fresh))

    def test_patch_preserves_opaque_bytes(self):
        raw = self.device()[1]
        set_dpi(self.after, 1, [400, 1600, 3200, 0, 0], 1, 0)
        patched = patch_sector(raw, self.before.profiles[1], self.after.profiles[1], 2, 2)
        changed = {i for i in range(255) if raw[i] != patched[i]}
        self.assertEqual(changed, {1, 3, 4, 7, 8, 96, 97, 98, 99, 253, 254})
        self.assertEqual(patched, with_crc(patched))
        self.assertEqual(patched[3:9], bytes.fromhex("900140 06800c".replace(" ", "")))
        self.after.profiles[1].report_rate = 2
        with self.assertRaises(ValueError):
            patch_sector(raw, self.before.profiles[1], self.after.profiles[1], 2, 2)

    def test_lighting_slot_layout_and_patch(self):
        self.assertEqual(model.led_bytes(0x01, 0x102030), bytes.fromhex("01102030") + bytes(7))
        self.assertEqual(
            model.led_bytes(0x0A, 0xFF0000, 4000, 80), bytes.fromhex("0aff00000fa00050") + bytes(3)
        )
        self.assertEqual(
            model.led_bytes(0x03, 0, 1000, 100),
            bytes.fromhex("03") + bytes(5) + bytes.fromhex("03e864") + bytes(2),
        )
        for bad in (
            (0x0F,),
            (0x03, 0, 50, 100),
            (0x0A, 0, 4050, 100),
            (0x0A, 0, 4000, 0),
            (0x01, 1 << 24),
        ):
            with self.assertRaises(ValueError):
                model.led_bytes(*bad)
        self.assertIsNone(model.led_params(bytes.fromhex("0f0000000000000064") + bytes(2)))
        self.assertIsNone(
            model.led_params(bytes.fromhex("01102030") + b"\x01" + bytes(6))
        )  # ramp set: kept
        fresh = copy.deepcopy(self.before)
        model.set_lighting(fresh, 1, 0x0A, 0x00FF80, 4000, 80)
        fresh = load(dump(fresh))
        self.assertEqual(
            changes(self.before, fresh),
            ["Profile 1, lighting: Other effect 0xff (kept) → Breathe #00ff80 · 4000 ms · 80 %"],
        )
        raw = bytearray(self.device()[1])
        raw[219:230] = bytes.fromhex(
            "0f0000000000000064aabb"
        )  # factory effect with opaque tail bytes
        raw = with_crc(raw)
        before = model.attach_animation(copy.deepcopy(self.before), {1: raw})
        self.assertEqual(before.profiles[1].animation, 3)
        self.assertIn(
            "overridden by custom animation 3", model.describe_lighting(before, before.profiles[1])
        )
        fresh = copy.deepcopy(before)
        model.set_lighting(fresh, 1, 0x0A, 0x00FF80, 4000, 80)
        fresh = load(dump(fresh))
        patched = patch_sector(raw, before.profiles[1], fresh.profiles[1], 2, 2, True)
        led = model.led_bytes(0x0A, 0x00FF80, 4000, 80)
        self.assertEqual((patched[219:230], patched[241:252], patched[252]), (led, led, 0))
        self.assertEqual(
            {i for i in range(255) if raw[i] != patched[i]}
            - set(range(219, 230))
            - set(range(241, 252)),
            {252, 253, 254},
        )
        # Factory animation restores the slots and byte 252.
        back = copy.deepcopy(fresh)
        model.set_factory_lighting(back, 1)
        self.assertEqual(changes(fresh, back)[0][-17:], "Factory animation")
        restored = patch_sector(patched, fresh.profiles[1], back.profiles[1], 2, 2, True)
        self.assertEqual((restored[219:230], restored[252]), (model.X_PLUS_FACTORY[1], 3))
        # Stale animation byte is refused.
        with self.assertRaises(ValueError):
            patch_sector(
                with_crc(raw[:252] + b"\x05" + raw[253:]),
                before.profiles[1],
                fresh.profiles[1],
                2,
                2,
                True,
            )
        # Other slots (other zone, power saving) stay locked.
        other = copy.deepcopy(self.before)
        other.profiles[1].lighting[0] = LEDEffectSetting.from_bytes(model.led_bytes(0x00))
        with self.assertRaises(ValueError):
            changes(self.before, other)
        self.assertEqual(model.led_slot("G502 SE HERO Gaming Mouse"), 0)

    def two_profiles(self):
        both = copy.deepcopy(fixture())
        both.profiles[2] = copy.deepcopy(both.profiles[1])
        both.profiles[2].sector, both.profiles[2].enabled = 2, 0
        raw = with_crc(
            bytes.fromhex("000101ff000200ff") + b"\xff" * 247
        )  # X PLUS style padding, no marker
        return both, raw

    def test_enable_rules_and_directory_patch(self):
        before, raw = self.two_profiles()
        after = copy.deepcopy(before)
        with self.assertRaises(ValueError):
            set_enabled(after, 1, False)
        self.assertEqual(after.profiles[1].enabled, 1)
        set_enabled(after, 2, True)
        self.assertEqual(changes(before, after), ["Profile 2: enable"])
        patched = patch_directory(raw, before, after)
        self.assertEqual({i for i in range(255) if raw[i] != patched[i]}, {6, 253, 254})
        self.assertEqual((patched[6], patched), (1, with_crc(patched)))
        with self.assertRaises(ValueError):
            patch_directory(
                with_crc(bytes.fromhex("000101ff000300ff") + b"\xff" * 247), before, after
            )
        after.profiles[2].enabled = 2
        with self.assertRaises(ValueError):
            changes(before, after)

    def test_directory_written_last_and_active_profile_protected(self):
        self.before, raw0 = self.two_profiles()
        sectors = self.device()
        sectors[0], sectors[2] = raw0, sectors[1]
        self.after = copy.deepcopy(self.before)
        set_enabled(self.after, 2, True)
        set_dpi(self.after, 2, [400, 0, 0, 0, 0], 0, 0)
        calls = []

        def write(dev, sector, data):
            calls.append(sector)
            sectors[sector] = data

        with (
            tempfile.TemporaryDirectory() as self.directory,
            patch.object(OnboardProfiles, "from_device", side_effect=[self.before, self.after]),
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: sectors[s]),
            patch.object(model, "write_sector", side_effect=write),
        ):
            self.assertEqual(self.invoke(allow=True)["sectors_written"], 2)
        self.assertEqual(calls, [2, 0])
        # Now try to disable profile 1 while it is the active one (0x40 -> sector 1).
        self.before, self.after = self.after, copy.deepcopy(self.after)
        set_enabled(self.after, 1, False)
        self.directory = "/unused"
        with (
            patch.object(OnboardProfiles, "from_device", return_value=self.before),
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: sectors[s]),
            patch.object(model, "write_sector") as w,
        ):
            with self.assertRaisesRegex(ValueError, "active profile"):
                self.invoke(allow=True)
            w.assert_not_called()

    def macro_sector(self):
        # Two key macros like on the real mouse: 11 commands each, at offsets 0 and 34.
        one = (
            bytes.fromhex(
                "4302e1 400032 4308e3 400032 43004f 400032 44004f 400032 4408e3 400032 4402e1"
            )
            + b"\xff"
        )
        return with_crc(one + one + b"\xff" * (255 - 68))

    def test_click_macro_placement_and_reuse(self):
        sectors = self.device()
        sectors[6] = self.macro_sector()
        self.assertEqual(macro_layout(sectors[6]), ([0, 34], 68))
        data = click_macro(2, 1)
        self.assertEqual(data.hex(), "430200410001014200014402 00ff".replace(" ", ""))
        for bad in ((0, 1), (2, 8), (16, 1)):
            with self.assertRaises(ValueError):
                click_macro(*bad)
        self.assertEqual(
            place_macro(sectors, [0, 1], data, []), (2, 0, True)
        )  # first non-profile sector
        self.assertEqual(place_macro(sectors, [0, 1, 2, 3, 4, 5], data, []), (6, 68, True))
        pending = [{"modifiers": 2, "button": 1, "sector": 6, "address": 68}]
        self.assertEqual(place_macro(sectors, [0, 1, 2, 3, 4, 5], data, pending), (6, 68, False))
        self.assertEqual(
            place_macro(sectors, [0, 1, 2, 3, 4, 5], click_macro(1, 2), pending), (6, 82, True)
        )
        patched = apply_macros(sectors, pending, [0, 1, 2, 3, 4, 5])
        self.assertEqual(list(patched), [6])
        self.assertEqual(
            {i for i in range(255) if patched[6][i] != sectors[6][i]} - {253, 254},
            set(range(68, 81)),
        )
        self.assertEqual(patched[6], with_crc(patched[6]))
        self.assertEqual(
            describe(Button(behavior=0, sector=6, address=68), {6: patched[6]}),
            "Shift+Left click (macro, held)",
        )
        self.assertEqual(
            click_macro_of(Button(behavior=0, sector=6, address=68), {6: patched[6]}),
            {"modifiers": 2, "button": 1},
        )
        self.assertIsNone(
            click_macro_of(Button(behavior=0, sector=6, address=0), {6: patched[6]})
        )  # key macro
        self.assertIsNone(click_macro_of(Button(behavior=8, type=1, value=1), {6: patched[6]}))
        self.assertEqual(click_macro(8, 1)[1], 8)  # Super+left click is a valid held macro
        self.assertEqual(
            describe(Button(behavior=0, sector=6, address=0), {6: patched[6]}),
            "Macro: Shift+Super+Right arrow",
        )
        self.assertIn("sector 6", describe(Button(behavior=0, sector=6, address=0)))
        with self.assertRaises(ValueError):
            apply_macros(sectors, [{**pending[0], "address": 70}], [0, 1])
        with self.assertRaises(ValueError):
            apply_macros(sectors, [{**pending[0], "sector": 1, "address": 0}], [0, 1])
        full = dict(sectors)
        full[6] = with_crc(bytes.fromhex("400001") * 80 + b"\xff" * 15)
        with self.assertRaises(ValueError):
            place_macro(full, [0, *range(1, 6), *range(7, 16)], data, [])
        full[6] = with_crc(b"\x99" + b"\xff" * 254)  # unknown opcode: sector is never touched
        with self.assertRaises(ValueError):
            place_macro(full, [0, *range(1, 6), *range(7, 16)], data, [])

    def test_macro_written_before_profile_and_dangling_reference_rejected(self):
        sectors = self.device()
        sectors[6] = self.macro_sector()
        self.after = copy.deepcopy(self.before)
        replace(self.after, 1, "gbuttons", 0, Button(behavior=0, sector=6, address=68))
        macros = [{"modifiers": 2, "button": 1, "sector": 6, "address": 68}]
        self.directory = "/unused"
        with (
            patch.object(OnboardProfiles, "from_device", return_value=self.before),
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: sectors[s]),
            patch.object(model, "write_sector") as w,
        ):
            with self.assertRaisesRegex(ValueError, "macro reference"):
                self.invoke(allow=True)
            w.assert_not_called()
        calls = []

        def write(dev, sector, data):
            calls.append(sector)
            sectors[sector] = data

        with (
            tempfile.TemporaryDirectory() as self.directory,
            patch.object(OnboardProfiles, "from_device", side_effect=[self.before, self.after]),
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: sectors[s]),
            patch.object(model, "write_sector", side_effect=write),
        ):
            result = self.invoke(allow=True, macros=macros)
        self.assertEqual(calls, [6, 1])
        self.assertEqual(bytes.fromhex(result["sectors"]["6"])[68:82], click_macro(2, 1))
        self.assertEqual(sectors[1][96:100], bytes.fromhex("00060044"))
        # A second button may now reuse the macro that is on the mouse without pending data.
        self.before, self.after = self.after, copy.deepcopy(self.after)
        replace(self.after, 1, "buttons", 0, Button(behavior=0, sector=6, address=68))
        with (
            tempfile.TemporaryDirectory() as self.directory,
            patch.object(OnboardProfiles, "from_device", side_effect=[self.before, self.after]),
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: sectors[s]),
            patch.object(model, "write_sector", side_effect=write),
        ):
            self.invoke(allow=True)
        self.assertEqual(calls, [6, 1, 1])

    def test_default_and_hero_cannot_write(self):
        self.directory = "/unused"
        with patch.object(model, "write_sector") as write:
            with self.assertRaises(ValueError):
                self.invoke()
            self.dev.name = "G502 SE Hero Gaming Mouse"
            with self.assertRaises(ValueError):
                self.invoke(allow=True)
            write.assert_not_called()

    def test_stale_and_bad_crc_blocked(self):
        self.directory = "/unused"
        with (
            patch.object(OnboardProfiles, "from_device", return_value=self.after),
            patch.object(model, "write_sector") as write,
        ):
            with self.assertRaises(ValueError):
                self.invoke(allow=True)
            write.assert_not_called()
        sectors = self.device()
        sectors[1] = sectors[1][:-1] + b"\x00"
        with (
            patch.object(OnboardProfiles, "from_device", return_value=self.before),
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: sectors[s]),
            patch.object(model, "write_sector") as write,
        ):
            with self.assertRaisesRegex(ValueError, "CRC"):
                self.invoke(allow=True)
            write.assert_not_called()

    def test_rom_sector_rejected_before_write(self):
        self.directory = "/unused"
        self.before.profiles[1].sector = 256
        self.after.profiles[1].sector = 256
        with (
            patch.object(OnboardProfiles, "from_device", return_value=self.before),
            patch.object(model, "write_sector") as write,
        ):
            with self.assertRaisesRegex(ValueError, "not unambiguously writable"):
                self.invoke(allow=True)
            write.assert_not_called()

    def test_wrong_identity_rejected(self):
        with patch.object(model, "write_sector") as write:
            with self.assertRaises(ValueError):
                write_checked(
                    self.dev,
                    self.before,
                    self.after,
                    {"serial": "other"},
                    self.identity,
                    "/unused",
                    allow=True,
                )
            write.assert_not_called()

    def test_backup_before_raw_write_reload_and_readback(self):
        from pathlib import Path

        sectors = self.device()
        original = dict(sectors)
        set_dpi(self.after, 1, [400, 1600, 0, 0, 0], 1, 0)
        requests = []
        self.dev.feature_request = lambda f, *a: requests.append(a) or b"\x00\x01"

        def write(dev, sector, data):
            paths = list(Path(self.directory).glob("*/device-and-sectors.json"))
            self.assertEqual(len(paths), 1)
            saved = __import__("json").loads(paths[0].read_text())["sectors"]
            self.assertEqual({int(k): bytes.fromhex(v) for k, v in saved.items()}, original)
            sectors[sector] = data

        with (
            tempfile.TemporaryDirectory() as self.directory,
            patch.object(OnboardProfiles, "from_device", side_effect=[self.before, self.after]),
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: sectors[s]),
            patch.object(model, "write_sector", side_effect=write),
        ):
            self.assertEqual(self.invoke(allow=True)["sectors_written"], 1)
        self.assertEqual([s for s in range(16) if sectors[s] != original[s]], [1])
        self.assertEqual((sectors[1][252], sectors[1][40:44]), (3, b"\x12\x34\x56\x78"))
        self.assertEqual(requests, [(0x40,), (0x40,), (0x30, b"\x00\x01")])

    def test_backup_failure_prevents_write(self):
        self.directory = "/unused"
        sectors = self.device()
        with (
            patch.object(OnboardProfiles, "from_device", return_value=self.before),
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: sectors[s]),
            patch("model.backup", side_effect=OSError("disk full")),
            patch.object(model, "write_sector") as write,
        ):
            with self.assertRaises(OSError):
                self.invoke(allow=True)
            write.assert_not_called()

    def test_failed_readback_reports_backup(self):
        sectors = self.device()
        with (  # noqa: SIM117
            tempfile.TemporaryDirectory() as self.directory,
            patch.object(OnboardProfiles, "from_device", side_effect=[self.before, self.before]),
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: sectors[s]),
            patch.object(model, "write_sector"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Partial changes possible.*Backup:"):
                self.invoke(allow=True)


if __name__ == "__main__":
    unittest.main()
