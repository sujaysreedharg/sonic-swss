# Private Mac Linux kernel candidate

This is an unbuilt candidate for the existing private ARM64 Linux laboratory.
No candidate kernel has booted, no default kernel has changed, and no SONiC startup is claimed.
The workflow runs only on matching pushes to the isolated `mini-switch/linux-kernel-20261007` branch, with a manual dispatch entry retained for a registered workflow.

The source is real Linux 6.18.35 from kernel.org, pinned to archive SHA-256 `f78602932219125e211c5f5bfd84edcfd4ec5ce88fc944f8248413f665bef236` and stable commit `acb7cf4c1184e27622be0faf89244d5001ed1e87`.
The original configuration is the actual booted Kata 3.32.0 kernel configuration captured from `/proc/config.gz` on October 7, 2026.
Its decoded SHA-256 is `a5dfc1ee37dcd4bc6d727e4de617bc45a40cf32e7ebc52fb6f0472c8fbf60c21`.
Both the compressed and decoded files are retained byte for byte.

The build applies the standard Kata 3.32.0 Linux 6.18.x DAX patch from commit `337b6002681479fb6a605ca8a7a1138e81b6098c`.
An independent forward patch check against the exact upstream DAX source passed, and the reverse check failed.
The patch remains necessary and must apply without a skip or reverse fallback.
See the pinned [Kata kernel instructions](https://github.com/kata-containers/kata-containers/blob/3.32.0/tools/packaging/kernel/README.md) and [patch](https://github.com/kata-containers/kata-containers/blob/337b6002681479fb6a605ca8a7a1138e81b6098c/tools/packaging/kernel/patches/6.18.x/0001-fs-dax-check-zero-or-empty-entry-before-converting-xarray.patch).

Only these four previously disabled networking features are selected:

```text
CONFIG_VLAN_8021Q=y
CONFIG_BRIDGE_VLAN_FILTERING=y
CONFIG_DUMMY=y
CONFIG_NET_SCH_NETEM=y
```

VLAN support and bridge filtering address an actual missing capability in the Mac guest kernel.
Dummy interfaces address a separate SONiC host requirement.
Netem is included for subsequent controlled loss, delay and reorder experiments.
These additions do not implement SONiC, an ASIC data plane or an independent MRC endpoint.
The [bridge Kconfig](https://github.com/gregkh/linux/blob/acb7cf4c1184e27622be0faf89244d5001ed1e87/net/bridge/Kconfig) requires both bridge and VLAN support for filtering, and the [scheduler Kconfig](https://github.com/gregkh/linux/blob/acb7cf4c1184e27622be0faf89244d5001ed1e87/net/sched/Kconfig) identifies netem as a network emulator.

The builder first runs `olddefconfig` on the captured configuration using the selected source and compiler.
It permits only four explicit metadata changes: compiler description, assembler version, linker version and pahole version.
GCC remains exactly 11.4.0, and every existing functional configuration value remains unchanged.
It then enables the four features, runs `olddefconfig` again, and rejects every additional semantic change.
Absent and explicitly disabled options are treated equivalently because enabling VLAN support exposes additional disabled child options.
The complete semantic comparison also preserves every existing virtiofs, vsock, interrupt, timer, console, DAX, filesystem and namespace option.
Critical existing built-ins are checked separately.

Current Ubuntu updates can provide GCC 11.5, which changes compiler capability flags.
The workflow pins the matching ARM64 GCC 11.4.0 package and dependencies and refuses a different `gcc-11 -dumpfullversion` result.
See the official [Ubuntu package inventory](https://packages.ubuntu.com/noble/gcc-11).
Compiler binary hashes, package versions, all actual command arguments and logs are retained.
The subprocess tool search uses standard Ubuntu package directories, keeping runner-managed Rustup out of the captured configuration.
A packaged Rust compiler or unreviewed toolchain capability change causes a failure.

Before extraction, the builder verifies the exact source size and SHA-256.
It obtains the kernel developer key through kernel.org's Web Key Directory and requires fingerprint `647F28654894E3BD457199BE38DBBDC86092693E`.
It verifies the detached developer signature against the decompressed tar stream, following the official [kernel signature instructions](https://www.kernel.org/signature.html).
The source tag and peeled commit are also checked against the official stable Git repository.
The locally retained GitHub maintainer API tag receipt reports a valid signature; this source review is separate from the future builder's actual GPG verification.

The isolated workflow uses the documented `ubuntu-24.04-arm` native ARM64 runner with two build jobs and a 75-minute job timeout.
The kernel compilation command has a 60-minute timeout, and timed-out build process groups are killed together.
The source download has a five-minute bound and the disposable build requires 12 GiB of free disk before expansion.
These are cloud build constraints, not minimum requirements for the Mac guest.
Runner details are documented by [GitHub](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

Required packages are `build-essential`, `gcc-11`, `cpp-11`, `gcc-11-base`, `libgcc-11-dev`, `flex`, `bison`, `libelf-dev`, `libssl-dev`, `dwarves`, `bc`, `xz-utils`, `gnupg` and `git`.
The builder never installs a kernel or modules.
It builds the upstream `Image` target and emits both the raw uncompressed ARM64 boot `Image` and a debug-stripped ELF for linked-kernel inspection, plus configurations, source pins, the Kata patch, compiler identities, feature symbols and logs.
The known-booting official file named `vmlinux-6.18.35-197-debug` is a raw ARM64 `Image`, despite its filename, with SHA-256 `fb2cfb79eb1ae19447a85d75682d7fa5cfec97e24beb2609a492b806e8072c8d`.
The pinned [Kata installation code](https://github.com/kata-containers/kata-containers/blob/337b6002681479fb6a605ca8a7a1138e81b6098c/tools/packaging/kernel/build-kernel.sh) installs `arch/arm64/boot/Image` under that `vmlinux` name on ARM64.
The builder checks the raw Image magic, effective memory size, preserved little-endian 4 KiB-page flags and ARM64 EFI header using the documented [ARM64 boot format](https://docs.kernel.org/arch/arm64/booting.html).
The effective memory size can exceed the file length because it includes memory-only space.
It independently repeats the pinned upstream objcopy recipe and requires identical raw bytes, binding the boot payload to the actual linked ELF whose feature symbols were checked.
It also requires unchanged ELF load addresses and payload hashes after debug stripping.
The artifact must be at most 64 MiB before upload.
This bound includes both kernel formats and all retained evidence, and a build fails if their measured combined size exceeds it.
The 154,511,164-byte source archive and the expanded build tree are disposable cloud inputs and are not included in that artifact.
The linked kernel must contain initialization symbols for all four requested features.
A successful receipt is labeled `PASS_BUILD_ONLY`, with boot, VLAN probe, SONiC, ASIC RTL and physical hardware flags false.

Local source and guard validation, without downloads or builds:

```sh
python3 -B host/linux-kernel-build/guard.py
python3 -B host/linux-kernel-build/test_guards.py
```

On a separately provisioned native Ubuntu 24.04 ARM64 cloud runner with those packages, the exact build entry point is:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B host/linux-kernel-build/build.py \
  --work /tmp/mini-switch-kernel-source \
  --output "$PWD/kernel-build-out"
```

Both paths must be new.
The prepared workflow is `.github/workflows/mini-switch-kernel.yml` on branch `mini-switch/linux-kernel-20261007`.
The reviewed candidate has been pushed on that isolated branch; it is not a booted kernel.
After authenticating a successful build, the guest boot must verify the exact raw Image hash, unchanged boot services and the actual bridge/VLAN/dummy/netem probes.
The current private runtime helper still pins the original kernel and must not silently accept this candidate.
