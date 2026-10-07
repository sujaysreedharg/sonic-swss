# Bounded real Linux ARM64 compilation and public runtime export

This candidate runs the complete adapted SWSS build on a disposable public GitHub Actions ARM64 runner.
It uses official upstream-built dependencies authenticated to exact source commits and package SHA-256 values.
It contains no private ASIC RTL, MiniSwitch vendor SAI implementation, PDF, credentials or purchases.
It does not start SONiC services, exercise ASIC traffic or claim that the Jetson/Mac has a working runtime.
The full compile workflow has been executed by the parent task.
The runtime-export extension described below is prepared locally and has not yet been published or executed.

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
No orchagent service process is launched.
The actual Linux dynamic loader is invoked by `ldd -r` to check its relocations and dependencies.

The actual build command sequence is `./autogen.sh`, `./configure` and top-level `make -j2`.
It compiles the complete adapted program with the normal upstream warnings and link rules; it does not extract policy methods or substitute dependency mocks.
The source hashes must match all eleven adapted files.
The final receipt requires a real Linux ARM64 ELF orchagent, successful complete build exit and authenticated six-program runtime export before recording `PASS`.

## Export the genuine public runtime subset

After the complete build, `export_runtime.py` copies the actual production `orchagent`, `portsyncd`, `portmgrd`, `vlanmgrd`, `swssconfig` and `fdbsyncd` ELFs into a separate export tree.
It recognizes a real ELF in the normal path or libtool `.libs` path and rejects a wrapper script.
Every selected program must be an actual ELF64 little-endian AArch64 linked binary with the Linux AArch64 interpreter and a GNU build ID.
The original build output remains unchanged and its hash is checked again after export.

Only the copies are processed with GNU [`strip --strip-unneeded`](https://sourceware.org/binutils/docs/binutils/strip.html), retaining `.note.gnu.build-id`.
That option removes debugging material and symbols unnecessary for relocation processing.
The exporter records original and stripped byte counts, SHA-256 values and build IDs.
It checks every program with actual GNU [`readelf`](https://sourceware.org/binutils/docs/binutils/readelf.html) metadata and actual `ldd -r` before and after stripping.
Any failed command, missing library, unresolved symbol, virtual-switch or ambiguous vendor SAI mapping fails the job.
Nonempty RPATH/RUNPATH entries are rejected, including any temporary build-tree search path.
Original and stripped loader mappings, direct dependencies and build IDs must match.
The actual orchagent closure must include `libsairedis.so.0`.

The resolved recursive library closure is measured separately.
Every resolved library must live in a system library directory, have an actual AArch64 ELF/build-ID identity, have no forbidden embedded search path and map to an installed Debian package owner/version/architecture.
The receipt records each resolved path, loader aliases, direct dependencies, consumers, bytes, SHA-256 and package identities.
All recursive DT_NEEDED edges must appear in the relevant program's actual loader closure.
This measurement includes dependencies the restricted Layer 2 profile does not actively exercise when they remain in the complete program's link graph.
The exporter does not copy shared-library bytes into the archive.
The guest must install the recorded matching runtime dependencies and repeat loader checks before starting any service.

The six stripped executables must total at most 64 MiB, and the compressed archive must be at most 32 MiB.
These are rejection limits, not measured artifact sizes or a guest storage-fit claim.
The archive includes only the selected public executables, public source manifests, source license/notices and actual exporter logs/manifest.
It excludes unstripped binaries, private RTL, vendor SAI, endpoint software, dependency `.deb` files and the full container filesystem.
The archive has stable tar/gzip metadata while retaining the actual executable hashes/build IDs.
`runtime-export.json` records the resulting compressed archive's actual hash and size.

The compiler evidence artifact retains the old receipts/logs plus actual export logs.
A separate `mini-switch-arm64-swss-runtime` artifact retains the bounded archive and external runtime receipt for seven days.
It is uploaded only after all job checks pass, using the existing pinned official upload action.
The compiler receipt independently rechecks the six exported and original hashes and archive identity.
Its archive hash is recorded under `public_runtime_export`, rather than pretending the separate archive is a member of the compiler evidence artifact.
Receipt generation failure now fails the shell job instead of being discarded by the EXIT trap.

`test_export_runtime.py` checks rejection/parsing policy using explicit text fixtures.
It does not emulate Linux acceptance or supply fake ELF/runtime success.
The actual positive export gate is exercised only by the full cloud build.

## Evidence and limits

The workflow uploads JSON receipts, text package/compiler inventories and build/loader/storage logs for seven days.
It excludes dependency packages and unstripped compiled binaries from upload.
`build-receipt.json` records the executed checkout, eleven source hashes, planned patch identity, pinned image, package provenance and ELF bytes/hash/machine.
Its success gate also requires all 22 authenticated packages and installed version/architecture identities, successful verification of every pinned SAI header, the exact compile commands, complete compiler/configure/ELF/loader logs and hashes of those artifacts and manifests.
The receipt explicitly marks dependency sources as upstream-built rather than rebuilt in this job.
SONiC startup, vendor SAI loading and ASIC traffic remain false even if compilation succeeds.

The immutable baseline [run 37700085394](https://github.com/sujaysreedharg/sonic-swss/actions/runs/37700085394) executed the complete adapted SWSS build successfully at checkout `eb72122c20807376ae9f3833383b7e36003d136e`.
Its collected receipt records a real 274,074,072-byte AArch64 orchagent with SHA-256 `5969db326e902555c1d7187aa895994a56e38b18ccc7bea4779315548274183e`.
Its real loader log resolves Redis SAI and records no missing libraries or unresolved symbols.
The baseline retained compiler evidence, not executable bytes, so it cannot be reused as a downloadable runtime artifact.
The six-program export changes need a new successful cloud execution and independently authenticated artifact collection.
Local policy tests, syntax checks and a genuine macOS export rejection are preparation evidence only.
SONiC startup and live service acceptance remain unexecuted by this workflow.
