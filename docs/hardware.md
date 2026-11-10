---
layout: default
title: Hardware and Linux support
---

# Hardware and Linux support

**CPU HyperNOID Miner v1.0.0 is a Linux CPU miner.** Its source includes five
backends so that CPUs with different instruction sets can compute the same
Parano1d proof of work. The public launcher detects usable CPU and operating-
system features, checks the available binaries, and selects a supported
backend. The selection order is a capability policy; it does not guarantee
the best hashrate on every processor.

## Supported build targets

The initial local release checks ran on **Linux x86-64**. All four optimized
backends passed their offline correctness suites, and the portable backend
was checked separately. These results cover the tested code and environment;
they are not measurements of every CPU model.

**Native ARM64 support uses the portable backend.** ARM64 was not executed in
the local preparation workspace. ARM64 release assets are published only
after the native ARM64 workflow passes for that exact release commit. A build
configuration or an emulated feature list is not a substitute for that result.
The archive's `build/validation/ci-validation.json` and native workflow record
identify the completed checks for its source commit.

This release does not provide Windows, macOS, or 32-bit binaries. The portable
backend requires a compatible 64-bit, little-endian Linux build; its name does
not mean an x86-64 executable can run on an ARM processor.

## The five backends

| Backend ID | Required CPU features | Required OS state | Purpose |
|---|---|---|---|
| `avx512_gfni` | AVX2, PCLMULQDQ, AVX512F, AVX512BW, GFNI | AVX and AVX512 register state | Wide GFNI field implementation and polynomial specialization |
| `avx2_vpclmul256` | AVX2, VPCLMULQDQ | AVX register state | Native 256-bit carryless multiplication |
| `avx2_pclmul_packed` | AVX2, PCLMULQDQ | AVX register state | Packed YMM field state with separate XMM carryless products |
| `ssse3_pclmul128` | SSSE3, PCLMULQDQ | Standard x86-64 state | Legacy 128-bit accelerated field implementation |
| `portable` | Compatible 64-bit, little-endian build | No optional x86 vector or crypto state | Software field arithmetic for CPUs without the optimized feature sets |

The first row is a **conservative public requirement**. The earlier GFNI core
can fall back when PCLMUL is absent, but the public worker requires PCLMUL for
its polynomial acceleration and complete polynomial-field test coverage.

PCLMULQDQ and VPCLMULQDQ are different capability flags. The packed backend
uses AVX2 for its surrounding vector operations and legal VEX.128/XMM PCLMUL
instructions for products. It does not require native YMM VPCLMUL support.
The exact source and feature requirements are recorded in
`engines/PROVENANCE.json` and the runtime manifest.

## Check your machine without mining

After building or extracting a verified release, run:

```bash
./hypernoid-miner --list-backends
./hypernoid-miner --check
```

The list reports CPU/OS support separately from whether a binary is installed.
An explicit override is accepted only when the chosen backend is both
supported and present in the verified runtime manifest:

```bash
./hypernoid-miner --self-test --backend avx2_pclmul_packed
```

`--self-test` performs bounded offline work and does not contact a pool.
A successful self-test does not establish a hashrate or long-term stability.

The probe checks every CPU allowed by the launcher's current affinity and
uses their shared feature set. It guards XGETBV with the required CPU/OS
flags, restores its own affinity, and reports the probed CPU IDs. The launcher
preserves that CPU set when starting work. This matters on systems where
available cores do not all expose the same instructions.

## Threads, temperatures, and runtime libraries

Thread count is independent of backend selection. The worker accepts **1 to
1,024 threads**, and the launcher defaults to the allowed affinity count,
within that limit. A 64-nonce internal hash batch does not require 64 CPU
threads. Choose a thread count suitable for the machine's other workloads.

Mining requires an explicit temperature policy. Use `--stop-temperature`
with a limit appropriate for the CPU, or make the deliberate choice to use
`--no-temperature-guard`. There is no universal temperature default for all
CPU models. Automatic CPU sensor discovery supports the `coretemp`, `k10temp`,
`k8temp`, and `zenpower` hwmon drivers. Explicit inputs supplied with
`--temperature-file` must resolve below `/sys/devices/` and have a
`tempN_input` filename. ARM thermal-zone paths are not accepted, so CPU backend
compatibility does not establish temperature-sensor coverage. The bare engine
worker is not the public thermal supervisor. See [configuration](configuration.md)
for sensor selection and the explicit opt-out's consequences.

The public launcher requires **Python 3.9 or newer and working Linux pidfd
support**. Container policy can restrict pidfds even when the installed Python
provides the required functions. Runtime checks must pass before work starts.

The locally prepared x86-64 binaries and capability probe require **glibc
2.34 or newer**. The authoritative requirement for each release binary is
its `build/build-info.json` entry. This requirement belongs to that prebuilt
artifact; a native source build links against its own environment. A native
ARM64 build also needs its own runtime metadata and validation results.
