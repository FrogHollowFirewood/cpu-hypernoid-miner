---
layout: default
title: Building and checking the miner
---

# Building and checking the miner

**Build and test the source on Linux before using a newly compiled engine.**
CPU HyperNOID Miner v1.0.0 includes the field implementations, generators,
consensus constants, tests, capability probe, and foreground launcher. The
build does not install a service or start a mining session.

## Build tools

Use a 64-bit GNU C toolchain with unsigned `__int128` and POSIX threads,
GNU make, Python 3.10 or newer, and GNU binutils. The OpenSSL command-line
program is needed for the separate local TLS tests. Python 3.10 or newer is
the source-generation and full-check recommendation; the prebuilt runtime
launcher has a lower minimum of Python 3.9 plus functional Linux pidfds.
The public Python components use the standard library; no third-party Python
package is needed for the engine build.

On Debian or Ubuntu, the usual build packages are:

```bash
sudo apt-get update
sudo apt-get install -y build-essential python3 binutils ca-certificates openssl git
```

Package-manager installation changes the local system and is separate from
running the miner's build. Use the corresponding packages on another Linux
distribution. A compiler wrapper or a cross compiler must be configured
explicitly; the build does not download one.

The local preparation build used GCC 14.2.0, Python 3.12.14, GNU make 4.4.1,
and binutils 2.44. These are recorded tested versions, not a claim that every
other toolchain has been tested. Native CI results qualify the release
artifacts built by those CI jobs.

## Build from the package root

Clone the published source and select the release tag:

```bash
git clone https://github.com/FrogHollowFirewood/cpu-hypernoid-miner.git
cd cpu-hypernoid-miner
git checkout v1.0.0
```

Alternatively, extract the verified `cpu-hypernoid-miner-v1.0.0-source.tar.gz`
release asset and enter its directory. A source archive is sufficient for the
normal build and correctness checks. The release qualification and packaging
helpers require a clean Git checkout, as explained below.

Verify a delivered source checksum list before editing its files:

```bash
sha256sum -c engines/SOURCE-SHA256SUMS
make all
```

On x86-64, `make all` builds the four optimized backends and portable. It
builds the baseline-safe capability probe, then writes:

- `build/runtime-manifest.json`: hashes of the root wrapper, launcher entry
  point and module, client, disclosed fee policy, probe, and every built worker,
  with engine-version metadata.
- `build/build-info.json`: compiler/tool identity and actual GLIBC symbol
  requirements for the compiled binaries.

The helper forces native worker rebuilds so that an old prebuilt file cannot
silently stand in for a new compiler or architecture. Preserve the supplied
baseline flags. Architecture-specific instructions are enabled only in the
appropriate backend functions; adding broad host-specific flags can change
the portability of a build.

To select GCC explicitly:

```bash
make all CC=gcc
```

On a **native ARM64 Linux machine**, the corresponding build is:

```bash
make all ARCH=aarch64 CC=gcc
```

That builds portable and the ARM64 probe branch. It does not build the x86
optimized engines. `ARCH=aarch64` alone does not turn an x86 compiler into
an ARM compiler, and a successful x86 build does not validate ARM execution.
Use a passing native ARM64 CI job for the release commit before qualifying
an ARM64 binary asset.

## Offline correctness checks

```bash
make check-probe
make check BACKEND=auto
make check-supported
```

`check-probe` verifies CPU/OS-state selection fixtures and the live affinity
contract. On x86-64 it also inspects the exact probe executable for accidental
optional vector or crypto instructions.

`make check` runs the chosen supported engine's socket-free `check-offline`
target. Auto selection follows the documented capability order among built
engines. `make check-supported` runs every supported built engine's offline
suite. An unsupported override fails before its test binary is executed.
You can select one backend explicitly:

```bash
make check BACKEND=avx2_pclmul_packed
make check BACKEND=portable
```

The optimized suites include independent scalar digest comparisons, public
consensus vectors, target-boundary checks, job and nonce-range transitions,
field arithmetic, worker accounting, and nonce ownership. The GFNI engine
also checks its polynomial and sparse-transform specializations. The
portable implementation has its own independent software-field tests.
Exact scope and counts belong in the completed validation record.

The nonce-ownership tests allow a bounded scheduling deadline through
`NONCE_TEST_TIMEOUT`, defaulting to 60 seconds per epoch where used. Valid
settings are integers from 1 through 300 seconds. This changes waiting time,
not the nonce assertions, epoch counts, or required work.

The optimized backend directories also retain `make check`, which includes
local TLS/Stratum tests. Those use loopback test servers and OpenSSL; they do
not open a public pool session. The top-level offline checks do not open
those test sockets.

## Refresh integrity metadata after final assembly

```bash
make manifest
```

This command hashes existing runtime files and refreshes build metadata. It
does **not** rebuild an engine or certify its correctness. Run it after the
reviewed launcher files and engine binaries are final. If source or runtime
behavior changes, repeat the relevant checks before distributing the result.

Then use the public foreground entry point for inspection:

```bash
./hypernoid-miner --version
./hypernoid-miner --list-backends
./hypernoid-miner --check
```

These commands do not start mining. Actual mining requires a user-supplied
TLS pool, public receiving address, and temperature policy. Do not substitute
a direct `noid-worker` invocation for the public launcher's integrity and
runtime protections.

`make clean` removes generated executables and selected generated build
files. It does not stop another process or alter a system service. Verify
release checksums against a trusted publication before executing downloaded
code: a locally generated manifest detects inconsistent files but does not
authenticate an unknown distributor.

## Launcher tests and qualified release packaging

After building the portable worker, run the shared launcher tests with its
exact path:

```bash
NOID_TEST_WORKER="$PWD/engines/portable/noid-worker" \
  python3 -B -m unittest discover -s launcher/tests -v
```

These tests use local TLS servers and temporary processes. They do not mine
at a public pool. They cover the public runtime separately from the engine
arithmetic checks, including the disclosed fee schedule and phase cleanup.

Source archives can be built and checked without Git metadata. Producing a
qualified release archive is stricter. From a **clean Git commit**, with no
untracked nonignored files and the final native build and manifest present:

```bash
python3 -B tools/validate_release.py
python3 -B tools/package_release.py --source
```

The validation command runs probe checks, all supported engine suites,
launcher tests, the public check, and the public self-test. It writes
`build/validation/ci-validation.json` only after those phases pass. The
receipt identifies the source commit, native architecture, and exact runtime
manifest hash. The packager requires that matching receipt and a clean commit;
changing the source or runtime manifest requires new qualification. The
optional `--source` also creates a source archive from the immutable Git tree.

Release artifacts use native Ubuntu 22.04 workflows for x86-64 and ARM64.
ARM64 assets are published only after their exact commit passes native ARM64
validation. Consult each archive's build identity and validation receipt for
the actual toolchain and results; local x86 checks cannot establish ARM64
execution or a CPU performance improvement.
