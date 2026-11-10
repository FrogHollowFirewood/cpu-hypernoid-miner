# Parano1d community directory submission draft

This is prepared text for the [Parano1d community project form](https://github.com/ignotusnemo/parano1d-site/issues/new?template=community-project.yml). Publication of this repository does not establish that the project has been submitted to, accepted by, or endorsed by the Parano1d directory maintainers.

## Request

Add a new project

## Project name

CPU HyperNOID Miner

## Project URL

https://froghollowfirewood.github.io/cpu-hypernoid-miner/

## Description

CPU HyperNOID Miner is a Linux CPU miner for Parano1d with automatic CPU-feature selection, four specialized x86 engines, and a portable fallback. It provides a TLS Stratum client, explicit temperature controls, source builds, release checksums, and detailed setup documentation.

## Maintainer or team

FrogHollowFirewood — https://github.com/FrogHollowFirewood

## Category

Miners

## Source code status

Open source, Apache License 2.0

## Source code URL

https://github.com/FrogHollowFirewood/cpu-hypernoid-miner

License: https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/blob/main/LICENSE

## Platforms

Linux

## Documentation and support

- Setup: https://froghollowfirewood.github.io/cpu-hypernoid-miner/getting-started.html
- Hardware: https://froghollowfirewood.github.io/cpu-hypernoid-miner/hardware.html
- Release: https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/releases/tag/v1.0.0
- Validation: https://froghollowfirewood.github.io/cpu-hypernoid-miner/validation.html
- Support: https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/issues

## Additional details

Version 1.0.0 targets 64-bit Linux x86-64 and ARM64. ARM64 uses the portable implementation; dedicated ARM vector optimization is not included. The published binary packages require glibc 2.34 or newer, Python 3.9 or newer, and Linux pidfd support. Release artifacts are qualified through native correctness workflows; their exact commit, architecture, and runtime hashes are recorded with each package.

The miner includes a clearly disclosed 3% miner-use fee allocated by scheduled runtime: 97 minutes for the user's pool and wallet, followed by up to 3 minutes for the published developer destination in a 100-minute cycle. A failed fee attempt returns to user work without catch-up. This is separate from pool fees and does not promise exactly 3% of shares or payouts. The full policy and receiving address are published at https://froghollowfirewood.github.io/cpu-hypernoid-miner/fees.html.

The client targets the Parano1d TLS Stratum flow exercised during development. Universal pool compatibility, direct solo-node RPC, a guaranteed hashrate, and long-duration stability across all supported CPU models are not claimed. The project is independent community software and is not an official Parano1d release.
