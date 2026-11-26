import unittest
from types import SimpleNamespace
from unittest.mock import patch

from logitech_receiver.hidpp20 import Button, OnboardProfiles

import migrate_profile1
from migrate_profile1 import button_map, crc, execute, interim, plan


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.source = {s: b"\xff" * 255 for s in range(16)}
        self.target = dict(self.source)
        raw = bytearray(b"\xff" * 255)
        for base in (32, 96):
            for i in range(11):
                raw[base + i * 4 : base + i * 4 + 4] = Button(
                    behavior=8, type=1, value=1
                ).to_bytes()
        raw[36:40] = Button(behavior=8, type=1, value=2).to_bytes()
        raw[52:56] = Button(behavior=9, value=11, data=0).to_bytes()
        raw[132:136] = Button(behavior=0, sector=6, address=0).to_bytes()
        self.source[1] = crc(raw)
        self.source[6] = bytes.fromhex("430004440004ff") + b"\xff" * 248
        raw = bytearray(b"\xaa" * 255)
        raw[252] = 3
        self.target[1] = crc(raw)
        self.mapping = [0, 1, 2, 3, 5, 4, 10, 9, 8, 7, 6]

    def test_preserve_opaque_fields_and_map_physical_buttons(self):
        result = plan(self.source, self.target, self.mapping)
        self.assertEqual(list(result), [6, 1])
        self.assertEqual(result[6], crc(self.source[6]))
        self.assertEqual(result[6][:253], self.source[6][:253])
        self.assertEqual(result[1][48:52], self.source[1][52:56])
        allowed = set(range(13)) | set(range(32, 76)) | set(range(96, 140)) | {253, 254}
        for i in range(255):
            if i not in allowed:
                self.assertEqual(result[1][i], self.target[1][i])
        self.assertEqual(result[1], crc(result[1]))

    def test_factory_padding_does_not_change_physical_mapping(self):
        source = bytearray(b"\xff" * 255)
        defaults = [
            bytes.fromhex(h)
            for h in (
                "80010001",
                "80010002",
                "80010004",
                "80010008",
                "80010010",
                "9007ffff",
                "9004ffff",
                "9003ffff",
                "900affff",
                "9002ffff",
                "9001ffff",
            )
        ]
        source[32:76] = b"".join(defaults)
        target = bytearray(source)
        target[32:76] = b"".join(
            defaults[i][:2] + b"\x00\x00" if defaults[i][0] == 0x90 else defaults[i]
            for i in self.mapping
        )
        self.assertEqual(button_map(source, target), self.mapping)

    def test_nonempty_macro_sector_is_rejected(self):
        self.target[6] = bytes(255)
        with self.assertRaises(ValueError):
            plan(self.source, self.target, self.mapping)

    def test_macro_reaching_crc_bytes_is_rejected(self):
        self.source[6] = bytes.fromhex("430004") * 84 + b"\x44\x00\x04"
        with self.assertRaises(ValueError):
            plan(self.source, self.target, self.mapping)

    def test_invalid_macro_is_rejected(self):
        self.source[6] = b"\x01" + b"\xff" * 254
        with self.assertRaises(ValueError):
            plan(self.source, self.target, self.mapping)

    def test_write_and_restore_byte_exact_with_stale_guard(self):
        desired = plan(self.source, self.target, self.mapping)
        state = dict(self.target)
        calls = []

        def write(dev, sector, data):
            calls.append(sector)
            state[sector] = data

        dev = SimpleNamespace(feature_request=lambda *a: b"\x00\x01")
        with (
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: state[s]),
            patch.object(migrate_profile1, "write_sector", side_effect=write),
        ):
            execute(dev, self.target, desired)
            self.assertEqual(calls, [6, 1])
            self.assertEqual(state[6], crc(self.source[6]))
            execute(dev, self.target, desired, True)
            self.assertEqual(calls, [6, 1, 1, 6])
            self.assertEqual(state, self.target)
            state[2] = bytes(255)
            with self.assertRaises(ValueError):
                execute(dev, self.target, desired)
            self.assertEqual(calls, [6, 1, 1, 6])

    def test_partial_state_from_failed_attempt_is_tolerated_only_for_macro_sector(self):
        desired = plan(self.source, self.target, self.mapping)
        self.assertEqual(interim(self.source, desired), {6: self.source[6]})
        state = {**self.target, 6: self.source[6]}
        calls = []

        def write(dev, sector, data):
            calls.append(sector)
            state[sector] = data

        dev = SimpleNamespace(feature_request=lambda *a: b"\x00\x01")
        with (
            patch.object(OnboardProfiles, "read_sector", side_effect=lambda d, s, n: state[s]),
            patch.object(migrate_profile1, "write_sector", side_effect=write),
        ):
            with self.assertRaises(ValueError):
                execute(dev, self.target, desired)
            with self.assertRaises(ValueError):
                execute(dev, self.target, desired, True)
            self.assertEqual(calls, [])
            execute(dev, self.target, desired, tolerated=interim(self.source, desired))
            self.assertEqual(calls, [6, 1])
            self.assertEqual(state[6], crc(self.source[6]))
            state[1] = self.source[6]
            with self.assertRaises(ValueError):
                execute(dev, self.target, desired, tolerated={1: self.target[1]})

    def test_write_sector_sends_full_sequence_even_if_only_crc_differs(self):
        sent = []
        dev = SimpleNamespace(feature_request=lambda f, *a: sent.append(a) or b"")
        raw = crc(self.source[6])
        migrate_profile1.write_sector(dev, 6, raw)
        self.assertEqual(sent[0], (0x60, 0, 6, 0, 0, 0, 255))
        self.assertEqual(len(sent), 18)
        self.assertEqual(b"".join(sent[i][1] for i in range(1, 17)), raw)
        self.assertEqual(sent[-1], (0x80,))


if __name__ == "__main__":
    unittest.main()
