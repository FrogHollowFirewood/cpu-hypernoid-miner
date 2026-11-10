---
layout: default
title: Linux CPU mining, with clear setup
---

<section class="hero">
  <div class="eyebrow">Independent Parano1d community software</div>
  <h1>Your CPU.<br>One Linux miner.</h1>
  <p class="lede">CPU HyperNOID Miner brings five CPU engines into a single interface, with automatic compatibility checks and step-by-step instructions.</p>
  <div class="actions"><a class="button" href="https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/releases/latest">Download v1.0.0</a><a class="button secondary" href="getting-started.html">Read the setup guide</a></div>
  <p class="release-note">Linux x86-64 and ARM64 · Apache 2.0 source · Disclosed 3% miner-use fee</p>
</section>

<div class="cards">
  <section class="card"><div class="number">5 engines</div><h2>Broader CPU coverage</h2><p>AVX-512/GFNI, two AVX2 paths, an older x86 path, and a portable fallback. ARM64 uses the portable engine.</p></section>
  <section class="card"><div class="number">1 command</div><h2>Choose your thread count</h2><p>The launcher checks usable CPU features and selects a compatible installed engine. Thread count is a separate setting.</p></section>
  <section class="card"><div class="number">Open source</div><h2>Inspect the work</h2><p>Read the code, build it yourself, verify package checksums, and inspect the correctness tests and source history.</p></section>
</div>

## Start with the right Linux package

Run `uname -m` on the machine that will mine. Download the x86-64 archive for `x86_64`, or the ARM64 archive for `aarch64`. Keep the complete extracted folder together. You do not need a different download for 24, 32, or 192 threads.

| Before you start | What to check |
|---|---|
| Operating system | 64-bit Linux, working pidfds, and glibc 2.34 or newer for the release binaries |
| Python | Python 3.9 or newer |
| CPU | x86-64 or ARM64; optional x86 extensions are checked automatically |
| Pool | A compatible Parano1d TLS Stratum endpoint |
| Payout | Your own public Parano1d receiving address |
| Cooling | A suitable temperature limit and readable CPU sensor, or an explicit choice to disable software monitoring |

The [installation guide](getting-started.md) walks through downloads, checksums, package extraction, machine checks, your first mining command, accepted shares, and a clean shutdown.

## See what will run before connecting

```bash
./hypernoid-miner --version
./hypernoid-miner --list-backends
./hypernoid-miner --check
./hypernoid-miner --self-test
```

The first three commands do not mine. The self-test performs bounded offline hashing. The public launcher keeps the package checks, CPU selection, temperature policy, and cleanup in one place.

## Understand the 3% fee

The public release includes a **3% miner-use fee allocated by scheduled mining time**. The fee is disclosed in the program and documentation, with the destination pool and public receiving address available for inspection. It is separate from any fee charged by a pool.

Read the [fee policy](fees.md) for the exact schedule, what happens during connection failures, and why a time allocation is not a promise about an exact percentage of payouts.

## Detailed guides

| Guide | What it covers |
|---|---|
| [Install and start](getting-started.md) | Prerequisites, download verification, launch commands, and accepted shares |
| [Configuration](configuration.md) | Every public option, wallets, pools, TLS, threads, sensors, and temperature policy |
| [CPU compatibility](hardware.md) | Exact engine requirements, older x86 CPUs, ARM64, and unsupported platforms |
| [Build from source](building.md) | Native compilers, dependencies, build commands, tests, and artifacts |
| [Troubleshooting](troubleshooting.md) | CPU selection, missing libraries, certificates, sensors, locks, and pool errors |
| [Update or roll back](updating.md) | Verify a new release, preserve settings, and return to a previous folder |
| [Tests and limitations](validation.md) | What was actually checked and what those checks establish |
| [Source and licensing](provenance.md) | Prototype lineage, public changes, Apache 2.0, and upstream attribution |

## Measured claims, clear limits

This release makes no guaranteed MH/s claim. A compatible engine is not automatically the fastest engine for every CPU. The portable backend extends compatibility and can be slower than a specialized backend.

The public client targets the documented Parano1d TLS Stratum message flow used during development. It does not claim every pool implementation, direct solo-node RPC, or dedicated ARM vector optimization. Native CI and local correctness results are listed in the [validation record](validation.md).

<div class="notice"><p><strong>Independent project.</strong> CPU HyperNOID Miner is maintained by FrogHollowFirewood. It is not an official Parano1d release or an endorsement by the Parano1d maintainers.</p></div>
