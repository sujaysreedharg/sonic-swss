#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Export six genuine public SWSS ARM64 ELFs after a complete Linux build.

Copies are stripped; original build outputs remain unchanged.
Actual readelf and ldd -r results authenticate the exported runtime subset.
No SONiC service is started and no private vendor implementation is included.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import tarfile
import traceback


PROGRAMS = {
    'orchagent': 'orchagent', 'portsyncd': 'portsyncd',
    'portmgrd': 'cfgmgr', 'vlanmgrd': 'cfgmgr',
    'swssconfig': 'swssconfig', 'fdbsyncd': 'fdbsyncd',
}
MAX_STRIPPED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
SYSTEM_ROOTS = (Path('/usr/lib'), Path('/lib'))
BAD_LOADER_TEXT = ('not found', 'undefined symbol:', 'libsaivs.so')


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def elf_identity(path: Path) -> dict:
    with path.open('rb') as stream:
        header = stream.read(64)
    if len(header) < 64 or header[:7] != b'\x7fELF\x02\x01\x01':
        raise ValueError('Not an ELF64 little-endian version-one binary: ' + str(path))
    kind, machine = struct.unpack_from('<HH', header, 16)
    if machine != 183 or kind not in (2, 3):
        raise ValueError('Not an actual AArch64 linked ELF: ' + str(path))
    return {'elf_machine': machine, 'elf_type': kind,
            'bytes': path.stat().st_size, 'sha256': sha(path)}


def parse_readelf(text: str, *, executable: bool) -> dict:
    needed = re.findall(r'\(NEEDED\).*Shared library: \[([^\]]+)\]', text)
    paths = re.findall(r'\((?:RPATH|RUNPATH)\).*\[([^\]]*)\]', text)
    # The shipped subset needs no build-tree or custom library search paths.
    # Reject every nonempty embedded path instead of trying to guess safe ones.
    if any(paths):
        raise ValueError('Embedded ELF RPATH/RUNPATH is not permitted: ' + repr(paths))
    if any(name.startswith(('libsaivs.so', 'libsai.so')) for name in needed):
        raise ValueError('A virtual-switch or ambiguous vendor SAI dependency is not permitted')
    build_ids = set(re.findall(r'Build ID:\s*([0-9a-fA-F]+)', text))
    if len(build_ids) != 1:
        raise ValueError('A single actual GNU build ID is required')
    interpreters = re.findall(r'Requesting program interpreter:\s*([^\]]+)\]', text)
    if executable and interpreters != ['/lib/ld-linux-aarch64.so.1']:
        raise ValueError('The exported program needs the real AArch64 Linux loader')
    return {'needed': needed, 'build_id': next(iter(build_ids)).lower(),
            'interpreter': interpreters[0] if interpreters else None,
            'embedded_search_paths': paths}


def parse_loader(text: str) -> dict[str, str]:
    if any(value in text for value in BAD_LOADER_TEXT):
        raise ValueError('Missing, unresolved or virtual-switch runtime dependency')
    mappings = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r'linux-vdso\.so\.1\s+\(0x[0-9a-fA-F]+\)', line):
            continue
        mapped = re.fullmatch(r'(\S+)\s+=>\s+(/\S+)\s+\(0x[0-9a-fA-F]+\)', line)
        direct = re.fullmatch(r'(/\S+)\s+\(0x[0-9a-fA-F]+\)', line)
        if mapped:
            name, path = mapped.groups()
        elif direct:
            path = direct.group(1)
            name = Path(path).name
        else:
            raise ValueError('Unrecognized actual ldd -r output line: ' + line)
        if name.startswith(('libsaivs.so', 'libsai.so')):
            raise ValueError('Unsupported SAI backend in runtime closure')
        if name in mappings and mappings[name] != path:
            raise ValueError('Conflicting runtime SONAME mapping: ' + name)
        mappings[name] = path
    if not mappings or 'ld-linux-aarch64.so.1' not in mappings:
        raise ValueError('The actual runtime loader closure is absent')
    return mappings


