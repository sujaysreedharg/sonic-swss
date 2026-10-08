#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Strict input and configuration gates for the unbooted kernel candidate."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re
import struct

HERE = Path(__file__).resolve().parent


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(256 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def inputs():
    pins = json.loads((HERE / "pins.json").read_text())
    require(pins["kernel"]["version"] == "6.18.35"
            and pins["compiler"]["full_version"] == "11.4.0"
            and pins["artifact_limit_bytes"] == 64 * 1024 * 1024
            and pins["build_jobs"] == 2, "Unexpected source/compiler/resource contract")
    for name, identity in pins["files"].items():
        path = HERE / name
        require(path.is_file() and path.resolve() == path
                and path.stat().st_size == identity["bytes"]
                and digest(path) == identity["sha256"], "Input identity differs: " + name)
    require(gzip.decompress((HERE / "inputs/baseline.config.gz").read_bytes())
            == (HERE / "inputs/baseline.config").read_bytes(),
            "Booted compressed and decoded configurations disagree")
    tag = json.loads((HERE / "inputs/linux-release-tag.json").read_text())
    require(tag["sha"] == pins["kernel"]["tag_object"]
            and tag["object"]["sha"] == pins["kernel"]["commit"]
            and tag["verification"]["verified"] is True,
            "Retained official stable release tag identity differs")
    require(set(pins["features"]) == {"CONFIG_VLAN_8021Q", "CONFIG_BRIDGE_VLAN_FILTERING",
                                      "CONFIG_DUMMY", "CONFIG_NET_SCH_NETEM"}
            and all(value == "y" for value in pins["features"].values()),
            "Unexpected requested feature changes")
    require('install --mode 0644 -D "arch/${arch_target}/boot/Image" "${install_path}/${vmlinux}"'
            in (HERE / "inputs/kata-build-kernel.sh").read_text()
            and "OBJCOPYFLAGS_Image :=-O binary -R .note -R .note.gnu.build-id -R .comment -S"
            in (HERE / "inputs/arm64-boot-Makefile").read_text(),
            "Pinned ARM64 boot image production or Kata installation semantics differ")
    return pins


def config(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        enabled = re.fullmatch(r"(CONFIG_[A-Z0-9_]+)=(.*)", line)
        disabled = re.fullmatch(r"# (CONFIG_[A-Z0-9_]+) is not set", line)
        if enabled or disabled:
            name = enabled[1] if enabled else disabled[1]
            require(name not in values, "Duplicate configuration option: " + name)
            values[name] = enabled[2] if enabled else "n"
    require(values.get("CONFIG_ARM64") == "y", "Not the ARM64 configuration")
    return values


def differences(before: dict, after: dict):
    return {name: {"before": before.get(name, "n"), "after": after.get(name, "n")}
            for name in sorted(set(before) | set(after))
            if before.get(name, "n") != after.get(name, "n")}


def preserved(values: dict, pins: dict):
    require(all(values.get(name) == "y" for name in pins["protected_builtin"]),
            "A required existing VM boot/network feature is no longer built in")
    require(values.get("CONFIG_GCC_VERSION") == "110400"
            and values.get("CONFIG_CC_IS_GCC") == "y"
            and values.get("CONFIG_DEBUG_INFO_BTF") == "y"
            and int(values.get("CONFIG_PAHOLE_VERSION", "0")) >= 124,
            "Compiler family/version or retained BTF support differs")


def normalization(original: Path, normalized: Path, pins: dict):
    before, after = config(original), config(normalized)
    preserved(before, pins)
    preserved(after, pins)
    delta = differences(before, after)
    allowed = set(pins["compiler_metadata_change_allowlist"])
    require("CONFIG_GCC_VERSION" not in allowed and not (set(delta) - allowed),
            "Unreviewed baseline olddefconfig changes: " + str(sorted(set(delta) - allowed)))
    for name in ("CONFIG_AS_VERSION", "CONFIG_LD_VERSION"):
        require(23800 <= int(after.get(name, "0")) < 30000,
                "Unreviewed GNU binutils major version")
    require(124 <= int(after.get("CONFIG_PAHOLE_VERSION", "0")) < 200,
            "Unreviewed pahole major version")
    return {"status": "PASS", "compiler_metadata_changes": delta,
            "unreviewed_configuration_changes": []}


def candidate(normalized: Path, selected: Path, pins: dict):
    before, after = config(normalized), config(selected)
    preserved(before, pins)
    preserved(after, pins)
    delta = differences(before, after)
    require(set(delta) == set(pins["features"]),
            "Candidate configuration changed beyond the four selected features: " + str(delta))
    require(all(before.get(name, "n") == "n" and after.get(name) == "y"
                for name in pins["features"]), "Requested features are not newly built in")
    return {"status": "PASS", "selected_feature_changes": delta,
            "protected_builtin_unchanged": pins["protected_builtin"],
            "unreviewed_configuration_changes": [], "candidate_boot_executed": False}


def load_segments(path: Path):
    """Identify executable ARM64 load bytes, excluding nonloaded debug sections."""
    size = path.stat().st_size
    records = []
    with path.open("rb") as stream:
        header = stream.read(64)
        require(len(header) == 64 and header[:6] == b"\x7fELF\x02\x01"
                and struct.unpack_from("<HH", header, 16) == (2, 183),
                "Not a little-endian 64-bit ARM executable ELF kernel")
        offset = struct.unpack_from("<Q", header, 32)[0]
        entry_bytes, count = struct.unpack_from("<HH", header, 54)
        require(entry_bytes == 56 and 0 < count <= 256
                and offset >= 64 and offset + count * entry_bytes <= size,
                "Invalid ELF program header bounds")
        for index in range(count):
            stream.seek(offset + index * entry_bytes)
            kind, flags, file_offset, virtual, physical, file_bytes, memory_bytes, alignment = \
                struct.unpack("<IIQQQQQQ", stream.read(entry_bytes))
            if kind != 1:
                continue
            require(file_bytes <= 64 * 1024 * 1024 and memory_bytes >= file_bytes
                    and file_offset + file_bytes <= size,
                    "Kernel load segment exceeds its file or byte bound")
            stream.seek(file_offset)
            value, remaining = hashlib.sha256(), file_bytes
            while remaining:
                block = stream.read(min(256 * 1024, remaining))
                require(block, "Truncated kernel load segment")
                value.update(block)
                remaining -= len(block)
            records.append({"flags": flags, "virtual_address": virtual,
                            "physical_address": physical, "file_bytes": file_bytes,
                            "memory_bytes": memory_bytes, "alignment": alignment,
                            "load_bytes_sha256": value.hexdigest()})
    require(records, "Executable kernel has no loadable segments")
    return records


def arm64_image(path: Path, pins: dict):
    """Validate the raw ARM64 Image header; this cannot establish successful boot."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(64)
        require(len(header) == 64 and header[56:60] == b"ARM\x64",
                "Not an uncompressed ARM64 Image boot header")
        _, _, text_offset, image_size, flags, res2, res3, res4, magic, pe_offset = \
            struct.unpack("<IIQQQQQQII", header)
        require(text_offset == pins["baseline"]["kernel_text_offset"]
                and flags == pins["baseline"]["kernel_flags"]
                and not (flags & 1) and ((flags >> 1) & 3) == 1,
                "ARM64 Image load offset, endianness or 4K-page flags changed")
        # image_size includes memory-only space, so it need not equal the file length.
        require(64 <= size <= image_size <= 128 * 1024 * 1024
                and res2 == res3 == res4 == 0,
                "ARM64 Image file/effective size or reserved header fields are invalid")
        require(pins["baseline"]["kernel_efi_stub"] is True and header[:2] == b"MZ"
                and 64 <= pe_offset <= size - 24,
                "Captured EFI-stub boot image shape was not preserved")
        stream.seek(pe_offset)
        pe = stream.read(24)
        require(pe[:4] == b"PE\x00\x00" and struct.unpack_from("<H", pe, 4)[0] == 0xAA64,
                "Raw Image lacks its ARM64 EFI PE header")
    return {"format": "uncompressed ARM64 Image", "header_bytes_hex": header.hex(),
            "file_bytes": size, "effective_image_bytes": image_size,
            "text_offset": text_offset, "flags": flags, "efi_pe_offset": pe_offset,
            "candidate_boot_executed": False}


if __name__ == "__main__":
    print(json.dumps({"status": "PASS", "input_files": len(inputs()["files"]),
                      "kernel_build_executed": False, "candidate_boot_executed": False}, indent=2))
