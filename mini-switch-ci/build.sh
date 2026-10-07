#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Run only inside the pinned disposable official Linux ARM64 container.
set -euo pipefail
ci_root=$(cd "$(dirname "$0")" && pwd)
source_root=$(cd "$ci_root/.." && pwd)
output_dir="$source_root/mini-switch-ci-out"
mkdir -p "$output_dir"
finish() {
    build_exit=$?
    python3 "$ci_root/build_receipt.py" --source "$source_root" --ci "$ci_root" \
        --output "$output_dir" --exit-code "$build_exit" || true
    exit "$build_exit"
}
trap finish EXIT
[ "$(uname -s)" = Linux ]
[ "$(uname -m)" = aarch64 ]
[ "$(dpkg --print-architecture)" = arm64 ]
# The runner-owned bind mount is inspected by container root.
# Trust only this exact owned checkout inside the disposable container.
git config --global --add safe.directory "$source_root"
[ "$(git -C "$source_root" rev-parse --show-toplevel)" = "$source_root" ]
df -h > "$output_dir/storage-before.log"
git -C "$source_root" merge-base --is-ancestor c65602228bf663c19dbc6a1ea3e2e5684a800af5 HEAD
python3 - "$source_root" "$ci_root" <<'PY'
import hashlib,json,pathlib,sys
source,ci=map(pathlib.Path,sys.argv[1:])
for entry in json.loads((ci/'adaptation.json').read_text())['sources']:
    assert hashlib.sha256((source/entry['path']).read_bytes()).hexdigest()==entry['sha256'],entry['path']
PY
# Prevent package installation from starting services in the disposable container.
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 0755 /usr/sbin/policy-rc.d
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C
dpkg-query -W > "$output_dir/installed-packages-before.txt"
# The pinned image supplies Boost 1.83; generic Debian Boost names select 1.74
# and conflict with those official SONiC image packages.
dpkg-query -W libboost1.83-dev libboost-serialization1.83-dev \
    > "$output_dir/image-boost-version.txt"
python3 - <<'PY'
from pathlib import Path
text = Path('/usr/include/boost/version.hpp').read_text()
assert '#define BOOST_VERSION 108300' in text, 'Pinned image Boost headers differ'
PY
apt-get update > "$output_dir/apt-update.log" 2>&1
apt-get install -y --no-install-recommends \
    libhiredis-dev libzmq3-dev libdbus-1-dev libteam-dev libjansson-dev \
    libjemalloc-dev nlohmann-json3-dev libprotobuf-dev protobuf-compiler \
    libgmock-dev dh-exec \
    > "$output_dir/apt-tools.log" 2>&1
python3 "$ci_root/fetch_dependencies.py" "$ci_root/dependencies.json" "$output_dir" \
    > "$output_dir/dependency-download.log" 2>&1
apt-get install -y --no-install-recommends "$output_dir"/*.deb \
    > "$output_dir/dependency-install.log" 2>&1
dpkg-query -W > "$output_dir/installed-package-versions.txt"
python3 - "$ci_root" "$output_dir" <<'PY'
import hashlib,json,pathlib,subprocess,sys
ci,out=map(pathlib.Path,sys.argv[1:])
manifest=json.loads((ci/'sai-headers.json').read_text())
for name,expected in manifest['headers'].items():
    assert hashlib.sha256((pathlib.Path('/usr/include/sai')/name).read_bytes()).hexdigest()==expected,name
(out/'sai-header-verification.json').write_text(json.dumps({'source_commit':manifest['commit'],'verified_headers':manifest['headers']},indent=2)+'\n')
data=subprocess.check_output(['dpkg-query','-W','-f=${Package}\t${Version}\t${Architecture}\n'],text=True)
rows=[]
for row in data.splitlines():
    package,version,architecture=row.split('\t')
    rows.append({'package':package,'version':version,'architecture':architecture})
(out/'installed-package-identities.json').write_text(json.dumps(rows,indent=2)+'\n')
PY
ldconfig
# Configure's SAI probe uses the real upstream Redis SAI, never a stub or vendor RTL.
ci_library_dir="$source_root/mini-switch-ci-libraries"
mkdir -p "$ci_library_dir"
ln -s /usr/lib/aarch64-linux-gnu/libsairedis.so "$ci_library_dir/libsai.so"
c++ --version > "$output_dir/compiler-version.txt"
python3 - "$source_root" "$output_dir" <<'PY'
import json,pathlib,sys
source,out=map(pathlib.Path,sys.argv[1:])
commands=[['./autogen.sh'],['./configure','--with-extra-lib='+str(source/'mini-switch-ci-libraries')],['make','-j2']]
(out/'compile-commands.json').write_text(json.dumps(commands,indent=2)+'\n')
PY
cd "$source_root"
./autogen.sh > "$output_dir/autogen.log" 2>&1
./configure --with-extra-lib="$ci_library_dir" > "$output_dir/configure.log" 2>&1
make -j2 > "$output_dir/make.log" 2>&1
python3 - "$source_root" "$output_dir" <<'PY'
import pathlib,subprocess,sys
root,out=map(pathlib.Path,sys.argv[1:])
for path in (root/'orchagent/.libs/orchagent',root/'orchagent/orchagent'):
    if path.is_file():
        with path.open('rb') as stream:magic=stream.read(4)
        if magic==b'\x7fELF':
            result=subprocess.run(['ldd','-r',str(path)],capture_output=True,text=True)
            text=result.stdout+result.stderr
            (out/'orchagent-loader.log').write_text(text)
            assert result.returncode==0 and 'not found' not in text and 'undefined symbol:' not in text,text
            assert 'libsairedis.so' in text and 'libsaivs.so' not in text,text
            subprocess.run(['readelf','-h',str(path)],stdout=(out/'orchagent-elf.log').open('w'),check=True)
            break
else:raise RuntimeError('Real ELF orchagent missing after complete build')
PY
df -h > "$output_dir/storage-after.log"
# Keep only receipts and logs for upload; do not publish large binaries or dependency debs.
find "$output_dir" -maxdepth 1 -type f -name '*.deb' -delete
