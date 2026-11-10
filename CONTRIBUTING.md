# Contributing

Start with a GitHub issue that describes the problem and the observed behavior. A useful report identifies the release, Linux distribution, CPU architecture, selected backend, command options with addresses removed, and the exact error. Share the smallest log excerpt that demonstrates the issue.

For code changes, create a branch and keep the change focused. Explain why it is needed, what behavior changes, and the relevant validation. Preserve the Apache-2.0 license, upstream NOTICE, copyright headers, and changed-file notices. Record meaningful arithmetic or dispatch changes in the provenance and validation documentation.

## Required validation by change type

| Change | Evidence to include |
|---|---|
| Field arithmetic, permutation, nonce construction, or target comparison | Independent reference comparisons, public vectors, target boundaries, job transitions, and the affected engine's offline suite |
| CPU feature or affinity selection | Probe fixtures, actual affinity checks, baseline instruction audit, unsupported-path rejection |
| Launcher or network client | Launcher/protocol suite, relevant certificate or cleanup failure test, and manifest regeneration |
| Build or packaging | Clean native build, archive contents, complete checksums, and an extracted-package smoke check |
| Documentation only | Verify commands against the current CLI and check links; no mining benchmark is needed |

Use the [build guide](docs/building.md) for exact commands. The CI workflow must remain limited to offline work and temporary localhost tests. Do not add live pool mining to GitHub Actions.

## Performance changes

Report the CPU, allowed CPU set, thread count, compiler, build flags, engine, timing method, sample lengths, and thermal/frequency conditions. Preserve the exact binaries or hashes used for both sides of a comparison. Separate an offline scan rate from a pool's credited rate and accepted shares. Do not treat a faster incorrect digest as an improvement.

Do not include private receiving addresses, rig inventories, internal hostnames, production logs, access tokens, or remote-access scripts in a contribution. The maintainer explicitly authorized publication of the developer receiving address in the disclosed 3% fee policy. That public fee destination does not authorize including unrelated user wallet settings. Changes to payout behavior or fees require prominent documentation and an explicit project decision.

## New CPU architectures

The release currently targets x86-64 and ARM64 Linux. A new architecture needs native build and correctness evidence, a baseline-safe capability probe, a supported process-cleanup mechanism, packaging, and honest documentation of the tested scope. A compiler accepting a source file is not enough to declare a platform supported.