class Export:
    def __init__(self, source: Path, ci: Path, output: Path):
        self.source, self.ci, self.output = source, ci, output
        self.root = output / 'runtime-export'
        self.logs = self.root / 'logs'
        self.report = {'schema_version': 1, 'status': 'FAIL',
                       'generated_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       'scope': 'Six copied and stripped actual public SWSS AArch64 programs; Linux loader checks only',
                       'sonic_startup_executed': False, 'vendor_sai_loaded': False,
                       'private_project_sources_included': False,
                       'destination_guest_loader_checked': False,
                       'limits': {'stripped_executable_bytes': MAX_STRIPPED_BYTES,
                                  'compressed_archive_bytes': MAX_ARCHIVE_BYTES},
                       'programs': [], 'resolved_libraries': [], 'commands': []}

    def command(self, args, label, *, required=True):
        completed = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                   text=True, timeout=90, check=False)
        text = completed.stdout + completed.stderr
        log = self.logs / (label + '.log')
        log.write_text(text)
        self.report['commands'].append({'arguments': args, 'returncode': completed.returncode,
                                       'log': str(log.relative_to(self.root)),
                                       'log_sha256': sha(log)})
        if required and completed.returncode:
            raise RuntimeError('Actual export command failed: ' + repr(args) + '\n' + text)
        return completed, text

    def inspect(self, path, label, *, executable):
        identity = elf_identity(path)
        _, text = self.command(['readelf', '--wide', '-h', '-l', '-d', '-V', '-n', '-S', str(path)],
                               label + '-readelf')
        return {**identity, **parse_readelf(text, executable=executable)}

    def loader(self, path, label):
        _, text = self.command(['ldd', '-r', str(path)], label + '-ldd-r')
        return parse_loader(text)

    def owner(self, path: Path, aliases, label) -> dict:
        # Debian usrmerge can give a registered /lib name for a real /usr/lib path.
        candidates = [str(path), *sorted(aliases)]
        if str(path).startswith('/usr/lib/'):
            candidates.append(str(path)[4:])
        for index, candidate in enumerate(dict.fromkeys(candidates)):
            completed, text = self.command(['dpkg-query', '-S', candidate],
                                           f'{label}-owner-{index}', required=False)
            if completed.returncode:
                continue
            owners = sorted({line.partition(': ')[0] for line in text.splitlines() if ': ' in line})
            if not owners:
                continue
            _, versions = self.command(['dpkg-query', '-W',
                                        '-f=${binary:Package}\t${Version}\t${Architecture}\n', *owners],
                                       label + '-package-identities')
            rows = []
            for line in versions.splitlines():
                package, version, architecture = line.split('\t')
                if architecture not in ('arm64', 'all'):
                    raise ValueError('A runtime library has a foreign package architecture')
                rows.append({'package': package, 'version': version, 'architecture': architecture})
            return {'registered_path': candidate, 'packages': rows}
        raise ValueError('Resolved runtime library has no actual installed package owner: ' + str(path))

    def execute(self):
        if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
            raise RuntimeError('An actual Linux ARM64 build environment is required')
        if self.root.exists() or (self.output / 'mini-switch-arm64-runtime.tar.gz').exists():
            raise RuntimeError('Refusing to overwrite a previous runtime export')
        self.logs.mkdir(parents=True)
        (self.root / 'bin').mkdir()
        (self.root / 'provenance').mkdir()
        for tool in ('readelf', 'strip', 'ldd'):
            self.command([tool, '--version'], tool + '-version')
        _, checkout = self.command(['git', '-C', str(self.source), 'rev-parse', 'HEAD'], 'checkout')
        self.report['executed_checkout_commit'] = checkout.strip()
        adaptation = json.loads((self.ci / 'adaptation.json').read_text())
        self.report['source_hashes'] = []
        for entry in adaptation['sources']:
            actual = sha(self.source / entry['path'])
            if actual != entry['sha256']:
                raise ValueError('Adapted public source hash changed: ' + entry['path'])
            self.report['source_hashes'].append({'path': entry['path'], 'sha256': actual})
        self.report['cloud_script_hashes'] = {path.name: sha(path) for path in sorted(self.ci.iterdir())
                                             if path.is_file()}
        for name in ('adaptation.json', 'dependencies.json', 'sai-headers.json'):
            shutil.copyfile(self.ci / name, self.root / 'provenance' / name)
        for name in ('LICENSE', 'ThirdPartyLicenses.txt'):
            shutil.copyfile(self.source / name, self.root / name)
        closure = {}
        original_bytes = 0
        stripped_bytes = 0
        for name, directory in PROGRAMS.items():
            candidates = [self.source / directory / '.libs' / name,
                          self.source / directory / name]
            actual = None
            for candidate in candidates:
                if candidate.is_file():
                    with candidate.open('rb') as stream:
                        if stream.read(4) == b'\x7fELF':
                            actual = candidate
                            break
            if actual is None:
                raise ValueError('Real complete-build ELF is absent: ' + name)
            if actual.is_symlink() or self.source not in actual.resolve().parents:
                raise ValueError('Program is outside the actual public build tree')
            original = self.inspect(actual, name + '-original', executable=True)
            before = self.loader(actual, name + '-original')
            destination = self.root / 'bin' / name
            shutil.copyfile(actual, destination)
            destination.chmod(0o755)
            self.command(['strip', '--strip-unneeded', '--keep-section=.note.gnu.build-id', str(destination)],
                         name + '-strip')
            stripped = self.inspect(destination, name + '-stripped', executable=True)
            after = self.loader(destination, name + '-stripped')
            if before != after or original['build_id'] != stripped['build_id']:
                raise ValueError('Stripping changed the actual runtime closure or GNU build ID')
            if original['needed'] != stripped['needed'] or sha(actual) != original['sha256']:
                raise ValueError('Original program or dynamic dependencies changed during export')
            if name == 'orchagent' and 'libsairedis.so.0' not in after:
                raise ValueError('Actual orchagent does not use the real upstream Redis SAI')
            stripped_bytes += stripped['bytes']
            original_bytes += original['bytes']
            if stripped_bytes > MAX_STRIPPED_BYTES:
                raise ValueError('Actual stripped programs exceed the 64 MiB export bound')
            self.report['programs'].append({'name': name,
                                           'original_path': str(actual.relative_to(self.source)),
                                           'original': original, 'exported_path': 'bin/' + name,
                                           'stripped': stripped, 'loader_mappings': after,
                                           'original_unchanged': True})
            for soname, path in after.items():
                resolved = Path(path).resolve(strict=True)
                if not any(root.resolve() in resolved.parents for root in SYSTEM_ROOTS):
                    raise ValueError('Resolved runtime library is outside system library directories: ' + str(resolved))
                item = closure.setdefault(resolved, {'sonames': set(), 'paths': set(), 'consumers': set()})
                item['sonames'].add(soname)
                item['paths'].add(path)
                item['consumers'].add(name)
        if len(self.report['programs']) != 6:
            raise ValueError('The complete selected six-program set is required')
        for index, (path, item) in enumerate(sorted(closure.items(), key=lambda pair: str(pair[0]))):
            label = f'library-{index:02d}'
            inspected = self.inspect(path, label, executable=False)
            owner = self.owner(path, item['paths'], label)
            self.report['resolved_libraries'].append({
                'resolved_path': str(path), 'loader_paths': sorted(item['paths']),
                'sonames': sorted(item['sonames']), 'consumers': sorted(item['consumers']),
                **inspected, 'installed_package_ownership': owner,
                'library_bytes_exported': False})
        # Every recorded direct DT_NEEDED edge must exist in that program's actual closure.
        for program in self.report['programs']:
            names = set(program['loader_mappings'])
            for library in self.report['resolved_libraries']:
                if program['name'] in library['consumers']:
                    if not set(library['needed']) <= names:
                        raise ValueError('Actual recursive dependency is missing from the loader closure')
            if not set(program['stripped']['needed']) <= names:
                raise ValueError('Actual direct dependency is missing from the loader closure')
        self.report['sizes'] = {'original_executable_bytes': original_bytes,
                                'stripped_executable_bytes': stripped_bytes,
                                'resolved_library_bytes_not_exported': sum(
                                    item['bytes'] for item in self.report['resolved_libraries'])}
        self.report['acceptance_checks'] = {
            'all_six_actual_aarch64_programs': True,
            'all_original_program_hashes_unchanged': True,
            'original_and_stripped_loader_closures_equal': True,
            'all_build_ids_retained': True, 'all_recursive_needed_edges_resolved': True,
            'all_resolved_libraries_package_owned': True,
            'no_virtual_switch_or_ambiguous_vendor_sai': True,
            'no_embedded_rpath_or_runpath': True,
            'all_runtime_paths_are_system_libraries': True,
            'stripped_programs_within_size_limit': True}
        self.report['status'] = 'PASS'
        (self.root / 'runtime-manifest.json').write_text(json.dumps(self.report, indent=2) + '\n')
        archive = self.output / 'mini-switch-arm64-runtime.tar.gz'
        # Stable archive metadata; input ELF build IDs and hashes remain actual measurements.
        with archive.open('xb') as raw:
            with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as zipped:
                with tarfile.open(fileobj=zipped, mode='w') as tar:
                    for path in sorted(self.root.rglob('*')):
                        if not path.is_file():
                            continue
                        if path.is_symlink():
                            raise ValueError('Symlinks are not permitted in the runtime export')
                        info = tar.gettarinfo(str(path), arcname=str(path.relative_to(self.root)))
                        info.uid = info.gid = 0
                        info.uname = info.gname = ''
                        info.mtime = 0
                        with path.open('rb') as stream:
                            tar.addfile(info, stream)
        if archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError('Actual compressed runtime artifact exceeds the 32 MiB bound')
        self.report['archive'] = {'filename': archive.name, 'bytes': archive.stat().st_size,
                                  'sha256': sha(archive)}
        self.report['acceptance_checks']['archive_within_size_limit'] = True
        self.report['export_files'] = [{'path': str(path.relative_to(self.root)),
                                       'bytes': path.stat().st_size, 'sha256': sha(path)}
                                      for path in sorted(self.root.rglob('*')) if path.is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--ci', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    export = Export(args.source.resolve(strict=True), args.ci.resolve(strict=True), output)
    try:
        export.execute()
    except Exception:
        export.report['status'] = 'FAIL'
        export.report['error'] = traceback.format_exc()
    output.mkdir(parents=True, exist_ok=True)
    (output / 'runtime-export.json').write_text(json.dumps(export.report, indent=2) + '\n')
    print(export.report['status'] + ': six-program ARM64 runtime export; service startup unexecuted')
    return 0 if export.report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
