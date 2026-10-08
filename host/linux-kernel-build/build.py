#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build only a bounded, unbooted public ARM64 kernel candidate on Ubuntu 24.04."""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path, PurePosixPath
import platform
import signal
import shutil
import subprocess
import tarfile
import time
import traceback
import urllib.request

import guard


HERE = Path(__file__).resolve().parent
LOG_LIMIT = 8 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="New disposable source/build directory")
    parser.add_argument("--output", type=Path, required=True, help="New bounded artifact directory")
    args = parser.parse_args()
    work, output = args.work.absolute(), args.output.absolute()
    guard.require(not work.exists() and not work.is_symlink()
                  and not output.exists() and not output.is_symlink()
                  and work != output and work not in output.parents and output not in work.parents,
                  "Source work and artifact outputs must be distinct, new directories")
    pins = guard.inputs()
    guard.require(platform.system() == "Linux" and platform.machine() == "aarch64"
                  and tuple(os.sys.version_info[:2]) >= (3, 12),
                  "Build requires native Linux ARM64 and Python 3.12 or newer")
    os_release = Path("/etc/os-release").read_text()
    guard.require('\nID=ubuntu\n' in '\n' + os_release
                  and 'VERSION_ID="24.04"' in os_release,
                  "This candidate is restricted to the reviewed Ubuntu 24.04 toolchain")
    guard.require(shutil.disk_usage(work.parent).free >= 12 * 1024**3,
                  "Disposable cloud build requires 12 GiB free before source expansion")
    # Use packaged Ubuntu tools, excluding runner-managed Rustup/compiler overrides.
    env = dict(os.environ, PATH="/usr/sbin:/usr/bin:/sbin:/bin",
               KBUILD_BUILD_USER="mini-switch", KBUILD_BUILD_HOST="arm64-cloud",
               KBUILD_BUILD_TIMESTAMP="Wed Oct 7 00:00:00 UTC 2026",
               LC_ALL="C", PYTHONDONTWRITEBYTECODE="1")
    for option in ("CROSS_COMPILE", "LLVM", "LLVM_IAS", "KCONFIG_CONFIG", "KCFLAGS", "KAFLAGS",
                   "KCPPFLAGS", "KBUILD_EXTRA_SYMBOLS", "KBUILD_EXTMOD"):
        guard.require(not env.get(option), "Unreviewed inherited kernel option: " + option)
    guard.require(shutil.which("rustc", path=env["PATH"]) is None,
                  "Packaged Rust is present; its configuration drift requires a separate review")
    for executable in ("gcc-11", "make", "git", "xz", "gpg", "pahole", "objcopy", "nm", "readelf"):
        guard.require(shutil.which(executable, path=env["PATH"]) is not None,
                      "Missing reviewed build prerequisite: " + executable)
    version = subprocess.run(["gcc-11", "-dumpfullversion"], capture_output=True,
                             text=True, check=True, env=env, timeout=10).stdout.strip()
    guard.require(version == pins["compiler"]["full_version"],
                  "Exact GCC 11.4.0 is required; no silent GCC 11.5 migration")
    work.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    receipt = {"status": "FAIL", "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "uname": list(platform.uname()), "os_release": os_release,
               "source_pins": pins, "jobs": 2, "commands": [],
               "scope": {"kernel_compilation_succeeded": False, "candidate_boot_executed": False,
                         "candidate_vlan_probe_executed": False, "sonic_runtime_executed": False,
                         "asic_rtl_executed": False, "physical_hardware_executed": False}}

    def command(name, arguments, *, cwd=work, timeout=120, stdin=None):
        path = output / (name + ".log")
        started = time.monotonic()
        row = {"name": name, "arguments": list(map(str, arguments)), "cwd": str(cwd),
               "timeout_seconds": timeout, "log": path.name}
        receipt["commands"].append(row)
        try:
            with path.open("xb") as stream:
                process = subprocess.Popen(row["arguments"], cwd=cwd, env=env, stdin=stdin,
                                           stdout=stream, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                try:
                    row["exit_code"] = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                    raise
        except subprocess.TimeoutExpired:
            row.update(exit_code=None, timed_out=True)
            raise
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - started, 6)
            if path.is_file():
                row.update(log_bytes=path.stat().st_size, log_sha256=guard.digest(path))
        guard.require(row["exit_code"] == 0, "Command failed: " + name)
        guard.require(path.stat().st_size <= LOG_LIMIT,
                      "Command log exceeds the retained artifact bound: " + name)
        return path

    try:
        for name in ("pins.json", "guard.py", "build.py", "README.md"):
            shutil.copyfile(HERE / name, output / name)
        (output / "inputs").mkdir()
        for name in pins["files"]:
            shutil.copyfile(HERE / name, output / name)
        compiler_path = Path(shutil.which("gcc-11", path=env["PATH"])).resolve()
        receipt["compiler"] = {"path": str(compiler_path), "sha256": guard.digest(compiler_path),
                               "dumpfullversion": version}
        command("compiler-version", ["gcc-11", "--version"])
        command("package-versions", ["dpkg-query", "-W", "gcc-11", "cpp-11", "gcc-11-base",
                                     "libgcc-11-dev", "binutils", "dwarves", "libelf-dev", "libssl-dev"])
        command("stable-release-refs", ["git", "ls-remote", pins["kernel"]["git_url"],
                                       "refs/tags/v6.18.35", "refs/tags/v6.18.35^{}"], timeout=120)
        refs = (output / "stable-release-refs.log").read_text()
        guard.require(pins["kernel"]["tag_object"] + "\trefs/tags/v6.18.35\n" in refs
                      and pins["kernel"]["commit"] + "\trefs/tags/v6.18.35^{}\n" in refs,
                      "Official stable release refs no longer match the accepted pin")
        archive = work / "linux-6.18.35.tar.xz"
        download_started = time.monotonic()
        with urllib.request.urlopen(pins["kernel"]["archive_url"], timeout=30) as response, archive.open("xb") as stream:
            downloaded = 0
            for block in iter(lambda: response.read(256 * 1024), b""):
                downloaded += len(block)
                guard.require(downloaded <= pins["kernel"]["archive_bytes"], "Source archive exceeds its pinned size")
                guard.require(time.monotonic() - download_started < 300, "Source download exceeded five minutes")
                stream.write(block)
        guard.require(archive.stat().st_size == pins["kernel"]["archive_bytes"]
                      and guard.digest(archive) == pins["kernel"]["archive_sha256"],
                      "Downloaded official source size or SHA-256 differs")
        receipt["downloaded_source"] = {"url": pins["kernel"]["archive_url"],
                                         "bytes": archive.stat().st_size, "sha256": guard.digest(archive)}
        keyhome = work / "developer-keyring"
        keyhome.mkdir(mode=0o700)
        gpg = ["gpg", "--batch", "--homedir", str(keyhome)]
        command("developer-key-wkd", gpg + ["--auto-key-locate", "clear,wkd", "--locate-keys",
                                           pins["kernel"]["signer_address"]], timeout=180)
        keylog = command("developer-key-identity", gpg + ["--with-colons", "--fingerprint"])
        guard.require("fpr:::::::::" + pins["kernel"]["signer_fingerprint"] + ":" in keylog.read_text(),
                      "Kernel developer WKD key does not match the official fingerprint")
        decoder = subprocess.Popen(["xz", "-cd", str(archive)], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env)
        try:
            signature_log = command("source-developer-signature", gpg + ["--status-fd", "1", "--verify",
                                    str(HERE / "inputs/linux-release.tar.sign"), "-"],
                                    stdin=decoder.stdout, timeout=300)
            decoder.stdout.close()
            guard.require(decoder.wait(timeout=30) == 0, "Source decompression failed during signature verification")
        finally:
            if decoder.poll() is None:
                decoder.kill()
                decoder.wait(timeout=10)
        valid = [line.split() for line in signature_log.read_text().splitlines()
                 if line.startswith("[GNUPG:] VALIDSIG ")]
        guard.require(len(valid) == 1 and pins["kernel"]["signer_fingerprint"] in
                      (valid[0][2], valid[0][-1]), "Developer signature verified with an unexpected key")
        receipt["developer_signature_verified"] = True
        with tarfile.open(archive, mode="r:xz") as package:
            members = package.getmembers()
            guard.require(len(members) <= 200000
                          and all(PurePosixPath(member.name).parts
                                  and PurePosixPath(member.name).parts[0] == "linux-6.18.35"
                                  and ".." not in PurePosixPath(member.name).parts
                                  for member in members)
                          and sum(member.size for member in members if member.isfile()) <= 3 * 1024**3,
                          "Official source tree exceeds its unpacking/path bounds")
            package.extractall(work, members=members, filter="data")
        source = work / "linux-6.18.35"
        guard.require(guard.digest(source / "arch/arm64/boot/Makefile") ==
                      pins["files"]["inputs/arm64-boot-Makefile"]["sha256"],
                      "Authenticated archive ARM64 Image recipe differs from reviewed release source")
        patch = str(HERE / "inputs/kata-dax.patch")
        command("kata-patch-check", ["git", "apply", "--check", "--verbose", patch], cwd=source)
        command("kata-patch-apply", ["git", "apply", "--verbose", patch], cwd=source)
        build = work / "objects"
        build.mkdir()
        shutil.copyfile(HERE / "inputs/baseline.config", build / ".config")
        make = ["make", "ARCH=arm64", "CC=gcc-11", "HOSTCC=gcc-11", "O=" + str(build)]
        command("baseline-olddefconfig", make + ["olddefconfig"], cwd=source)
        normalized = output / "normalized-baseline.config"
        shutil.copyfile(build / ".config", normalized)
        receipt["normalization"] = guard.normalization(HERE / "inputs/baseline.config", normalized, pins)
        options = []
        for name in pins["features"]:
            options += ["--enable", name.removeprefix("CONFIG_")]
        command("select-four-network-features", [str(source / "scripts/config"), "--file", str(build / ".config"), *options])
        command("candidate-olddefconfig", make + ["olddefconfig"], cwd=source)
        candidate = output / "candidate.config"
        shutil.copyfile(build / ".config", candidate)
        receipt["configuration_gate"] = guard.candidate(normalized, candidate, pins)
        command("build-image-and-vmlinux", make + ["-j2", "Image"], cwd=source,
                timeout=pins["build_timeout_seconds"])
        receipt["scope"]["kernel_compilation_succeeded"] = True
        nm = subprocess.run(["nm", str(build / "vmlinux")], capture_output=True,
                            text=True, check=True, env=env, timeout=60).stdout
        required_symbols = ("vlan_proto_init", "br_vlan_init", "dummy_init_module", "netem_module_init")
        symbol_lines = [line for line in nm.splitlines() if line.split()[-1:] and line.split()[-1] in required_symbols]
        guard.require({line.split()[-1] for line in symbol_lines} == set(required_symbols),
                      "A requested built-in feature is absent from the actual linked kernel")
        (output / "built-in-feature-symbols.txt").write_text("\n".join(symbol_lines) + "\n")
        headers = command("linked-elf-headers", ["readelf", "-h", "-l", "-n", str(build / "vmlinux")])
        guard.require("AArch64" in headers.read_text(), "Actual linked kernel is not ARM64")
        stripped = work / "vmlinux-mini-switch-6.18.35"
        command("strip-debug-only", ["objcopy", "--strip-debug", str(build / "vmlinux"), str(stripped)])
        original_load = guard.load_segments(build / "vmlinux")
        candidate_load = guard.load_segments(stripped)
        guard.require(original_load == candidate_load,
                      "Debug stripping changed executable kernel load bytes or addresses")
        receipt["unchanged_elf_load_segments"] = candidate_load
        raw = build / "arch/arm64/boot/Image"
        image_header = guard.arm64_image(raw, pins)
        compared_raw = work / "Image-from-linked-elf"
        # Independently repeat the pinned upstream Image recipe to bind the boot
        # payload to the actual linked ELF whose feature symbols were checked.
        command("verify-image-from-linked-elf", ["objcopy", "-O", "binary", "-R", ".note",
                "-R", ".note.gnu.build-id", "-R", ".comment", "-S",
                str(build / "vmlinux"), str(compared_raw)])
        guard.require(raw.stat().st_size == compared_raw.stat().st_size
                      and guard.digest(raw) == guard.digest(compared_raw),
                      "Raw boot Image bytes differ from the reviewed linked-ELF conversion")
        receipt["raw_image_linked_elf_identity"] = {
            "status": "PASS", "sha256": guard.digest(raw),
            "upstream_recipe_sha256": pins["files"]["inputs/arm64-boot-Makefile"]["sha256"],
            "actual_elf_conversion_bytes_equal": True}
        retained = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
        guard.require(retained + stripped.stat().st_size + raw.stat().st_size + 65536
                      <= pins["artifact_limit_bytes"],
                      "Raw boot Image plus ELF and evidence exceeds the 64 MiB artifact bound")
        target = output / stripped.name
        shutil.copyfile(stripped, target)
        receipt["candidate_elf"] = {"path": target.name, "bytes": target.stat().st_size,
                                    "sha256": guard.digest(target), "debug_info_stripped": True,
                                    "purpose": "linked ARM64 ELF inspection, not the raw boot image"}
        boot_target = output / "Image-mini-switch-6.18.35"
        shutil.copyfile(raw, boot_target)
        receipt["candidate_kernel"] = {"path": boot_target.name, "bytes": boot_target.stat().st_size,
                                       "sha256": guard.digest(boot_target), "header": image_header,
                                       "boot_status": "NOT_EXECUTED"}
        receipt["status"] = "PASS_BUILD_ONLY"
    except Exception:
        receipt["error"] = traceback.format_exc()
    finally:
        receipt["files"] = {str(path.relative_to(output)): {"bytes": path.stat().st_size,
                              "sha256": guard.digest(path)} for path in sorted(output.rglob("*")) if path.is_file()}
        with (output / "build-receipt.json").open("x") as stream:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")
        total = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
        guard.require(total <= pins["artifact_limit_bytes"], "Retained artifact exceeds 64 MiB; upload must refuse it")
    print(json.dumps({"status": receipt["status"], "candidate_boot_executed": False,
                      "receipt": str(output / "build-receipt.json")}))
    return 0 if receipt["status"] == "PASS_BUILD_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
