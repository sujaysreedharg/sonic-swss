#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fetch only SHA-pinned official upstream ARM64 dependency packages."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import time
import urllib.parse
import urllib.request
import zipfile


def get_json(url):
    with urllib.request.urlopen(url, timeout=45) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    receipt = []
    for artifact in manifest["azure_artifacts"]:
        base = "https://dev.azure.com/mssonic/build/_apis/build/builds/" + str(artifact["build_id"])
        build = get_json(base + "?api-version=7.1")
        if build["sourceVersion"] != artifact["source_version"]:
            raise RuntimeError("Official build source-version drift")
        query = urllib.parse.urlencode({"artifactName": artifact["artifact_name"], "api-version": "7.1", "x": int(time.time())})
        metadata = get_json(base + "/artifacts?" + query)
        resource = metadata["resource"]
        if resource["data"] != artifact["artifact_resource_data"] or resource["properties"]["RootId"] != artifact["artifact_root_id"]:
            raise RuntimeError("Official artifact identity drift")
        with urllib.request.urlopen(resource["downloadUrl"] + "&x=" + str(int(time.time())), timeout=90) as response:
            payload = response.read(180 * 1024 * 1024 + 1)
        if len(payload) > 180 * 1024 * 1024:
            raise RuntimeError("Official artifact exceeds bounded download")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for package in artifact["packages"]:
                name = package["filename"]
                if name != PurePosixPath(name).name or not name.endswith("_arm64.deb"):
                    raise RuntimeError("Unsafe or non-ARM64 package name")
                entry = archive.getinfo(package["archive_path"])
                if entry.file_size != package["size"]:
                    raise RuntimeError("Package length drift")
                data = archive.read(entry)
                if hashlib.sha256(data).hexdigest() != package["sha256"]:
                    raise RuntimeError("Package SHA-256 mismatch: " + name)
                destination = args.output / name
                with destination.open("xb") as stream:
                    stream.write(data)
                receipt.append({"component": artifact["component"], "official_build_id": artifact["build_id"],
                                "source_version": artifact["source_version"], **package})
        print(artifact["component"] + ": authenticated pinned official packages", flush=True)
    (args.output / "verified-packages.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
