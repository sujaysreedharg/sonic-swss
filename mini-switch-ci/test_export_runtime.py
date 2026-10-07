#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unit checks for export rejection rules, not a simulated Linux acceptance."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('export_runtime', Path(__file__).with_name('export_runtime.py'))
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)

READELF_FIXTURE = '''
 [Requesting program interpreter: /lib/ld-linux-aarch64.so.1]
 0x0000000000000001 (NEEDED) Shared library: [libsairedis.so.0]
 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]
    Build ID: 1234abcdeffedcba
'''
LOADER_FIXTURE = '''
 linux-vdso.so.1 (0x0000ffff00100000)
 libsairedis.so.0 => /lib/aarch64-linux-gnu/libsairedis.so.0 (0x0000ffff00200000)
 libc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x0000ffff00300000)
 /lib/ld-linux-aarch64.so.1 (0x0000ffff00400000)
'''


class ExportPolicyTests(unittest.TestCase):
    def test_exact_six_production_targets(self):
        self.assertEqual(EXPORT.PROGRAMS, {'orchagent': 'orchagent', 'portsyncd': 'portsyncd',
                                          'portmgrd': 'cfgmgr', 'vlanmgrd': 'cfgmgr',
                                          'swssconfig': 'swssconfig', 'fdbsyncd': 'fdbsyncd'})

    def test_selected_loader_fixture_parses_without_assuming_runtime_execution(self):
        actual = EXPORT.parse_loader(LOADER_FIXTURE)
        self.assertEqual(actual['libsairedis.so.0'], '/lib/aarch64-linux-gnu/libsairedis.so.0')
        self.assertEqual(actual['ld-linux-aarch64.so.1'], '/lib/ld-linux-aarch64.so.1')
        self.assertEqual(len(actual), 3)

    def test_missing_library_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing'):
            EXPORT.parse_loader(LOADER_FIXTURE + ' libfoo.so => not found\n')

    def test_unresolved_symbol_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            EXPORT.parse_loader(LOADER_FIXTURE + ' undefined symbol: missing_symbol (program)\n')

    def test_virtual_switch_mapping_is_rejected(self):
        with self.assertRaises(ValueError):
            EXPORT.parse_loader(LOADER_FIXTURE + ' libsaivs.so.0 => /lib/libsaivs.so.0 (0x1234)\n')

    def test_ambiguous_sai_mapping_is_rejected(self):
        with self.assertRaises(ValueError):
            EXPORT.parse_loader(LOADER_FIXTURE + ' libsai.so => /lib/libsai.so (0x1234)\n')

    def test_unrecognized_loader_diagnostic_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unrecognized'):
            EXPORT.parse_loader(LOADER_FIXTURE + 'unexpected loader warning\n')

    def test_conflicting_soname_resolution_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            EXPORT.parse_loader(LOADER_FIXTURE + ' libc.so.6 => /usr/other/libc.so.6 (0x1234)\n')

    def test_missing_runtime_loader_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'loader closure'):
            EXPORT.parse_loader(' libc.so.6 => /lib/libc.so.6 (0x1234)\n')

    def test_readelf_fixture_retains_needed_and_build_id(self):
        actual = EXPORT.parse_readelf(READELF_FIXTURE, executable=True)
        self.assertEqual(actual['needed'], ['libsairedis.so.0', 'libc.so.6'])
        self.assertEqual(actual['build_id'], '1234abcdeffedcba')

    def test_build_directory_rpath_and_runpath_are_rejected(self):
        for tag in ('RPATH', 'RUNPATH'):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, 'RPATH/RUNPATH'):
                EXPORT.parse_readelf(READELF_FIXTURE + f' ({tag}) Library runpath: [/work/build/.libs]\n',
                                     executable=True)

    def test_missing_or_conflicting_build_ids_are_rejected(self):
        for value in ('', READELF_FIXTURE + ' Build ID: 0000000000000000\n'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'build ID'):
                EXPORT.parse_readelf(value, executable=False)

    def test_foreign_interpreter_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'real AArch64'):
            EXPORT.parse_readelf(READELF_FIXTURE.replace('ld-linux-aarch64.so.1', 'ld-linux-x86-64.so.2'),
                                 executable=True)

    def test_non_elf_header_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='export-policy-') as directory:
            path = Path(directory) / 'non-elf'
            path.write_bytes(b'not an executable')
            with self.assertRaisesRegex(ValueError, 'Not an ELF64'):
                EXPORT.elf_identity(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
