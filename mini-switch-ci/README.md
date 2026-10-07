# Bounded real Linux ARM64 compilation

This candidate runs the complete adapted SWSS build on a disposable public GitHub Actions ARM64 runner.
It uses official upstream-built dependencies authenticated to exact source commits and package SHA-256 values.
It contains no private ASIC RTL, MiniSwitch vendor SAI implementation, PDF, credentials or purchases.
It does not start SONiC services, exercise ASIC traffic or claim that the Jetson/Mac has a working runtime.
The workflow has been prepared locally and has not been pushed or dispatched by its author.

## Proven public inputs

The official pinned [SWSS CI template](https://github.com/sonic-net/sonic-swss/blob/c65602228bf663c19dbc6a1ea3e2e5684a800af5/.azure-pipelines/build-template.yml) uses the SONiC bookworm slave image and official common, sairedis and DASH artifacts.
Anonymous registry access was checked on October 7, 2026.
The ARM64 image is pinned to `sha256:f0553eaa83e204edd30b3bcb06bbb353c27d65129a0115b1a0604b646ea36c1a`.
Its manifest/config confirm Linux ARM64, bash and 2,798,060,190 compressed layer bytes.
Expanded disk consumption has not been measured; a successful manifest read is not a successful Docker pull or a storage-fit result.

`dependencies.json` records the following public [Azure build API](https://dev.azure.com/mssonic/build/_apis/build/builds?api-version=7.1) inputs and 22 selected package hashes.
The metadata source versions and immutable artifact identities were checked, then every selected `.deb` was downloaded and hashed in memory.
No dependency binaries were saved on the Mac.

| Dependency | Official build | Exact source revision | ARM64 artifact |
|---|---|---|---|
| Common libraries | 1240175, succeeded | `3091f379290eca9560ef18300b0c0527abc643e2` | `common-lib.arm64`, bookworm files only |
| swss-common | 1232536, succeeded | `8a2e71b6523faeae6b0107fe54c22793b0509067` | `sonic-swss-common-bookworm.arm64` |
| sairedis | 1229508, partiallySucceeded | `3ee202d5191838f9da4bef25ce0088d36c323fe2` | `sonic-sairedis-bookworm.arm64` |
| DASH API | 1241059, succeeded | `2ce7ce648ee77a76fcf567191eb4b9ed6cfeed38` | `sonic-dash-api.arm64` |

The overall sairedis pipeline result is retained honestly; its named ARM64 package artifact exists and its selected bytes were authenticated.
The common-library and DASH revisions match the pinned buildimage commit and its DASH submodule.
The source candidate and pinned official SAI header hashes are recorded in `adaptation.json` and `sai-headers.json`.
These files contain open-source identities, with no private RTL or vendor implementation.

## Stage only the public source candidate

Use a fresh isolated fork branch based on `c65602228bf663c19dbc6a1ea3e2e5684a800af5` with the eleven adapted SWSS files matching `adaptation.json`.
Copy this directory's regular source/JSON/Markdown files to `mini-switch-ci/` in that branch.
Do not copy `__pycache__`, reports, private project directories, vendor SAI or RTL.
Copy `workflow.yml` to `.github/workflows/mini-switch-linux.yml` as well.
The workflow's push trigger is limited to `mini-switch/l2-linux-validation-20261007`; it also permits an explicit workflow dispatch.
The parent task owns branch publication and dispatch decisions.

GitHub currently documents `ubuntu-24.04-arm` among standard public [hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Use the public fork and standard runner, without selecting a paid larger runner.
This route was not exercised by the preparation checks.
The workflow grants only `contents: read`, uses pinned action commits and does not pass secrets into its container.

## What the job proves if it passes

The job records runner/storage information and requires at least 12 GiB free before pulling the image.
That is an explicit planning gate, not a measured proof of expanded image/build fit.
It uses the digest-pinned official container, two compiler jobs and a 90-minute total job limit.
It makes no host cleanup changes and should fail with actual disk evidence if the runner allocation is insufficient.
The container root trusts only the exact runner-owned bind-mounted source path in Git's `safe.directory` setting; it does not trust arbitrary repositories.

The disposable container installs generic signed Debian development dependencies and records the exact installed package versions.
Their versions remain dependent on the container's Debian apt sources at execution time; the receipt preserves them rather than pretending all apt dependencies are immutable.
The SONiC-specific packages must match their individually pinned bytes and official build/artifact identities before installation.
The script checks every pinned SAI header in `/usr/include/sai` before compiling.

Configure's `-lsai` probe uses a temporary symlink to the real official `libsairedis.so` implementation.
This is a library-name alias for the actual Redis SAI library, not a stub implementation.
The job does not supply MiniSwitch vendor SAI and does not claim to compile or run vendor syncd.
Official `libsaivs`/development packages supply the normal pinned SAI headers, but the finished orchagent must resolve `libsairedis` and exclude `libsaivs` in its actual `ldd -r` output.
No orchagent process is launched.

The actual build command sequence is `./autogen.sh`, `./configure` and top-level `make -j2`.
It compiles the complete adapted program with the normal upstream warnings and link rules; it does not extract policy methods or substitute dependency mocks.
The source hashes must match all eleven adapted files.
The final receipt requires a real Linux ARM64 ELF orchagent and successful complete build exit before recording `PASS`.

## Evidence and limits

The workflow uploads only small JSON receipts, text package/compiler inventories and build/loader/storage logs for seven days.
It excludes dependency packages and compiled binaries from upload.
`build-receipt.json` records the executed checkout, eleven source hashes, planned patch identity, pinned image, package provenance and ELF bytes/hash/machine.
Its success gate also requires all 22 authenticated packages and installed version/architecture identities, successful verification of every pinned SAI header, the exact compile commands, complete compiler/configure/ELF/loader logs and hashes of those artifacts and manifests.
The receipt explicitly marks dependency sources as upstream-built rather than rebuilt in this job.
SONiC startup, vendor SAI loading and ASIC traffic remain false even if compilation succeeds.

Preparation executed anonymous manifest/config reads, official build/artifact metadata verification, selected package hashing, Python compilation and shell syntax checks.
The cloud job, Docker pull, full Linux build and live service acceptance have not yet executed.
Do not relabel preparation as compilation or runtime evidence.
