#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Record actual cloud compiler artifacts without claiming SONiC startup."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import platform
import struct
import subprocess


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--ci", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    identity = json.loads((args.ci / "adaptation.json").read_text())
    dependencies = json.loads((args.ci / "dependencies.json").read_text())
    sources = [{"path": item["path"], "sha256": sha(args.source / item["path"])}
               for item in identity["sources"] if (args.source / item["path"]).is_file()]
    binary = None
    for candidate in (args.source / "orchagent/.libs/orchagent", args.source / "orchagent/orchagent"):
        if candidate.is_file():
            with candidate.open("rb") as stream:
                header = stream.read(64)
            if header[:6] == b"\x7fELF\x02\x01":
                kind, machine = struct.unpack_from("<HH", header, 16)
                binary = {"path": str(candidate), "sha256": sha(candidate), "bytes": candidate.stat().st_size,
                          "elf_type": kind, "elf_machine": machine}
                break
    source_match = sources == identity["sources"]
    expected_packages = [{"component": artifact["component"], "official_build_id": artifact["build_id"],
                          "source_version": artifact["source_version"], **package}
                         for artifact in dependencies["azure_artifacts"] for package in artifact["packages"]]
    packages_path = args.output / "verified-packages.json"
    packages = json.loads(packages_path.read_text()) if packages_path.is_file() else []
    packages_match = packages == expected_packages and len(packages) == 22
    headers = json.loads((args.ci / "sai-headers.json").read_text())
    header_receipt = args.output / "sai-header-verification.json"
    verified_headers = json.loads(header_receipt.read_text()) if header_receipt.is_file() else {}
    headers_match = verified_headers.get("source_commit") == headers["commit"] and verified_headers.get("verified_headers") == headers["headers"]
    commands_path = args.output / "compile-commands.json"
    observed_commands = json.loads(commands_path.read_text()) if commands_path.is_file() else []
    expected_commands = [["./autogen.sh"], ["./configure", "--with-extra-lib=" + str(args.source / "mini-switch-ci-libraries")], ["make", "-j2"]]
    commands_match = observed_commands == expected_commands
    required_logs = ("autogen.log", "configure.log", "make.log", "orchagent-loader.log", "orchagent-elf.log", "compiler-version.txt", "installed-package-versions.txt")
    logs_present = all((args.output / name).is_file() and (args.output / name).stat().st_size > 0 for name in required_logs)
    loader = (args.output / "orchagent-loader.log").read_text() if (args.output / "orchagent-loader.log").is_file() else ""
    loader_valid = "libsairedis.so" in loader and not any(value in loader for value in ("libsaivs.so", "not found", "undefined symbol:"))
    installed_path = args.output / "installed-package-identities.json"
    installed = json.loads(installed_path.read_text()) if installed_path.is_file() else []
    installed_set = {(item["package"], item["version"], item["architecture"]) for item in installed}
    installed_match = all(tuple(package["filename"][:-4].split("_")) in installed_set for package in expected_packages)
    runtime_path = args.output / "runtime-export.json"
    runtime = json.loads(runtime_path.read_text()) if runtime_path.is_file() else {}
    expected_runtime = {"orchagent", "portsyncd", "portmgrd", "vlanmgrd", "swssconfig", "fdbsyncd"}
    programs = runtime.get("programs", [])
    runtime_programs_match = (len(programs) == 6 and {item.get("name") for item in programs} == expected_runtime)
    if runtime_programs_match:
        for item in programs:
            exported = args.output / "runtime-export" / "bin" / item["name"]
            original = args.source / item["original_path"]
            runtime_programs_match = runtime_programs_match and (
                exported.is_file() and original.is_file() and
                sha(exported) == item["stripped"]["sha256"] and
                exported.stat().st_size == item["stripped"]["bytes"] and
                sha(original) == item["original"]["sha256"] and
                item["stripped"]["elf_machine"] == 183 and item["original_unchanged"])
    runtime_archive = args.output / "mini-switch-arm64-runtime.tar.gz"
    archive = runtime.get("archive", {})
    runtime_archive_match = (runtime_archive.is_file() and
                             archive.get("filename") == runtime_archive.name and
                             archive.get("bytes") == runtime_archive.stat().st_size and
                             archive.get("sha256") == sha(runtime_archive) and
                             runtime_archive.stat().st_size <= 32 * 1024 * 1024)
    runtime_valid = (runtime.get("status") == "PASS" and runtime_programs_match and runtime_archive_match and
                     runtime.get("executed_checkout_commit") and runtime.get("resolved_libraries") and
                     runtime.get("acceptance_checks") and all(runtime["acceptance_checks"].values()))
    complete = (args.exit_code == 0 and platform.system() == "Linux" and platform.machine() in ("aarch64", "arm64") and
                source_match and binary and binary["elf_machine"] == 183 and packages_match and headers_match and
                commands_match and logs_present and loader_valid and installed_match and runtime_valid)
    report = {"schema_version": 1, "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "status": "PASS" if complete else "FAIL", "exit_code": args.exit_code,
              "scope": "Actual complete adapted SWSS build on Linux ARM64 using authenticated official upstream-built dependencies",
              "system": platform.system(), "machine": platform.machine(), "image": dependencies["image"],
              "base_swss_commit": identity["base_commit"], "patch_sha256": identity["patch_sha256"],
              "sources": sources, "source_hashes_match": source_match, "orchagent_elf": binary,
              "full_swss_compile_attempted": (args.output / "make.log").is_file(),
              "full_swss_compile_executed": bool(complete), "full_swss_compile_passed": bool(complete),
              "dependency_sources_rebuilt_here": False,
              "sonic_startup_executed": False, "vendor_sai_loaded": False,
              "asic_rtl_or_physical_traffic_executed": False,
              "public_runtime_export": {"passed": bool(runtime_valid),
                                        "receipt_sha256": sha(runtime_path) if runtime_path.is_file() else None,
                                        "actual_program_names": [item.get("name") for item in programs],
                                        "archive": archive,
                                        "resolved_library_count": len(runtime.get("resolved_libraries", [])),
                                        "destination_guest_loader_checked": False},
              "cloud_source_files": [{"path": path.name, "sha256": sha(path)} for path in sorted(args.ci.iterdir()) if path.is_file()]}
    report["acceptance_checks"] = {"all_22_official_packages_match": packages_match,
                                   "installed_package_versions_and_architectures_match": installed_match,
                                   "all_pinned_sai_headers_verified": headers_match,
                                   "recorded_compile_commands_match": commands_match,
                                   "complete_build_logs_present": logs_present,
                                   "real_redis_loader_mapping_and_no_unresolved_symbols": loader_valid,
                                   "six_public_runtime_programs_and_archive_authenticated": bool(runtime_valid)}
    report["verified_official_packages"] = packages
    report["compile_commands"] = observed_commands
    report["manifest_hashes"] = {name: sha(args.ci / name) for name in ("adaptation.json", "dependencies.json", "sai-headers.json")}
    report["evidence_file_hashes"] = [{"path": path.name, "sha256": sha(path), "bytes": path.stat().st_size}
                                     for path in sorted(args.output.iterdir()) if path.is_file() and
                                     path.name not in ("build-receipt.json", "mini-switch-arm64-runtime.tar.gz")]
    result = subprocess.run(["git", "-C", str(args.source), "rev-parse", "HEAD"], capture_output=True, text=True)
    report["executed_checkout_commit"] = result.stdout.strip() if result.returncode == 0 else None
    (args.output / "build-receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(report["status"] + ": Linux ARM64 SWSS compile; startup and ASIC traffic remain unexecuted")
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
