# CPU HyperNOID Miner v1.0.0 engine build interface

Run commands from the package root on Linux. The package keeps the validated
optimized field code and records original snapshot hashes in
`engines/PROVENANCE.json`. Public worker identification, conservative desktop
CPU gating, build flags, and bounded test waiting are explicitly documented
changes. Original Apache-2.0 LICENSE, NOTICE, and source attributions remain
in each optimized source directory. The portable implementation has its own
`engines/portable/PROVENANCE.json`.

## Commands

```bash
make all
make check-probe
make check BACKEND=auto
make check BACKEND=avx2_pclmul_packed
make check-supported
make manifest
```

`make all` forces a native build of the probe and applicable engine workers,
then writes the runtime manifest. On x86_64 it builds all five backends; on
native ARM64 it builds only portable. The optional `ARCH=aarch64` override
makes that choice explicit, but does not supply a cross compiler or emulator.
`CC=gcc` selects the compiler. A 64-bit compiler supporting GNU C extensions,
unsigned `__int128`, and POSIX threads is required. Python 3.10 or newer is
recommended for the generators and checks; GNU make and GNU binutils are
used by the build and ISA audits. Actual tested tool versions are recorded
in `build/build-info.json`. No ARM validation is implied by source support.

`make check` runs the chosen supported backend's `check-offline` target.
`BACKEND=auto` selects the first supported built backend in the documented
capability order. `make check-supported` runs every supported built backend's
offline correctness suite. Unsupported explicit selections fail before a
backend test is run. These targets do not connect to a pool or open test
sockets. Individual optimized engine directories retain their original
`make check` targets, which also include local TLS/Stratum tests and need
the OpenSSL command-line program.

`make manifest` only refreshes hashes and build metadata from existing
artifacts. It does not rebuild or run a correctness suite. Run it after any
reviewed launcher-file change or final package assembly. Its output records
build identity separately from completed validation evidence.

## Engine IDs and feature policy

| ID | Required CPU features | AVX OS state | AVX512 OS state |
|---|---|---|---|
| `avx512_gfni` | AVX2, PCLMULQDQ, AVX512F, AVX512BW, GFNI | Yes | Yes |
| `avx2_vpclmul256` | AVX2, VPCLMULQDQ | Yes | No |
| `avx2_pclmul_packed` | AVX2, PCLMULQDQ | Yes | No |
| `ssse3_pclmul128` | SSSE3, PCLMULQDQ | No | No |
| `portable` | Supported 64-bit, little-endian Linux build | No | No |

The desktop row is a conservative public policy: the frozen GFNI core can
fall back when PCLMUL is unavailable, but the public worker requires it for
the optimized polynomial path and complete polynomial-field test coverage.
The listed order is deterministic capability selection, not a guarantee of
the fastest engine on every CPU. Thread counts are independent of backend;
the common worker accepts 1 through 1,024 threads.

## Probe contract

`build/cpu-probe` prints one JSON object with `schema: 1`, `architecture`,
`features`, `os_avx`, `os_avx512`, `supported_backends`, `preferred_backend`,
`affinity_cpus`, and `cpu_ids`. Features are the hardware intersection over
every CPU in the process's allowed affinity; OS AVX state is reported
separately and included in backend eligibility. The probe temporarily pins
only its own process and restores its original affinity. Query or restore
failures produce a nonzero exit status and no backend recommendation.
XGETBV executes only after XSAVE, OSXSAVE, and AVX flags permit it. The probe
is compiled with fixed baseline flags, independent of user CFLAGS.

A launcher must filter supported IDs by installed, manifest-listed workers,
validate any override, and preserve the probed affinity when starting work.
The probe performs no hashing and makes no network connection.

## Integrity and runtime requirements

`build/runtime-manifest.json` has schema 1, package version 1.0.0, relative
runtime file SHA256 values, and engine-version/provenance metadata. It covers
the probe, root wrapper, launcher entry point and module, client, disclosed
fee-policy file, and every built worker.
`build/build-info.json` records compiler/tool versions and each binary's actual
GLIBC symbol requirements. Those requirements apply to the corresponding
prebuilt artifact, not to all native source builds. Local manifest hashes
detect inconsistent package files; distribution authenticity depends on the
release's separately trusted checksums or signatures.

`engines/SOURCE-SHA256SUMS` records the engine build inputs and shared build
helpers using paths relative to the package root. Verify it before modifying
or rebuilding a delivered source package. It excludes generated executables,
bytecode, test outputs, and the generated desktop assembly. Source arithmetic
changes require new correctness evidence; a successful build alone is not a
speed or mining-validity claim.
