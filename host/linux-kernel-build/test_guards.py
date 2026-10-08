#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Native guard tests only; fixtures do not represent executed kernel builds."""
import copy
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

import guard


class ConfigurationGuards(unittest.TestCase):
    def setUp(self):
        self.pins = guard.inputs()
        self.original = guard.HERE / "inputs/baseline.config"
        self.temporary = tempfile.TemporaryDirectory(prefix="mini-switch-kernel-guard-")
        self.directory = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def selected(self, changes, name="selected.config"):
        lines = self.original.read_text().splitlines()
        for option, value in changes.items():
            lines = [line for line in lines if not re.match(re.escape(option) + r"=", line)
                     and line != "# " + option + " is not set"]
            lines.append(option + "=" + value)
        destination = self.directory / name
        destination.write_text("\n".join(lines) + "\n")
        return destination

    def test_actual_boot_config_and_release_inputs_match(self):
        self.assertEqual(guard.digest(self.original),
                         "a5dfc1ee37dcd4bc6d727e4de617bc45a40cf32e7ebc52fb6f0472c8fbf60c21")
        self.assertEqual(len(self.pins["features"]), 4)
        guard.preserved(guard.config(self.original), self.pins)

    def test_exact_four_features_accept_without_boot_claim(self):
        result = guard.candidate(self.original, self.selected(self.pins["features"]), self.pins)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["candidate_boot_executed"])

    def test_new_disabled_children_are_semantically_unchanged(self):
        values = dict(self.pins["features"], CONFIG_VLAN_8021Q_GVRP="n", CONFIG_VLAN_8021Q_MVRP="n")
        guard.candidate(self.original, self.selected(values), self.pins)

    def test_missing_or_module_features_are_rejected(self):
        for option in self.pins["features"]:
            for value in ("n", "m"):
                with self.subTest(option=option, value=value):
                    changes = dict(self.pins["features"], **{option: value})
                    with self.assertRaises(ValueError):
                        guard.candidate(self.original, self.selected(changes), self.pins)

    def test_each_existing_critical_builtin_remains_enabled(self):
        for option in self.pins["protected_builtin"]:
            with self.subTest(option=option):
                changes = dict(self.pins["features"], **{option: "n"})
                with self.assertRaises(ValueError):
                    guard.candidate(self.original, self.selected(changes), self.pins)

    def test_non_boot_functional_changes_also_reject(self):
        for option in ("CONFIG_NETFILTER", "CONFIG_IPV6", "CONFIG_DEBUG_INFO_BTF"):
            with self.subTest(option=option):
                changes = dict(self.pins["features"], **{option: "n"})
                with self.assertRaises(ValueError):
                    guard.candidate(self.original, self.selected(changes), self.pins)

    def test_compiler_metadata_only_normalization(self):
        selected = self.selected({"CONFIG_CC_VERSION_TEXT": '"reviewed GCC 11.4.0"',
                                  "CONFIG_AS_VERSION": "24200", "CONFIG_LD_VERSION": "24200"})
        result = guard.normalization(self.original, selected, self.pins)
        self.assertEqual(len(result["compiler_metadata_changes"]), 3)

    def test_gcc_115_cannot_masquerade_as_metadata(self):
        with self.assertRaises(ValueError):
            guard.normalization(self.original, self.selected({"CONFIG_GCC_VERSION": "110500"}), self.pins)

    def test_toolchain_capability_changes_are_not_metadata(self):
        for option in ("CONFIG_AS_HAS_MOPS", "CONFIG_GCC_ASM_GOTO_OUTPUT_BROKEN",
                       "CONFIG_PAHOLE_HAS_SPLIT_BTF", "CONFIG_LD_CAN_USE_KEEP_IN_OVERLAY"):
            with self.subTest(option=option), self.assertRaises(ValueError):
                guard.normalization(self.original, self.selected({option: "n"}), self.pins)

    def test_rust_detection_drift_rejects(self):
        with self.assertRaises(ValueError):
            guard.normalization(self.original, self.selected({"CONFIG_RUSTC_VERSION": "190000"}), self.pins)

    def test_binutils_and_btf_version_bounds(self):
        for option, value in (("CONFIG_AS_VERSION", "23700"), ("CONFIG_LD_VERSION", "30000"),
                              ("CONFIG_PAHOLE_VERSION", "123"), ("CONFIG_PAHOLE_VERSION", "200")):
            with self.subTest(option=option, value=value), self.assertRaises(ValueError):
                guard.normalization(self.original, self.selected({option: value}), self.pins)

    def test_functional_normalization_drift_rejects(self):
        with self.assertRaises(ValueError):
            guard.normalization(self.original, self.selected({"CONFIG_VLAN_8021Q": "y"}), self.pins)

    def test_duplicate_config_option_rejects(self):
        path = self.selected({})
        with path.open("a") as stream:
            stream.write("CONFIG_ARM64=y\n")
        with self.assertRaises(ValueError):
            guard.config(path)

    def test_candidate_extra_enabled_feature_rejects(self):
        selected = self.selected(dict(self.pins["features"], CONFIG_VLAN_8021Q_GVRP="y"))
        with self.assertRaises(ValueError):
            guard.candidate(self.original, selected, self.pins)

    def test_coherent_signature_receipt_forgery_rejects(self):
        package = self.directory / "package"
        shutil.copytree(guard.HERE, package)
        path = package / "inputs/linux-release-tag.json"
        tag = json.loads(path.read_text())
        tag["verification"]["verified"] = False
        path.write_text(json.dumps(tag))
        pins = copy.deepcopy(self.pins)
        pins["files"]["inputs/linux-release-tag.json"] = {
            "bytes": path.stat().st_size, "sha256": guard.digest(path)}
        (package / "pins.json").write_text(json.dumps(pins))
        with patch.object(guard, "HERE", package), self.assertRaises(ValueError):
            guard.inputs()

    def elf_fixture(self, *, payload=b"kernel load bytes", offset=128, machine=183):
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<HH", header, 16, 2, machine)
        struct.pack_into("<Q", header, 32, 64)
        struct.pack_into("<HH", header, 54, 56, 1)
        program = struct.pack("<IIQQQQQQ", 1, 5, offset, 0x800000, 0x800000,
                              len(payload), len(payload) + 32, 4096)
        path = self.directory / ("elf-" + str(offset) + "-" + str(machine))
        path.write_bytes(header + program + bytes(offset - 120) + payload)
        return path

    def test_elf_repacking_retains_same_load_identity(self):
        first = guard.load_segments(self.elf_fixture(offset=128))
        second = guard.load_segments(self.elf_fixture(offset=192))
        self.assertEqual(first, second)

    def test_changed_load_bytes_or_wrong_architecture_reject(self):
        first = guard.load_segments(self.elf_fixture())
        changed = guard.load_segments(self.elf_fixture(payload=b"different bytes", offset=192))
        self.assertNotEqual(first, changed)
        with self.assertRaises(ValueError):
            guard.load_segments(self.elf_fixture(machine=62))

    def test_truncated_elf_load_segment_rejects(self):
        path = self.elf_fixture()
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            guard.load_segments(path)

    def image_fixture(self, *, file_bytes=256, image_size=4096, flags=10,
                      magic=0x644D5241, pe_machine=0xAA64, pe_offset=64):
        header = struct.pack("<IIQQQQQQII", 0xFA405A4D, 0, 0, image_size,
                             flags, 0, 0, 0, magic, pe_offset)
        contents = bytearray(header + bytes(file_bytes - 64))
        if pe_offset + 24 <= file_bytes:
            contents[pe_offset:pe_offset + 4] = b"PE\x00\x00"
            struct.pack_into("<H", contents, pe_offset + 4, pe_machine)
        path = self.directory / "Image-fixture"
        path.write_bytes(contents)
        return path

    def test_raw_image_accepts_memory_size_larger_than_file(self):
        result = guard.arm64_image(self.image_fixture(), self.pins)
        self.assertEqual(result["file_bytes"], 256)
        self.assertEqual(result["effective_image_bytes"], 4096)
        self.assertFalse(result["candidate_boot_executed"])

    def test_elf_cannot_masquerade_as_raw_boot_image(self):
        with self.assertRaises(ValueError):
            guard.arm64_image(self.elf_fixture(), self.pins)

    def test_raw_image_invalid_magic_size_flags_or_pe_reject(self):
        for changes in ({"magic": 0}, {"image_size": 128}, {"image_size": 0},
                        {"image_size": 129 * 1024 * 1024}, {"flags": 11},
                        {"flags": 12}, {"pe_machine": 0x8664}, {"pe_offset": 1024}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                guard.arm64_image(self.image_fixture(**changes), self.pins)

    def test_raw_image_truncation_and_reserved_fields_reject(self):
        path = self.image_fixture()
        contents = bytearray(path.read_bytes())
        struct.pack_into("<Q", contents, 32, 1)
        path.write_bytes(contents)
        with self.assertRaises(ValueError):
            guard.arm64_image(path, self.pins)
        path.write_bytes(contents[:63])
        with self.assertRaises(ValueError):
            guard.arm64_image(path, self.pins)


if __name__ == "__main__":
    unittest.main(verbosity=2)
