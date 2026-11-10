---
layout: default
title: Source provenance and licensing
---

# Source provenance and licensing

**CPU HyperNOID Miner v1.0.0 preserves the source identity and attribution of
its field implementations.** The public product version identifies the
packaged application. Earlier core version numbers identify the development
snapshots from which individual backends were copied.

## Backend lineage

| Public backend | Recorded core snapshot | Public adaptation |
|---|---|---|
| `avx512_gfni` | Prototype 0.10.0 candidate | Public identification, conservative AVX2/PCLMUL gate, baseline build policy, and bounded test waiting |
| `avx2_vpclmul256` | Prototype 0.10.1 rig candidate | Public identification and offline build/test interface |
| `avx2_pclmul_packed` | Prototype 0.10.2 rig candidate | Public identification and offline build/test interface |
| `ssse3_pclmul128` | Prototype 0.10.0 rig candidate | Public identification, offline build/test interface, and bounded test waiting |
| `portable` | HyperNOID 1.0.0 software implementation | CPU-neutral field arithmetic and explicit little-endian encoding, using the shared protocol constants and worker design |

The four optimized arithmetic sources were copied from the frozen validated
snapshots. Their field and permutation code was preserved. The public worker
banner identifies both the product and the original core. The desktop
worker's additional feature gate is deliberately conservative; it is not a
claim that its older fallback implementation could never run with fewer
features.

`engines/PROVENANCE.json` records each optimized snapshot's manifest hash,
the original hash of every copied input, the public source hashes, shared
public build-helper hashes, and the explicit modifications.
`engines/portable/PROVENANCE.json` records the
portable implementation's copied inputs and changes. This makes it possible
to distinguish inherited arithmetic from packaging and launcher work.

## Consensus and protocol inputs

The delivered table headers identify consensus constants from **Parano1d
v2.1.0**. Independent scalar implementations and the public proof-of-work
vectors remain part of the checks. The source package records these upstream
references:

- [Parano1d v2.1.0 source](https://github.com/ignotusnemo/parano1d/tree/v2.1.0)
- [Parano1d proof-of-work documentation](https://docs.parano1d.org/protocol/proof-of-work)
- [Suprnova Stratum protocol and public vectors](https://noid.suprnova.cc/StratumProtocol)

These identifiers document the inputs used by this release. They do not claim
that v2.1.0 is the latest upstream version or that a future consensus change
will be supported automatically.

The native VPCLMUL engine and the packed-XMM engine use different instruction
forms. Intel's instruction table distinguishes VEX.128 XMM PCLMUL from the
VEX.256 YMM form; GCC documents AVX instruction selection and VEX equivalents:

- [Intel SDM, PCLMULQDQ—Carry-Less Multiplication Quadword](https://cdrdv2-public.intel.com/835748/252046-sdm-change-document.pdf)
- [GCC x86 options](https://gcc.gnu.org/onlinedocs/gcc/x86-Options.html)

Actual compiled binaries are checked against the required instruction sets.
The feature probe itself uses baseline code and guarded CPU/OS queries.

## License and notices

The inherited implementation is distributed with **Apache License 2.0**.
Original `LICENSE` and `NOTICE` files, SPDX headers, consensus-table copyright
notices, and attribution text are retained in the backend directories.
Modified copied files carry a prominent change notice.

The inherited NOTICE credits Paranoid Zero and Ignotus Nemo, along with the
referenced hekate-math, binius64, and bolt-rs lineage and their named holders.
The original notice text is preserved; this page is not a replacement for
the license or its redistribution requirements. New public build and probe
helpers carry Apache-2.0 SPDX identifiers as well.

## Source, binary, and validation identity

Three records serve different purposes:

1. `engines/SOURCE-SHA256SUMS` identifies the engine build inputs and shared
   helpers delivered in the source package. Its paths are relative to the
   package root.
2. `build/runtime-manifest.json` identifies the runtime files in a particular
   native build, including the workers, root wrapper, launcher components,
   and disclosed fee policy.
3. `build/build-info.json` records toolchain identity and the actual GLIBC
   symbol requirements of those compiled binaries.

The source list excludes generated executables, bytecode, test outputs, and
the desktop assembly regenerated from its checked-in generator. Different
compilers or target architectures can produce different executable hashes
from the same source, so each release artifact needs its own build metadata,
checksums, and completed test evidence.

A successful build is not a completed correctness test. The release
validation record should name the commands that finished, their results,
the exact binaries tested, the architecture, and any skipped unsupported
backend. Native ARM64 evidence must come from a native ARM64 run for the
release commit; the local x86-64 checks do not establish it.

The provided correctness evidence does not promise a hashrate, profitability,
or an improvement over another miner on an unmeasured machine. Capability
selection is deterministic and can be overridden only with a supported,
verified installed backend. Distribution authenticity comes from separately
trusted release checksums or signatures; a runtime manifest by itself cannot
authenticate an untrusted package.
